"""W2T7 inventory / equipment / stat aggregation / durability tests (doc 4.6 + 4.8 + 8.2).

Fake clock: all time flows through explicit ``now_ms`` arguments, so durability
decay (10 min -> -1, N13) is testable without real time. Real game_data (items.json,
classes.json) pins data-driven behavior; synthetic items pin mechanics in isolation.

Acceptance coverage (plan task 19):
1. slot conflict rejected: equipping into an occupied 100..107 slot fails;
2. cross-class equipment rejected (class_req), plus level_req;
3. stat aggregation includes premium bonus (drop-side Bonus, doc 4.6 N12);
4. stack limits: potions/scrolls to 99, equipment always qty 1;
5. durability decay (10 min -1) and stat disable at 0;
6. stats reflect equip/unequip immediately (pure recompute, no stale cache);
7. pickup into bag merges into existing stacks first; full bag -> False (D27);
8. drink_hp / drink_mp consume one stack and return the items.json effect amount.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.content.loader import Content, load_all_content
from app.content.models import GameClass, Item, ItemEffect, StatRange
from app.world.drops import Bonus, Drop
from app.world.inventory import (
    BAG_SIZE,
    DURABILITY_TICK_MS,
    Inventory,
    InventoryError,
)
from app.world.stats import apply_item_stats, class_base_stats

DATA = Path(__file__).resolve().parents[1] / "game_data"

MIN_MS = 60_000


# ---------------------------------------------------------------------------
# Fixtures and synthetic items
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def content() -> Content:
    return load_all_content(DATA)


@pytest.fixture(scope="module")
def items(content) -> dict[str, Item]:
    return {i.code: i for i in content.items.items}


@pytest.fixture(scope="module")
def warrior(content) -> GameClass:
    return next(c for c in content.classes.classes if c.code == "warrior")


def _potion(code: str = "potion", hp: int | None = 50, mp: int | None = None) -> Item:
    return Item(
        code=code, name="药水", category="potion", class_req="all", level_req=1,
        stack_max=99, effect=ItemEffect(hp_heal=hp, mp_heal=mp),
    )


def _weapon(
    code: str = "sword", cls: object = "all", level: int = 1,
    atk: tuple[int, int] = (10, 20),
) -> Item:
    return Item(
        code=code, name="武器", category="weapon", slot="weapon", class_req=cls,
        level_req=level, attack=StatRange(min=atk[0], max=atk[1]), durability=50,
    )


def _helmet() -> Item:
    return Item(
        code="helm", name="头盔", category="helmet", slot="helmet",
        class_req="all", level_req=1, defense=StatRange(min=0, max=1),
    )


def _ring(code: str = "ring") -> Item:
    return Item(
        code=code, name="戒指", category="ring", slot="ring", class_req="all",
        level_req=1, attack=StatRange(min=0, max=5),
    )


def _bracelet(code: str = "bracelet") -> Item:
    return Item(
        code=code, name="手镯", category="bracelet", slot="bracelet",
        class_req="all", level_req=1, defense=StatRange(min=0, max=1),
    )


def _inv(*defs: Item, char: GameClass, level: int = 1) -> Inventory:
    inv = Inventory({i.code: i for i in defs}, char)
    inv.level = level
    return inv


def _drop(item_code: str, qty: int = 1, bonus: tuple[Bonus, ...] = ()) -> Drop:
    return Drop(
        id="drop-x", item_code=item_code, qty=qty, bonus=bonus,
        x=0, y=0, killer_id="k", created_ms=0,
    )


def _bag_slot_for(inv: Inventory, code: str) -> int:
    for slot, inst in inv.bag_contents().items():
        if inst.item_code == code:
            return slot
    msg = f"{code} not in bag"
    raise AssertionError(msg)


# ---------------------------------------------------------------------------
# Bag: 40 slots, stacking to 99 (N18), equipment qty 1
# ---------------------------------------------------------------------------


def test_bag_has_forty_slots(warrior) -> None:
    assert BAG_SIZE == 40
    defs = [_potion(f"pot_{i:02d}") for i in range(BAG_SIZE + 1)]
    inv = _inv(*defs, char=warrior)
    for d in defs[:-1]:
        assert inv.add(d.code, 1)
    assert inv.is_full
    assert len(inv.bag_contents()) == BAG_SIZE
    assert inv.add(defs[-1].code, 1) is False
    assert len(inv.bag_contents()) == BAG_SIZE


def test_stackable_merges_into_existing_stack_first(warrior) -> None:
    inv = _inv(_potion(), char=warrior)
    inv.add("potion", 30)
    inv.add("potion", 40)
    contents = list(inv.bag_contents().values())
    assert len(contents) == 1
    assert contents[0].quantity == 70


def test_stack_caps_at_99_then_new_slot(warrior) -> None:
    inv = _inv(_potion(), char=warrior)
    inv.add("potion", 99)
    inv.add("potion", 5)
    contents = sorted(inv.bag_contents().items())
    assert len(contents) == 2
    assert (contents[0][1].quantity, contents[1][1].quantity) == (99, 5)


def test_full_bag_with_full_stack_rejects_further_add(warrior) -> None:
    fillers = [_potion(f"p{i:02d}") for i in range(39)]
    inv = _inv(*fillers, _potion(code="bulk"), char=warrior)
    for f in fillers:
        inv.add(f.code, 1)
    assert inv.add("bulk", 99)
    assert inv.is_full
    assert inv.add("bulk", 1) is False


def test_equipment_always_qty_one_and_carries_durability(warrior) -> None:
    inv = _inv(_weapon(), _weapon(code="sword2"), char=warrior)
    inv.add("sword", 1)
    inv.add("sword2", 1)
    contents = list(inv.bag_contents().values())
    assert len(contents) == 2
    for inst in contents:
        assert inst.quantity == 1
        assert inst.durability == 50


def test_add_unknown_item_raises(warrior) -> None:
    inv = _inv(_potion(), char=warrior)
    with pytest.raises(InventoryError):
        inv.add("ghost_item", 1)


# ---------------------------------------------------------------------------
# Pickup from ground Drop (W2T6 seam)
# ---------------------------------------------------------------------------


def test_pickup_drop_potion_merges_into_stack(warrior) -> None:
    inv = _inv(_potion(), char=warrior)
    inv.add("potion", 97)
    assert inv.pickup(_drop("potion", 3))
    contents = list(inv.bag_contents().values())
    assert len(contents) == 2
    assert sorted(i.quantity for i in contents) == [1, 99]


def test_pickup_rare_equip_drop_carries_bonus_and_durability(warrior) -> None:
    bonus = (Bonus("attack", 2), Bonus("hp", 3))
    inv = _inv(_weapon(atk=(1, 2)), char=warrior)
    assert inv.pickup(_drop("sword", 1, bonus))
    inst = next(iter(inv.bag_contents().values()))
    assert inst.bonus == bonus
    assert inst.durability == 50
    assert inst.quantity == 1


def test_pickup_full_bag_returns_false(warrior) -> None:
    fillers = [_potion(f"p{i:02d}") for i in range(BAG_SIZE)]
    inv = _inv(*fillers, _potion(code="late"), char=warrior)
    for f in fillers:
        inv.add(f.code, 1)
    assert inv.is_full
    assert inv.pickup(_drop("late", 1)) is False
    assert len(inv.bag_contents()) == BAG_SIZE


# ---------------------------------------------------------------------------
# Equip / unequip: slot mapping (8.2), conflict, class/level requirements
# ---------------------------------------------------------------------------


def test_equip_moves_bag_item_into_equip_slot(warrior) -> None:
    inv = _inv(_weapon(atk=(10, 20)), char=warrior)
    inv.add("sword", 1)
    inst = inv.equip(0, 100)
    assert inv.bag_contents() == {}
    assert inv.equipped_contents() == {100: inst}


def test_equip_slot_conflict_rejected(warrior) -> None:
    inv = _inv(_weapon(), _weapon(code="sword2"), char=warrior)
    inv.add("sword", 1)
    inv.add("sword2", 1)
    inv.equip(0, 100)
    with pytest.raises(InventoryError):
        inv.equip(1, 100)
    assert inv.equipped_contents()[100].item_code == "sword"


def test_equip_wrong_category_slot_rejected(warrior) -> None:
    inv = _inv(_ring(), _weapon(), char=warrior)
    inv.add("ring", 1)
    inv.add("sword", 1)
    with pytest.raises(InventoryError):
        inv.equip(0, 103)  # ring into the necklace slot
    with pytest.raises(InventoryError):
        inv.equip(1, 101)  # weapon into the armor slot


def test_equip_two_bracelets_fill_both_bracelet_slots(warrior) -> None:
    inv = _inv(_bracelet(code="b1"), _bracelet(code="b2"), char=warrior)
    inv.add("b1", 1)
    inv.add("b2", 1)
    inv.equip(0, 104)
    inv.equip(1, 105)
    assert set(inv.equipped_contents()) == {104, 105}


def test_equip_rejects_consumable(warrior) -> None:
    inv = _inv(_potion(), char=warrior)
    inv.add("potion", 1)
    with pytest.raises(InventoryError):
        inv.equip(0, 100)


def test_equip_rejects_other_class(warrior) -> None:
    inv = _inv(_weapon(code="mage_rod", cls=["mage"]), char=warrior)
    inv.add("mage_rod", 1)
    with pytest.raises(InventoryError):
        inv.equip(0, 100)


def test_equip_rejects_insufficient_level(warrior) -> None:
    inv = _inv(_weapon(level=30), char=warrior, level=29)
    inv.add("sword", 1)
    with pytest.raises(InventoryError):
        inv.equip(0, 100)
    inv.level = 30
    inv.equip(0, 100)


def test_real_heavy_armor_class_list(items, warrior) -> None:
    # doc 4.7-B: heavy armor class_req is the list [warrior, taoist], not "all"
    inv = Inventory({"heavy_armor": items["heavy_armor"]}, warrior)
    inv.level = 16
    inv.add("heavy_armor", 1)
    inv.equip(0, 101)
    assert next(iter(inv.equipped_contents().values())).item_code == "heavy_armor"


def test_unequip_returns_item_to_free_bag_slot(warrior) -> None:
    inv = _inv(_weapon(atk=(10, 20)), char=warrior)
    base = class_base_stats(warrior, 1)
    inv.add("sword", 1)
    inv.equip(0, 100)
    assert inv.stats().atk_min == base.atk_min + 10
    inst = inv.unequip(100)
    assert inv.equipped_contents() == {}
    assert list(inv.bag_contents().values()) == [inst]
    assert inv.stats() == base


def test_unequip_full_bag_rejected(warrior) -> None:
    fillers = [_potion(f"p{i:02d}") for i in range(39)]
    inv = _inv(*fillers, _potion(code="late"), _weapon(), char=warrior)
    for f in fillers:
        inv.add(f.code, 1)
    inv.add("sword", 1)
    inv.equip(39, 100)
    inv.add("late", 1)  # fills the freed slot -> bag full
    assert inv.is_full
    with pytest.raises(InventoryError):
        inv.unequip(100)


# ---------------------------------------------------------------------------
# Stat aggregation: base + equipment + premium bonus (pure recompute)
# ---------------------------------------------------------------------------


def test_stats_bare_equal_class_base(warrior) -> None:
    inv = _inv(_potion(), char=warrior, level=35)
    assert inv.stats() == class_base_stats(warrior, 35)


def test_stats_include_equipment_immediately_after_equip(warrior) -> None:
    inv = _inv(_weapon(atk=(10, 20)), char=warrior)
    base = class_base_stats(warrior, 1)
    inv.add("sword", 1)
    inv.equip(0, 100)
    stats = inv.stats()
    assert (stats.atk_min, stats.atk_max) == (base.atk_min + 10, base.atk_max + 20)


def test_stats_include_premium_bonus(warrior) -> None:
    bonus = (Bonus("attack", 2), Bonus("hp", 3), Bonus("defense", 1))
    inv = _inv(_weapon(atk=(10, 20)), char=warrior)
    base = class_base_stats(warrior, 1)
    inv.add("sword", 1, bonus=bonus)
    inv.equip(0, 100)
    stats = inv.stats()
    # paired stats (attack/defense) add to both bounds; hp is a flat add
    assert (stats.atk_min, stats.atk_max) == (base.atk_min + 12, base.atk_max + 22)
    assert stats.hp == base.hp + 3
    assert (stats.def_min, stats.def_max) == (base.def_min + 1, base.def_max + 1)


def test_stats_recompute_after_swap(warrior) -> None:
    inv = _inv(_weapon(code="w1", atk=(1, 2)), _weapon(code="w2", atk=(5, 6)), char=warrior)
    base = class_base_stats(warrior, 1)
    inv.add("w1", 1)
    inv.add("w2", 1)
    inv.equip(0, 100)
    assert inv.stats().atk_min == base.atk_min + 1
    inv.unequip(100)
    assert inv.stats() == base
    inv.equip(1, 100)
    assert (inv.stats().atk_min, inv.stats().atk_max) == (base.atk_min + 5, base.atk_max + 6)


def test_stats_follow_level_change(warrior) -> None:
    inv = _inv(_potion(), char=warrior)
    first = inv.stats()
    inv.level = 35
    assert inv.stats() == class_base_stats(warrior, 35)
    assert inv.stats() != first


def test_stats_accumulate_over_real_equipment_set(items, warrior) -> None:
    # data-pinning: warrior endgame set from real items.json
    codes = [
        "judgement_rod", "heavenly_demon_armor", "holy_war_helmet",
        "holy_war_necklace", "holy_war_bracelet", "holy_war_ring",
    ]
    slots = [100, 101, 102, 103, 104, 106]
    inv = Inventory({c: items[c] for c in codes}, warrior)
    inv.level = 40
    expected = class_base_stats(warrior, 40)
    for code, slot in zip(codes, slots):
        assert inv.add(code, 1)
        inv.equip(_bag_slot_for(inv, code), slot)
        expected = apply_item_stats(expected, items[code], {})
    assert inv.stats() == expected


# ---------------------------------------------------------------------------
# Durability: 10 min -1 in combat (N13), 0 disables stats
# ---------------------------------------------------------------------------


def test_durability_first_tick_only_baselines(warrior) -> None:
    inv = _inv(_weapon(), char=warrior)
    inv.add("sword", 1)
    inv.equip(0, 100)
    assert inv.tick_durability(0) == 0
    assert inv.tick_durability(9 * MIN_MS) == 0


def test_durability_decays_one_per_ten_minutes(warrior) -> None:
    inv = _inv(_weapon(), char=warrior)
    inv.add("sword", 1)
    inv.equip(0, 100)
    inst = inv.slot_instance(100)
    assert inst is not None and inst.durability == 50
    now = 0
    inv.tick_durability(now)
    for _ in range(3):
        now += DURABILITY_TICK_MS
        assert inv.tick_durability(now) == 1
    assert inst.durability == 47
    # sub-10-minute tick: no loss
    assert inv.tick_durability(now + 5 * MIN_MS) == 0
    assert inst.durability == 47


def test_durability_catchup_after_skipped_ticks(warrior) -> None:
    inv = _inv(_weapon(), char=warrior)
    inv.add("sword", 1)
    inv.equip(0, 100)
    inv.tick_durability(0)
    # 35 minutes pass in one tick: floor(35/10) = 3 losses
    assert inv.tick_durability(35 * MIN_MS) == 3
    assert inv.slot_instance(100).durability == 47


def test_durability_zero_disables_stats_then_swap_recovers(warrior) -> None:
    inv = _inv(_weapon(code="w1", atk=(10, 20)), _weapon(code="w2", atk=(1, 1)), char=warrior)
    base = class_base_stats(warrior, 1)
    inv.add("w1", 1)
    inv.add("w2", 1)
    inv.equip(0, 100)
    assert inv.stats().atk_min == base.atk_min + 10
    now = 0
    inv.tick_durability(now)
    total_lost = 0
    while inv.slot_instance(100).durability > 0:
        now += DURABILITY_TICK_MS
        total_lost += inv.tick_durability(now)
    assert total_lost == 50
    assert inv.slot_instance(100).durability == 0
    # broken gear contributes nothing
    assert inv.stats() == base
    # swap in a fresh weapon: stats recover immediately
    inv.unequip(100)
    inv.equip(1, 100)
    assert (inv.stats().atk_min, inv.stats().atk_max) == (base.atk_min + 1, base.atk_max + 1)


def test_durability_ignores_zero_durability_gear_and_bag(warrior) -> None:
    # helmet has no durability (None); the potion stack in the bag is untouched
    inv = _inv(_potion(), _weapon(), _helmet(), char=warrior)
    inv.add("potion", 3)
    inv.add("sword", 1)
    inv.add("helm", 1)
    inv.equip(1, 100)
    inv.equip(2, 102)
    inv.tick_durability(0)
    inv.tick_durability(DURABILITY_TICK_MS)
    assert inv.slot_instance(100).durability == 49
    assert inv.slot_instance(102).durability is None
    assert inv.slot_instance(0).quantity == 3


# ---------------------------------------------------------------------------
# Drink: consume one stack, return the items.json effect amount
# ---------------------------------------------------------------------------


def test_drink_hp_returns_effect_and_consumes_one(warrior) -> None:
    inv = _inv(_potion(code="hp_pot", hp=100), char=warrior)
    inv.add("hp_pot", 5)
    assert inv.drink_hp("hp_pot") == 100
    assert inv.slot_instance(0).quantity == 4


def test_drink_last_one_removes_stack(warrior) -> None:
    inv = _inv(_potion(code="hp_pot", hp=100), char=warrior)
    inv.add("hp_pot", 1)
    assert inv.drink_hp("hp_pot") == 100
    assert inv.bag_contents() == {}


def test_drink_real_sun_water_effect(items, warrior) -> None:
    # data-pinning: sun_water = HP+150 / MP+50 (doc 4.8)
    inv = Inventory({"sun_water": items["sun_water"]}, warrior)
    inv.add("sun_water", 3)
    assert inv.drink_hp("sun_water") == 150
    assert inv.drink_mp("sun_water") == 50
    assert inv.slot_instance(0).quantity == 1


def test_drink_wrong_stat_raises(warrior) -> None:
    inv = _inv(_potion(code="mp_only", hp=None, mp=60), char=warrior)
    inv.add("mp_only", 2)
    with pytest.raises(InventoryError):
        inv.drink_hp("mp_only")
    assert inv.drink_mp("mp_only") == 60


def test_drink_absent_or_non_potion_raises(warrior) -> None:
    inv = _inv(_potion(), _weapon(), char=warrior)
    with pytest.raises(InventoryError):
        inv.drink_hp("potion")  # not in the bag yet
    inv.add("potion", 1)
    inv.add("sword", 1)
    with pytest.raises(InventoryError):
        inv.drink_hp("sword")
