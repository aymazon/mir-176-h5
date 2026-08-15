"""Integration tests for the REST auth endpoints against the live database.

The httpx ASGI transport and the pool fixture share one event loop, so the
handlers run on the same loop that owns the asyncpg pool (TestClient's
portal loop would not). The exception is ``test_app_lifespan_pool_wiring``,
which deliberately drives the real lifespan through TestClient in isolation.
"""

import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import asyncpg
import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.auth import verify_ws_token
from app.config import Settings
from app.db.pool import close_pool, init_pool
from app.main import app

TEST_PASSWORD = "correct-horse-battery"


def unique_username() -> str:
    return f"t{uuid.uuid4().hex[:16]}"


async def register(client: httpx.AsyncClient, username: str, password: str = TEST_PASSWORD) -> int:
    response = await client.post(
        "/api/auth/register", json={"username": username, "password": password}
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


async def login(client: httpx.AsyncClient, username: str, password: str = TEST_PASSWORD) -> str:
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["token"])


async def issue_ws_token(client: httpx.AsyncClient, jwt_token: str) -> str:
    response = await client.post(
        "/api/auth/ws_token", headers={"Authorization": f"Bearer {jwt_token}"}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["ws_token"])


@pytest.fixture
async def db() -> AsyncIterator[asyncpg.Pool]:
    pool = await init_pool()
    try:
        yield pool
    finally:
        await close_pool()


@pytest.fixture
async def client(db: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


@pytest.fixture
async def account_ids(db: asyncpg.Pool) -> AsyncIterator[list[int]]:
    """Registry of account ids created during a test; purged on teardown."""
    created: list[int] = []
    yield created
    if created:
        await db.execute(
            "DELETE FROM account_session WHERE account_id = ANY($1::bigint[])", created
        )
        await db.execute("DELETE FROM account WHERE id = ANY($1::bigint[])", created)


async def test_register_login_ws_token_round_trip(
    client: httpx.AsyncClient,
    db: asyncpg.Pool,
    account_ids: list[int],
) -> None:
    username = unique_username()
    account_id = await register(client, username)
    account_ids.append(account_id)

    jwt_token = await login(client, username)
    ws_token = await issue_ws_token(client, jwt_token)
    assert len(ws_token) == 64

    token_hash = hashlib.sha256(ws_token.encode("ascii")).hexdigest()
    row = await db.fetchrow(
        "SELECT account_id, expires_at FROM account_session WHERE ws_token_hash = $1",
        token_hash,
    )
    assert row is not None
    assert row["account_id"] == account_id
    assert row["expires_at"] > datetime.now(UTC)
    assert await verify_ws_token(db, ws_token) == account_id


async def test_register_duplicate_username_returns_409(
    client: httpx.AsyncClient, account_ids: list[int]
) -> None:
    username = unique_username()
    account_ids.append(await register(client, username))

    response = await client.post(
        "/api/auth/register", json={"username": username, "password": TEST_PASSWORD}
    )
    assert response.status_code == 409


async def test_login_wrong_password_returns_401(
    client: httpx.AsyncClient, account_ids: list[int]
) -> None:
    username = unique_username()
    account_ids.append(await register(client, username))

    response = await client.post(
        "/api/auth/login", json={"username": username, "password": "wrong-password-1"}
    )
    assert response.status_code == 401


async def test_login_unknown_username_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/auth/login",
        json={"username": unique_username(), "password": TEST_PASSWORD},
    )
    assert response.status_code == 401


async def test_expired_ws_token_session_rejected(
    client: httpx.AsyncClient,
    db: asyncpg.Pool,
    account_ids: list[int],
) -> None:
    username = unique_username()
    account_id = await register(client, username)
    account_ids.append(account_id)
    jwt_token = await login(client, username)
    ws_token = await issue_ws_token(client, jwt_token)

    assert await verify_ws_token(db, ws_token) == account_id

    await db.execute(
        "UPDATE account_session SET expires_at = now() - interval '1 minute' WHERE account_id = $1",
        account_id,
    )
    assert await verify_ws_token(db, ws_token) is None

    planted = secrets.token_hex(32)
    await db.execute(
        "INSERT INTO account_session (ws_token_hash, account_id, expires_at) VALUES ($1, $2, now() - interval '1 minute')",
        hashlib.sha256(planted.encode("ascii")).hexdigest(),
        account_id,
    )
    assert await verify_ws_token(db, planted) is None


async def test_ws_token_requires_valid_jwt(
    client: httpx.AsyncClient, account_ids: list[int]
) -> None:
    username = unique_username()
    account_ids.append(await register(client, username))

    missing = await client.post("/api/auth/ws_token")
    assert missing.status_code == 401

    garbage = await client.post(
        "/api/auth/ws_token", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert garbage.status_code == 401


async def test_register_rejects_out_of_bounds_credentials(
    client: httpx.AsyncClient,
) -> None:
    short_user = await client.post(
        "/api/auth/register", json={"username": "ab", "password": TEST_PASSWORD}
    )
    assert short_user.status_code == 422

    long_user = await client.post(
        "/api/auth/register", json={"username": "x" * 33, "password": TEST_PASSWORD}
    )
    assert long_user.status_code == 422

    short_password = await client.post(
        "/api/auth/register", json={"username": unique_username(), "password": "short"}
    )
    assert short_password.status_code == 422


async def test_config_returns_version_and_tick_ms(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/config")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == "0.1.0"
    assert body["tick_ms"] == Settings().tick_rate_ms
    assert isinstance(body["notice"], str)


def test_app_lifespan_pool_wiring() -> None:
    with TestClient(app) as test_client:
        assert test_client.get("/api/health").status_code == 200
        config = test_client.get("/api/config")
        assert config.status_code == 200
        assert config.json()["version"] == "0.1.0"
