"""/api/stream SSE 通道全链路测试（httpx-sse + 真实 uvicorn 临时服务器桥接）。

覆盖验收：递增 id 流、Last-Event-ID 重放（不含 ≤id）、buffer 滚出 → 首条
event: gap 且其后 id 从 1 重排、无/伪造 cookie → 401、非法 Last-Event-ID
按全新连接容错、ENV=prod 不启动模拟源。

为何不用 ASGITransport：httpx 的 ASGITransport 会把整个响应 body 攒完才返回，
无限 SSE 流会永久死锁；故起真实 uvicorn（ephemeral 端口）同事件循环服务。
模拟源节拍与 buffer 容量经 settings 注入调小，避免真实秒级等待；
autouse fixture 清理会话注册表并取消模拟任务，防跨用例泄漏。
"""

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import uvicorn
from httpx import AsyncClient
from httpx_sse import aconnect_sse

from app.api.stream import _dev_mock_enabled, _mock_log_tasks, shutdown_mock_sources
from app.core.config import settings
from app.core.db import engine
from app.main import app
from app.sse.session_state import reset_sessions

type ReceivedEvent = tuple[str, str]  # (event 名, id 字符串)


@pytest.fixture(autouse=True)
async def _clean_sse_runtime() -> AsyncIterator[None]:
    await shutdown_mock_sources()
    reset_sessions()
    yield
    await shutdown_mock_sources()
    reset_sessions()


@pytest.fixture
async def server() -> AsyncIterator[str]:
    """同事件循环内起真实 uvicorn（ephemeral 端口），退出时优雅关停。"""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    uv_server = uvicorn.Server(config)
    serve_task = asyncio.create_task(uv_server.serve())
    for _ in range(500):
        if uv_server.started:
            break
        await asyncio.sleep(0.01)
    assert uv_server.started, "uvicorn 未能启动"
    port = uv_server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    uv_server.should_exit = True
    await serve_task


@pytest.fixture
async def client(server: str) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(base_url=server) as c:
        yield c
    await engine.dispose()

async def _register_login_create(client: AsyncClient) -> None:
    """注册→登录（拿 cookie）→建号（SSE 按 character 维度取会话状态）。"""
    creds = {"username": f"u_{uuid4().hex[:12]}", "password": "p@ssw0rd"}
    r = await client.post("/api/auth/register", json=creds)
    assert r.status_code == 201, r.text
    r = await client.post("/api/auth/login", json=creds)
    assert r.status_code == 200, r.text
    r = await client.post(
        "/api/character", json={"name": f"c_{uuid4().hex[:10]}", "class": "warrior"}
    )
    assert r.status_code == 201, r.text


async def _collect(
    event_source: object, count: int, timeout: float = 5.0
) -> list[ReceivedEvent]:
    """从 httpx-sse 流收满 count 条事件；整体超时防挂死。"""

    async def _drain() -> list[ReceivedEvent]:
        received: list[ReceivedEvent] = []
        async for sse in event_source.aiter_sse():  # type: ignore[attr-defined]
            received.append((sse.event, sse.id))
            if len(received) >= count:
                break
        return received

    return await asyncio.wait_for(_drain(), timeout=timeout)


def test_stream_route_registered() -> None:
    # FastAPI 0.141+ include_router 走 _IncludedRouter 惰性包装，app.routes 不再平铺
    # path；改用 OpenAPI schema 断言（同时验证路由确实可被文档化/访问）。
    assert "/api/stream" in app.openapi()["paths"]


async def test_stream_no_cookie_or_forged_401(client: AsyncClient) -> None:
    # 无 cookie
    r = await client.get("/api/stream")
    assert r.status_code == 401, r.text

    # 伪造 cookie：不在服务端内存 session 表
    client.cookies.set("session_id", f"forged-{uuid4().hex}")
    r = await client.get("/api/stream")
    assert r.status_code == 401, r.text


async def test_stream_increasing_ids(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SSE_MOCK_INTERVAL", 0.02)
    await _register_login_create(client)

    async with aconnect_sse(client, "GET", "/api/stream") as event_source:
        assert event_source.response.status_code == 200
        events = await _collect(event_source, 10)

    assert all(name == "message" for name, _ in events), events
    ids = [int(event_id) for _, event_id in events]
    assert len(set(ids)) == 10
    assert ids == sorted(ids), f"id 必须严格递增：{ids}"


async def test_replay_with_last_event_id_no_duplicates(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SSE_MOCK_INTERVAL", 0.02)
    monkeypatch.setattr(settings, "SSE_BUFFER_SIZE", 500)
    await _register_login_create(client)

    async with aconnect_sse(client, "GET", "/api/stream") as event_source:
        first_batch = await _collect(event_source, 5)
    last_id = int(first_batch[-1][1])

    # 断开期间模拟源继续产事件（宽限期内 buffer 续写），但远小于容量 500。
    await asyncio.sleep(0.15)

    async with aconnect_sse(
        client, "GET", "/api/stream", headers={"Last-Event-ID": str(last_id)}
    ) as event_source:
        replayed = await _collect(event_source, 5)

    names = [name for name, _ in replayed]
    ids = [int(event_id) for _, event_id in replayed]
    assert "gap" not in names, names
    assert ids[0] == last_id + 1, f"重放必须从 {last_id + 1} 开始：{ids}"
    assert all(event_id > last_id for event_id in ids), ids
    assert ids == sorted(ids)


async def test_gap_when_last_event_id_rolled_out(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SSE_MOCK_INTERVAL", 0.05)
    monkeypatch.setattr(settings, "SSE_BUFFER_SIZE", 5)
    await _register_login_create(client)

    async with aconnect_sse(client, "GET", "/api/stream") as event_source:
        head = await _collect(event_source, 2)
    stale_id = int(head[-1][1])

    # 断开期间产出 ≈7 条 > 容量 5 → Last-Event-ID 已滚出 buffer。
    await asyncio.sleep(0.35)

    async with aconnect_sse(
        client, "GET", "/api/stream", headers={"Last-Event-ID": str(stale_id)}
    ) as event_source:
        got = await _collect(event_source, 4)

    assert got[0][0] == "gap", got
    assert got[0][1] == "", "gap 事件不携带 id"
    after_ids = [int(event_id) for _, event_id in got[1:]]
    # 新 epoch：id 从 1 重新递增（前端收到 gap 先清水位线，否则会被误判丢弃）。
    assert after_ids == [1, 2, 3], after_ids


@pytest.mark.parametrize("bad_id", ["abc", "-1", ""])
async def test_invalid_last_event_id_treated_as_fresh(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, bad_id: str
) -> None:
    monkeypatch.setattr(settings, "SSE_MOCK_INTERVAL", 0.02)
    await _register_login_create(client)

    async with aconnect_sse(
        client, "GET", "/api/stream", headers={"Last-Event-ID": bad_id}
    ) as event_source:
        assert event_source.response.status_code == 200
        events = await _collect(event_source, 3)

    assert all(name != "gap" for name, _ in events), events
    ids = [int(event_id) for _, event_id in events]
    assert all(event_id >= 1 for event_id in ids)
    assert ids == sorted(ids)


async def test_prod_env_disables_mock_source(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ENV", "prod")
    assert _dev_mock_enabled() is False
    await _register_login_create(client)

    async with aconnect_sse(client, "GET", "/api/stream") as event_source:
        assert event_source.response.status_code == 200
        assert _mock_log_tasks == {}, "prod 模式不得启动模拟源任务"
        with pytest.raises(asyncio.TimeoutError):
            await _collect(event_source, 1, timeout=0.6)
