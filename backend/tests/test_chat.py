"""Chat rules (D14): unit tests for the policy module plus wire-level tests
through the real gateway (broadcast, drop, rate limit, sanitization).

Wire tests run the real app lifespan inside TestClient against the database
(same pattern as test_gateway.py); the rate-limit tests swap
``handlers.CHAT_RULES`` for a fake-clock instance so timing is deterministic.
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

from app.config import Settings
from app.main import app
from app.net import handlers
from app.net.chat import CH_MAX_LENGTH, CH_RATE_LIMIT_S, ChatRules, load_sensitive_words

TEST_PASSWORD = "correct-horse-battery"


def unique_username() -> str:
    return f"t{uuid.uuid4().hex[:16]}"


def unique_character_name() -> str:
    return f"c{uuid.uuid4().hex[:15]}"


class FakeClock:
    """Manual monotonic clock for deterministic rate-limit tests."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


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
        ws.send_text(
            json.dumps(
                {"t": "auth", "ws_token": issue_ws_token(client, jwt_token), "device": "test-h5"}
            )
        )
        yield ws


def chat_events(frames: list[dict[str, Any]]) -> list[str]:
    return [
        str(frame["data"]["text"])
        for frame in frames
        if frame["t"] == "event" and frame.get("kind") == "chat"
    ]


def drain_until(
    ws: WebSocketTestSession, frame_type: str, timeout_s: float = 3.0
) -> list[dict[str, Any]]:
    """Read frames until one of frame_type arrives; return the frames seen before it."""
    collected: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout_s
    while True:
        assert time.monotonic() < deadline, f"no {frame_type} frame within {timeout_s}s"
        frame = json.loads(ws.receive_text())
        if frame["t"] == frame_type:
            return collected
        collected.append(frame)


def expect_chat(
    ws: WebSocketTestSession, expected_text: str, timeout_s: float = 3.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while True:
        assert time.monotonic() < deadline, f"no chat frame with {expected_text!r} in {timeout_s}s"
        frame = json.loads(ws.receive_text())
        if frame["t"] != "event" or frame.get("kind") != "chat":
            continue
        if frame["data"]["text"] == expected_text:
            return frame


# ---------------------------------------------------------------------------
# ChatRules unit tests (no server involved)
# ---------------------------------------------------------------------------


def test_process_accepts_up_to_max_length() -> None:
    rules = ChatRules()
    assert rules.process(1, "x" * CH_MAX_LENGTH) == "x" * CH_MAX_LENGTH
    assert rules.process(2, "x" * (CH_MAX_LENGTH + 1)) is None


def test_process_rate_limit_uses_fake_clock_deterministically() -> None:
    clock = FakeClock()
    rules = ChatRules(clock=clock)
    assert rules.process(1, "one") == "one"
    clock.now = CH_RATE_LIMIT_S - 0.001
    assert rules.process(1, "two") is None  # within the 1s window: dropped
    clock.now = CH_RATE_LIMIT_S
    assert rules.process(1, "three") == "three"  # window elapsed: accepted


def test_rate_limit_is_independent_per_character() -> None:
    clock = FakeClock()
    rules = ChatRules(clock=clock)
    assert rules.process(1, "a") == "a"
    clock.now = 0.3
    assert rules.process(2, "b") == "b"  # different character: not limited by 1
    clock.now = 0.5
    assert rules.process(1, "c") is None  # character 1 is still within its window


def test_sanitize_replaces_every_occurrence() -> None:
    rules = ChatRules(words=frozenset({"骗子"}))
    assert rules.sanitize("他是骗子，是个骗子") == "他是**，是个**"
    assert rules.sanitize("没有命中") == "没有命中"


def test_sanitize_prefers_longest_matching_word() -> None:
    rules = ChatRules(words=frozenset({"赌", "赌博"}))
    assert rules.sanitize("赌博") == "**"
    assert rules.sanitize("赌") == "**"


def test_load_sensitive_words_skips_comments_and_blank_lines(tmp_path) -> None:
    path = tmp_path / "words.txt"
    path.write_text("# comment\n\n骗子\n  诈骗  \n#骗子\n", encoding="utf-8")
    assert load_sensitive_words(path) == frozenset({"骗子", "诈骗"})


def test_load_sensitive_words_missing_file_yields_no_words(tmp_path) -> None:
    assert load_sensitive_words(tmp_path / "absent.txt") == frozenset()


# ---------------------------------------------------------------------------
# Wire-level tests through the real gateway
# ---------------------------------------------------------------------------


def test_chat_is_broadcast_to_two_in_game_clients(
    client: TestClient, account_ids: list[int]
) -> None:
    jwt_a = register_and_login(client, account_ids)
    jwt_b = register_and_login(client, account_ids)
    char_a = create_character(client, jwt_a)
    char_b = create_character(client, jwt_b)
    with authed_session(client, jwt_a) as ws_a, authed_session(client, jwt_b) as ws_b:
        ws_a.send_text(json.dumps({"t": "select_char", "char_id": char_a["id"]}))
        ws_b.send_text(json.dumps({"t": "select_char", "char_id": char_b["id"]}))
        for ws in (ws_a, ws_b):
            json.loads(ws.receive_text())  # char_ready
        ws_a.send_text(json.dumps({"t": "chat", "text": "你好 mir"}))
        for ws in (ws_a, ws_b):
            event = expect_chat(ws, "你好 mir")
            assert event["data"]["name"] == char_a["name"]
            assert event["data"]["character_id"] == char_a["id"]
            assert isinstance(event["data"]["server_time"], int)


def test_chat_over_100_chars_is_dropped(
    client: TestClient, account_ids: list[int]
) -> None:
    jwt_a = register_and_login(client, account_ids)
    jwt_b = register_and_login(client, account_ids)
    char_a = create_character(client, jwt_a)
    char_b = create_character(client, jwt_b)
    with authed_session(client, jwt_a) as ws_a, authed_session(client, jwt_b) as ws_b:
        ws_a.send_text(json.dumps({"t": "select_char", "char_id": char_a["id"]}))
        ws_b.send_text(json.dumps({"t": "select_char", "char_id": char_b["id"]}))
        for ws in (ws_a, ws_b):
            json.loads(ws.receive_text())  # char_ready
        ws_a.send_text(json.dumps({"t": "chat", "text": "x" * (CH_MAX_LENGTH + 1)}))
        # The oversized frame is rejected before dispatch: a ping/pong round
        # drains whatever (nothing) the oversized chat could have produced.
        ws_a.send_text(json.dumps({"t": "ping"}))
        ws_b.send_text(json.dumps({"t": "ping"}))
        assert chat_events(drain_until(ws_a, "pong")) == []
        assert chat_events(drain_until(ws_b, "pong")) == []


def test_rate_limit_drops_second_chat_within_one_second(
    client: TestClient, account_ids: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    monkeypatch.setattr(handlers, "CHAT_RULES", ChatRules(clock=clock))
    jwt_a = register_and_login(client, account_ids)
    jwt_b = register_and_login(client, account_ids)
    char_a = create_character(client, jwt_a)
    char_b = create_character(client, jwt_b)
    with authed_session(client, jwt_a) as ws_a, authed_session(client, jwt_b) as ws_b:
        ws_a.send_text(json.dumps({"t": "select_char", "char_id": char_a["id"]}))
        ws_b.send_text(json.dumps({"t": "select_char", "char_id": char_b["id"]}))
        for ws in (ws_a, ws_b):
            json.loads(ws.receive_text())  # char_ready

        clock.now = 0.0
        ws_a.send_text(json.dumps({"t": "chat", "text": "one"}))
        expect_chat(ws_b, "one")

        clock.now = CH_RATE_LIMIT_S - 0.5
        ws_a.send_text(json.dumps({"t": "chat", "text": "two"}))  # rate-limited

        ws_a.send_text(json.dumps({"t": "ping"}))
        ws_b.send_text(json.dumps({"t": "ping"}))
        assert chat_events(drain_until(ws_a, "pong")) == ["one"]
        # B already consumed "one" above; "two" must never reach it.
        assert chat_events(drain_until(ws_b, "pong")) == []

        clock.now = CH_RATE_LIMIT_S
        ws_a.send_text(json.dumps({"t": "chat", "text": "three"}))
        ws_a.send_text(json.dumps({"t": "ping"}))
        assert "three" in chat_events(drain_until(ws_a, "pong"))


def test_rate_limit_independent_per_character(
    client: TestClient, account_ids: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    monkeypatch.setattr(handlers, "CHAT_RULES", ChatRules(clock=clock))
    jwt_a = register_and_login(client, account_ids)
    jwt_b = register_and_login(client, account_ids)
    char_a = create_character(client, jwt_a)
    char_b = create_character(client, jwt_b)
    with authed_session(client, jwt_a) as ws_a, authed_session(client, jwt_b) as ws_b:
        ws_a.send_text(json.dumps({"t": "select_char", "char_id": char_a["id"]}))
        ws_b.send_text(json.dumps({"t": "select_char", "char_id": char_b["id"]}))
        for ws in (ws_a, ws_b):
            json.loads(ws.receive_text())  # char_ready

        clock.now = 0.0
        ws_a.send_text(json.dumps({"t": "chat", "text": "from-a"}))
        clock.now = 0.4
        ws_b.send_text(json.dumps({"t": "chat", "text": "from-b"}))  # allowed

        ws_a.send_text(json.dumps({"t": "ping"}))
        ws_b.send_text(json.dumps({"t": "ping"}))
        assert chat_events(drain_until(ws_a, "pong")) == ["from-a", "from-b"]
        assert chat_events(drain_until(ws_b, "pong")) == ["from-a", "from-b"]


def test_chat_is_sanitized_before_broadcast(
    client: TestClient, account_ids: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    monkeypatch.setattr(handlers, "CHAT_RULES", ChatRules(words=frozenset({"骗子"}), clock=clock))
    jwt_a = register_and_login(client, account_ids)
    jwt_b = register_and_login(client, account_ids)
    char_a = create_character(client, jwt_a)
    char_b = create_character(client, jwt_b)
    with authed_session(client, jwt_a) as ws_a, authed_session(client, jwt_b) as ws_b:
        ws_a.send_text(json.dumps({"t": "select_char", "char_id": char_a["id"]}))
        ws_b.send_text(json.dumps({"t": "select_char", "char_id": char_b["id"]}))
        for ws in (ws_a, ws_b):
            json.loads(ws.receive_text())  # char_ready

        ws_a.send_text(json.dumps({"t": "chat", "text": "他是骗子，是个骗子"}))
        for ws in (ws_a, ws_b):
            event = expect_chat(ws, "他是**，是个**")
            assert "骗子" not in json.dumps(event, ensure_ascii=False)


def test_chat_without_character_is_ignored(
    client: TestClient, account_ids: list[int]
) -> None:
    jwt_token = register_and_login(client, account_ids)
    with authed_session(client, jwt_token) as ws:
        ws.send_text(json.dumps({"t": "chat", "text": "no character yet"}))
        ws.send_text(json.dumps({"t": "ping"}))
        assert chat_events(drain_until(ws, "pong")) == []
