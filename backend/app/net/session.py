"""Per-connection session state and the live-connection registry (MVP-DESIGN 3.4)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final

import anyio
import asyncpg
from fastapi import WebSocket, WebSocketDisconnect

from app.config import Settings
from app.net.protocol import (
    CharacterState,
    KickMsg,
    Outbound,
    SnapshotMsg,
    encode_message,
)
from app.net.snapshot import EntityIdentity, trim_snapshot, vision_entities
from app.world.world import GameWorld

CLOSE_NORMAL: Final[int] = 1000
CLOSE_KICKED: Final[int] = 4000
CLOSE_AUTH_FAILED: Final[int] = 4001
CLOSE_AUTH_TIMEOUT: Final[int] = 4008
CLOSE_HEARTBEAT_MISSED: Final[int] = 4009

_PLAYER_ID_PREFIX: Final[str] = "p"

_SAVE_CHARACTER_QUERY: Final[str] = (
    "UPDATE character SET level = $2, exp = $3, hp = $4, mp = $5, gold = $6, "
    "map_id = $7, x = $8, y = $9, last_save = now() WHERE id = $1 AND deleted_at IS NULL"
)


def player_id_for(character_id: int) -> str:
    """World entity key for a character; the p prefix keeps ids out of the numeric namespace."""
    return f"{_PLAYER_ID_PREFIX}{character_id}"


def character_id_of(player_id: str) -> int:
    return int(player_id.removeprefix(_PLAYER_ID_PREFIX))


async def save_character(pool: asyncpg.Pool, character: CharacterState) -> None:
    """Write the character's live state back to its row (MVP-DESIGN 8.3 event save)."""
    await pool.execute(
        _SAVE_CHARACTER_QUERY,
        character.id,
        character.level,
        character.exp,
        character.hp,
        character.mp,
        character.gold,
        character.map_id,
        character.x,
        character.y,
    )


@dataclass(frozen=True, slots=True)
class PendingSave:
    """A disconnected character kept in the world during its reconnect buffer."""

    character_id: int
    player_id: str
    character: CharacterState
    deadline_s: float
    owner: PlayerSession


class SessionRegistry:
    """Live-connection bookkeeping: character ownership plus pending reconnect saves."""

    def __init__(self) -> None:
        self._character_owners: dict[int, PlayerSession] = {}
        self._pending: dict[int, PendingSave] = {}

    def owner_of(self, character_id: int) -> PlayerSession | None:
        return self._character_owners.get(character_id)

    def assign(self, character_id: int, session: PlayerSession) -> None:
        self._character_owners[character_id] = session

    def release(self, character_id: int, session: PlayerSession) -> None:
        if self._character_owners.get(character_id) is session:
            del self._character_owners[character_id]

    def in_game(self) -> list[PlayerSession]:
        """Sessions that currently play a character (chat broadcast targets)."""
        return list(self._character_owners.values())

    def entity_identity(self, player_id: str) -> EntityIdentity | None:
        session = self._character_owners.get(character_id_of(player_id))
        if session is None or session.character is None:
            return None
        return EntityIdentity(character_id=session.character.id, name=session.character.name)

    def add_pending(self, entry: PendingSave) -> None:
        self._pending[entry.character_id] = entry

    def pop_pending(self, character_id: int) -> PendingSave | None:
        return self._pending.pop(character_id, None)

    def pending(self) -> list[PendingSave]:
        return list(self._pending.values())


@dataclass(frozen=True, slots=True)
class GatewayState:
    """App-scoped gateway wiring, stored on ``app.state`` by the lifespan."""

    world: GameWorld
    sessions: SessionRegistry
    pool: asyncpg.Pool
    settings: Settings


class PlayerSession:
    """One WS connection and the character it currently plays.

    Mutable by design: the message loop, the snapshot task, and the
    housekeeping task all advance this state across the connection's life.
    """

    websocket: WebSocket
    state: GatewayState
    account_id: int | None
    character: CharacterState | None
    player_id: str | None
    seq: int
    finished: bool
    kicked: bool
    logged_out: bool
    _send_lock: anyio.Lock

    def __init__(self, websocket: WebSocket, state: GatewayState) -> None:
        self.websocket = websocket
        self.state = state
        self.account_id = None
        self.character = None
        self.player_id = None
        self.seq = 0
        self.finished = False
        self.kicked = False
        self.logged_out = False
        self._send_lock = anyio.Lock()

    async def send(self, message: Outbound) -> None:
        if self.finished:
            return
        async with self._send_lock:
            if self.finished:
                return
            try:
                await self.websocket.send_text(encode_message(message))
            except WebSocketDisconnect:
                self.finished = True

    async def close(self, code: int) -> None:
        if self.finished:
            return
        async with self._send_lock:
            if self.finished:
                return
            self.finished = True
            try:
                await self.websocket.close(code=code)
            except WebSocketDisconnect:
                return

    async def snapshot_loop(self) -> None:
        """Push a D5-trimmed vision snapshot every ``snapshot_ms`` while in game."""
        interval_s = self.state.settings.snapshot_ms / 1000.0
        while True:
            await anyio.sleep(interval_s)
            await self.push_snapshot()

    async def push_snapshot(self) -> None:
        if self.finished or self.character is None or self.player_id is None:
            return
        player = self.state.world.get_player(self.player_id)
        if player is None:
            return
        self.character.x = player.x
        self.character.y = player.y
        self.character.map_id = player.map_id
        self.seq += 1
        entities = vision_entities(self.state.world, player, self.state.sessions.entity_identity)
        snapshot = SnapshotMsg(seq=self.seq, char=self.character, entities=entities)
        await self.send(trim_snapshot(snapshot))

    async def kick(self, reason: str) -> None:
        """Drop this connection (duplicate login); the new owner keeps the character."""
        self.kicked = True
        if self.character is not None and self.player_id is not None:
            self.state.sessions.release(self.character.id, self)
            _ = self.state.world.remove_player(self.player_id)
            self.character = None
            self.player_id = None
        await self.send(KickMsg(reason=reason))
        await self.close(CLOSE_KICKED)

    async def logout(self) -> None:
        """Save the character at its live position, leave the world, and close."""
        if self.logged_out or self.character is None:
            return
        self.logged_out = True
        character = self.character
        if self.player_id is not None:
            player = self.state.world.get_player(self.player_id)
            if player is not None:
                character.x, character.y, character.map_id = player.x, player.y, player.map_id
            self.state.sessions.release(character.id, self)
            _ = self.state.world.remove_player(self.player_id)
        self.character = None
        self.player_id = None
        await save_character(self.state.pool, character)
        await self.close(CLOSE_NORMAL)


def now_ms() -> int:
    return int(time.time() * 1000)
