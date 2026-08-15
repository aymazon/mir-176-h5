"""Integration tests for the character CRUD endpoints against the live database.

Self-cleaning: each test gets a unique account (uuid suffix) and the fixture
removes the account's character rows, session rows, and the account itself on
teardown, so the live DB stays clean and the suite is repeatable.
"""

import uuid
from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest

from app.db.pool import close_pool, init_pool
from app.main import app

TEST_PASSWORD = "correct-horse-battery"


def unique_username() -> str:
    return f"t{uuid.uuid4().hex[:16]}"


def unique_character_name() -> str:
    # 'c' + 15 hex chars = 16 chars, the maximum allowed character name.
    return f"c{uuid.uuid4().hex[:15]}"


def auth_header(token_value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_value}"}


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
async def account(db: asyncpg.Pool, client: httpx.AsyncClient) -> AsyncIterator[tuple[int, str]]:
    """Unique account per test; its characters, sessions, and row are purged on teardown."""
    username = unique_username()
    response = await client.post(
        "/api/auth/register", json={"username": username, "password": TEST_PASSWORD}
    )
    assert response.status_code == 201, response.text
    account_id = int(response.json()["id"])
    yield account_id, username
    await db.execute("DELETE FROM character WHERE account_id = $1", account_id)
    await db.execute("DELETE FROM account_session WHERE account_id = $1", account_id)
    await db.execute("DELETE FROM account WHERE id = $1", account_id)


async def login_token(client: httpx.AsyncClient, username: str) -> str:
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["token"])


async def create_character(
    client: httpx.AsyncClient,
    token_value: str,
    name: str,
    character_class: str = "warrior",
) -> httpx.Response:
    response = await client.post(
        "/api/characters",
        headers=auth_header(token_value),
        json={"name": name, "class": character_class},
    )
    return response


async def delete_character(
    client: httpx.AsyncClient,
    token_value: str,
    character_id: int,
    name: str,
) -> httpx.Response:
    # httpx's typed delete() takes no body; request() is the body-capable seam.
    response = await client.request(
        "DELETE",
        f"/api/characters/{character_id}",
        headers=auth_header(token_value),
        json={"name": name},
    )
    return response


async def list_characters(client: httpx.AsyncClient, token_value: str) -> httpx.Response:
    return await client.get("/api/characters", headers=auth_header(token_value))


@pytest.mark.parametrize(
    ("character_class", "hp", "mp"),
    [("warrior", 33, 11), ("mage", 11, 33), ("taoist", 22, 22)],
)
async def test_create_then_list_then_delete(
    client: httpx.AsyncClient,
    account: tuple[int, str],
    character_class: str,
    hp: int,
    mp: int,
) -> None:
    _account_id, username = account
    token_value = await login_token(client, username)
    name = unique_character_name()

    created = await create_character(client, token_value, name, character_class)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == name
    assert body["class"] == character_class
    assert body["level"] == 1
    assert body["exp"] == 0
    assert body["hp"] == hp
    assert body["mp"] == mp
    assert body["gold"] == 0
    assert body["bound_city"] == "bichon"
    assert body["map_id"] == "m3"
    assert body["x"] == 150
    assert body["y"] == 150
    character_id = int(body["id"])

    listed = await list_characters(client, token_value)
    assert listed.status_code == 200
    entries = listed.json()
    assert len(entries) == 1
    assert (entries[0]["id"], entries[0]["name"], entries[0]["class"], entries[0]["level"]) == (
        character_id,
        name,
        character_class,
        1,
    )

    deleted = await delete_character(client, token_value, character_id, name)
    assert deleted.status_code == 200, deleted.text

    relisted = await list_characters(client, token_value)
    assert relisted.status_code == 200
    assert relisted.json() == []


async def test_fourth_character_returns_409_and_slot_frees_after_delete(
    client: httpx.AsyncClient,
    account: tuple[int, str],
) -> None:
    _account_id, username = account
    token_value = await login_token(client, username)

    first_name = unique_character_name()
    created: list[int] = []
    created.append((await create_character(client, token_value, first_name)).status_code)
    for _ in range(2):
        created.append((await create_character(client, token_value, unique_character_name())).status_code)
    assert created == [201, 201, 201]

    fourth = await create_character(client, token_value, unique_character_name())
    assert fourth.status_code == 409

    first = await list_characters(client, token_value)
    first_id = int(first.json()[0]["id"])
    freed = await delete_character(client, token_value, first_id, first_name)
    assert freed.status_code == 200, freed.text

    replacement = await create_character(client, token_value, unique_character_name())
    assert replacement.status_code == 201, replacement.text


async def test_duplicate_name_returns_409(
    client: httpx.AsyncClient,
    account: tuple[int, str],
) -> None:
    _account_id, username = account
    token_value = await login_token(client, username)
    name = unique_character_name()

    first = await create_character(client, token_value, name)
    assert first.status_code == 201, first.text

    second = await create_character(client, token_value, name, "mage")
    assert second.status_code == 409


async def test_delete_with_wrong_name_returns_400_and_keeps_row(
    client: httpx.AsyncClient,
    db: asyncpg.Pool,
    account: tuple[int, str],
) -> None:
    _account_id, username = account
    token_value = await login_token(client, username)
    name = unique_character_name()
    created = await create_character(client, token_value, name)
    character_id = int(created.json()["id"])

    response = await delete_character(client, token_value, character_id, "WrongName")
    assert response.status_code == 400

    row = await db.fetchrow("SELECT deleted_at FROM character WHERE id = $1", character_id)
    assert row is not None
    assert row["deleted_at"] is None


async def test_delete_soft_deletes_row(
    client: httpx.AsyncClient,
    db: asyncpg.Pool,
    account: tuple[int, str],
) -> None:
    _account_id, username = account
    token_value = await login_token(client, username)
    name = unique_character_name()
    created = await create_character(client, token_value, name)
    character_id = int(created.json()["id"])

    deleted = await delete_character(client, token_value, character_id, name)
    assert deleted.status_code == 200, deleted.text

    relisted = await list_characters(client, token_value)
    assert relisted.json() == []

    row = await db.fetchrow("SELECT deleted_at FROM character WHERE id = $1", character_id)
    assert row is not None
    assert row["deleted_at"] is not None


async def test_delete_unknown_character_returns_404(
    client: httpx.AsyncClient,
    account: tuple[int, str],
) -> None:
    _account_id, username = account
    token_value = await login_token(client, username)

    response = await delete_character(client, token_value, 99_999_999, "Whatever")
    assert response.status_code == 404


async def test_character_endpoints_require_auth(client: httpx.AsyncClient) -> None:
    missing_get = await client.get("/api/characters")
    assert missing_get.status_code == 401

    invalid_get = await client.get(
        "/api/characters", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert invalid_get.status_code == 401

    missing_post = await client.post(
        "/api/characters", json={"name": "X", "class": "mage"}
    )
    assert missing_post.status_code == 401

    missing_delete = await client.request("DELETE", "/api/characters/1", json={"name": "X"})
    assert missing_delete.status_code == 401
