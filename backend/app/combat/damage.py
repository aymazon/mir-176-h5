"""伤害结算纯函数（todo 10）。

结算管线（docs/combat-formulas.md §2，顺序固定）::

    ① base = rng.randint(attack_min, attack_max)；u = (base − min) / span
    ② u' = clamp(u + luck×LUCK_HIT_BIAS − curse×CURSE_HIT_BIAS, 0, 1)
       特例：luck ≥ LUCK_MAX → u' = 1（"幸运9套刀刀上限"硬规则，方差归零）
       adjusted = attack_min + round_half_up(u' × span)
    ③ dmg = adjusted × skill_multiplier（平砍 = SKILL_MULTIPLIER_BASE 恒等元；
       各技能倍率定义处 = gamedata/skills/*.json，本函数只接受 float 入参）
    ④ dmg −= defender_stat × DEF_DAMAGE_REDUCTION_FACTOR
    ⑤ dmg = max(round_half_up(dmg), MIN_DAMAGE)   # 至少 MIN_DAMAGE 点

三通道（§3，同一底座不同属性对）：
物理走攻/防、魔法走魔/魔御、道术走道（防御方按 DEF 计，社区近似）。

纪律：
- 纯函数：不读 DB、不写库、不依赖全局随机源——``random.Random`` 实例由调用方
  注入（可复现）；
- 不引入暴击等新概念（176 无暴击，幸运即方差修正）；
- 系数一律取自 ``app.combat.balance``（本文件禁止出现小数字面量，
  tests/test_balance.py 有 grep 防回归断言）。
"""

from math import floor
from random import Random

from app.combat.balance import (
    CURSE_HIT_BIAS,
    DEF_DAMAGE_REDUCTION_FACTOR,
    LUCK_HIT_BIAS,
    LUCK_MAX,
    MIN_DAMAGE,
    SKILL_MULTIPLIER_BASE,
)

__all__ = ["magical_damage", "physical_damage", "taoist_damage"]


def _round_half_up(value: float) -> int:
    """四舍五入（half up；Python 内建 round 是 banker's rounding，不合契约）。"""
    return floor(value + 1 / 2)


def _settle(
    rng: Random,
    *,
    attack_min: int,
    attack_max: int,
    defender_stat: int,
    luck: int,
    curse: int,
    skill_multiplier: float,
) -> int:
    """三通道共用的结算底座（§2 管线①–⑤）。"""
    if attack_max < attack_min:
        raise ValueError(f"attack_max({attack_max}) 不得小于 attack_min({attack_min})")
    if luck < 0 or curse < 0:
        raise ValueError("luck/curse 必须非负")
    base = rng.randint(attack_min, attack_max)
    span = attack_max - attack_min
    position = (base - attack_min) / span if span > 0 else 0
    if luck >= LUCK_MAX:
        biased = 1
    else:
        biased = min(
            max(position + luck * LUCK_HIT_BIAS - curse * CURSE_HIT_BIAS, 0), 1
        )
    adjusted = attack_min + _round_half_up(biased * span)
    damage = adjusted * skill_multiplier - defender_stat * DEF_DAMAGE_REDUCTION_FACTOR
    return max(_round_half_up(damage), MIN_DAMAGE)


def physical_damage(
    rng: Random,
    *,
    attack_min: int,
    attack_max: int,
    target_defense: int,
    luck: int = 0,
    curse: int = 0,
    skill_multiplier: float = SKILL_MULTIPLIER_BASE,
) -> int:
    """物理通道：攻击区间 roll vs 目标防御（DEF）。"""
    return _settle(
        rng,
        attack_min=attack_min,
        attack_max=attack_max,
        defender_stat=target_defense,
        luck=luck,
        curse=curse,
        skill_multiplier=skill_multiplier,
    )


def magical_damage(
    rng: Random,
    *,
    magic_min: int,
    magic_max: int,
    target_mac: int,
    luck: int = 0,
    curse: int = 0,
    skill_multiplier: float = SKILL_MULTIPLIER_BASE,
) -> int:
    """魔法通道：魔法区间 roll vs 目标魔御（MAC）。"""
    return _settle(
        rng,
        attack_min=magic_min,
        attack_max=magic_max,
        defender_stat=target_mac,
        luck=luck,
        curse=curse,
        skill_multiplier=skill_multiplier,
    )


def taoist_damage(
    rng: Random,
    *,
    tao_min: int,
    tao_max: int,
    target_defense: int,
    luck: int = 0,
    curse: int = 0,
    skill_multiplier: float = SKILL_MULTIPLIER_BASE,
) -> int:
    """道术通道：道术区间 roll vs 目标防御（社区近似：道术按 DEF 计）。"""
    return _settle(
        rng,
        attack_min=tao_min,
        attack_max=tao_max,
        defender_stat=target_defense,
        luck=luck,
        curse=curse,
        skill_multiplier=skill_multiplier,
    )
