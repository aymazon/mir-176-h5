"""Combat formula + settlement pipeline tests (plan task 16, W2T4).

Covers docs/MVP/MVP-DESIGN.md 4.3 F1~F10 exactly, 6.4 kill credit, and 4.2 N3
natural regen. Statistical tests use a fixed seed and >= 10,000 samples so
they cannot flake; deterministic flow tests inject a scripted RNG stub that
fails loudly on any unexpected roll (e.g. a second gold roll).
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import override

import pytest

from app.content.loader import Content, load_all_content
from app.content.models import GameClass, MonsterDef, RegenFormula, Skill
from app.world.combat import (
    Mitigation,
    RegenGain,
    RegenTracker,
    attack_ready,
    exp_award,
    heal_amount,
    hit_chance,
    kill_credit,
    regen_per_second,
    roll_attack_damage,
    roll_damage,
    roll_gold,
    roll_hit,
    skill_available_at,
    skill_ready,
    skill_multiplier,
)
from app.world.settlement import (
    Attack,
    AttackResult,
    Attacker,
    KillRewards,
    Settlement,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "game_data"
SAMPLES = 10_000


@pytest.fixture(scope="session")
def content() -> Content:
    return load_all_content(DATA_DIR)


def _game_class(content: Content, code: str) -> GameClass:
    return next(c for c in content.classes.classes if c.code == code)


def _skill(content: Content, code: str) -> Skill:
    return next(s for s in content.skills.skills if s.code == code)


def _monster(**overrides: object) -> MonsterDef:
    base: dict[str, object] = {
        "code": "wolf",
        "name": "狼",
        "level": 2,
        "hp": 20,
        "attack_min": 1,
        "attack_max": 2,
        "defense": 0,
        "magic_def": 0,
        "exp": 30,
        "gold_min": 5,
        "gold_max": 10,
        "aggressive": False,
        "map_codes": ["m1"],
        "is_boss": False,
        "attack_ms": 1000,
        "move_ms": 450,
        "attack_range": 1,
        "undead": False,
    }
    base.update(overrides)
    return MonsterDef.model_validate(base)


_PLAYER_BASE = Attacker(id="p", kind="player")


def _player(id_: str, **overrides: object) -> Attacker:
    return replace(_PLAYER_BASE, id=id_, **overrides)


class _SeqRng(random.Random):
    """Scripted RNG: ``random()`` returns queued values (sticky: the last value
    repeats once the queue is empty); ``randint`` pops queued ints.

    Running out of queued randints raises StopIteration, so an unexpected
    extra roll (like a second gold roll on a settled kill) fails the test.
    """

    _randints: list[int]
    _randoms: list[float]
    _last_random: float

    def __init__(self, randints: Sequence[int], randoms: Sequence[float] = ()) -> None:
        super().__init__(0)
        self._randints = list(randints)
        self._randoms = list(randoms)
        self._last_random = 0.0  # guaranteed hit when no scripted value is queued
        self.calls: list[str] = []

    @override
    def random(self) -> float:
        self.calls.append("random")
        if self._randoms:
            self._last_random = self._randoms.pop(0)
        return self._last_random

    @override
    def randint(self, a: int, b: int) -> int:
        self.calls.append(f"randint({a},{b})")
        return self._randints.pop(0)


def _hit_rate(seed: int, accuracy: int, dodge: int) -> float:
    rng = random.Random(seed)
    hits = sum(roll_hit(rng, accuracy, dodge) for _ in range(SAMPLES))
    return hits / SAMPLES


# ---------------------------------------------------------------------------
# F1 hit rate
# ---------------------------------------------------------------------------
def test_hit_chance_midpoints() -> None:
    # 85% baseline; every (accuracy - dodge) point shifts 1%.
    assert hit_chance(0, 0) == pytest.approx(0.85)
    assert hit_chance(5, 0) == pytest.approx(0.90)
    assert hit_chance(0, 3) == pytest.approx(0.82)


def test_hit_chance_clamped_to_20_95_extremes() -> None:
    assert hit_chance(10, 0) == pytest.approx(0.95)  # exact upper bound
    assert hit_chance(1000, 0) == pytest.approx(0.95)  # clamped
    assert hit_chance(0, -1000) == pytest.approx(0.95)  # clamped
    assert hit_chance(-65, 0) == pytest.approx(0.20)  # exact lower bound
    assert hit_chance(0, 1000) == pytest.approx(0.20)  # clamped
    assert hit_chance(-1000, 0) == pytest.approx(0.20)  # clamped


def test_roll_hit_statistical_rates() -> None:
    assert _hit_rate(7, 0, 0) == pytest.approx(0.85, abs=0.03)
    assert _hit_rate(11, 0, 1000) == pytest.approx(0.20, abs=0.03)
    assert _hit_rate(13, 1000, 0) == pytest.approx(0.95, abs=0.03)


# ---------------------------------------------------------------------------
# F2/F4 raw roll, F3/F5 mitigation, F6 fire talisman
# ---------------------------------------------------------------------------
def test_roll_damage_covers_full_range() -> None:
    rng = random.Random(42)
    rolls = [roll_damage(rng, 1, 10, 1.0) for _ in range(SAMPLES)]
    assert min(rolls) == 1 and max(rolls) == 10
    assert set(rolls) == set(range(1, 11))


def test_roll_damage_multiplier_truncates() -> None:
    rng = random.Random(42)
    # rand(10,10) = 10: 10 * 1.15 = 11.5 -> 11 (truncated), 10 * 2.0 = 20.
    assert roll_damage(rng, 10, 10, 1.15) == 11
    assert roll_damage(rng, 10, 10, 2.0) == 20


def test_attack_damage_floor_one() -> None:
    rng = random.Random(99)
    for _ in range(SAMPLES):
        assert roll_attack_damage(rng, 1, 1, 1.0, Mitigation.flat(1000)) == 1


def test_attack_damage_mitigation_rolls_inside_range() -> None:
    rng = random.Random(5)
    rolls = [roll_attack_damage(rng, 10, 10, 1.0, Mitigation.flat(5)) for _ in range(SAMPLES)]
    assert min(rolls) == 5 and max(rolls) == 10  # 10 - rand(0,5)
    assert set(rolls) == set(range(5, 11))


def test_attack_damage_player_range_mitigation() -> None:
    rng = random.Random(6)
    rolls = [roll_attack_damage(rng, 10, 10, 1.0, Mitigation.of_range(3, 5)) for _ in range(SAMPLES)]
    assert min(rolls) == 5 and max(rolls) == 7  # 10 - rand(3,5)
    assert set(rolls) == {5, 6, 7}


def test_fire_talisman_damage_f6() -> None:
    rng = random.Random(43)
    # rand(5,5) = 5; 5 * 1.3 = 6.5 -> 6; magic_def flat 0 -> 6.
    assert roll_attack_damage(rng, 5, 5, 1.3, Mitigation.flat(0)) == 6
    # Against massive magic defense the floor of 1 always holds.
    for _ in range(SAMPLES):
        assert roll_attack_damage(rng, 5, 5, 1.3, Mitigation.flat(1000)) >= 1
        assert roll_attack_damage(rng, 1, 1, 1.3, Mitigation.flat(1000)) == 1


def test_fire_talisman_multiplier_comes_from_skills_data(content: Content) -> None:
    talisman = _skill(content, "soul_fire_talisman")
    assert talisman.formula_params["formula"] == "F6"
    assert talisman.formula_params["damage_multiplier"] == 1.3


# ---------------------------------------------------------------------------
# F7 skill rank
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("base", "rank", "expected"),
    [(1.0, 0, 1.0), (1.0, 1, 1.15), (1.0, 2, 1.3), (1.0, 3, 1.45), (1.3, 0, 1.3), (1.3, 3, 1.885)],
)
def test_skill_multiplier_per_rank(base: float, rank: int, expected: float) -> None:
    assert skill_multiplier(base, rank) == pytest.approx(expected)


@pytest.mark.parametrize("rank", [-1, 4])
def test_skill_multiplier_rejects_rank_out_of_range(rank: int) -> None:
    with pytest.raises(ValueError):
        _ = skill_multiplier(1.0, rank)


# ---------------------------------------------------------------------------
# F8 heal
# ---------------------------------------------------------------------------
def test_heal_amount_single_and_group() -> None:
    # 治愈术 = 15 + 道术上限 x 0.6; 群体治愈术 = 10 + 道术上限 x 0.4 (取整 = truncate).
    assert heal_amount(20, 15, 0.6) == 27
    assert heal_amount(32, 15, 0.6) == 34  # 34.2 -> 34
    assert heal_amount(20, 10, 0.4) == 18
    assert heal_amount(31, 10, 0.4) == 22  # 22.4 -> 22


def test_heal_params_come_from_skills_data(content: Content) -> None:
    heal = _skill(content, "heal")
    group = _skill(content, "group_heal")
    assert heal.formula_params["formula"] == "F8"
    assert heal.formula_params["heal_base"] == 15
    assert heal.formula_params["heal_per_taoist_max"] == 0.6
    assert group.formula_params["formula"] == "F8_group"
    assert group.formula_params["heal_base"] == 10
    assert group.formula_params["heal_per_taoist_max"] == 0.4


# ---------------------------------------------------------------------------
# F9 exp allocation / F10 gold
# ---------------------------------------------------------------------------
def test_exp_award_player_full_summon_half() -> None:
    assert exp_award(30, "player") == 30
    assert exp_award(30, "summon") == 15
    assert exp_award(25, "summon") == 12  # 12.5 -> 12 (truncated)
    assert exp_award(30, "monster") == 0  # no player-side credit


def test_roll_gold_stays_within_range() -> None:
    rng = random.Random(31)
    rolls = [roll_gold(rng, 5, 10) for _ in range(SAMPLES)]
    assert min(rolls) == 5 and max(rolls) == 10
    assert set(rolls) == set(range(5, 11))


# ---------------------------------------------------------------------------
# 6.4 kill credit
# ---------------------------------------------------------------------------
def test_kill_credit_highest_damage_wins() -> None:
    assert kill_credit({"a": 40, "b": 60}) == "b"
    assert kill_credit({"a": 60, "b": 40}) == "a"


def test_kill_credit_ties_go_to_earliest_attacker() -> None:
    assert kill_credit({"a": 50, "b": 50}) == "a"


def test_kill_credit_no_contribution_no_credit() -> None:
    assert kill_credit({}) is None
    assert kill_credit({"a": 0}) is None


# ---------------------------------------------------------------------------
# Settlement pipeline: hit -> damage -> death -> exp/gold, exactly once
# ---------------------------------------------------------------------------
def test_settlement_hit_damage_flow() -> None:
    # Given: an attacker with a fixed 10-10 physical roll and a 25 hp defender.
    settlement = Settlement(defender_id="m", hp=25, dodge=0)
    rng = _SeqRng(randints=[10, 0])
    # When: one guaranteed-hit attack resolves.
    result = settlement.attack(rng, _player("p1", attack_min=10, attack_max=10), Attack(kind="physical"))
    # Then: 10 damage lands, hp drops to 15, nobody died yet.
    assert result == AttackResult(hit=True, damage=10, defender_hp=15, killed=False)
    assert settlement.alive and settlement.rewards is None
    assert rng.calls == ["random", "randint(10,10)", "randint(0,0)"]


def test_settlement_kill_awards_exp_and_gold_once() -> None:
    # Given: a 20 hp monster worth 30 exp / 5-10 gold; attack rolls exactly 20.
    settlement = Settlement(defender_id="m", hp=20, monster=_monster(hp=20, exp=30))
    rng = _SeqRng(randints=[20, 0, 7])  # damage, mitigation, gold
    # When: the killing blow lands, then extra attacks / settle calls follow.
    result = settlement.attack(rng, _player("p1", attack_min=20, attack_max=20), Attack(kind="physical"))
    second = settlement.attack(rng, _player("p1", attack_min=20, attack_max=20), Attack(kind="physical"))
    again = settlement.settle_kill(rng)
    # Then: rewards resolve exactly once with the data-driven exp/gold.
    assert result.killed and result.defender_hp == 0
    assert result.rewards == KillRewards(credited_id="p1", exp=30, gold=7, summon_owner_id=None)
    assert second == AttackResult(hit=False, damage=0, defender_hp=0, killed=False)
    assert settlement.rewards is result.rewards  # same object, never re-rolled
    assert again is result.rewards
    assert rng.calls == ["random", "randint(20,20)", "randint(0,0)", "randint(5,10)"]


def test_settlement_overkill_clamps_hp_at_zero() -> None:
    settlement = Settlement(defender_id="m", hp=10, monster=_monster(hp=10))
    rng = _SeqRng(randints=[25, 0, 7])
    result = settlement.attack(rng, _player("p1", attack_min=25, attack_max=25), Attack(kind="physical"))
    assert result.killed and result.damage == 25 and result.defender_hp == 0
    assert result.rewards is not None and result.rewards.exp == 30


def test_settlement_two_player_contest_credit() -> None:
    # Given: a 100 hp monster; p1 lands 4x10, p2 lands 6x10 (60 > 40).
    settlement = Settlement(defender_id="m", hp=100, monster=_monster(hp=100, exp=30))
    rng = _SeqRng(randints=[10, 0] * 10 + [9])  # 10 attacks (damage+mitigation) + gold
    for _ in range(4):
        _ = settlement.attack(rng, _player("p1", attack_min=10, attack_max=10), Attack(kind="physical"))
    for _ in range(6):
        _ = settlement.attack(rng, _player("p2", attack_min=10, attack_max=10), Attack(kind="physical"))
    # When/Then: the highest damage contributor (p2, 60) owns the kill.
    assert dict(settlement.contributions) == {"p1": 40, "p2": 60}
    assert settlement.rewards is not None
    assert settlement.rewards.credited_id == "p2"
    assert settlement.rewards.exp == 30 and settlement.rewards.gold == 9
    assert not settlement.alive


def test_settlement_summon_kill_halves_exp_and_credits_owner() -> None:
    # Given: the killing blow is dealt by a summon owned by p1 (F9: 50% exp, drops to owner).
    settlement = Settlement(defender_id="m", hp=20, monster=_monster(hp=20, exp=30))
    rng = _SeqRng(randints=[20, 0, 8])
    summon = Attacker(id="skel_1", kind="summon", owner_id="p1", attack_min=20, attack_max=20)
    result = settlement.attack(rng, summon, Attack(kind="physical"))
    # Then: credit stays on the summon, exp halved, owner routed the gold.
    assert result.rewards == KillRewards(credited_id="skel_1", exp=15, gold=8, summon_owner_id="p1")


def test_settlement_miss_leaves_hp_and_ledger_untouched() -> None:
    # Given: rng.random() = 1.0 means every hit roll misses (dodge 1000 keeps chance at 20%).
    settlement = Settlement(defender_id="m", hp=20, monster=_monster(hp=20), dodge=1000)
    rng = _SeqRng(randints=[], randoms=[1.0])
    for _ in range(3):
        result = settlement.attack(rng, _player("p1", attack_min=5, attack_max=5), Attack(kind="physical"))
        assert result == AttackResult(hit=False, damage=0, defender_hp=20, killed=False)
    assert dict(settlement.contributions) == {}
    assert kill_credit(settlement.contributions) is None
    assert rng.calls == ["random"] * 3  # no damage roll ever happened


def test_settlement_player_defender_death_has_no_rewards() -> None:
    # Given: a monster hitting a player (no MonsterDef) - death only stops the fight.
    settlement = Settlement(defender_id="p1", hp=20)
    rng = _SeqRng(randints=[20, 0])
    monster = Attacker(id="m1", kind="monster", attack_min=20, attack_max=20)
    result = settlement.attack(rng, monster, Attack(kind="physical"))
    # Then: the kill is reported but no exp/gold resolution exists (DEAD is task 20's).
    assert result.killed and result.rewards is None
    assert settlement.settle_kill(rng) is None


def test_settlement_applies_f7_rank_multiplier() -> None:
    # Given: a rank-3 physical skill (base 1.0 -> 1.45) with a fixed 10 roll.
    settlement = Settlement(defender_id="m", hp=100)
    rng = _SeqRng(randints=[10, 0])
    # When: the rank-3 attack resolves.
    result = settlement.attack(
        rng, _player("p1", attack_min=10, attack_max=10), Attack(kind="physical", skill_rank=3)
    )
    # Then: 10 * 1.45 = 14.5 -> 14 damage (F7 then F2/F3 truncation).
    assert result.damage == 14
    assert result.defender_hp == 86


# ---------------------------------------------------------------------------
# Attack cadence / skill timing
# ---------------------------------------------------------------------------
def test_attack_ready_boundaries() -> None:
    assert not attack_ready(799, 0, 800)
    assert attack_ready(800, 0, 800)
    assert attack_ready(801, 0, 800)
    assert not attack_ready(999, 0, 1000)
    assert attack_ready(1000, 0, 1000)


def test_player_attack_cadence_comes_from_classes_data(content: Content) -> None:
    # doc 4.2: player normal attack is base_attack_ms; classes.json carries 800.
    warrior = _game_class(content, "warrior")
    assert warrior.base_attack_ms == 800
    assert not attack_ready(799, 0, warrior.base_attack_ms)
    assert attack_ready(800, 0, warrior.base_attack_ms)


def test_monster_attack_interval_comes_from_monsters_data(content: Content) -> None:
    # doc 5.3/N20: attack interval 800..1200 ms per monster; chicken carries 1000.
    chicken = next(m for m in content.monsters.monsters if m.code == "chicken")
    assert chicken.attack_ms == 1000
    assert all(800 <= m.attack_ms <= 1200 for m in content.monsters.monsters)
    assert not attack_ready(999, 0, chicken.attack_ms)
    assert attack_ready(1000, 0, chicken.attack_ms)


def test_skill_available_at_cast_then_cooldown() -> None:
    assert skill_available_at(5000, 1000, 2000) == 8000
    assert not skill_ready(7999, 5000, 1000, 2000)
    assert skill_ready(8000, 5000, 1000, 2000)


# ---------------------------------------------------------------------------
# 4.2 N3 natural regen
# ---------------------------------------------------------------------------
def test_regen_formulas_come_from_classes_data(content: Content) -> None:
    warrior, mage, taoist = (_game_class(content, c) for c in ("warrior", "mage", "taoist"))
    assert (warrior.regen.hp_per_second.base, warrior.regen.hp_per_second.level_divisor) == (1, 8)
    assert (warrior.regen.mp_per_second.base, warrior.regen.mp_per_second.level_divisor) == (1, 12)
    assert (mage.regen.hp_per_second.base, mage.regen.hp_per_second.level_divisor) == (1, 15)
    assert (mage.regen.mp_per_second.base, mage.regen.mp_per_second.level_divisor) == (1, 8)
    assert (taoist.regen.hp_per_second.base, taoist.regen.hp_per_second.level_divisor) == (1, 10)
    assert (taoist.regen.mp_per_second.base, taoist.regen.mp_per_second.level_divisor) == (1, 10)


def test_regen_per_second_formula() -> None:
    formula = RegenFormula(base=1, level_divisor=8)
    assert regen_per_second(formula, 8) == 2.0  # 1 + 8/8
    assert regen_per_second(formula, 7) == 1.875  # 1 + 7/8
    assert regen_per_second(RegenFormula(base=1, level_divisor=15), 15) == 2.0


def test_regen_tracker_carries_fractional_remainders() -> None:
    # Given: warrior level 7 hp regen = 1.875/s -> 0.1875 per 100 ms tick.
    tracker = RegenTracker(RegenFormula(base=1, level_divisor=8), RegenFormula(base=1, level_divisor=12))
    gains = [tracker.tick(level=7, tick_ms=100) for _ in range(8)]
    # Then: 8 ticks accumulate exactly 1.5 hp -> 1 whole point, 0.5 carried.
    assert sum(g.hp for g in gains) == 1
    # The carried half combines with the next 8 ticks (1.5) -> 2 more whole points.
    more = [tracker.tick(level=7, tick_ms=100) for _ in range(8)]
    assert sum(g.hp for g in more) == 2
    assert sum(g.hp for g in [*gains, *more]) == 3


def test_regen_tracker_mp_formula_is_independent() -> None:
    # Given: warrior level 12 -> hp 2.5/s, mp 2.0/s; ten 100 ms ticks = 1 second.
    tracker = RegenTracker(RegenFormula(base=1, level_divisor=8), RegenFormula(base=1, level_divisor=12))
    gain = [tracker.tick(level=12, tick_ms=100) for _ in range(10)]
    # Then: 2.5 -> 2 whole hp (0.5 carried) and 2.0 -> 2 whole mp.
    assert sum(g.hp for g in gain) == 2
    assert sum(g.mp for g in gain) == 2
    assert gain[0] == RegenGain(hp=0, mp=0)


def test_regen_tracker_fake_clock_varying_tick_ms() -> None:
    # Given: two ticks of 400 + 600 ms == exactly one second at 1.875/s.
    tracker = RegenTracker(RegenFormula(base=1, level_divisor=8), RegenFormula(base=1, level_divisor=12))
    first = tracker.tick(level=7, tick_ms=400)  # 0.75 -> 0
    second = tracker.tick(level=7, tick_ms=600)  # 0.75 + 1.125 = 1.875 -> 1
    # Then: exactly 1.875 accumulated -> 1 whole point, 0.875 carried.
    assert first.hp == 0 and second.hp == 1


def test_regression_no_float_slop_in_regen() -> None:
    # Guard the carry-over arithmetic against float drift across 10,000 ticks.
    tracker = RegenTracker(RegenFormula(base=1, level_divisor=8), RegenFormula(base=1, level_divisor=12))
    total = sum(tracker.tick(level=7, tick_ms=100).hp for _ in range(SAMPLES))
    expected = math.floor(1.875 * SAMPLES * 100 / 1000)
    assert total == expected
