"""GET /api/stream —— SSE 战斗事件流（todo 5）。

- cookie 鉴权复用 deps.get_session（未登录/伪造 cookie → 401）；
- 事件 id = 每会话单调递增 int 序号（从 1 起），Last-Event-ID 重放见 sse.buffer；
- ping / send_timeout / client_close_handler_callable 直通 sse-starlette，
  数值全部来自 settings（SSE_PING_INTERVAL / SSE_SEND_TIMEOUT）；
- 断开 hook 只标记 disconnected + 启动宽限期计时，不取消 tick task（todo 15 接入）；
- dev 模拟日志源：每会话一个后台 asyncio.Task 每秒产 1 条假战斗日志，
  仅 settings.ENV == "dev" 时启动——prod 绝不造数据污染真实链路。
"""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Final

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from sse_starlette import EventSourceResponse

from app.api.deps import CurrentPlayerId, DbSession
from app.core.config import settings
from app.models.character import Character
from app.sse.buffer import parse_last_event_id, plan_replay
from app.sse.session_state import SessionState, get_or_create_session

router = APIRouter(prefix="/api", tags=["stream"])

# 实时增量轮询间隔：buffer 是唯一事实源，轮询让重放与直播共用一条读取路径。
# 非规格数值（容量/ping/宽限期才进 config）；相对 1Hz tick，50ms 延迟可忽略。
_POLL_INTERVAL: Final[float] = 0.05

# dev 模拟源任务表：character_id → producer task。验收断言 prod 下恒为空。
_mock_log_tasks: Final[dict[int, asyncio.Task[None]]] = {}


def _dev_mock_enabled() -> bool:
    return settings.ENV == "dev"


async def _mock_log_producer(state: SessionState) -> None:
    """假战斗日志源：立即产首条后按 SSE_MOCK_INTERVAL 节拍持续产出。"""
    n = 0
    while True:
        n += 1
        state.publish(
            "message",
            json.dumps(
                {"text": f"[模拟战斗日志 #{n}] 你攻击 木桩 造成 {n % 97 + 1} 点伤害"},
                ensure_ascii=False,
            ),
        )
        await asyncio.sleep(settings.SSE_MOCK_INTERVAL)


def _ensure_mock_task(character_id: int, state: SessionState) -> None:
    """dev-only：为该角色懒启动模拟源；任务跨断线存活以支撑 Last-Event-ID 重放。"""
    if _dev_mock_enabled() and character_id not in _mock_log_tasks:
        _mock_log_tasks[character_id] = asyncio.create_task(_mock_log_producer(state))


async def shutdown_mock_sources() -> None:
    """取消全部模拟源任务（lifespan 关停与测试清理用）。"""
    tasks = list(_mock_log_tasks.values())
    _mock_log_tasks.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@router.get("/stream")
async def stream(
    db: DbSession, player_id: CurrentPlayerId, request: Request
) -> EventSourceResponse:
    character_id = (
        await db.execute(select(Character.id).where(Character.player_id == player_id))
    ).scalar_one_or_none()
    if character_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="尚未建号"
        )

    state = get_or_create_session(character_id)
    _ensure_mock_task(character_id, state)
    state.mark_connected()

    raw_last_event_id = request.headers.get("last-event-id")
    plan = plan_replay(state, raw_last_event_id)

    async def on_client_close(_message: object) -> None:
        # hook（todo 15 接管）：只标记断线并启动宽限期计时，不取消 tick task。
        state.mark_disconnected()

    async def event_stream() -> AsyncIterator[dict[str, str]]:
        # 游标 = 本连接已发出的最大 seq；gap 重置 epoch 后归零，新事件从 1 取。
        cursor = 0
        try:
            if plan.gap:
                # gap 不携带 id；前端收到后先清水位线再插"期间部分日志丢失"分隔行。
                yield {
                    "event": "gap",
                    "data": json.dumps({"reason": "buffer_overflow"}),
                }
            else:
                for event in plan.events:
                    cursor = event.seq
                    yield {
                        "event": event.event,
                        "data": event.data,
                        "id": str(event.seq),
                    }
                if not plan.events:
                    parsed = parse_last_event_id(raw_last_event_id)
                    # 合法但已是最新 → 从该 id 之后取增量；全新连接 → 全量 buffer。
                    cursor = parsed if parsed is not None else 0
            while True:
                if await request.is_disconnected():
                    break
                for event in state.buffer.after(cursor):
                    cursor = event.seq
                    yield {
                        "event": event.event,
                        "data": event.data,
                        "id": str(event.seq),
                    }
                await asyncio.sleep(_POLL_INTERVAL)
        finally:
            # 与 client_close_handler_callable 同一语义：标记断线 + 宽限期起表。
            state.mark_disconnected()

    return EventSourceResponse(
        event_stream(),
        ping=int(settings.SSE_PING_INTERVAL),
        send_timeout=settings.SSE_SEND_TIMEOUT,
        client_close_handler_callable=on_client_close,
    )
