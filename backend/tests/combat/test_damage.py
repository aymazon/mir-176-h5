"""damage.py 表驱动测试（todo 10）：幸运/诅咒/减防/钳制/三通道。

管线来源 docs/combat-formulas.md §2–§4：
roll → 幸运/诅咒位置修正（luck≥9 硬规则刀刀上限）→ 技能倍率 →
减防御/魔御（DEF_DAMAGE_REDUCTION_FACTOR）→ round_half_up → 钳制 ≥ MIN_DAMAGE。
"""

from collections.abc import Callable
from random import Random
from typing import Final

import pytest

from app.combat import balance
from app.combat.damage import magical_damage, physical_damage, taoist_damage

N_ROLLS: Final[int] = 100_000


class ScriptedRandom(Random):
    """randint 返回预置序列的注入 rng（表驱动用，不碰全局随机源）。"""

    def __init__(self, *values: int) -> None:
        super().__init__()
        self._values = list(values)
        self._next = 0

    def randint(self, a: int, b: int) -> int:
        value = self._values[self._next % len(self._values)]
        self._next += 1
        return value


# --- 结算管线表驱动（docs/combat-formulas.md §2 手算样例） ------------------------


@pytest.mark.parametrize(
    ("roll", "defense", "expected"),
    [
        (6, 2, 6),  # ①u=0.5 → adjusted=6 → ③×1.0 → ④−0.1=5.9 → ⑤rhu→6
        (4, 80, 1),  # 防御碾压：4−4=0 → rhu→0 → 钳制 MIN_DAMAGE
    ],
)
def test_physical_pipeline_doc_examples(roll: int, defense: int, expected: int) -> None:
    damage = physical_damage(
        ScriptedRandom(roll), attack_min=4, attack_max=8, target_defense=defense
    )
    assert damage == expected


# --- 幸运/诅咒（docs/combat-formulas.md §4 TDD 表 + 验收 #3/#4 钳制） -------------


@pytest.mark.parametrize("roll", [4, 6, 8])
@pytest.mark.parametrize("luck", [balance.LUCK_MAX, balance.LUCK_MAX + 10])
def test_luck_at_or_above_max_always_upper_bound(roll: int, luck: int) -> None:
    """幸运≥9 刀刀上限；幸运>上限（如 10/19）同样钳在上限不溢出。"""
    damage = physical_damage(
        ScriptedRandom(roll),
        attack_min=4,
        attack_max=8,
        target_defense=0,
        luck=luck,
    )
    assert damage == 8


@pytest.mark.parametrize("roll", [4, 6, 8])
def test_extreme_curse_always_lower_bound(roll: int) -> None:
    """诅咒极值贴下限（无硬规则但 clamp 到 u'=0），永不溢出下限。"""
    damage = physical_damage(
        ScriptedRandom(roll), attack_min=4, attack_max=8, target_defense=0, curse=99
    )
    assert damage == 4


@pytest.mark.parametrize(
    ("roll", "expected"), [(4, 4), (6, 6), (8, 8)]
)
def test_no_bias_identity(roll: int, expected: int) -> None:
    """luck=curse=0 时 adjusted 恒等于 roll 本身。"""
    damage = physical_damage(
        ScriptedRandom(roll), attack_min=4, attack_max=8, target_defense=0
    )
    assert damage == expected


def test_intermediate_luck_shifts_toward_max() -> None:
    """中间档幸运单调趋上限：luck=5 的单点结果 ≥ 无幸运结果。"""
    low = physical_damage(
        ScriptedRandom(6), attack_min=4, attack_max=8, target_defense=0
    )
    high = physical_damage(
        ScriptedRandom(6), attack_min=4, attack_max=8, target_defense=0, luck=5
    )
    assert high > low


# --- 三通道（docs/combat-formulas.md §3 TDD 表） ---------------------------------


@pytest.mark.parametrize(
    ("channel", "kwargs", "roll", "expected"),
    [
        # 物理：攻[4,8] roll=6 vs DEF=2 → 6 − 0.10 → 6
        (physical_damage, {"attack_min": 4, "attack_max": 8, "target_defense": 2}, 6, 6),
        # 魔法：魔[10,20] roll=12 vs MAC=15 → 12 − 0.75 = 11.25 → 11
        (magical_damage, {"magic_min": 10, "magic_max": 20, "target_mac": 15}, 12, 11),
        # 道术：道[7,11] roll=9 vs DEF=40（社区近似按 DEF 计）→ 9 − 2.00 → 7
        (taoist_damage, {"tao_min": 7, "tao_max": 11, "target_defense": 40}, 9, 7),
    ],
)
def test_three_channels_table(
    channel: Callable[..., int],
    kwargs: dict[str, int],
    roll: int,
    expected: int,
) -> None:
    assert channel(ScriptedRandom(roll), **kwargs) == expected


# --- 技能倍率入参（倍率本体属 gamedata/skills，todo 11/20） -----------------------


def test_skill_multiplier_applied() -> None:
    """[4,8] roll=6 → adjusted=6 → ×1.8 = 10.8 → rhu → 11（无防御）。"""
    damage = physical_damage(
        ScriptedRandom(6),
        attack_min=4,
        attack_max=8,
        target_defense=0,
        skill_multiplier=1.8,
    )
    assert damage == 11


def test_default_multiplier_is_balance_base() -> None:
    """缺省倍率 = SKILL_MULTIPLIER_BASE（恒等元）。"""
    plain = physical_damage(
        ScriptedRandom(6), attack_min=4, attack_max=8, target_defense=0
    )
    explicit = physical_damage(
        ScriptedRandom(6),
        attack_min=4,
        attack_max=8,
        target_defense=0,
        skill_multiplier=balance.SKILL_MULTIPLIER_BASE,
    )
    assert plain == explicit


# --- 钳制与边界 ------------------------------------------------------------------


def test_damage_floor_is_min_damage() -> None:
    """防御远超攻击时伤害恒为 MIN_DAMAGE（验收：防御≥攻击恒为 1）。"""
    for roll in range(1, 3):
        damage = physical_damage(
            ScriptedRandom(roll), attack_min=1, attack_max=2, target_defense=1000
        )
        assert damage == balance.MIN_DAMAGE


def test_degenerate_range_never_crashes() -> None:
    """min==max 区间（span=0）不除零，幸运硬规则下也稳定。"""
    for luck in (0, balance.LUCK_MAX):
        damage = physical_damage(
            ScriptedRandom(5),
            attack_min=5,
            attack_max=5,
            target_defense=0,
            luck=luck,
        )
        assert damage == 5


@pytest.mark.parametrize(("kwargs", "match"), [
    ({"attack_min": 8, "attack_max": 4, "target_defense": 0}, "attack_max"),
    ({"attack_min": 4, "attack_max": 8, "target_defense": 0, "luck": -1}, "非负"),
    ({"attack_min": 4, "attack_max": 8, "target_defense": 0, "curse": -1}, "非负"),
])
def test_invalid_inputs_rejected(kwargs: dict[str, int], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        physical_damage(ScriptedRandom(6), **kwargs)


# --- 统计用例：固定 seed 期望值（离散推导，见下方注释） ----------------------------


@pytest.mark.parametrize(
    ("luck", "curse", "expected_mean"),
    [
        (0, 0, 6.0),  # u∈{0,.25,.5,.75,1} → adjusted {4,5,6,7,8} 均匀 → E=6.0
        (3, 0, 6.8),  # u+0.3 截断 → {0.3,.55,.8,1,1} → {5,6,7,8,8} → E=6.8
        (0, 3, 5.2),  # u−0.3 截断 → {0,0,.2,.45,.7} → {4,4,5,6,7} → E=5.2（镜像）
    ],
)
def test_luck_curse_statistical_expectation_fixed_seed(
    luck: int, curse: int, expected_mean: float
) -> None:
    rng = Random(42)
    total = sum(
        physical_damage(
            rng, attack_min=4, attack_max=8, target_defense=0, luck=luck, curse=curse
        )
        for _ in range(N_ROLLS)
    )
    mean = total / N_ROLLS
    assert mean == pytest.approx(expected_mean, abs=0.1), (
        f"mean={mean:.4f} expected={expected_mean}"
    )


def test_same_seed_reproduces_sequence() -> None:
    """同 seed 序列逐位一致（纯函数 + rng 注入 ⇒ 可复现、无隐藏全局状态）。"""
    rng_a = Random(42)
    seq_a = [
        physical_damage(rng_a, attack_min=4, attack_max=8, target_defense=2)
        for _ in range(200)
    ]
    rng_b = Random(42)
    seq_b = [
        physical_damage(rng_b, attack_min=4, attack_max=8, target_defense=2)
        for _ in range(200)
    ]
    assert seq_a == seq_b
