"""Server-authoritative combat formulas (MVP-DESIGN 4.2/4.3/6.4).

All randomness flows through an injected :class:`random.Random` and all time
through explicit ``*_ms`` arguments, so the module is deterministic under a
fixed seed and a fake clock. The stateful fight ledger (damage
contributions, exactly-once death rewards) lives in
:mod:`app.world.settlement`, which imports from here; the auto-battle state
machine (task 20) owns entity and cooldown state and calls into both.

Doc 4.3 map: F1 hit rate = clamp(85% + (准确-躲避)x1%, 20%, 95%); F2/F4 D =
rand(下限,上限) x 技能倍率; F3/F5 damage = max(1, D - mitigation roll); F6 is
the same shape on the taoist stat vs magic defense (its x1.3 lives in
skills.json); F7 multiplier x (1 + 0.15 x rank), rank 0..3; F8 heal = base +
道术上限 x per_taoist_max (取整); F9 killer 100% exp / summon kill 50% to its
owner; F10 gold rolled once on death, straight to the credited killer's bag.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from app.content.models import RegenFormula

# ---------------------------------------------------------------------------
# Doc 4.3 constants
# ---------------------------------------------------------------------------
HIT_BASE: Final[float] = 0.85  # F1
HIT_PERCENT_PER_DIFF: Final[float] = 0.01  # F1: 1% per (accuracy - dodge)
HIT_MIN: Final[float] = 0.20  # F1
HIT_MAX: Final[float] = 0.95  # F1
DAMAGE_FLOOR: Final[int] = 1  # F3/F5/F6
SKILL_RANK_STEP: Final[float] = 0.15  # F7
SKILL_RANK_MAX: Final[int] = 3  # F7
SUMMON_KILL_EXP_FACTOR: Final[float] = 0.5  # F9

AttackKind = Literal["physical", "magic", "fire_talisman"]
AttackerKind = Literal["player", "summon", "monster"]


# ---------------------------------------------------------------------------
# F1 hit rate
# ---------------------------------------------------------------------------
def hit_chance(accuracy: int, dodge: int) -> float:
    """F1: hit rate = clamp(85% + (accuracy - dodge) x 1%, 20%, 95%)."""
    raw = HIT_BASE + (accuracy - dodge) * HIT_PERCENT_PER_DIFF
    return min(HIT_MAX, max(HIT_MIN, raw))


def roll_hit(rng: random.Random, accuracy: int, dodge: int) -> bool:
    """F1 roll: whether one attack lands."""
    return rng.random() < hit_chance(accuracy, dodge)


# ---------------------------------------------------------------------------
# F2/F4 raw roll, F3/F5/F6 settlement
# ---------------------------------------------------------------------------
def roll_damage(rng: random.Random, stat_min: int, stat_max: int, multiplier: float) -> int:
    """F2/F4: D = rand(下限, 上限) x 技能倍率, truncated to an integer."""
    return int(rng.randint(stat_min, stat_max) * multiplier)


@dataclass(frozen=True, slots=True)
class Mitigation:
    """Defender mitigation range; the F3/F5/F6 roll is uniform inside it.

    Flat stats (monsters, doc 4.3: ``rand(0, 防御)``) use :meth:`flat`; the
    min-max stats of players (doc 4.6: 防御 下限-上限) use :meth:`of_range`.
    """

    lo: int
    hi: int

    @classmethod
    def flat(cls, value: int) -> Mitigation:
        """Doc-literal mitigation for flat stats: roll ``rand(0, value)``."""
        return cls(0, value)

    @classmethod
    def of_range(cls, lo: int, hi: int) -> Mitigation:
        """Min-max mitigation (player equipment): roll ``rand(lo, hi)``."""
        return cls(lo, hi)

    def roll(self, rng: random.Random) -> int:
        return rng.randint(self.lo, self.hi)


def roll_attack_damage(
    rng: random.Random,
    stat_min: int,
    stat_max: int,
    multiplier: float,
    mitigation: Mitigation,
) -> int:
    """F2+F3 / F4+F5 / F6: roll the stat, apply the multiplier, subtract a mitigation roll, floor at 1."""
    return max(DAMAGE_FLOOR, roll_damage(rng, stat_min, stat_max, multiplier) - mitigation.roll(rng))


# ---------------------------------------------------------------------------
# F7 skill rank
# ---------------------------------------------------------------------------
def skill_multiplier(base_multiplier: float, skill_rank: int) -> float:
    """F7: effective multiplier = base x (1 + 0.15 x skill rank), rank 0..3."""
    if not 0 <= skill_rank <= SKILL_RANK_MAX:
        msg = f"skill rank must be 0..{SKILL_RANK_MAX}, got {skill_rank}"
        raise ValueError(msg)
    return base_multiplier * (1.0 + SKILL_RANK_STEP * skill_rank)


# ---------------------------------------------------------------------------
# F8 heal
# ---------------------------------------------------------------------------
def heal_amount(taoist_max: int, heal_base: int, per_taoist_max: float) -> int:
    """F8: heal = base + 道术上限 x per_taoist_max, truncated (取整).

    治愈术 = 15 + 道术上限 x 0.6; 群体治愈术 = 10 + 道术上限 x 0.4; the
    numbers live in skills.json (heal_base / heal_per_taoist_max).
    """
    return int(heal_base + taoist_max * per_taoist_max)


# ---------------------------------------------------------------------------
# F9 exp allocation / F10 gold
# ---------------------------------------------------------------------------
def exp_award(monster_exp: int, killer_kind: AttackerKind) -> int:
    """F9: a player kill earns 100% exp; a summon kill credits its owner at 50%."""
    match killer_kind:
        case "player":
            return monster_exp
        case "summon":
            return int(monster_exp * SUMMON_KILL_EXP_FACTOR)
        case "monster":
            return 0


def roll_gold(rng: random.Random, gold_min: int, gold_max: int) -> int:
    """F10: gold rolled once on death; goes straight to the credited killer's bag."""
    return rng.randint(gold_min, gold_max)


# ---------------------------------------------------------------------------
# 6.4 kill credit
# ---------------------------------------------------------------------------
def kill_credit(contributions: Mapping[str, int]) -> str | None:
    """The highest-damage contributor owns the kill; ties go to the earliest attacker."""
    credited: str | None = None
    best = 0
    for attacker_id, damage in contributions.items():
        if damage > best:
            credited, best = attacker_id, damage
    return credited


# ---------------------------------------------------------------------------
# Attack cadence / skill timing
# ---------------------------------------------------------------------------
def attack_ready(now_ms: int, last_attack_ms: int, interval_ms: int) -> bool:
    """True once ``interval_ms`` elapsed since the last attack (data-driven intervals: classes.json base_attack_ms / monsters.json attack_ms)."""
    return now_ms - last_attack_ms >= interval_ms


def skill_available_at(start_ms: int, cast_ms: int, cooldown_ms: int) -> int:
    """Earliest reuse time for a skill started at ``start_ms``: cast completes, then cooldown."""
    return start_ms + cast_ms + cooldown_ms


def skill_ready(now_ms: int, start_ms: int, cast_ms: int, cooldown_ms: int) -> bool:
    """True when a skill started at ``start_ms`` has finished casting and cooling down."""
    return now_ms >= skill_available_at(start_ms, cast_ms, cooldown_ms)


# ---------------------------------------------------------------------------
# 4.2 N3 natural regen
# ---------------------------------------------------------------------------
def regen_per_second(formula: RegenFormula, level: int) -> float:
    """N3: natural regen per second = base + level / level_divisor."""
    return formula.base + level / formula.level_divisor


@dataclass(frozen=True, slots=True)
class RegenGain:
    """Whole hp/mp points gained in one tick (fractions stay in the tracker)."""

    hp: int
    mp: int


@dataclass
class RegenTracker:
    """Applies N3 regen per tick, carrying fractional remainders so slow regen is never lost.

    ``tick`` is the fake-clock seam: the loop passes its tick length and the
    caller adds the whole points, capped at max hp/mp.
    """

    hp_formula: RegenFormula
    mp_formula: RegenFormula
    _hp_carry: float = field(default=0.0, repr=False)
    _mp_carry: float = field(default=0.0, repr=False)

    def tick(self, level: int, tick_ms: int) -> RegenGain:
        """Regen gained over one ``tick_ms`` tick at ``level`` (doc 4.2 N3)."""
        hp = self._hp_carry + regen_per_second(self.hp_formula, level) * tick_ms / 1000.0
        mp = self._mp_carry + regen_per_second(self.mp_formula, level) * tick_ms / 1000.0
        hp_gain, mp_gain = int(hp), int(mp)
        self._hp_carry = hp - hp_gain
        self._mp_carry = mp - mp_gain
        return RegenGain(hp=hp_gain, mp=mp_gain)
