"""Inbound message handling: one-time auth, character load, move/chat, dispatch."""

from __future__ import annotations

import hashlib
from typing import Final

import anyio
import asyncpg
from anyio.abc import TaskGroup
from fastapi import WebSocketDisconnect
from typing import assert_never

from app.api.auth import verify_ws_token
from app.config import Settings
from app.net.chat import ChatRules, load_sensitive_words
from app.net.protocol import (
    AuthMsg,
    CharacterState,
    ChatMsg,
    CharReadyMsg,
    CmdMsg,
    EventMsg,
    Inbound,
    InventoryState,
    LogoutCharMsg,
    MoveMsg,
    NpcMsg,
    PingMsg,
    PongMsg,
    QuestsState,
    SelectCharMsg,
    SnapshotMsg,
    SkillsState,
    parse_message,
)
from app.net.session import (
    CLOSE_AUTH_FAILED,
    CLOSE_AUTH_TIMEOUT,
    CLOSE_HEARTBEAT_MISSED,
    PlayerSession,
    now_ms,
    player_id_for,
)
from app.net.snapshot import vision_entities
from app.world.map import MapNotFoundError
from app.world.world import InvalidPositionError, PlayerAlreadyExistsError

# D14 chat rules, process-wide so the per-character rate-limit map survives a
# character reconnecting on a fresh connection (keyed by character_id, not session).
CHAT_RULES: Final[ChatRules] = ChatRules(
    words=load_sensitive_words(Settings().chat_sensitive_words_path)
)

_FETCH_CHARACTER_QUERY: Final[str] = (
    "SELECT id, name, class, level, exp, hp, mp, gold, bound_city, map_id, x, y "
    "FROM character WHERE id = $1 AND account_id = $2 AND deleted_at IS NULL"
)

_CONSUME_WS_TOKEN_QUERY: Final[str] = "DELETE FROM account_session WHERE ws_token_hash = $1"


def _character_from_row(row: asyncpg.Record) -> CharacterState:
    return CharacterState.model_validate(
        {
            "id": int(row["id"]),
            "name": row["name"],
            "class": row["class"],
            "level": int(row["level"]),
            "exp": int(row["exp"]),
            "hp": int(row["hp"]),
            "mp": int(row["mp"]),
            "gold": int(row["gold"]),
            "bound_city": row["bound_city"],
            "map_id": row["map_id"],
            "x": int(row["x"]),
            "y": int(row["y"]),
        }
    )


async def authenticate(session: PlayerSession) -> int | None:
    """Consume the one-time ws_token; None (socket already closed) on any failure."""
    settings = session.state.settings
    try:
        with anyio.fail_after(settings.ws_auth_timeout_s):
            raw = await session.websocket.receive_text()
    except TimeoutError:
        await session.close(CLOSE_AUTH_TIMEOUT)
        return None
    except WebSocketDisconnect:
        return None
    message = parse_message(raw)
    if not isinstance(message, AuthMsg):
        await session.close(CLOSE_AUTH_FAILED)
        return None
    pool = session.state.pool
    account_id = await verify_ws_token(pool, message.ws_token)
    if account_id is None:
        await session.close(CLOSE_AUTH_FAILED)
        return None
    token_hash = hashlib.sha256(message.ws_token.encode("ascii")).hexdigest()
    await pool.execute(_CONSUME_WS_TOKEN_QUERY, token_hash)
    return account_id


async def select_char(session: PlayerSession, tasks: TaskGroup, char_id: int) -> None:
    """Load the account's character into the shared world; kick a stale duplicate owner."""
    if session.character is not None or session.account_id is None:
        return
    pool = session.state.pool
    row = await pool.fetchrow(_FETCH_CHARACTER_QUERY, char_id, session.account_id)
    if row is None:
        return
    character = _character_from_row(row)
    state = session.state
    current_owner = state.sessions.owner_of(character.id)
    if current_owner is not None and current_owner is not session:
        await current_owner.kick("duplicate_login")
    try:
        player = state.world.add_player(
            player_id_for(character.id), character.map_id, character.x, character.y
        )
    except (InvalidPositionError, MapNotFoundError, PlayerAlreadyExistsError):
        return
    session.character = character
    session.player_id = player_id_for(character.id)
    state.sessions.assign(character.id, session)
    _ = tasks.start_soon(session.snapshot_loop)
    vision = SnapshotMsg(
        seq=0,
        char=character,
        entities=vision_entities(state.world, player, state.sessions.entity_identity),
    )
    await session.send(
        CharReadyMsg(
            character=character,
            vision=vision,
            inventory=InventoryState(gold=character.gold),
            skills=SkillsState(),
            quest_state=QuestsState(),
        )
    )


def _forward_move(session: PlayerSession, x: int, y: int) -> None:
    if session.character is None or session.player_id is None:
        return
    # The server owns reachability: a rejected intent is dropped silently.
    _ = session.state.world.move(session.player_id, x, y)


async def _broadcast_chat(session: PlayerSession, text: str) -> None:
    if session.character is None:
        return
    character = session.character
    broadcast_text = CHAT_RULES.process(character.id, text)
    if broadcast_text is None:
        return  # over-length or rate-limited: drop silently (no error frame)
    event = EventMsg(
        kind="chat",
        data={
            "character_id": character.id,
            "name": character.name,
            "text": broadcast_text,
            "server_time": now_ms(),
        },
    )
    for listener in session.state.sessions.in_game():
        await listener.send(event)


async def _dispatch(session: PlayerSession, tasks: TaskGroup, message: Inbound) -> None:
    match message:
        case AuthMsg():
            return  # already authenticated
        case SelectCharMsg(char_id=char_id):
            await select_char(session, tasks, char_id)
        case MoveMsg(x=x, y=y):
            _forward_move(session, x, y)
        case ChatMsg(text=text):
            await _broadcast_chat(session, text)
        case LogoutCharMsg():
            await session.logout()
        case PingMsg():
            await session.send(PongMsg())
        case CmdMsg() | NpcMsg():
            return  # manual combat and NPC flows land in W2/W3
        case _:
            assert_never(message)


async def _receive_with_heartbeat_timeout(session: PlayerSession) -> str | None:
    """The next inbound frame, or None when the heartbeat window (design 3.4) expires."""
    try:
        with anyio.fail_after(session.state.settings.ws_heartbeat_timeout_s):
            return await session.websocket.receive_text()
    except TimeoutError:
        return None


async def message_loop(session: PlayerSession, tasks: TaskGroup) -> None:
    """Read frames until disconnect, heartbeat expiry, or a server-side close."""
    while not session.finished:
        try:
            raw = await _receive_with_heartbeat_timeout(session)
        except WebSocketDisconnect:
            session.finished = True
            return
        if raw is None:
            await session.close(CLOSE_HEARTBEAT_MISSED)
            return
        message = parse_message(raw)
        if message is None:
            continue
        await _dispatch(session, tasks, message)
