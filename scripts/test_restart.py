"""M0 ④ restart-persistence gate helper (driven by scripts/test_restart.sh).

Phase ``before``: register a unique account, create a warrior, enter the world
on WS, walk from the Biqi spawn (150,150) to (146,150), wait until the W1T5
periodic online flush has written the new position to the database, record
``before`` from the newest snapshot, and then KEEP THE WS OPEN — the character
must still be ONLINE (connected) when the shell SIGTERMs the server.

Phase ``verify``: re-login with the same account, read the character back from
``char_ready`` (which loads the database row), record ``after``, and assert
``before == after`` (the diff must be empty) and that movement actually
happened. Writes scripts/artifacts/restart_test.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import asyncpg
import httpx
import websockets

BASE_URL = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/ws"
STATE_FILE = Path("/tmp/test_restart_state.json")
ARTIFACT_PATH = Path(__file__).resolve().parent / "artifacts" / "restart_test.json"
DB_URL = os.environ.get(
    "TEST_RESTART_DB_URL", "postgresql://mir176:mir176@127.0.0.1:5432/mir-176-h5"
)

PASSWORD = "restart-pass-123"
MOVE_TARGET = (146, 150)  # 4 cells west of the Biqi spawn (150,150)
FIELDS = ("map_id", "x", "y", "hp", "mp", "exp", "gold", "level")


def fail(message: str) -> None:
    print(f"RESTART-GATE FAIL: {message}", file=sys.stderr, flush=True)
    sys.exit(1)


async def rest_login(username: str) -> str:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as http:
        response = await http.post(
            "/api/auth/login", json={"username": username, "password": PASSWORD}
        )
        if response.status_code != 200:
            fail(f"login returned {response.status_code}: {response.text}")
    return str(response.json()["token"])


async def rest_register(username: str) -> int:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as http:
        response = await http.post(
            "/api/auth/register", json={"username": username, "password": PASSWORD}
        )
        if response.status_code != 201:
            fail(f"register returned {response.status_code}: {response.text}")
    return int(response.json()["id"])


async def rest_create_character(jwt_token: str, name: str) -> int:
    headers = {"Authorization": f"Bearer {jwt_token}"}
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as http:
        response = await http.post(
            "/api/characters", headers=headers, json={"name": name, "class": "warrior"}
        )
        if response.status_code != 201:
            fail(f"character create returned {response.status_code}: {response.text}")
    return int(response.json()["id"])


async def rest_ws_token(jwt_token: str) -> str:
    headers = {"Authorization": f"Bearer {jwt_token}"}
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as http:
        response = await http.post("/api/auth/ws_token", headers=headers)
        if response.status_code != 200:
            fail(f"ws_token returned {response.status_code}: {response.text}")
    return str(response.json()["ws_token"])


async def wait_db_position(pool: asyncpg.Pool, char_id: int, timeout_s: float) -> None:
    """Wait until the W1T5 periodic online flush wrote the target position to the DB."""
    deadline = time.monotonic() + timeout_s
    last: dict[str, object] = {}
    while True:
        row = await pool.fetchrow(
            "SELECT map_id, x, y, hp, mp, exp, gold, level "
            "FROM character WHERE id = $1 AND deleted_at IS NULL",
            char_id,
        )
        if row is None:
            fail(f"character {char_id} missing from database")
        last = {key: row[key] for key in FIELDS}
        if int(last["x"]) == MOVE_TARGET[0] and int(last["y"]) == MOVE_TARGET[1]:
            return
        if time.monotonic() >= deadline:
            fail(f"database never reached {MOVE_TARGET} within {timeout_s}s; last={last}")
        await asyncio.sleep(0.25)


async def phase_before() -> None:
    suffix = uuid.uuid4().hex[:8]
    username = f"restart_{suffix}"
    account_id = await rest_register(username)
    jwt_token = await rest_login(username)
    char_id = await rest_create_character(jwt_token, username)

    async with websockets.connect(WS_URL, open_timeout=10) as ws:
        if json.loads(await ws.recv())["t"] != "hello":
            fail("first WS frame was not hello")
        ws_token = await rest_ws_token(jwt_token)
        await ws.send(json.dumps({"t": "auth", "ws_token": ws_token, "device": "restart-gate"}))
        await ws.send(json.dumps({"t": "select_char", "char_id": char_id}))
        ready = None
        while ready is None:
            message = json.loads(await ws.recv())
            if message["t"] == "char_ready":
                ready = message
        if (ready["character"]["x"], ready["character"]["y"]) != (150, 150):
            fail(
                "spawn point drifted: "
                f"{ready['character']['x']}, {ready['character']['y']}"
            )

        # 4 move intents at decreasing x, 300 ms apart (D21: 1 per 200 ms/connection).
        for step in range(1, 5):
            await ws.send(json.dumps({"t": "move", "x": 150 - step, "y": MOVE_TARGET[1]}))
            await asyncio.sleep(0.3)

        current = dict(ready["character"])
        deadline = time.monotonic() + 30.0
        while True:
            if time.monotonic() >= deadline:
                fail(f"character never reached {MOVE_TARGET}; last={current}")
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
            if message["t"] == "snapshot":
                current = message["char"]
                if (current["x"], current["y"]) == MOVE_TARGET:
                    break

        # Wait for the periodic online flush to persist the moved position while
        # the character is still connected, then record `before`.
        pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=1)
        try:
            await wait_db_position(pool, char_id, timeout_s=20.0)
        finally:
            await pool.close()

        before = {key: current[key] for key in FIELDS}
        if int(before["x"]) >= 150:
            fail("movement did not change the character position")

        STATE_FILE.write_text(
            json.dumps(
                {
                    "account_id": account_id,
                    "username": username,
                    "char_id": char_id,
                    "before": before,
                }
            ),
            encoding="utf-8",
        )
        print(f"RESTART-GATE before: {before}", flush=True)

        # Stay connected: the character must be ONLINE when the server is SIGTERMed.
        try:
            while True:
                await asyncio.wait_for(ws.recv(), timeout=120.0)
        except (websockets.ConnectionClosed, asyncio.TimeoutError):
            pass
    print("RESTART-GATE phase before: ws closed, exiting", flush=True)


async def phase_verify() -> None:
    if not STATE_FILE.exists():
        fail(f"state file missing: {STATE_FILE}")
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    username = str(state["username"])
    char_id = int(state["char_id"])
    before = {key: state["before"][key] for key in FIELDS}

    jwt_token = await rest_login(username)
    ws_token = await rest_ws_token(jwt_token)

    after: dict[str, object] | None = None
    async with websockets.connect(WS_URL, open_timeout=10) as ws:
        if json.loads(await ws.recv())["t"] != "hello":
            fail("first WS frame was not hello (after restart)")
        await ws.send(json.dumps({"t": "auth", "ws_token": ws_token, "device": "restart-gate"}))
        await ws.send(json.dumps({"t": "select_char", "char_id": char_id}))
        while after is None:
            message = json.loads(await ws.recv())
            if message["t"] == "char_ready":
                after = {key: message["character"][key] for key in FIELDS}
    if after is None:
        fail("no char_ready after restart")

    diff = {key: {"before": before[key], "after": after[key]} for key in FIELDS if before[key] != after[key]}
    result = {"before": before, "after": after, "diff": diff}
    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"RESTART-GATE after: {after}", flush=True)
    print(f"RESTART-GATE diff: {diff}", flush=True)
    print(f"RESTART-GATE artifact: {ARTIFACT_PATH}", flush=True)

    if int(before["x"]) >= 150:
        fail("position never moved before the restart; gate is meaningless")
    if diff:
        fail(f"fields changed across the restart: {diff}")
    print("RESTART-GATE OK: all tracked fields survived the restart", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("before", "verify"))
    args = parser.parse_args()
    if args.phase == "before":
        asyncio.run(phase_before())
    else:
        asyncio.run(phase_verify())


if __name__ == "__main__":
    main()
