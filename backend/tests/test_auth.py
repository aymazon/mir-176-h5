"""鉴权与角色 API 全链路测试。

用 httpx.AsyncClient(transport=ASGITransport(app=app)) 直连真实 PG（mir_dev）：
TestClient 基于 requests 同步阻塞，对 SQLAlchemy async 会卡，故不用。
每个用例随机用户名/角色名，避免多次运行撞 DB 唯一约束；
fixture 收尾 dispose engine，防止连接池跨 event loop 复用报错。
"""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db
from app.core.db import engine
from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    await engine.dispose()


async def _register_and_login(client: AsyncClient) -> dict[str, str]:
    creds = {"username": f"u_{uuid4().hex[:12]}", "password": "p@ssw0rd"}
    r = await client.post("/api/auth/register", json=creds)
    assert r.status_code == 201, r.text
    r = await client.post("/api/auth/login", json=creds)
    assert r.status_code == 200, r.text
    return creds


async def test_register_login_create_me(client: AsyncClient) -> None:
    await _register_and_login(client)

    char_name = f"c_{uuid4().hex[:10]}"
    r = await client.post("/api/character", json={"name": char_name, "class": "warrior"})
    assert r.status_code == 201, r.text

    r = await client.get("/api/character/me")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == char_name
    assert body["class"] == "warrior"
    assert body["level"] == 1
    assert body["hp"] == 0
    assert body["mp"] == 0
    assert body["gold"] == 0
    assert body["bind_city"] == "yin-xing-cun"


async def test_duplicate_create_409(client: AsyncClient) -> None:
    await _register_and_login(client)
    char_name = f"c_{uuid4().hex[:10]}"

    r = await client.post("/api/character", json={"name": char_name, "class": "wizard"})
    assert r.status_code == 201, r.text

    # 同账号二次建号 → 409（单账号单角色）
    r = await client.post(
        "/api/character", json={"name": f"c_{uuid4().hex[:10]}", "class": "taoist"}
    )
    assert r.status_code == 409, r.text

    # 跨账号重复角色名 → 409（uq_character_name）
    other_creds = {"username": f"u_{uuid4().hex[:12]}", "password": "p@ssw0rd"}
    await client.post("/api/auth/register", json=other_creds)
    await client.post("/api/auth/login", json=other_creds)
    r = await client.post("/api/character", json={"name": char_name, "class": "taoist"})
    assert r.status_code == 409, r.text


async def test_no_cookie_401(client: AsyncClient) -> None:
    # 完全无 cookie
    r = await client.get("/api/character/me")
    assert r.status_code == 401, r.text

    # cookie 伪造：不存在的 session_id 不在服务端内存表
    client.cookies.set("session_id", f"forged-{uuid4().hex}")
    r = await client.get("/api/character/me")
    assert r.status_code == 401, r.text


async def test_wrong_password_401(client: AsyncClient) -> None:
    creds = {"username": f"u_{uuid4().hex[:12]}", "password": "correct-horse"}
    r = await client.post("/api/auth/register", json=creds)
    assert r.status_code == 201, r.text

    r = await client.post(
        "/api/auth/login", json={"username": creds["username"], "password": "wrong"}
    )
    assert r.status_code == 401, r.text

    # 不存在的用户名同样 401（不泄露账号存在性）
    r = await client.post(
        "/api/auth/login", json={"username": f"no_{uuid4().hex[:12]}", "password": "x"}
    )
    assert r.status_code == 401, r.text


async def test_health_200_then_503_when_db_down(client: AsyncClient) -> None:
    # 正常：PG 可达 → 200
    r = await client.get("/api/health")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok"}

    # PG 不可达：dependency_overrides 注入坏 session 模拟（阻断真实 DB）
    class _BrokenDB:
        async def execute(self, *_args: object, **_kwargs: object) -> object:
            msg = "simulated pg outage"
            raise RuntimeError(msg)

    async def _broken_db() -> AsyncIterator[object]:
        yield _BrokenDB()

    app.dependency_overrides[get_db] = _broken_db
    try:
        r = await client.get("/api/health")
        assert r.status_code == 503, r.text
    finally:
        app.dependency_overrides.clear()
