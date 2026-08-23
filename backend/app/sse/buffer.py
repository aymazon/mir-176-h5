"""SSE ring buffer、Last-Event-ID 解析与重放计划（含 gap 注入判定）。

gap 语义（与前端 todo 6 logStore 的关键契约）：
- Last-Event-ID 数字 < buffer 最小 seq ⇒ 所需事件已滚出 ⇒ 重放流首条必须是
  ``event: gap``（不带 id）；其后按"视同全新连接"处理——会话 epoch 重置，
  事件 id 从 1 重新递增。前端收到 gap 先重置去重水位线再插分隔行，
  否则新会话的小 id 会被旧水位误判丢弃。
- Last-Event-ID 非法（非数字 / 空 / 负数）⇒ 忽略，按全新连接处理，绝不 500。
"""

from collections import deque
from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True, frozen=True)
class SseEvent:
    """一条已入 buffer 的 SSE 事件；seq 即对外的事件 id。"""

    seq: int
    event: str
    data: str


def parse_last_event_id(raw: str | None) -> int | None:
    """把 Last-Event-ID 头解析为正整数；None/空/非数字/负数一律返回 None。

    返回 None 表示"按全新连接处理"（不报错）。
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    return value if value >= 1 else None


class SessionBuffer:
    """定长 ring buffer（deque maxlen），容量来自 settings.SSE_BUFFER_SIZE。"""

    def __init__(self, capacity: int) -> None:
        # 容量下限钳到 1，防配置出 0/负值时 deque(maxlen<=0) 静默吞事件。
        self._events: deque[SseEvent] = deque(maxlen=max(1, capacity))

    def append(self, event: SseEvent) -> None:
        self._events.append(event)

    def clear(self) -> None:
        self._events.clear()

    @property
    def min_seq(self) -> int | None:
        """buffer 中现存最小 seq；空 buffer 为 None。"""
        return self._events[0].seq if self._events else None

    def after(self, seq: int) -> list[SseEvent]:
        """所有 seq > seq 的事件（按 seq 升序）。"""
        return [event for event in self._events if event.seq > seq]


@dataclass(slots=True, frozen=True)
class ReplayPlan:
    """一次重连的重放决策。

    gap=True  ：首条须发 ``event: gap``（无 id），events 恒为空——epoch 已重置，
                后续实时事件从 1 重新递增。
    gap=False ：events 为需补发的历史事件（保留原 id，严格递增）；可为空表
                （全新连接或客户端已是最新）。
    """

    gap: bool
    events: tuple[SseEvent, ...]


class ReplaySource(Protocol):
    """plan_replay 所需的最小会话接口（SessionState 结构性满足，避免循环导入）。"""

    buffer: SessionBuffer

    @property
    def last_seq(self) -> int: ...

    def reset_epoch(self) -> None: ...


def plan_replay(state: ReplaySource, raw_last_event_id: str | None) -> ReplayPlan:
    """依据 Last-Event-ID 头计算重放计划；gap 分支会就地重置会话 epoch。"""
    last_id = parse_last_event_id(raw_last_event_id)
    if last_id is None:
        # 非法 / 缺失：全新连接，不重放也不 gap。
        return ReplayPlan(gap=False, events=())
    if last_id >= state.last_seq:
        # 客户端已是最新（或超前）：无需补发。
        return ReplayPlan(gap=False, events=())

    min_seq = state.buffer.min_seq
    if min_seq is not None and min_seq <= last_id + 1:
        # 所需事件完整落在 buffer 窗口内：原 id 原序补发。
        return ReplayPlan(gap=False, events=tuple(state.buffer.after(last_id)))

    # 已滚出（min_seq > last_id + 1，含 buffer 为空但 last_seq 更大的情形）：
    # 视同新连接——重置 epoch，其后事件 id 从 1 重新递增。
    state.reset_epoch()
    return ReplayPlan(gap=True, events=())
