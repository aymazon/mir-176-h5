"""PersistWorker tests (MVP-DESIGN 8.3/10.3): periodic flush, immediate save_now,
shutdown final flush, idempotent re-flush, and the reconnect-buffer save path.

Every test runs the worker against the live database with a short flush interval
injected into the constructor, so no test waits on the 30 s production default.
Each test builds a minimal online presence in memory (world player + session)
around a freshly inserted character row and deletes its rows in teardown.
"""

import uuid
from collections.abc import AsyncIterator, Callable, MutableMapping
from typing import Final

import anyio
import asyncpg
import pytest
from fastapi import WebSocket

from app.config import Settings
from app.db.persist_worker import PersistWorker
from app.net.protocol import CharacterState
from app.net.session import GatewayState, PendingSave, PlayerSession, SessionRegistry, player_id_for
from app.world.world import GameWorld, Player

MAP_ID = "m3"
SPAWN_X = 150
SPAWN_Y = 150

_INSERT_CHARACTER_SQL: Final[str] = (
    "WITH acct AS (INSERT INTO account (username, password_hash) VALUES ($1, $2) RETURNING id) "
    "INSERT INTO character (account_id, name, class, level, exp, hp, mp, gold, map_id, x, y) "
    "SELECT acct.id, $3, 'warrior', 1, 0, 100, 50, 10, $4, $5, $6 FROM acct RETURNING *"
)


async def _noop_receive() -> dict[str, object]:
    return {"type": "websocket.disconnect"}


async def _noop_send(_message: MutableMapping[str, object]) -> None:
    return None


@pytest.fixture
async def pool() -> AsyncIterator[asyncpg.Pool]:
    test_pool = await asyncpg.create_pool(Settings().database_url, min_size=1, max_size=2)
    yield test_pool
    await test_pool.close()


@pytest.fixture
async def character(pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Record]:
    row = await pool.fetchrow(
        _INSERT_CHARACTER_SQL,
        f"t{uuid.uuid4().hex[:16]}",
        "persist-test",
        f"c{uuid.uuid4().hex[:15]}",
        MAP_ID,
        SPAWN_X,
        SPAWN_Y,
    )
    assert row is not None, "character insert failed"
    yield row
    await pool.execute("DELETE FROM character WHERE id = $1", int(row["id"]))
    await pool.execute("DELETE FROM account WHERE id = $1", int(row["account_id"]))


def make_state(pool: asyncpg.Pool) -> GatewayState:
    return GatewayState(world=GameWorld(), sessions=SessionRegistry(), pool=pool, settings=Settings())


def online_character(row: asyncpg.Record) -> CharacterState:
    return CharacterState.model_validate(
        {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "class": str(row["class"]),
            "level": int(row["level"]),
            "exp": int(row["exp"]),
            "hp": int(row["hp"]),
            "mp": int(row["mp"]),
            "gold": int(row["gold"]),
            "bound_city": str(row["bound_city"]),
            "map_id": str(row["map_id"]),
            "x": int(row["x"]),
            "y": int(row["y"]),
        }
    )


def place_online(state: GatewayState, char_state: CharacterState) -> tuple[PlayerSession, Player]:
    """Register the character as connected: world player + session + ownership.

    The session's websocket is a stand-in; the tests never send frames on it.
    """
    session = PlayerSession(WebSocket({"type": "websocket"}, _noop_receive, _noop_send), state)
    player = state.world.add_player(player_id_for(char_state.id), char_state.map_id, SPAWN_X, SPAWN_Y)
    session.character = char_state
    session.player_id = player.id
    state.sessions.assign(char_state.id, session)
    return session, player


def row_values(row: asyncpg.Record) -> tuple[int, int, int, int, int, int]:
    """(level, exp, hp, gold, x, y) as ints, for compact assertions."""
    return (int(row["level"]), int(row["exp"]), int(row["hp"]), int(row["gold"]), int(row["x"]), int(row["y"]))


async def wait_for_row(
    pool: asyncpg.Pool,
    character_id: int,
    matches: Callable[[asyncpg.Record], bool],
    timeout_s: float = 5.0,
) -> asyncpg.Record:
    deadline = anyio.current_time() + timeout_s
    while True:
        row = await pool.fetchrow("SELECT * FROM character WHERE id = $1", character_id)
        assert row is not None, f"character {character_id} vanished from the database"
        if matches(row):
            return row
        if anyio.current_time() >= deadline:
            pytest.fail(f"row did not match within {timeout_s}s: {dict(row)}")
        await anyio.sleep(0.02)


async def test_periodic_flush_writes_live_state_and_position(
    pool: asyncpg.Pool, character: asyncpg.Record
) -> None:
    character_id = int(character["id"])
    state = make_state(pool)
    char_state = online_character(character)
    _session, player = place_online(state, char_state)
    char_state.level, char_state.exp, char_state.hp, char_state.gold = 2, 500, 80, 777
    player.x, player.y = SPAWN_X + 1, SPAWN_Y + 1
    worker = PersistWorker(state, flush_interval_s=0.05)
    async with anyio.create_task_group() as tasks:
        _ = tasks.start_soon(worker.run)
        row = await wait_for_row(
            pool, character_id, lambda r: row_values(r) == (2, 500, 80, 777, SPAWN_X + 1, SPAWN_Y + 1)
        )
        assert str(row["map_id"]) == MAP_ID
        await worker.stop()


async def test_save_now_writes_immediately_without_periodic_loop(
    pool: asyncpg.Pool, character: asyncpg.Record
) -> None:
    character_id = int(character["id"])
    state = make_state(pool)
    char_state = online_character(character)
    _session, player = place_online(state, char_state)
    char_state.level, char_state.gold = 3, 4242
    player.x, player.y = 160, 140
    worker = PersistWorker(state)
    assert await worker.save_now(character_id) is True
    row = await pool.fetchrow("SELECT * FROM character WHERE id = $1", character_id)
    assert row is not None
    assert row_values(row) == (3, 0, 100, 4242, 160, 140)
    assert await worker.save_now(character_id + 1) is False


async def test_stop_flushes_changes_made_after_last_periodic(
    pool: asyncpg.Pool, character: asyncpg.Record
) -> None:
    character_id = int(character["id"])
    state = make_state(pool)
    char_state = online_character(character)
    _session, player = place_online(state, char_state)
    char_state.level, char_state.gold = 2, 111
    worker = PersistWorker(state, flush_interval_s=0.3)
    async with anyio.create_task_group() as tasks:
        _ = tasks.start_soon(worker.run)
        _ = await wait_for_row(pool, character_id, lambda r: row_values(r) == (2, 0, 100, 111, SPAWN_X, SPAWN_Y))
        char_state.level, char_state.gold = 3, 222
        player.x, player.y = 170, 130
        await worker.stop()
    row = await pool.fetchrow("SELECT * FROM character WHERE id = $1", character_id)
    assert row is not None
    assert row_values(row) == (3, 0, 100, 222, 170, 130)


async def test_flush_is_idempotent(pool: asyncpg.Pool, character: asyncpg.Record) -> None:
    character_id = int(character["id"])
    state = make_state(pool)
    char_state = online_character(character)
    _session, _player = place_online(state, char_state)
    char_state.gold = 999
    worker = PersistWorker(state, flush_interval_s=0.05)
    assert await worker.flush_all() == 1
    assert await worker.flush_all() == 1
    row = await pool.fetchrow("SELECT * FROM character WHERE id = $1", character_id)
    assert row is not None
    assert row_values(row) == (1, 0, 100, 999, SPAWN_X, SPAWN_Y)
    assert await worker.save_now(character_id) is True


async def test_save_now_covers_reconnect_buffered_character(
    pool: asyncpg.Pool, character: asyncpg.Record
) -> None:
    character_id = int(character["id"])
    state = make_state(pool)
    char_state = online_character(character)
    session, player = place_online(state, char_state)
    char_state.gold = 555
    state.sessions.add_pending(
        PendingSave(
            character_id=character_id,
            player_id=player.id,
            character=char_state.model_copy(deep=True),
            deadline_s=anyio.current_time() + 30.0,
            owner=session,
        )
    )
    session.character = None
    session.player_id = None
    worker = PersistWorker(state)
    assert await worker.save_now(character_id) is True
    row = await pool.fetchrow("SELECT * FROM character WHERE id = $1", character_id)
    assert row is not None
    assert int(row["gold"]) == 555
