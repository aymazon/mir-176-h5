"""hit.py 表驱动测试（todo 10）：命中概率截断 + 固定 seed 统计。

公式来源 docs/combat-formulas.md §1（thelegendofmir.com 2:1 公认式的离散均匀读法）：
P_raw = min(accuracy, agility + 1) / (agility + 1)，截断 [MIN_HIT_RATE, MAX_HIT_RATE]。
"""

from random import Random
from typing import Final

import pytest

from app.combat import balance
from app.combat.hit import hit_check, hit_probability

N_ROLLS: Final[int] = 100_000
STAT_TOLERANCE: Final[float] = 0.02


class ScriptedRandom(Random):
    """random() 返回预置序列的注入 rng（验证判定方向用，不碰全局随机源）。"""

    def __init__(self, *values: float) -> None:
        super().__init__()
        self._values = list(values)
        self._next = 0

    def random(self) -> float:
        value = self._values[self._next % len(self._values)]
        self._next += 1
        return value


# --- 概率表驱动（docs/combat-formulas.md §1 TDD 表） -----------------------------


@pytest.mark.parametrize(
    ("accuracy", "agility", "expected"),
    [
        (10, 2, balance.MAX_HIT_RATE),  # P_raw = min(10,3)/3 = 100% → 截上限
        (5, 8, 5 / 9),  # P_raw = 5/9 ≈ 55.56%，不触截断
        (0, 2, balance.MIN_HIT_RATE),  # P_raw = 0% → 截下限（永不绝对 miss）
        (100, 0, balance.MAX_HIT_RATE),  # agility=0 → P_raw = 100% → 截上限
    ],
)
def test_hit_probability_table(accuracy: int, agility: int, expected: float) -> None:
    assert hit_probability(accuracy, agility) == pytest.approx(expected)


def test_hit_probability_always_clamped() -> None:
    """任意 (准确, 敏捷) 组合下概率都落在 [MIN_HIT_RATE, MAX_HIT_RATE]。"""
    for accuracy in range(0, 60, 7):
        for agility in range(0, 60, 7):
            probability = hit_probability(accuracy, agility)
            assert balance.MIN_HIT_RATE <= probability <= balance.MAX_HIT_RATE


@pytest.mark.parametrize(("accuracy", "agility"), [(-1, 2), (5, -2)])
def test_negative_stats_rejected(accuracy: int, agility: int) -> None:
    with pytest.raises(ValueError, match="非负"):
        hit_probability(accuracy, agility)


# --- 单次 roll：判定方向 + rng 注入 ----------------------------------------------


def test_hit_check_direction_uses_injected_rng() -> None:
    """rng.random() < P_hit 判 hit，否则 miss（ScriptedRandom 精确控制落点）。"""
    # (acc=5, agi=8) → P_hit = 5/9 ≈ 0.5556
    assert hit_check(ScriptedRandom(0.1), 5, 8) is True
    assert hit_check(ScriptedRandom(0.9), 5, 8) is False


def test_hit_check_is_strictly_less_than() -> None:
    """边界语义：roll 恰等于 P_hit 时判 miss（严格小于）。"""
    assert hit_check(ScriptedRandom(balance.MAX_HIT_RATE), 10, 2) is False


# --- 统计用例：固定 seed 10 万次 roll，偏差 < 2%（验收 #2） -----------------------


@pytest.mark.parametrize(
    ("accuracy", "agility", "expected"),
    [
        (5, 8, 5 / 9),  # 未截断区：2:1 比率式统计有效
        (10, 2, balance.MAX_HIT_RATE),  # 截断区：稳在 95%
    ],
)
def test_hit_rate_statistics_fixed_seed(
    accuracy: int, agility: int, expected: float
) -> None:
    rng = Random(42)
    hits = sum(hit_check(rng, accuracy, agility) for _ in range(N_ROLLS))
    empirical = hits / N_ROLLS
    assert abs(empirical - expected) < STAT_TOLERANCE, (
        f"empirical={empirical:.4f} expected={expected:.4f}"
    )


def test_same_seed_reproduces_sequence() -> None:
    """同 seed 序列逐位一致（可复现），不同 seed 几乎必然不同。"""
    rng_a = Random(42)
    seq_a = [hit_check(rng_a, 5, 8) for _ in range(200)]
    rng_b = Random(42)
    seq_b = [hit_check(rng_b, 5, 8) for _ in range(200)]
    rng_c = Random(43)
    seq_c = [hit_check(rng_c, 5, 8) for _ in range(200)]
    assert seq_a == seq_b
    assert seq_a != seq_c


# --- 截断的行为学 sanity：截断值永不绝对化 ---------------------------------------


def test_min_hit_rate_never_absolute_miss() -> None:
    """P 被钳到 MIN_HIT_RATE 后仍会命中（10 万次全 miss 概率 ≈ 0）。"""
    rng = Random(7)
    hits = sum(hit_check(rng, 0, 50) for _ in range(N_ROLLS))
    assert hits > 0


def test_max_hit_rate_never_absolute_hit() -> None:
    """P 被钳到 MAX_HIT_RATE 后仍会 miss（10 万次全 hit 概率 ≈ 0）。"""
    rng = Random(7)
    misses = sum(not hit_check(rng, 999, 0) for _ in range(N_ROLLS))
    assert misses > 0
