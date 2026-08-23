"""每 character 一份的 SSE 会话状态（进程内存单例注册表）。

字段即 todo 5 规格要求的：seq、buffer、heartbeat、disconnected flag、
grace_period_started_at。断线只标记 + 启动宽限期计时，**不取消任何后台任务**
（tick task 由 todo 15 接入并消费宽限期字段）。

并发模型：所有变更均为同步操作（无 await），在单事件循环内天然原子。
SSE 端点以轮询 buffer 方式取增量（见 api/stream.py），buffer 是唯一事实源，
重放与实时推送共用同一条读取路径。MVP 假设单角色单连接（todo 15 抢占语义）；
若同角色出现第二个连接触发 gap 重置，旧连接的游标失效属预期行为。
"""

import time
from dataclasses import dataclass

from app.core.config import settings
from app.sse.buffer import SessionBuffer, SseEvent


@dataclass(slots=True)
class SessionState:
    """单个角色的 SSE 会话状态。"""

    character_id: int
    buffer: SessionBuffer
    _next_seq: int = 0
    disconnected: bool = False
    grace_period_started_at: float | None = None
    heartbeat_at: float | None = None

    @property
    def last_seq(self) -> int:
        """已分配的最大 seq（0 = 尚未发布过任何事件）。"""
        return self._next_seq

    def next_seq(self) -> int:
        self._next_seq += 1
        return self._next_seq

    def reset_epoch(self) -> None:
        """gap 后重置序号空间：buffer 清空、seq 归零，下一条事件从 1 起。"""
        self._next_seq = 0
        self.buffer.clear()

    def publish(self, event_name: str, data: str) -> SseEvent:
        """分配递增 seq 并写入 ring buffer（供在线连接轮询取走）。"""
        event = SseEvent(seq=self.next_seq(), event=event_name, data=data)
        self.buffer.append(event)
        self.heartbeat_at = time.monotonic()
        return event

    def mark_connected(self) -> None:
        """连接建立/重连：清除断线标记与宽限期计时起点。"""
        self.disconnected = False
        self.grace_period_started_at = None
        self.heartbeat_at = time.monotonic()

    def mark_disconnected(self) -> None:
        """SSE 断开 hook：标记 disconnected 并启动宽限期计时（幂等）。

        刻意**不**取消 tick task——宽限期内重连无缝续跑，超时处理归 todo 15。
        """
        if self.disconnected:
            return
        self.disconnected = True
        self.grace_period_started_at = time.monotonic()
        self.heartbeat_at = time.monotonic()

    @property
    def grace_period_elapsed(self) -> bool:
        """宽限期是否已耗尽（todo 15 据此取消 tick task）。"""
        if not self.disconnected or self.grace_period_started_at is None:
            return False
        elapsed = time.monotonic() - self.grace_period_started_at
        return elapsed >= settings.SSE_GRACE_PERIOD_SECONDS


_sessions: dict[int, SessionState] = {}


def get_or_create_session(character_id: int) -> SessionState:
    """按 character_id 取会话状态；不存在则建空 state（再发流）。"""
    state = _sessions.get(character_id)
    if state is None:
        state = SessionState(
            character_id=character_id,
            buffer=SessionBuffer(capacity=settings.SSE_BUFFER_SIZE),
        )
        _sessions[character_id] = state
    return state


def reset_sessions() -> None:
    """测试专用：清空会话注册表（生产路径永不调用）。"""
    _sessions.clear()
