"""combat/effects.py 单测（todo 11，TDD）。

契约：效果逻辑是纯函数（apply/tick/expire 三函数语义明确），实例由 sim 层持有；
期望值一律从 EFFECT_REGISTRY 定义推导，测试不硬编码数值。
"""

from typing import Any

import pytest

from app.combat.effects import (
    EFFECT_REGISTRY,
    EffectDefinition,
    EffectInstance,
    UnknownEffectError,
    absorb_damage,
    active_stat_bonus,
    apply_effect,
    apply_ground_effect,
    can_move,
    expire_effect,
    get_effect,
    is_targetable,
    tick_effect,
    tick_ground_effect,
)

# MVP 六种效果的注册表锚点（计划 todo 11 规格点名；id 是外部契约，数值不是）
MVP_EFFECT_IDS: frozenset[str] = frozenset(
    {"poison_dot", "fire_wall", "invisibility", "trap_root", "magic_shield", "divine_water"}
)


@pytest.fixture(scope="module")
def poison() -> EffectDefinition:
    return get_effect("poison_dot")


@pytest.fixture(scope="module")
def fire_wall() -> EffectDefinition:
    return get_effect("fire_wall")


# --- 1. 注册表 -------------------------------------------------------------------


def test_builtin_registry_covers_six_mvp_effects() -> None:
    """推导：规格点名的六类效果必须全部注册；kind 与语义一一对应。"""
    assert MVP_EFFECT_IDS <= set(EFFECT_REGISTRY)
    expected_kinds: dict[str, str] = {
        "poison_dot": "dot",
        "fire_wall": "ground_dot",
        "invisibility": "buff",
        "trap_root": "control",
        "magic_shield": "absorb",
        "divine_water": "buff",
    }
    for effect_id, kind in expected_kinds.items():
        assert EFFECT_REGISTRY[effect_id].kind == kind, effect_id


def test_get_unknown_effect_raises() -> None:
    """fail case 实证：未知效果 id → 报错（防 sim 层拼错 key 静默吞掉）。"""
    with pytest.raises(UnknownEffectError, match="meteor_rain"):
        get_effect("meteor_rain")


# --- 2. 毒 dot：按间隔掉血、到期自动结束 -------------------------------------------


def test_poison_ticks_every_interval_then_expires(poison: EffectDefinition) -> None:
    """推导：duration=N、interval=1、每跳 damage → N 次 tick 共 N×damage 点，末次过期。"""
    per_tick = int(poison.params["damage_per_tick"])
    inst = apply_effect(poison, owner_id="monster_1")
    assert inst.remaining_ticks == poison.duration_ticks
    total = 0
    for i in range(1, poison.duration_ticks + 1):
        outcome = tick_effect(inst, poison)
        total += sum(ev.amount for ev in outcome.damage_events)
        assert outcome.expired is (i == poison.duration_ticks)
    assert total == per_tick * poison.duration_ticks


def test_poison_skips_non_interval_ticks() -> None:
    """推导：interval>1 的自定义 dot 只在 elapsed % interval == 0 时结算。"""
    custom = EffectDefinition(
        effect_id="slow_bleed",
        kind="dot",
        duration_ticks=4,
        tick_interval_ticks=2,
        params={"damage_per_tick": 7},
    )
    inst = apply_effect(custom, owner_id="m")
    hits = [
        sum(ev.amount for ev in tick_effect(inst, custom).damage_events)
        for _ in range(4)
    ]
    assert hits == [0, 7, 0, 7]  # 第 1/3 tick 不结算，第 2/4 tick 结算


def test_tick_after_expire_is_noop(poison: EffectDefinition) -> None:
    """推导：过期后再 tick 不产生事件也不复活（sim 层可安全多调一次）。"""
    inst = apply_effect(poison, owner_id="m")
    for _ in range(poison.duration_ticks):
        tick_effect(inst, poison)
    assert tick_effect(inst, poison).damage_events == ()
    assert inst.expired is True


def test_expire_effect_forces_removal(poison: EffectDefinition) -> None:
    """推导：expire 三函数之一——驱散/净化路径直接置过期，幂等。"""
    inst = apply_effect(poison, owner_id="m")
    assert expire_effect(inst).expired is True
    assert expire_effect(inst).expired is True  # 幂等


# --- 3. 火墙地面 dot：格内每 tick 掉血、走出停止 ------------------------------------


def test_fire_wall_burns_only_occupants_inside(fire_wall: EffectDefinition) -> None:
    """推导：伤害只落在 cells 内的实体；格外观测者不掉血。"""
    cells = {(2, 2), (2, 3)}
    ground = apply_ground_effect(fire_wall, cells)
    inside, outside = "hero_a", "hero_b"
    outcome = tick_ground_effect(
        ground,
        fire_wall,
        occupants={(2, 2): [inside], (5, 5): [outside]},
    )
    damaged = {ev.owner_id for ev in outcome.damage_events}
    assert damaged == {inside}
    per_tick = int(fire_wall.params["damage_per_tick"])
    assert all(ev.amount == per_tick for ev in outcome.damage_events)


def test_fire_wall_stops_when_walking_out(fire_wall: EffectDefinition) -> None:
    """fail case 实证：走出火墙 → 不再 dot（occupants 由 sim 每 tick 重算）。"""
    ground = apply_ground_effect(fire_wall, {(0, 0)})
    runner = "runner"
    burned = tick_ground_effect(ground, fire_wall, {(0, 0): [runner]})
    assert {ev.owner_id for ev in burned.damage_events} == {runner}
    escaped = tick_ground_effect(ground, fire_wall, {(3, 3): [runner]})
    assert escaped.damage_events == ()  # 人已离开格子范围


def test_fire_wall_expires_after_duration(fire_wall: EffectDefinition) -> None:
    """推导：地面效果存留 duration_ticks 后过期，之后不再结算任何人。"""
    ground = apply_ground_effect(fire_wall, {(1, 1)})
    victim = "victim"
    last = None
    for _ in range(fire_wall.duration_ticks):
        last = tick_ground_effect(ground, fire_wall, {(1, 1): [victim]})
    assert last is not None and last.expired is True
    after = tick_ground_effect(ground, fire_wall, {(1, 1): [victim]})
    assert after.damage_events == () and after.expired is True


# --- 4. 隐身：怪物 AI 丢失目标 ------------------------------------------------------


class MockMonsterAI:
    """mock 怪物索敌：只依赖 effects.is_targetable 谓词（真实 AI 在 todo 15）。"""

    def acquire_target(
        self,
        candidates: list[str],
        effects_of: Any,
    ) -> str | None:
        visible = [c for c in candidates if is_targetable(effects_of(c))]
        return visible[0] if visible else None


def test_invisible_unit_is_not_targetable() -> None:
    """推导：隐身定义 blocks_targeting=True；生效期间谓词必须为 False。"""
    invisibility = get_effect("invisibility")
    stealth_hero: list[EffectInstance] = [apply_effect(invisibility, "stealth_hero")]
    assert is_targetable(stealth_hero) is False


def test_mock_ai_loses_target_when_all_candidates_invisible() -> None:
    """fail case 实证：隐身时 mock AI 索敌返回 None。"""
    invisibility = get_effect("invisibility")
    ai = MockMonsterAI()
    effects_map: dict[str, list[EffectInstance]] = {
        "hero": [apply_effect(invisibility, "hero")],
    }
    assert ai.acquire_target(["hero"], effects_map.get) is None


def test_ai_picks_visible_target_among_mixed() -> None:
    """推导：混合候选下 AI 只能选中未隐身者（隐身保命语义）。"""
    invisibility = get_effect("invisibility")
    ai = MockMonsterAI()
    effects_map: dict[str, list[EffectInstance]] = {
        "cloaked": [apply_effect(invisibility, "cloaked")],
        "exposed": [],
    }
    assert ai.acquire_target(["cloaked", "exposed"], effects_map.get) == "exposed"


def test_invisibility_expiry_restores_targetability() -> None:
    """推导：隐身到时过期后，谓词恢复 True（sim 移除实例等价于空列表）。"""
    invisibility = get_effect("invisibility")
    inst = apply_effect(invisibility, "hero")
    for _ in range(invisibility.duration_ticks):
        tick_effect(inst, invisibility)
    assert is_targetable([inst]) is True  # 已 expired，不再拦截


# --- 5. 困魔咒定身 -----------------------------------------------------------------


def test_trap_root_blocks_movement_until_expiry() -> None:
    """推导：blocks_movement 生效期 can_move=False；到期后恢复。"""
    trap = get_effect("trap_root")
    inst = apply_effect(trap, "boss")
    assert can_move([inst]) is False
    for _ in range(trap.duration_ticks):
        tick_effect(inst, trap)
    assert can_move([inst]) is True


def test_free_unit_can_move() -> None:
    """推导：无控制效果 → 可移动（谓词对空集的恒等元）。"""
    assert can_move([]) is True


# --- 6. 魔法盾吸收 -----------------------------------------------------------------


def test_magic_shield_absorbs_partial_damage() -> None:
    """推导：吸收池 50（registry），先扣池后透传；池余量随伤害递减。"""
    shield = get_effect("magic_shield")
    pool = int(shield.params["absorb_pool"])
    inst = apply_effect(shield, "mage")
    passthrough, remaining = absorb_damage(inst, pool - 20)
    assert passthrough == 0 and remaining == 20
    assert inst.expired is False


def test_magic_shield_breaks_when_pool_exhausted() -> None:
    """fail case 实证：超量伤害击破护盾 → 溢出部分透传 HP，盾标记过期。"""
    shield = get_effect("magic_shield")
    pool = int(shield.params["absorb_pool"])
    inst = apply_effect(shield, "mage")
    passthrough, remaining = absorb_damage(inst, pool + 13)
    assert passthrough == 13 and remaining == 0
    assert inst.expired is True


def test_magic_shield_also_expires_by_duration() -> None:
    """推导：没被打穿也会到时消失（双终止条件：池空 or 时长归零）。"""
    shield = get_effect("magic_shield")
    inst = apply_effect(shield, "mage")
    for _ in range(shield.duration_ticks):
        outcome = tick_effect(inst, shield)
    assert outcome.expired is True
    assert inst.absorb_remaining == int(shield.params["absorb_pool"])  # 没挨打，池满


# --- 7. 神水限时 buff ---------------------------------------------------------------


def test_divine_water_grants_stat_bonus_while_active() -> None:
    """推导：buff 加值 = 未过期实例 params[stat] 求和；stat 键来自注册表定义。"""
    water = get_effect("divine_water")
    stat = next(iter(water.params))
    bonus = int(water.params[stat])
    inst = apply_effect(water, "grinder")
    assert active_stat_bonus([inst], stat) == bonus


def test_divine_water_bonus_gone_after_expiry() -> None:
    """推导：限时 buff 到期后加值归零（神水「限时」语义）。"""
    water = get_effect("divine_water")
    stat = next(iter(water.params))
    inst = apply_effect(water, "grinder")
    for _ in range(water.duration_ticks):
        tick_effect(inst, water)
    assert active_stat_bonus([inst], stat) == 0


def test_apply_tick_expire_are_distinct_phases(poison: EffectDefinition) -> None:
    """推导：三函数契约——apply 建满时长实例 / tick 推进并结算 / expire 强制终结。"""
    fresh = apply_effect(poison, "m")
    assert fresh.remaining_ticks == poison.duration_ticks and not fresh.expired
    mid = tick_effect(fresh, poison)
    assert fresh.remaining_ticks == poison.duration_ticks - 1
    assert not mid.expired
    done = expire_effect(fresh)
    assert done.expired and done.remaining_ticks == 0
