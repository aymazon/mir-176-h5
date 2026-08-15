"""Gateway tests: WS handshake, one-time auth, character load, vision, kick, heartbeat.

The real app lifespan runs inside TestClient, so the database pool, the shared
world, the game loop, and the housekeeping task all live on the TestClient
portal loop and are torn down with the client context. Timings are shrunk via
env overrides (SNAPSHOT_MS, WS_AUTH_TIMEOUT_S, WS_RECONNECT_BUFFER_S) so the
suite stays fast without touching the production defaults in Settings.
"""

import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import anyio
import asyncpg
import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.main import app
from app.net.protocol import CharacterState, EntityState, SnapshotMsg, encode_message
from app.net.session import (
    CLOSE_AUTH_FAILED,
    CLOSE_AUTH_TIMEOUT,
    CLOSE_KICKED,
    CLOSE_NORMAL,
)
from app.net.snapshot import SNAPSHOT_MAX_BYTES, trim_snapshot

TEST_PASSWORD = "correct-horse-battery"


def unique_username() -> str:
    return f"t{uuid.uuid4().hex[:16]}"


def unique_character_name() -> str:
    return f"c{uuid.uuid4().hex[:15]}"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SNAPSHOT_MS", "50")
    monkeypatch.setenv("WS_AUTH_TIMEOUT_S", "1")
    monkeypatch.setenv("WS_RECONNECT_BUFFER_S", "1")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def account_ids() -> Iterator[list[int]]:
    created: list[int] = []
    yield created
    if created:
        anyio.run(_purge_accounts, list(created))


async def _purge_accounts(ids: list[int]) -> None:
    pool = await asyncpg.create_pool(Settings().database_url, min_size=1, max_size=1)
    try:
        await pool.execute("DELETE FROM character WHERE account_id = ANY($1::bigint[])", ids)
        await pool.execute("DELETE FROM account_session WHERE account_id = ANY($1::bigint[])", ids)
        await pool.execute("DELETE FROM account WHERE id = ANY($1::bigint[])", ids)
    finally:
        await pool.close()


def register_and_login(client: TestClient, account_ids: list[int]) -> str:
    username = unique_username()
    response = client.post(
        "/api/auth/register", json={"username": username, "password": TEST_PASSWORD}
    )
    assert response.status_code == 201, response.text
    account_ids.append(int(response.json()["id"]))
    login = client.post(
        "/api/auth/login", json={"username": username, "password": TEST_PASSWORD}
    )
    assert login.status_code == 200, login.text
    return str(login.json()["token"])


def create_character(client: TestClient, jwt_token: str) -> dict[str, Any]:
    response = client.post(
        "/api/characters",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={"name": unique_character_name(), "class": "warrior"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def issue_ws_token(client: TestClient, jwt_token: str) -> str:
    response = client.post(
        "/api/auth/ws_token", headers={"Authorization": f"Bearer {jwt_token}"}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["ws_token"])


@contextmanager
def authed_session(client: TestClient, jwt_token: str) -> Iterator[WebSocketTestSession]:
    """A WS session that has verified hello and completed the one-time auth handshake."""
    with client.websocket_connect("/ws") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["t"] == "hello"
        assert isinstance(hello["server_time"], int)
        assert hello["tick_ms"] == Settings().tick_rate_ms
        ws.send_text(
            json.dumps(
                {"t": "auth", "ws_token": issue_ws_token(client, jwt_token), "device": "test-h5"}
            )
        )
        yield ws


def expect_message(
    ws: WebSocketTestSession, message_type: str, timeout_s: float = 3.0
) -> dict[str, Any]:
    """Read frames until one of message_type arrives; assert it within the deadline."""
    deadline = time.monotonic() + timeout_s
    while True:
        assert time.monotonic() < deadline, f"no {message_type} frame within {timeout_s}s"
        frame = json.loads(ws.receive_text())
        if frame["t"] == message_type:
            return frame


def entity_in_snapshots(
    ws: WebSocketTestSession, character_id: int, timeout_s: float = 3.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while True:
        assert time.monotonic() < deadline, f"character {character_id} never entered vision"
        frame = json.loads(ws.receive_text())
        if frame["t"] != "snapshot":
            continue
        for entity in frame["entities"]:
            if entity["id"] == character_id:
                return entity


def test_connect_sends_hello_and_closes_without_auth(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["t"] == "hello"
        start = time.monotonic()
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_text()
        assert time.monotonic() - start < 3.0
        assert excinfo.value.code == CLOSE_AUTH_TIMEOUT


def test_invalid_ws_token_is_rejected(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"t": "auth", "ws_token": "0" * 64, "device": "test-h5"}))
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_text()
        assert excinfo.value.code == CLOSE_AUTH_FAILED


def test_select_char_receives_char_ready_then_logout_closes(
    client: TestClient, account_ids: list[int]
) -> None:
    jwt_token = register_and_login(client, account_ids)
    character = create_character(client, jwt_token)
    with authed_session(client, jwt_token) as ws:
        ws.send_text(json.dumps({"t": "select_char", "char_id": character["id"]}))
        ready = expect_message(ws, "char_ready")
        assert ready["character"]["id"] == character["id"]
        assert ready["character"]["name"] == character["name"]
        assert ready["character"]["class"] == "warrior"
        assert ready["character"]["map_id"] == "m3"
        assert ready["vision"]["seq"] == 0
        assert ready["vision"]["entities"] == []
        assert ready["inventory"] == {"items": [], "gold": 0}
        assert ready["skills"] == {"skills": []}
        assert ready["quest_state"] == {"quests": []}
        ws.send_text(json.dumps({"t": "logout_char"}))
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_text()
        assert excinfo.value.code == CLOSE_NORMAL


def test_two_clients_in_same_map_see_each_other(
    client: TestClient, account_ids: list[int]
) -> None:
    jwt_a = register_and_login(client, account_ids)
    jwt_b = register_and_login(client, account_ids)
    char_a = create_character(client, jwt_a)
    char_b = create_character(client, jwt_b)
    with authed_session(client, jwt_a) as ws_a, authed_session(client, jwt_b) as ws_b:
        ws_a.send_text(json.dumps({"t": "select_char", "char_id": char_a["id"]}))
        ws_b.send_text(json.dumps({"t": "select_char", "char_id": char_b["id"]}))
        expect_message(ws_a, "char_ready")
        expect_message(ws_b, "char_ready")
        seen_b = entity_in_snapshots(ws_a, char_b["id"])
        seen_a = entity_in_snapshots(ws_b, char_a["id"])
        assert seen_b["name"] == char_b["name"]
        assert seen_b["kind"] == "player"
        assert (seen_b["x"], seen_b["y"]) == (150, 150)
        assert seen_a["name"] == char_a["name"]
        assert (seen_a["x"], seen_a["y"]) == (150, 150)


def test_snapshot_seq_is_monotonically_increasing(
    client: TestClient, account_ids: list[int]
) -> None:
    jwt_token = register_and_login(client, account_ids)
    character = create_character(client, jwt_token)
    with authed_session(client, jwt_token) as ws:
        ws.send_text(json.dumps({"t": "select_char", "char_id": character["id"]}))
        expect_message(ws, "char_ready")
        seqs = [int(expect_message(ws, "snapshot")["seq"]) for _ in range(3)]
        assert seqs == [1, 2, 3]


def test_duplicate_login_kicks_first_connection(
    client: TestClient, account_ids: list[int]
) -> None:
    jwt_token = register_and_login(client, account_ids)
    character = create_character(client, jwt_token)
    with authed_session(client, jwt_token) as ws1:
        ws1.send_text(json.dumps({"t": "select_char", "char_id": character["id"]}))
        expect_message(ws1, "char_ready")
        with authed_session(client, jwt_token) as ws2:
            ws2.send_text(json.dumps({"t": "select_char", "char_id": character["id"]}))
            ready2 = expect_message(ws2, "char_ready")
            assert ready2["character"]["id"] == character["id"]
            assert ready2["vision"]["entities"] == []
            kick = expect_message(ws1, "kick")
            assert kick["reason"] == "duplicate_login"
            with pytest.raises(WebSocketDisconnect) as excinfo:
                ws1.receive_text()
            assert excinfo.value.code == CLOSE_KICKED


def test_ping_receives_pong(client: TestClient, account_ids: list[int]) -> None:
    jwt_token = register_and_login(client, account_ids)
    character = create_character(client, jwt_token)
    with authed_session(client, jwt_token) as ws:
        ws.send_text(json.dumps({"t": "select_char", "char_id": character["id"]}))
        expect_message(ws, "char_ready")
        ws.send_text(json.dumps({"t": "ping"}))
        assert expect_message(ws, "pong") == {"t": "pong"}


def test_chat_is_broadcast_to_all_online_players(
    client: TestClient, account_ids: list[int]
) -> None:
    jwt_a = register_and_login(client, account_ids)
    jwt_b = register_and_login(client, account_ids)
    char_a = create_character(client, jwt_a)
    char_b = create_character(client, jwt_b)
    with authed_session(client, jwt_a) as ws_a, authed_session(client, jwt_b) as ws_b:
        ws_a.send_text(json.dumps({"t": "select_char", "char_id": char_a["id"]}))
        ws_b.send_text(json.dumps({"t": "select_char", "char_id": char_b["id"]}))
        expect_message(ws_a, "char_ready")
        expect_message(ws_b, "char_ready")
        ws_a.send_text(json.dumps({"t": "chat", "text": "hello mir"}))
        for ws in (ws_a, ws_b):
            event = expect_message(ws, "event")
            assert event["kind"] == "chat"
            assert event["data"]["text"] == "hello mir"
            assert event["data"]["name"] == char_a["name"]


def test_move_advances_character_position(
    client: TestClient, account_ids: list[int]
) -> None:
    jwt_token = register_and_login(client, account_ids)
    character = create_character(client, jwt_token)
    with authed_session(client, jwt_token) as ws:
        ws.send_text(json.dumps({"t": "select_char", "char_id": character["id"]}))
        expect_message(ws, "char_ready")
        ws.send_text(json.dumps({"t": "move", "x": 151, "y": 150}))
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            frame = json.loads(ws.receive_text())
            if frame["t"] == "snapshot" and frame["char"]["x"] == 151:
                break
        else:
            pytest.fail("character never reached (151, 150) in 5s")


def test_unknown_frames_after_auth_are_ignored(
    client: TestClient, account_ids: list[int]
) -> None:
    jwt_token = register_and_login(client, account_ids)
    character = create_character(client, jwt_token)
    with authed_session(client, jwt_token) as ws:
        ws.send_text(json.dumps({"t": "select_char", "char_id": character["id"]}))
        expect_message(ws, "char_ready")
        ws.send_text(json.dumps({"t": "bogus", "payload": {"deep": True}}))
        ws.send_text("{this is not json")
        ws.send_text(json.dumps({"t": "ping"}))
        assert expect_message(ws, "pong") == {"t": "pong"}


def test_snapshot_is_trimmed_to_the_byte_budget() -> None:
    character = CharacterState.model_validate(
        {
            "id": 1,
            "name": "hero",
            "class": "warrior",
            "level": 1,
            "exp": 0,
            "hp": 33,
            "mp": 11,
            "gold": 0,
            "bound_city": "bichon",
            "map_id": "m3",
            "x": 150,
            "y": 150,
        }
    )
    entities = [
        EntityState(id=1000 + i, name=f"walker-{i:04d}", x=150 + i % 40, y=150 + (i * 7) % 40)
        for i in range(300)
    ]
    snapshot = SnapshotMsg(seq=1, char=character, entities=entities)
    assert len(encode_message(snapshot).encode("utf-8")) > SNAPSHOT_MAX_BYTES
    trimmed = trim_snapshot(snapshot)
    assert len(encode_message(trimmed).encode("utf-8")) <= SNAPSHOT_MAX_BYTES
    kept_ids = {entity.id for entity in trimmed.entities}
    assert 1000 in kept_ids  # the on-cell entity is closest and always survives
    assert 1299 not in kept_ids  # an edge entity at Chebyshev distance 40 is trimmed
