"""W2T6 ground drops on monster death tests (plan task 19, doc 5.5 + 3.3 + 4.6).

Fake clock + injected rng: all time flows through explicit ``now_ms`` and all
randomness through an injected :class:`random.Random`, so the manager is fully
deterministic. A scripted RNG (like test_combat's) fails loudly on an unexpected
extra roll.

Acceptance coverage:
1. on_kill always returns gold (F10) plus 0..2 item drops for a normal monster and
   0..3 for a boss, each registered on the ground at the monster's cell;
2. normal monsters roll only their level-band pool; each boss rolls only its table;
3. the weighted "none" bucket makes per-item marginal probability == table weight;
4. equipment has a 1% rare chance carrying 1-3 bonus stats (doc 4.6 N12);
5. the killer holds a 60s pickup-protection window; afterward anyone may pick; 120s expiry;
6. constructor validation: boss table/monster mismatch, unknown drop item, equipment qty!=1.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import override

import pytest

from app.content.models import (
    BossPool,
    DropEntry,
    DropsFile,
    Item,
    ItemEffect,
    LevelBandPool,
    MonsterDef,
)
from app.world.drops import BONUS_STATS, DropManager
from app.world.monster_ai import Monster

# The level band covers the whole 1..40 range so any test monster level resolves.
_BAND_ENTRIES = (DropEntry(item="potion", weight=40), DropEntry(item="sword", weight=20))


class _SeqRng(random.Random):
    """Scripted RNG: ``random()`` pops queued floats (sticky when empty);
    ``randint()`` pops queued ints, raising on an unexpected extra roll."""

    _randints: list[int]
    _randoms: list[float]
    _last: float

    def __init__(self, randints: Sequence[int] = (), randoms: Sequence[float] = ()) -> None:
        super().__init__(0)
        self._randints = list(randints)
        self._randoms = list(randoms)
        self._last = 0.0

    @override
    def random(self) -> float:
        if self._randoms:
            self._last = self._randoms.pop(0)
        return self._last

    @override
    def randint(self, a: int, b: int) -> int:
        if not self._randints:
            msg = f"unexpected extra randint({a},{b}) roll"
            raise AssertionError(msg)
        return self._randints.pop(0)


def _potion() -> Item:
    return Item(
        code="potion", name="金疮药", category="potion", class_req="all",
        level_req=1, stack_max=99, effect=ItemEffect(hp_heal=50),
    )


def _sword() -> Item:
    return Item(
        code="sword", name="铁剑", category="weapon", slot="weapon",
        class_req="all", level_req=1, durability=50,
    )


def _robe() -> Item:
    return Item(
        code="robe", name="布袍", category="armor", slot="armor",
        class_req="all", level_req=1, durability=50,
    )


def _defn(**overrides: object) -> MonsterDef:
    data: dict[str, object] = {
        "code": "zombie",
        "name": "僵尸",
        "level": 3,
        "hp": 100,
        "attack_min": 5,
        "attack_max": 8,
        "defense": 2,
        "magic_def": 1,
        "exp": 10,
        "gold_min": 1,
        "gold_max": 10,
        "aggressive": False,
        "map_codes": ["m1"],
        "is_boss": False,
        "attack_ms": 1000,
        "move_ms": 500,
        "attack_range": 1,
        "undead": False,
    }
    data.update(overrides)
    return MonsterDef.model_validate(data)


def _zombie() -> Monster:
    return Monster(id="mon-z", defn=_defn(), map_id="m1", x=7, y=9, spawn_x=7, spawn_y=9, hp=100)


def _boss() -> Monster:
    return Monster(
        id="mon-b",
        defn=_defn(code="boss1", name="头目", level=10, hp=500, is_boss=True, gold_min=10, gold_max=50),
        map_id="m1", x=7, y=9, spawn_x=7, spawn_y=9, hp=500,
    )


def _drops_file() -> DropsFile:
    # Band pool is potion+sword; the boss table is a single 100% robe entry, so a boss
    # kill is guaranteed to drop exactly 3 robes (no "none" bucket) - a crisp discriminator.
    return DropsFile(
        normal_bands=[LevelBandPool(level_lo=1, level_hi=40, entries=list(_BAND_ENTRIES))],
        boss_tables=[BossPool(boss="boss1", entries=[DropEntry(item="robe", weight=100)])],
    )


def _manager() -> DropManager:
    zombie = _defn()
    boss = _defn(code="boss1", name="头目", level=10, hp=500, is_boss=True, gold_min=10, gold_max=50)
    items = {i.code: i for i in (_potion(), _sword(), _robe())}
    monsters = {zombie.code: zombie, boss.code: boss}
    return DropManager(_drops_file(), items, monsters)


# ---------------------------------------------------------------------------
# on_kill: gold (F10) + ground drops at the monster's cell
# ---------------------------------------------------------------------------


def test_boss_kill_produces_three_ground_drops() -> None:
    mgr = _manager()
    gold, drops = mgr.on_kill(_boss(), "killer", now_ms=0, rng=random.Random(42))
    assert 10 <= gold <= 50
    assert len(drops) == 3
    for d in drops:
        assert d.item_code == "robe"
        assert d.qty == 1
        assert (d.x, d.y) == (7, 9)
        assert d.killer_id == "killer"
        assert d.created_ms == 0


def test_on_kill_gold_and_drops_registered() -> None:
    mgr = _manager()
    # Force both pool rolls to a potion (r=10 lands in the weight-40 potion bucket).
    rng = _SeqRng(randints=[5], randoms=[0.1])
    gold, drops = mgr.on_kill(_zombie(), "killer", now_ms=123, rng=rng)
    assert gold == 5
    assert len(drops) == 2
    for d in drops:
        assert d.item_code == "potion"
        assert d.qty == 1
        assert (d.x, d.y) == (7, 9)
        assert d.created_ms == 123
    # Both drops are on the ground: the killer can pick one up inside the protection window.
    assert mgr.pickup("killer", drops[0], now_ms=123) is True


def test_normal_kill_uses_band_pool_only() -> None:
    mgr = _manager()
    seen: set[str] = set()
    for seed in range(200):
        gold, drops = mgr.on_kill(_zombie(), "killer", 0, random.Random(seed))
        assert 1 <= gold <= 10
        assert len(drops) <= 2
        for d in drops:
            # robe exists only in the boss table, so a normal kill must never yield it.
            assert d.item_code in {"potion", "sword"}
            seen.add(d.item_code)
    assert {"potion", "sword"} <= seen


# ---------------------------------------------------------------------------
# Weighted "none" bucket: per-item marginal probability == table weight
# ---------------------------------------------------------------------------


def test_roll_pool_none_bucket_probability() -> None:
    rng = random.Random(7)
    counts = {"potion": 0, "sword": 0, "none": 0}
    for _ in range(10_000):
        entry = DropManager._roll_pool(_BAND_ENTRIES, rng)
        counts["none" if entry is None else entry.item] += 1
    total = 10_000
    assert abs(counts["potion"] / total - 0.40) < 0.03
    assert abs(counts["sword"] / total - 0.20) < 0.03
    assert abs(counts["none"] / total - 0.40) < 0.03


# ---------------------------------------------------------------------------
# 1% rare equipment bonus (doc 4.6 N12)
# ---------------------------------------------------------------------------


def test_roll_bonus_consumable_never_rare() -> None:
    assert DropManager._roll_bonus(_potion(), random.Random(1)) == ()


def test_roll_bonus_equip_not_rare_by_default() -> None:
    # random()=0.5 >= 0.01 -> not a rare roll.
    assert DropManager._roll_bonus(_sword(), _SeqRng(randoms=[0.5])) == ()


def test_roll_bonus_rare_carries_three_stats() -> None:
    # random()=0.005 < 0.01 -> rare; count randint=3; the 3 stat values are 2,2,2.
    bonus = DropManager._roll_bonus(_sword(), _SeqRng(randints=[3, 2, 2, 2], randoms=[0.005]))
    assert len(bonus) == 3
    for b in bonus:
        assert b.stat in BONUS_STATS
        assert b.value == 2


# ---------------------------------------------------------------------------
# Pickup protection window + 120s expiry (doc 3.3)
# ---------------------------------------------------------------------------


def test_pickup_protection_window() -> None:
    mgr = _manager()
    _, drops = mgr.on_kill(_boss(), "killer", now_ms=0, rng=random.Random(1))
    d = drops[0]
    # Inside the 60s window only the killer may pick up.
    assert mgr.pickup("other", d, now_ms=10_000) is False
    # The failed attempt did not remove it; the killer succeeds.
    assert mgr.pickup("killer", d, now_ms=20_000) is True


def test_pickup_after_protection_anyone() -> None:
    mgr = _manager()
    _, drops = mgr.on_kill(_boss(), "killer", now_ms=0, rng=random.Random(1))
    assert mgr.pickup("other", drops[0], now_ms=61_000) is True


def test_pickup_after_expiry() -> None:
    mgr = _manager()
    _, drops = mgr.on_kill(_boss(), "killer", now_ms=0, rng=random.Random(1))
    # Past the 120s lifespan no one may pick, even the killer.
    assert mgr.pickup("killer", drops[0], now_ms=125_000) is False


def test_tick_expires_after_120s() -> None:
    mgr = _manager()
    _, drops = mgr.on_kill(_boss(), "killer", now_ms=0, rng=random.Random(1))
    assert len(drops) == 3
    assert mgr.tick(now_ms=119_999) == []
    expired = mgr.tick(now_ms=120_000)
    assert len(expired) == 3
    # Ground is empty now: the vanished drops are no longer pickable.
    assert mgr.pickup("killer", drops[0], now_ms=120_000) is False


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


def _items(*codes: str) -> dict[str, Item]:
    by_code = {"potion": _potion(), "sword": _sword(), "robe": _robe()}
    return {c: by_code[c] for c in codes}


def _file(band_entries: list[DropEntry], boss_pools: list[BossPool]) -> DropsFile:
    return DropsFile(
        normal_bands=[LevelBandPool(level_lo=1, level_hi=40, entries=band_entries)],
        boss_tables=boss_pools,
    )


def test_manager_rejects_boss_table_for_unknown_monster() -> None:
    drops = _file([DropEntry(item="potion", weight=10)], [BossPool(boss="ghost", entries=[DropEntry(item="potion", weight=10)])])
    with pytest.raises(ValueError, match="unknown or non-boss"):
        DropManager(drops, _items("potion"), {_defn().code: _defn()})


def test_manager_rejects_boss_table_for_non_boss_monster() -> None:
    zombie = _defn()  # is_boss defaults False
    drops = _file([DropEntry(item="potion", weight=10)], [BossPool(boss="zombie", entries=[DropEntry(item="potion", weight=10)])])
    with pytest.raises(ValueError, match="unknown or non-boss"):
        DropManager(drops, _items("potion"), {zombie.code: zombie})


def test_manager_rejects_boss_without_table() -> None:
    boss = _defn(code="boss1", name="头目", level=10, hp=500, is_boss=True)
    drops = _file([DropEntry(item="potion", weight=10)], [])
    with pytest.raises(ValueError, match="must match boss monsters"):
        DropManager(drops, _items("potion"), {boss.code: boss})


def test_manager_rejects_unknown_drop_item() -> None:
    drops = _file([DropEntry(item="ghost_item", weight=10)], [])
    with pytest.raises(ValueError, match="unknown item"):
        DropManager(drops, _items("potion"), {_defn().code: _defn()})


def test_manager_rejects_equipment_drop_qty_not_one() -> None:
    drops = _file([DropEntry(item="sword", weight=10, qty=2)], [])
    with pytest.raises(ValueError, match="must drop qty 1"):
        DropManager(drops, _items("potion", "sword"), {_defn().code: _defn()})
