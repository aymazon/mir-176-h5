"""Character bag + equipment + stat aggregation + durability (MVP-DESIGN 4.6 + 4.8 + 8.2).

Bag: 40 slots (0..39); potions/scrolls stack to 99 (N18), equipment is always
qty 1 in a free slot. Equipment slots are fixed (8.2): 100 weapon, 101 armor,
102 helmet, 103 necklace, 104/105 bracelets, 106/107 rings (doc 4.6: no belt /
boots / cloak slots). Wearing checks class/level requirements; stats are a pure
recompute on every query (class base + worn gear + premium bonus). Worn
weapon/armor loses 1 durability every 10 minutes of combat (N13, explicit
``now_ms`` ticks from the caller) and a zero-durability item grants no stats.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from app.content.models import SLOT_CODES, GameClass, Item
from app.world.drops import Bonus, Drop
from app.world.stats import CharacterStats, apply_item_stats, class_base_stats

__all__ = [
    "BAG_SIZE",
    "DURABILITY_TICK_MS",
    "Inventory",
    "InventoryError",
    "ItemInstance",
]

BAG_SIZE: Final[int] = 40  # doc 4.8 N18: 背包 40 格
DURABILITY_TICK_MS: Final[int] = 600_000  # doc 4.6 N13: 战斗每 10 分钟 -1

# doc 8.2 item_instance.slot mapping (slot set per doc 4.6: 手镯x2, 戒指x2)
SLOT_BY_CATEGORY: Final[dict[str, tuple[int, ...]]] = {
    "weapon": (100,),
    "armor": (101,),
    "helmet": (102,),
    "necklace": (103,),
    "bracelet": (104, 105),
    "ring": (106, 107),
}

#: drop-side Bonus stats (app.world.drops.BONUS_STATS) that add to BOTH bounds
#: of a paired stat; hp/mp are flat adds. Values are apply_item_stats keys.
_BONUS_PAIRS: Final[dict[str, tuple[str, str]]] = {
    "attack": ("atk_min", "atk_max"),
    "magic": ("mag_min", "mag_max"),
    "taoist": ("dao_min", "dao_max"),
    "defense": ("def_min", "def_max"),
    "magic_def": ("mdef_min", "mdef_max"),
}


class InventoryError(ValueError):
    """Invalid bag/equipment operation (unknown item, full bag, requirement not met)."""


@dataclass(slots=True)
class ItemInstance:
    """One bag/equip entry (8.2 item_instance minus the DB id).

    ``durability`` is None unless the item decays (weapon/armor, max 50);
    ``bonus`` carries the drop-side rare stats (doc 4.6 N12).
    """

    item_code: str
    quantity: int = 1
    durability: int | None = None
    bonus: tuple[Bonus, ...] = ()


def _bonus_map(bonus: tuple[Bonus, ...]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for entry in bonus:
        if entry.stat in ("hp", "mp"):
            totals[entry.stat] = totals.get(entry.stat, 0) + entry.value
            continue
        lo, hi = _BONUS_PAIRS[entry.stat]
        totals[lo] = totals.get(lo, 0) + entry.value
        totals[hi] = totals.get(hi, 0) + entry.value
    return totals


class Inventory:
    """One character's bag + equipment. All time via explicit ``now_ms`` args."""

    def __init__(self, items: Mapping[str, Item], char: GameClass) -> None:
        self._items = dict(items)
        self._char = char
        self._level = 1
        self._bag: dict[int, ItemInstance] = {}
        self._equipped: dict[int, ItemInstance] = {}
        self._durability_since: dict[int, int] = {}

    @property
    def char(self) -> GameClass:
        return self._char

    @property
    def level(self) -> int:
        return self._level

    @level.setter
    def level(self, value: int) -> None:
        if not 1 <= value <= 40:
            raise ValueError(f"level out of range 1..40: {value}")
        self._level = value

    @property
    def is_full(self) -> bool:
        return len(self._bag) >= BAG_SIZE

    def bag_contents(self) -> Mapping[int, ItemInstance]:
        return MappingProxyType(self._bag)

    def equipped_contents(self) -> Mapping[int, ItemInstance]:
        return MappingProxyType(self._equipped)

    def slot_instance(self, slot: int) -> ItemInstance | None:
        inst = self._bag.get(slot)
        if inst is None:
            inst = self._equipped.get(slot)
        return inst

    def add(self, item_code: str, qty: int, bonus: tuple[Bonus, ...] = ()) -> bool:
        """Put ``qty`` of ``item_code`` into the bag.

        Stackables merge into an existing stack first, then take free slots;
        equipment takes one free slot each (qty must be 1). Returns False only
        when the bag cannot hold the rest (D27: the caller stops picking up).
        """
        item = self._items.get(item_code)
        if item is None:
            raise InventoryError(f"unknown item {item_code!r}")
        if qty < 1:
            raise InventoryError(f"qty must be >= 1, got {qty}")
        if item.stack_max > 1:
            for inst in self._bag.values():
                if inst.item_code != item_code or inst.quantity >= item.stack_max:
                    continue
                moved = min(qty, item.stack_max - inst.quantity)
                inst.quantity += moved
                qty -= moved
            while qty > 0:
                slot = self._free_slot()
                if slot is None:
                    return False
                take = min(qty, item.stack_max)
                self._bag[slot] = ItemInstance(item_code, take)
                qty -= take
            return True
        if qty != 1:
            raise InventoryError(f"equipment {item_code!r} must enter with qty 1")
        if bonus and item.category not in SLOT_CODES:
            raise InventoryError(f"bonus only attaches to equipment, not {item_code!r}")
        slot = self._free_slot()
        if slot is None:
            return False
        self._bag[slot] = ItemInstance(item_code, 1, item.durability or None, tuple(bonus))
        return True

    def pickup(self, drop: Drop) -> bool:
        """Bag-insert a picked-up ground drop (W2T6 seam); False = bag cannot hold it."""
        return self.add(drop.item_code, drop.qty, drop.bonus)

    def equip(self, from_slot: int, to_slot: int) -> ItemInstance:
        """Move a bag item into an equipment slot, validating category, class, level."""
        inst = self._bag.get(from_slot)
        if inst is None:
            raise InventoryError(f"bag slot {from_slot} is empty")
        item = self._items[inst.item_code]
        if item.category not in SLOT_CODES:
            raise InventoryError(f"{item.code} is not equipment")
        if to_slot not in SLOT_BY_CATEGORY[item.category]:
            raise InventoryError(f"slot {to_slot} does not fit a {item.category}")
        if to_slot in self._equipped:
            raise InventoryError(f"equip slot {to_slot} is occupied")
        self._check_requirements(item)
        del self._bag[from_slot]
        self._equipped[to_slot] = inst
        self._durability_since.pop(to_slot, None)
        return inst

    def unequip(self, from_slot: int) -> ItemInstance:
        """Move a worn item back to a free bag slot."""
        inst = self._equipped.get(from_slot)
        if inst is None:
            raise InventoryError(f"equip slot {from_slot} is empty")
        slot = self._free_slot()
        if slot is None:
            raise InventoryError("bag is full, cannot unequip")
        del self._equipped[from_slot]
        self._durability_since.pop(from_slot, None)
        self._bag[slot] = inst
        return inst

    def tick_durability(self, now_ms: int) -> int:
        """Combat tick (N13): each worn decaying item loses 1 per 10 min of combat.

        The caller invokes this only while in combat. The first tick after an
        equip (or re-equip) only baselines the window; skipped ticks are
        settled as ``floor(elapsed / 10min)`` points, capped at remaining HP.
        Returns the points lost on this call.
        """
        lost = 0
        for slot, inst in list(self._equipped.items()):
            if inst.durability is None or inst.durability <= 0:
                continue
            since = self._durability_since.get(slot)
            if since is None:
                self._durability_since[slot] = now_ms
                continue
            points = (now_ms - since) // DURABILITY_TICK_MS
            if points < 1:
                continue
            points = min(points, inst.durability)
            inst.durability -= points
            self._durability_since[slot] = since + points * DURABILITY_TICK_MS
            lost += points
        return lost

    def stats(self) -> CharacterStats:
        """Pure recompute: class base (4.2) + worn gear + premium bonus.

        Zero-durability gear contributes nothing (doc 4.6). Never cached, so
        equip/unequip/level/durability changes show up on the next call.
        """
        total = class_base_stats(self._char, self._level)
        for slot in sorted(self._equipped):
            inst = self._equipped[slot]
            if inst.durability == 0:
                continue
            total = apply_item_stats(total, self._items[inst.item_code], _bonus_map(inst.bonus))
        return total

    def drink_hp(self, item_code: str) -> int:
        """Consume one potion from the bag; return the HP it restores (caller caps)."""
        return self._drink(item_code, "hp_heal")

    def drink_mp(self, item_code: str) -> int:
        """Consume one potion from the bag; return the MP it restores (caller caps)."""
        return self._drink(item_code, "mp_heal")

    def _check_requirements(self, item: Item) -> None:
        req = item.class_req
        if req != "all" and self._char.code not in req:
            raise InventoryError(f"{item.code} requires class {req}, character is {self._char.code}")
        if self._level < item.level_req:
            raise InventoryError(
                f"{item.code} requires level {item.level_req}, character is level {self._level}"
            )

    def _free_slot(self) -> int | None:
        occupied = set(self._bag)
        for slot in range(BAG_SIZE):
            if slot not in occupied:
                return slot
        return None

    def _drink(self, item_code: str, kind: Literal["hp_heal", "mp_heal"]) -> int:
        item = self._items.get(item_code)
        if item is None or item.category != "potion" or item.effect is None:
            raise InventoryError(f"{item_code!r} is not a drinkable potion")
        amount = getattr(item.effect, kind)
        if not amount:
            raise InventoryError(f"{item_code!r} restores no {kind}")
        for slot, inst in self._bag.items():
            if inst.item_code != item_code:
                continue
            inst.quantity -= 1
            if inst.quantity == 0:
                del self._bag[slot]
            return amount
        raise InventoryError(f"{item_code!r} is not in the bag")
