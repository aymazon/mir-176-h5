"""行进中状态注册表（todo 30 传送员/跑图倒计时）。

跨图跑动的活跃倒计时记账：character_id → TravelState。PlayerSession 是
slots dataclass 且 sim/session.py 冻结不可改——仿 tick_loop._GROUND_DROPS /
pet._owners 的模块级注册表模式，把行进态挂在独立模块而非会话对象上。

- 进程内存是活跃会话的事实源；character.traveling_until 列在 warp/到点时
  同事务镜像落库（审计 + 跨进程可见）。进程重启后注册表清空 = 行进自然取消
  （不据 DB 列恢复倒计时——目标图只存内存，恢复无据）。
- 增删只发生在单事件循环内（REST warp / tick 到点 / 会话注销），天然原子。
- balance.TRAVEL_SECONDS_MIN/MAX 一律经模块属性读取（monkeypatch 可生效）。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

from app.combat import balance

__all__ = [
    "TravelState",
    "clear_travel",
    "has_travel",
    "is_traveling",
    "pop_due_travel",
    "start_travel",
    "travel_of",
    "travel_seconds",
]


@dataclass(frozen=True)
class TravelState:
    """一次跨图行进的快照（warp 时登记、到点被 pop 消费）。"""

    target_map_id: str
    # time.time() 墙钟到期时刻（与 character.traveling_until 同源）
    until: float
    fee: int = 0
    # 洞穴层档位（纯播报语义，MVP 单图代表整洞）
    layer: int | None = None


_TRAVELS: dict[int, TravelState] = {}


def start_travel(character_id: int, state: TravelState) -> None:
    """登记行进态（warp 锁内 commit 后调用）。"""
    _TRAVELS[character_id] = state


def has_travel(character_id: int) -> bool:
    """是否存在未消费的行进记录（含已到期未 pop 的窗口期）。

    tick 战斗跳过判定用本函数而非 is_traveling：到期与 pop 之间的一拍内
    也不允许出手（到点切图由 tick_loop 编排，先切完再恢复战斗）。
    """
    return character_id in _TRAVELS


def is_traveling(character_id: int) -> bool:
    """倒计时未到点（REST 侧「行进中拒绝新操作」闸门用）。"""
    state = _TRAVELS.get(character_id)
    return state is not None and state.until > time.time()


def travel_of(character_id: int) -> TravelState | None:
    """当前行进态快照（面板展示用），无行进返回 None。"""
    return _TRAVELS.get(character_id)


def remaining_seconds(character_id: int) -> float | None:
    """剩余秒数（向上取整到 0.1s 展示精度）；无行进返回 None。"""
    state = _TRAVELS.get(character_id)
    if state is None:
        return None
    return max(0.0, round(state.until - time.time(), 1))


def pop_due_travel(
    character_id: int, *, now: float | None = None
) -> TravelState | None:
    """到期则弹出并返回状态；未到期/无行进返回 None（tick_loop 每 tick 探询）。

    now 参数供测试注入墙钟。
    """
    state = _TRAVELS.get(character_id)
    if state is None:
        return None
    if state.until > (time.time() if now is None else now):
        return None
    return _TRAVELS.pop(character_id)


def clear_travel(character_id: int) -> None:
    """注销会话/宽限超时时清理（防复活旧会话残留幽灵行进）。"""
    _TRAVELS.pop(character_id, None)


def travel_seconds(rng: random.Random) -> int:
    """本次行进时长（整秒）：MIN..MAX 均匀随机；经 rng 可复现。

    MIN/MAX 经模块属性读取（learnings：monkeypatch 常量必须走 balance.X）。
    """
    lo = balance.TRAVEL_SECONDS_MIN
    hi = max(lo, balance.TRAVEL_SECONDS_MAX)
    return lo + int(rng.random() * (hi - lo + 1))
