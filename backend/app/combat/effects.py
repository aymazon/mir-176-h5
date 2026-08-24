"""状态 / 地面效果纯函数（todo 11）。

三阶段契约（tests/combat/test_effects.py 被测约定）：
- ``apply_effect`` / ``apply_ground_effect``：由定义建满时长实例；
- ``tick_effect`` / ``tick_ground_effect``：推进一 tick 并结算（dot 按间隔
  掉血、到期自动过期；地面 dot 只结算 cells 内的占位者）；
- ``expire_effect``：驱散/净化路径强制终结，幂等。

生命周期纪律：
- **实例状态由 sim 层持有**——本模块不设任何全局可变状态；``EffectInstance``
  是 sim 层容器里的普通值对象，tick/absorb 就地推进它并返回事件快照
  （``TickOutcome``），sim 层据此扣血/广播；
- ``EFFECT_REGISTRY`` 是封闭集合的**不可变定义表**（id 是外部契约，数值是
  平衡数据）：未知 id → UnknownEffectError fail-fast，防 sim 层拼错 key 被静默吞掉。

MVP 六效果与 gamedata 锚点（数值手抄 tests/fixtures/skills_32.json 同源字段，
保持「同一技能在技能表和效果表里语义一致」）：
- poison_dot   ← 施毒术 poison_amulet（dot_damage_per_tick=3, 8 tick）
- fire_wall    ← 火墙 fire_wall（damage_per_tick=5, duration_ticks=9）
- invisibility ← 隐身术 invisibility（duration_ticks=15，blocks_targeting）
- trap_root    ← 困魔咒 trap_curse（root_duration_ticks=5，blocks_movement）
- magic_shield ← 魔法盾 magic_shield（absorb_pool=50, duration_ticks=30）
- divine_water ← 神水（限时 buff；无 fixture 锚点，自创占位待 todo 37 校准）

纪律：纯函数、不读 DB、不写库、无随机源；本文件无小数字面量（效果参数
一律整数点数，倍率类需求归 skills.get_multiplier / balance）。
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "EFFECT_REGISTRY",
    "EffectDefinition",
    "EffectInstance",
    "UnknownEffectError",
    "absorb_damage",
    "active_stat_bonus",
    "apply_effect",
    "apply_ground_effect",
    "can_move",
    "expire_effect",
    "get_effect",
    "is_targetable",
    "tick_effect",
    "tick_ground_effect",
]

# 效果大类（与注册表 kind 一一对应；sim 层按 kind 决定挂载位置：
# dot/control/absorb/buff 挂单位，ground_dot 挂地图格子）。
EffectKind = Literal["dot", "ground_dot", "buff", "control", "absorb"]


class UnknownEffectError(LookupError):
    """未知效果 id（不在 EFFECT_REGISTRY）；message 携带该 id。"""


class EffectDefinition(BaseModel):
    """效果静态定义（不可变值对象；params 键随 kind 有约定语义）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    effect_id: str
    kind: EffectKind
    duration_ticks: int = Field(ge=1)
    # 每 N tick 结算一次（dot 类）；1 = 每 tick 结算。
    tick_interval_ticks: int = Field(default=1, ge=1)
    # 参数包：dot→damage_per_tick；absorb→absorb_pool；buff→<stat>: 加值。
    params: dict[str, int | float] = Field(default_factory=dict)
    # 生效期间拦截索敌（隐身）/ 拦截移动（定身）；谓词消费见 is_targetable/can_move。
    blocks_targeting: bool = False
    blocks_movement: bool = False


@dataclass(slots=True)
class EffectInstance:
    """挂在单个实体上的效果实例（sim 层持有；tick/absorb 就地推进）。

    params/blocks_* 是 apply 时从定义**快照**下来的——实例自洽，sim 层
    不必回查注册表即可做谓词判断。
    """

    definition_id: str
    owner_id: str
    remaining_ticks: int
    params: dict[str, int | float] = field(default_factory=dict)
    blocks_targeting: bool = False
    blocks_movement: bool = False
    # 吸收池余量（仅 absorb 类 > 0；打穿即过期）。
    absorb_remaining: int = 0
    expired: bool = False


@dataclass(slots=True)
class GroundEffectInstance:
    """地面效果实例（火墙等；cells 内每 tick 对占位者结算）。"""

    definition_id: str
    cells: frozenset[tuple[int, int]]
    remaining_ticks: int
    expired: bool = False


@dataclass(frozen=True, slots=True)
class DamageEvent:
    """单次结算伤害事件（owner_id 由调用方占位表提供，本模块不做命中判定）。"""

    owner_id: str
    amount: int


@dataclass(frozen=True, slots=True)
class TickOutcome:
    """单次 tick 的结算快照：本 tick 产生的事件 + 实例是否已过期。"""

    damage_events: tuple[DamageEvent, ...] = ()
    expired: bool = False


# --- MVP 六效果注册表（封闭集合；数值出处见模块 docstring 锚点表）----------------

EFFECT_REGISTRY: Final[dict[str, EffectDefinition]] = {
    "poison_dot": EffectDefinition(
        effect_id="poison_dot",
        kind="dot",
        duration_ticks=8,
        tick_interval_ticks=1,
        params={"damage_per_tick": 3},
    ),
    "fire_wall": EffectDefinition(
        effect_id="fire_wall",
        kind="ground_dot",
        duration_ticks=9,
        params={"damage_per_tick": 5},
    ),
    "invisibility": EffectDefinition(
        effect_id="invisibility",
        kind="buff",
        duration_ticks=15,
        blocks_targeting=True,
    ),
    "trap_root": EffectDefinition(
        effect_id="trap_root",
        kind="control",
        duration_ticks=5,
        blocks_movement=True,
    ),
    "magic_shield": EffectDefinition(
        effect_id="magic_shield",
        kind="absorb",
        duration_ticks=30,
        params={"absorb_pool": 50},
    ),
    "divine_water": EffectDefinition(
        effect_id="divine_water",
        kind="buff",
        duration_ticks=60,
        params={"attack_bonus": 5},
    ),
}


def get_effect(effect_id: str) -> EffectDefinition:
    """按 id 取效果定义；未知 id → UnknownEffectError（fail-fast）。"""
    try:
        return EFFECT_REGISTRY[effect_id]
    except KeyError:
        raise UnknownEffectError(f"unknown effect id: {effect_id}") from None


# --- 单位效果：apply / tick / expire ---------------------------------------------


def apply_effect(definition: EffectDefinition, owner_id: str) -> EffectInstance:
    """由定义创建满时长实例（快照 params 与拦截旗标）。"""
    return EffectInstance(
        definition_id=definition.effect_id,
        owner_id=owner_id,
        remaining_ticks=definition.duration_ticks,
        params=dict(definition.params),
        blocks_targeting=definition.blocks_targeting,
        blocks_movement=definition.blocks_movement,
        absorb_remaining=int(definition.params.get("absorb_pool", 0)),
    )


def tick_effect(inst: EffectInstance, definition: EffectDefinition) -> TickOutcome:
    """推进一 tick：先扣时长，再按间隔结算 dot 伤害。

    - 第 i 次 tick（i 从 1 计）仅在 ``i % tick_interval_ticks == 0`` 时结算，
      保证 interval>1 的慢速 dot 跳过中间拍；
    - 末次 tick 置过期；过期后再 tick 是 no-op（sim 层可安全多调）；
    - 无 damage_per_tick 的效果（buff/control/absorb）只走计时，不出伤害事件。
    """
    if inst.expired:
        return TickOutcome(expired=True)
    inst.remaining_ticks -= 1
    if inst.remaining_ticks <= 0:
        inst.expired = True
    elapsed = definition.duration_ticks - inst.remaining_ticks
    events: tuple[DamageEvent, ...] = ()
    if elapsed % max(1, definition.tick_interval_ticks) == 0:
        amount = int(definition.params.get("damage_per_tick", 0))
        if amount > 0:
            events = (DamageEvent(inst.owner_id, amount),)
    return TickOutcome(events, inst.expired)


def expire_effect(inst: EffectInstance) -> EffectInstance:
    """强制终结（驱散/净化/被打断路径）；幂等，返回原实例便于链式书写。"""
    inst.expired = True
    inst.remaining_ticks = 0
    return inst


def absorb_damage(inst: EffectInstance, incoming: int) -> tuple[int, int]:
    """吸收池结算：先扣池后透传，返回 ``(透传伤害, 池余量)``。

    - 池够：全额吸收，透传 0，池余量递减；
    - 超量：溢出部分透传 HP，池清零并标记过期（护盾被击破）；
    - 已过期或非吸收类实例：原样透传（no-op 安全性）。
    """
    if incoming < 0:
        raise ValueError(f"incoming 必须 ≥ 0，实得 {incoming}")
    if inst.expired or inst.absorb_remaining <= 0:
        return incoming, inst.absorb_remaining
    absorbed = min(incoming, inst.absorb_remaining)
    inst.absorb_remaining -= absorbed
    if inst.absorb_remaining == 0:
        inst.expired = True  # 双终止条件之一：池空即破
    return incoming - absorbed, inst.absorb_remaining


# --- 谓词（怪物 AI / 移动系统消费）-------------------------------------------------


def is_targetable(instances: Iterable[EffectInstance]) -> bool:
    """存在未过期的 blocks_targeting 效果（隐身）→ 不可被索敌。"""
    return not any(inst.blocks_targeting and not inst.expired for inst in instances)


def can_move(instances: Iterable[EffectInstance]) -> bool:
    """存在未过期的 blocks_movement 效果（定身）→ 不可移动。"""
    return not any(inst.blocks_movement and not inst.expired for inst in instances)


def active_stat_bonus(instances: Iterable[EffectInstance], stat: str) -> int:
    """未过期实例 params[stat] 加值求和（限时 buff 的面板贡献）。"""
    return sum(
        int(inst.params[stat])
        for inst in instances
        if not inst.expired and stat in inst.params
    )


# --- 地面效果：apply / tick -------------------------------------------------------


def apply_ground_effect(
    definition: EffectDefinition,
    cells: Iterable[tuple[int, int]],
) -> GroundEffectInstance:
    """在给定格子集合上放置地面效果（满时长实例）。"""
    return GroundEffectInstance(
        definition_id=definition.effect_id,
        cells=frozenset(cells),
        remaining_ticks=definition.duration_ticks,
    )


def tick_ground_effect(
    ground: GroundEffectInstance,
    definition: EffectDefinition,
    occupants: Mapping[tuple[int, int], Sequence[str]],
) -> TickOutcome:
    """推进地面效果一 tick：只对 cells 内的占位者结算 damage_per_tick。

    occupants 由 sim 层每 tick 重算传入——走出区域下次自然不再掉血；
    存留 duration_ticks 后过期，之后不再结算任何人（幂等 no-op）。
    """
    if ground.expired:
        return TickOutcome(expired=True)
    ground.remaining_ticks -= 1
    if ground.remaining_ticks <= 0:
        ground.expired = True
    per_tick = int(definition.params.get("damage_per_tick", 0))
    events = tuple(
        DamageEvent(owner_id, per_tick)
        for cell, owners in occupants.items()
        if cell in ground.cells
        for owner_id in owners
    )
    return TickOutcome(events, ground.expired)
