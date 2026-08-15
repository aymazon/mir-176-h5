"""Background persistence worker (MVP-DESIGN 8.3/10.3).

Flushes online character state to the database three ways:
- periodically, every ``flush_interval_s`` (default ``Settings.save_interval_s``);
- immediately on demand via ``save_now`` for levelup/death/logout/disconnect events;
- one final full flush in ``stop`` on graceful shutdown.

The worker owns no character state of its own: online characters come from the
gateway's session registry, their live position from the shared world, and
every write reuses the gateway's single-row save query.
"""

from __future__ import annotations

import anyio

from app.net.protocol import CharacterState
from app.net.session import GatewayState, save_character
from app.world.world import Player


class PersistWorker:
    """Periodic + event-driven + shutdown flusher for online characters."""

    _state: GatewayState
    _interval_s: float
    _stopped: bool
    _flush_lock: anyio.Lock

    def __init__(self, state: GatewayState, flush_interval_s: float | None = None) -> None:
        self._state = state
        self._interval_s = state.settings.save_interval_s if flush_interval_s is None else flush_interval_s
        self._stopped = False
        self._flush_lock = anyio.Lock()

    @property
    def flush_interval_s(self) -> float:
        return self._interval_s

    async def run(self) -> None:
        """Flush every online character on the interval until stop() is called."""
        while True:
            await anyio.sleep(self._interval_s)
            if self._stopped:
                return
            _ = await self.flush_all()

    async def save_now(self, character_id: int) -> bool:
        """Immediate event save; True when a connected or reconnect-buffered character was written."""
        session = self._state.sessions.owner_of(character_id)
        if session is not None and session.character is not None:
            player = (
                self._state.world.get_player(session.player_id)
                if session.player_id is not None
                else None
            )
            self._sync_position(session.character, player)
            await save_character(self._state.pool, session.character)
            return True
        for entry in self._state.sessions.pending():
            if entry.character_id != character_id:
                continue
            self._sync_position(entry.character, self._state.world.get_player(entry.player_id))
            await save_character(self._state.pool, entry.character)
            return True
        return False

    async def flush_all(self) -> int:
        """Write every connected, selected character; return how many rows were saved."""
        async with self._flush_lock:
            saved = 0
            for session in self._state.sessions.in_game():
                if session.character is None or session.player_id is None:
                    continue
                self._sync_position(session.character, self._state.world.get_player(session.player_id))
                await save_character(self._state.pool, session.character)
                saved += 1
            return saved

    async def stop(self) -> None:
        """Exit the periodic loop and run the final full flush (graceful shutdown)."""
        self._stopped = True
        _ = await self.flush_all()

    @staticmethod
    def _sync_position(character: CharacterState, player: Player | None) -> None:
        """Track the live world position onto the character, as snapshots do."""
        if player is not None:
            character.x, character.y, character.map_id = player.x, player.y, player.map_id
