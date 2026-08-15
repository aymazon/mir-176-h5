"""The /ws endpoint and the app-lifetime gateway housekeeping task.

Per-connection flow (MVP-DESIGN 3.4/3.6): hello -> one-time auth -> in-game
session with a 200 ms snapshot task; on unexpected disconnect the character
stays in the world for the reconnect buffer, then the housekeeping task
saves it and removes it from the world.
"""

from __future__ import annotations

import time
from typing import Final

import anyio
from fastapi import APIRouter, WebSocket

from app.net.handlers import authenticate, message_loop
from app.net.protocol import HelloMsg
from app.net.session import (
    GatewayState,
    PendingSave,
    PlayerSession,
    now_ms,
    save_character,
)

router = APIRouter()

_HOUSEKEEPING_INTERVAL_S: Final[float] = 0.1


@router.websocket("/ws")
async def websocket_gateway(websocket: WebSocket) -> None:
    """WS endpoint: hello -> one-time auth -> shared-world session."""
    state: GatewayState = websocket.app.state.gateway
    await websocket.accept()
    session = PlayerSession(websocket, state)
    await session.send(HelloMsg(server_time=now_ms(), tick_ms=state.settings.tick_rate_ms))
    account_id = await authenticate(session)
    if account_id is None:
        return
    session.account_id = account_id
    async with anyio.create_task_group() as tasks:
        try:
            await message_loop(session, tasks)
        finally:
            tasks.cancel_scope.cancel()
    _schedule_reconnect_buffer(session)


def _schedule_reconnect_buffer(session: PlayerSession) -> None:
    """Keep an unexpectedly disconnected character in the world for the reconnect buffer."""
    if session.character is None or session.player_id is None:
        return
    if session.kicked or session.logged_out:
        return
    state = session.state
    state.sessions.add_pending(
        PendingSave(
            character_id=session.character.id,
            player_id=session.player_id,
            character=session.character.model_copy(deep=True),
            deadline_s=time.monotonic() + state.settings.ws_reconnect_buffer_s,
            owner=session,
        )
    )
    session.character = None
    session.player_id = None


async def gateway_housekeeping(state: GatewayState) -> None:
    """App-lifetime task: save and evict characters whose reconnect buffer expired."""
    while True:
        await anyio.sleep(_HOUSEKEEPING_INTERVAL_S)
        now = time.monotonic()
        for entry in state.sessions.pending():
            if entry.deadline_s > now:
                continue
            if state.sessions.pop_pending(entry.character_id) is None:
                continue
            if state.sessions.owner_of(entry.character_id) is not entry.owner:
                continue  # a reconnect took the character over
            player = state.world.get_player(entry.player_id)
            if player is not None:
                entry.character.x, entry.character.y, entry.character.map_id = (
                    player.x,
                    player.y,
                    player.map_id,
                )
            _ = state.world.remove_player(entry.player_id)
            state.sessions.release(entry.character_id, entry.owner)
            await save_character(state.pool, entry.character)
