"""todo 12 TDD：自动吃药策略纯函数 plan_potion_use（先写测试，后实现）。

推导注释纪律：每个用例标注「由哪条策略规则推出期望」，不复述实现代码。

规则基线（docs/MVP/README.md §8 挂机策略 + 计划 todo 12 规格）：
  R1 HP < POTION_HP_PCT 喝普通 HP 药；MP < POTION_MP_PCT 喝 MP 药（严格小于）；
  R2 HP < POTION_URGENT_HP_PCT 危险线：优先高级药（priority 大者）；
  R3 同分支内首选药缺货/冷却时沿 priority 逐级替代——只有太阳水就喝太阳水；
  R4 每个 item_id 喝后进入 cooldown_ticks 冷却；冷却中同款不再喝（防抖动）；
  R5 背包扣减只写入返回值 bag_after（纯函数：入参 bag 不被修改、不碰 DB）；
  R6 背包空格 < N 追加回城动作；free_slots=None 关闭该检查；
  R7 无药可吃 → 空动作且不报错。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.combat import balance
from app.combat.potion import PotionAction, PotionSpec, plan_potion_use

# 药品目录由 sim 层从 gamedata/items/*.json 派生后传入（本模块保持纯函数）：
# 金创药/魔法药 priority=1，太阳水瞬回 HP+MP priority=2（金创药 < 太阳水）。
CATALOG: dict[str, PotionSpec] = {
    "potion_hp_small": PotionSpec(hp_restore=30, priority=1),
    "potion_mp_small": PotionSpec(mp_restore=20, priority=1),
    "sun_water": PotionSpec(hp_restore=50, mp_restore=30, priority=2),
}

_BASE: dict[str, Any] = {
    "hp": 100,
    "max_hp": 100,
    "mp": 100,
    "max_mp": 100,
    "bag": {},
    "catalog": CATALOG,
    "now_tick": 0,
}


def _plan(**over: Any) -> Any:
    kw = {**_BASE, **over}
    return plan_potion_use(**kw)


# --- 表驱动主矩阵：(输入覆盖, 期望动作序列) ------------------------------------

CASES = [
    pytest.param(
        {},
        (),
        id="healthy_no_trigger",
    ),
    # R1: 25% < 30% 阈值 → 喝背包里的普通金创药
    pytest.param(
        {"hp": 25, "max_hp": 100, "bag": {"potion_hp_small": 3}},
        (PotionAction("drink", "potion_hp_small"),),
        id="hp_below_line_drinks_basic",
    ),
    # R1 严格小于：恰好压在阈值线上不触发
    pytest.param(
        {"hp": 30, "max_hp": 100, "bag": {"potion_hp_small": 3}},
        (),
        id="hp_at_line_not_below_no_drink",
    ),
    # fail-case 实证：HP 25%、背包只有太阳水 → R3 替代链喝太阳水
    pytest.param(
        {"hp": 25, "max_hp": 100, "bag": {"sun_water": 2}},
        (PotionAction("drink", "sun_water"),),
        id="hp_25pct_fallback_sun_water",
    ),
    # R2: HP 10% 越过危险线 → 两药都在也直接用高级的太阳水
    pytest.param(
        {"hp": 10, "max_hp": 100, "bag": {"potion_hp_small": 5, "sun_water": 2}},
        (PotionAction("drink", "sun_water"),),
        id="urgent_prefers_advanced_potion",
    ),
    # R1 MP 分支：19% < 20%
    pytest.param(
        {"mp": 19, "max_mp": 100, "bag": {"potion_mp_small": 4}},
        (PotionAction("drink", "potion_mp_small"),),
        id="mp_below_line_drinks_mp_potion",
    ),
    # HP 与 MP 同时低 → 各喝一瓶，顺序 HP 在前
    pytest.param(
        {
            "hp": 10,
            "max_hp": 100,
            "mp": 5,
            "max_mp": 100,
            "bag": {"potion_hp_small": 1, "potion_mp_small": 1},
        },
        (
            PotionAction("drink", "potion_hp_small"),
            PotionAction("drink", "potion_mp_small"),
        ),
        id="both_low_drinks_two_potions",
    ),
    # R7 fail-case 实证：背包无药 → 空动作不报错
    pytest.param(
        {"hp": 10, "max_hp": 100, "bag": {}},
        (),
        id="empty_bag_empty_plan_no_error",
    ),
    # 数量为 0 视为没有该药
    pytest.param(
        {"hp": 10, "max_hp": 100, "bag": {"potion_hp_small": 0}},
        (),
        id="zero_quantity_skipped",
    ),
    # R4 fail-case 实证：CD 到期 tick=3 > now=2 → 冷却中不重复吃
    pytest.param(
        {
            "hp": 10,
            "max_hp": 100,
            "bag": {"potion_hp_small": 2},
            "cooldowns": {"potion_hp_small": 3},
            "now_tick": 2,
        },
        (),
        id="on_cooldown_skips_same_item",
    ),
    # R4 边界：now == CD 到期 tick 即已解冻 → 可再吃
    pytest.param(
        {
            "hp": 10,
            "max_hp": 100,
            "bag": {"potion_hp_small": 2},
            "cooldowns": {"potion_hp_small": 3},
            "now_tick": 3,
        },
        (PotionAction("drink", "potion_hp_small"),),
        id="cooldown_expired_boundary_drinks_again",
    ),
    # R6: 空格 2 < N=5 → 只回城不吃药（血量健康）
    pytest.param(
        {"bag": {}, "free_slots": 2},
        (PotionAction("recall", None),),
        id="low_free_slots_triggers_recall",
    ),
    # 动作顺序：先吃药保命，再回城
    pytest.param(
        {
            "hp": 10,
            "max_hp": 100,
            "bag": {"potion_hp_small": 1},
            "free_slots": 2,
        },
        (PotionAction("drink", "potion_hp_small"), PotionAction("recall", None)),
        id="drink_before_recall_order",
    ),
]


@pytest.mark.parametrize(("over", "expected_actions"), CASES)
def test_plan_actions_table(over: dict[str, Any], expected_actions: tuple) -> None:
    plan = _plan(**over)
    assert plan.actions == expected_actions


# --- 背包 diff / CD 出参（R4/R5）-----------------------------------------------


def test_bag_diff_and_cooldown_written_to_output_only() -> None:
    # 推导：喝 1 瓶 → bag_after 余 2；CD = now(0) + cooldown_ticks(默认 balance 值)
    bag = {"potion_hp_small": 3}
    plan = _plan(hp=10, max_hp=100, bag=bag)
    assert plan.bag_after == {"potion_hp_small": 2}
    assert plan.cooldowns_after == {"potion_hp_small": balance.POTION_COOLDOWN_TICKS}
    # R5 纯函数证据：入参 bag 不被原地修改
    assert bag == {"potion_hp_small": 3}


def test_last_potion_removed_from_bag_after() -> None:
    # 推导：qty 1 喝掉后堆叠归零 → 键从 bag_after 移除（空堆叠不入库）
    plan = _plan(hp=10, max_hp=100, bag={"potion_hp_small": 1})
    assert plan.bag_after == {}


def test_urgent_cd_blocks_sun_water_falls_back_to_basic() -> None:
    # 推导：危险线内太阳水进 CD → R3 替代链降级喝金创药（不同 item_id 不受对方 CD 影响）
    plan = _plan(
        hp=10,
        max_hp=100,
        bag={"potion_hp_small": 2, "sun_water": 1},
        cooldowns={"sun_water": 99},
        now_tick=0,
    )
    assert plan.actions == (PotionAction("drink", "potion_hp_small"),)


# --- fail-case 实证：CD 中连续低 HP → 第一瓶吃，后续不动，解冻后再吃 -------------


def test_consecutive_low_hp_first_drink_then_hold_until_cd_ends() -> None:
    first = _plan(hp=10, max_hp=100, bag={"potion_hp_small": 2})
    assert len(first.actions) == 1

    second = _plan(
        hp=10,
        max_hp=100,
        bag=first.bag_after,
        cooldowns=first.cooldowns_after,
        now_tick=1,
    )
    assert second.actions == ()

    cd_end = first.cooldowns_after["potion_hp_small"]
    third = _plan(
        hp=10,
        max_hp=100,
        bag=second.bag_after,
        cooldowns=second.cooldowns_after,
        now_tick=cd_end,
    )
    assert len(third.actions) == 1


# --- 默认阈值与策略配置 -------------------------------------------------------


def test_default_thresholds_come_from_balance_constants() -> None:
    # 推导：边界取自 balance 常量本身——max*POTION_HP_PCT - 1 必触发、线上值不触发
    max_hp = 1000
    line = int(max_hp * balance.POTION_HP_PCT)
    below = _plan(hp=line - 1, max_hp=max_hp, bag={"potion_hp_small": 1})
    at_line = _plan(hp=line, max_hp=max_hp, bag={"potion_hp_small": 1})
    assert below.actions == (PotionAction("drink", "potion_hp_small"),)
    assert at_line.actions == ()

    max_mp = 500
    mp_line = int(max_mp * balance.POTION_MP_PCT)
    mp_below = _plan(mp=mp_line - 1, max_mp=max_mp, bag={"potion_mp_small": 1})
    assert mp_below.actions == (PotionAction("drink", "potion_mp_small"),)


def test_policy_dict_overrides_thresholds() -> None:
    # 推导：40% 高于默认 30% 线本不该喝；策略 dict 把 hp_pct 抬到 50% 后应触发
    over = {"hp": 40, "max_hp": 100, "bag": {"potion_hp_small": 1}}
    assert _plan(**over).actions == ()
    tuned = _plan(**over, policy={"hp_pct": 0.5})
    assert tuned.actions == (PotionAction("drink", "potion_hp_small"),)


def test_free_slots_none_disables_recall_even_with_zero_slots() -> None:
    # 推导：R6 的 free_slots=None 是显式关闭信号（sim 层未开启挂机回城时不误报）
    plan = _plan(bag={}, free_slots=None)
    assert plan.actions == ()
