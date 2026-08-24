"""自动吃药策略纯函数（todo 12）。

策略规则基线（tests/combat/test_potion.py 被测契约、docs/MVP/README.md §8）::

    R1 HP < POTION_HP_PCT×max_hp 喝回血药；MP < POTION_MP_PCT×max_mp 喝回蓝药
       （均严格小于，压线不触发）；
    R2 HP < POTION_URGENT_HP_PCT×max_hp 危险线：分支内改为优先高级药
       （priority 大者，太阳水 > 金创药）；
    R3 同分支内沿 priority 替代链逐级找药——首选缺货/冷却中则顺延下一档，
       只有太阳水就喝太阳水；
    R4 每个 item_id 喝后进入 POTION_COOLDOWN_TICKS 冷却（绝对到期 tick 记入
       cooldowns_after）；now_tick < 到期 tick 视为冷却中，同款不再喝；
    R5 纯函数：背包扣减与 CD 只写入返回值 bag_after / cooldowns_after，
       入参 bag / cooldowns 不被原地修改，不读不写 DB；
    R6 free_slots 非 None 且 < BAG_FREE_SLOTS_RECALL → 追加 recall 动作；
       free_slots=None 是显式关闭信号（sim 层未开启挂机回城时不误报）；
    R7 无药可吃 → 空动作且不报错。

动作顺序契约：先吃药保命（HP 分支在前、MP 分支在后），再回城。

数值纪律：阈值默认值一律取自 ``app.combat.balance``，本文件禁止出现小数
字面量（tests/test_balance.py 有 grep 防回归断言）；policy dict 可按键覆盖
（hp_pct / mp_pct / urgent_hp_pct / free_slots）。

药品目录由 sim 层从 gamedata/items/*.json 派生后传入（本模块保持纯函数，
不 import app.core.db / sqlalchemy；落库是 sim 层的事）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.combat.balance import (
    BAG_FREE_SLOTS_RECALL,
    POTION_COOLDOWN_TICKS,
    POTION_HP_PCT,
    POTION_MP_PCT,
    POTION_URGENT_HP_PCT,
)

__all__ = [
    "PotionAction",
    "PotionPlan",
    "PotionSpec",
    "plan_potion_use",
]


@dataclass(frozen=True)
class PotionSpec:
    """单种药品的策略快照（item_id → spec 由 catalog 映射携带）。"""

    hp_restore: int = 0
    mp_restore: int = 0
    # 分支内替代链顺序：数值大者优先级高（太阳水瞬回双恢复 > 金创药缓回）。
    priority: int = 1


@dataclass(frozen=True)
class PotionAction:
    """返回给 sim 层执行的单个动作。

    kind: "drink"（item_id 为药品 id）| "recall"（item_id 恒为 None）。
    """

    kind: str
    item_id: str | None = None


@dataclass(frozen=True)
class PotionPlan:
    """一次规划的全部出参：动作序列 + 背包/CD 快照 diff（R4/R5）。"""

    actions: tuple[PotionAction, ...] = ()
    bag_after: dict[str, int] = field(default_factory=dict)
    cooldowns_after: dict[str, int] = field(default_factory=dict)


def _pick_potion(
    catalog: Mapping[str, PotionSpec],
    bag: Mapping[str, int],
    cooldowns: Mapping[str, int],
    now_tick: int,
    *,
    restore_attr: str,
    prefer_advanced: bool,
) -> str | None:
    """沿替代链选一个可立即饮用的药品 id；无可用药返回 None（R3/R4/R7）。

    prefer_advanced=False（常规线）：priority 升序，省着用高级药；
    prefer_advanced=True（危险线 R2）：priority 降序，保命优先。
    """
    candidates = [
        (spec.priority, item_id)
        for item_id, spec in catalog.items()
        if getattr(spec, restore_attr) > 0
    ]
    candidates.sort(reverse=prefer_advanced)
    for _, item_id in candidates:
        if bag.get(item_id, 0) <= 0:
            continue  # 缺货（含数量为 0）→ 顺延下一档
        cd_end = cooldowns.get(item_id)
        if cd_end is not None and now_tick < cd_end:
            continue  # 冷却中（严格小于：now == 到期 tick 即已解冻）
        return item_id
    return None


def plan_potion_use(
    *,
    hp: int,
    max_hp: int,
    mp: int,
    max_mp: int,
    bag: Mapping[str, int],
    catalog: Mapping[str, PotionSpec],
    now_tick: int = 0,
    cooldowns: Mapping[str, int] | None = None,
    free_slots: int | None = None,
    policy: Mapping[str, Any] | None = None,
) -> PotionPlan:
    """按阈值/CD/回城三道闸规划本 tick 的吃药动作（纯函数，R1-R7）。"""
    overrides: Mapping[str, Any] = policy or {}
    hp_pct = float(overrides.get("hp_pct", POTION_HP_PCT))
    mp_pct = float(overrides.get("mp_pct", POTION_MP_PCT))
    urgent_hp_pct = float(overrides.get("urgent_hp_pct", POTION_URGENT_HP_PCT))
    # 显式实参优先于 policy；两者都缺省（None）= 关闭回城检查（R6）。
    effective_free_slots: int | None
    if free_slots is not None:
        effective_free_slots = free_slots
    else:
        effective_free_slots = overrides.get("free_slots")

    working_bag = dict(bag)
    working_cooldowns = dict(cooldowns or {})
    actions: list[PotionAction] = []

    # HP 分支在前（先保命），MP 分支在后。
    if hp < max_hp * hp_pct:
        urgent = hp < max_hp * urgent_hp_pct
        item_id = _pick_potion(
            catalog,
            working_bag,
            working_cooldowns,
            now_tick,
            restore_attr="hp_restore",
            prefer_advanced=urgent,
        )
        if item_id is not None:
            actions.append(PotionAction("drink", item_id))
            working_bag[item_id] -= 1
            if working_bag[item_id] <= 0:
                del working_bag[item_id]  # 空堆叠不入库
            working_cooldowns[item_id] = now_tick + POTION_COOLDOWN_TICKS

    if mp < max_mp * mp_pct:
        item_id = _pick_potion(
            catalog,
            working_bag,
            working_cooldowns,
            now_tick,
            restore_attr="mp_restore",
            prefer_advanced=False,
        )
        if item_id is not None:
            actions.append(PotionAction("drink", item_id))
            working_bag[item_id] -= 1
            if working_bag[item_id] <= 0:
                del working_bag[item_id]
            working_cooldowns[item_id] = now_tick + POTION_COOLDOWN_TICKS

    # 回城闸最后追加（先吃药保命，再回城）。
    if (
        effective_free_slots is not None
        and effective_free_slots < BAG_FREE_SLOTS_RECALL
    ):
        actions.append(PotionAction("recall", None))

    return PotionPlan(
        actions=tuple(actions),
        bag_after=working_bag,
        cooldowns_after=working_cooldowns,
    )
