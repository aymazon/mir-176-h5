"""Ground drops on monster death (MVP-DESIGN 5.5 + 3.3 + 4.6).

Every monster death is guaranteed gold (F10, via :func:`app.world.combat.roll_gold`)
plus a number of independent drop-pool rolls: normal monsters roll their level-band
pool twice, each boss rolls its own table three times. Each roll is a weighted
single-select over the pool's entries with an implicit "none" bucket of weight
``100 - sum(weights)``, so an item's marginal per-roll probability equals its table
weight (doc 5.5 note: 实现时用权重表). Dropped equipment has a 1% chance to be a
rare item (doc 4.6 N12) carrying 1-3 random bonus stats.

Ground ownership follows doc 3.3: the killer holds a 60s pickup-protection window
(afterward anyone may pick up) and each drop vanishes after 120s. All time flows
through explicit ``*_ms`` arguments and all randomness through an injected
:class:`random.Random`, so the manager is deterministic under a fixed seed and a
fake clock. Bag insertion of a picked-up drop is a later task (W2T7/W2T8); this
module owns only the ground side (spawning, ownership, expiry, pickup eligibility).
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from app.content.models import SLOT_CODES, DropEntry, DropsFile, Item, MonsterDef
from app.world.combat import roll_gold
from app.world.monster_ai import Monster

ROLLS_NORMAL: int = 2  # doc 5.5: 普通怪独立掷 2 次掉落池
ROLLS_BOSS: int = 3  # doc 5.5: 每只 BOSS 每次死亡独立掷 3 次
RARE_CHANCE: float = 0.01  # doc 4.6 N12: 1% 极品
PROTECT_MS: int = 60_000  # doc 3.3: 击杀者 60s 拾取保护
DISAPPEAR_MS: int = 120_000  # doc 3.3: 掉落物 120s 后消失

BonusStat = Literal["attack", "magic", "taoist", "defense", "magic_def", "hp", "mp"]
# doc 4.6 N12: rare bonus stat set = 攻/魔/道/防/魔御/HP/MP
BONUS_STATS: tuple[BonusStat, ...] = ("attack", "magic", "taoist", "defense", "magic_def", "hp", "mp")


@dataclass(frozen=True, slots=True)
class Bonus:
    stat: BonusStat
    value: int  # +1..+3


@dataclass(frozen=True, slots=True)
class Drop:
    id: str
    item_code: str
    qty: int  # 1 for equipment; BOSS potion stacks carry 3
    bonus: tuple[Bonus, ...]  # empty unless a rare equipment roll
    x: int
    y: int
    killer_id: str
    created_ms: int


class DropManager:
    """Spawns and owns ground drops; deterministic under an injected rng and explicit ms clock."""

    def __init__(
        self,
        drops: DropsFile,
        items: Mapping[str, Item],
        monsters: Mapping[str, MonsterDef],
    ) -> None:
        self._items: dict[str, Item] = dict(items)
        self._monsters: dict[str, MonsterDef] = dict(monsters)
        self._band_by_level: dict[int, tuple[DropEntry, ...]] = {}
        self._boss_pools: dict[str, tuple[DropEntry, ...]] = {}
        self._drops: dict[str, Drop] = {}
        self._id_counter: int = 0
        for band in drops.normal_bands:
            self._check_entries(band.entries)
            for level in range(band.level_lo, band.level_hi + 1):
                self._band_by_level[level] = tuple(band.entries)
        for pool in drops.boss_tables:
            boss = self._monsters.get(pool.boss)
            if boss is None or not boss.is_boss:
                msg = f"boss drop table references unknown or non-boss monster {pool.boss}"
                raise ValueError(msg)
            self._check_entries(pool.entries)
            self._boss_pools[pool.boss] = tuple(pool.entries)
        boss_codes = {m.code for m in self._monsters.values() if m.is_boss}
        if boss_codes != set(self._boss_pools):
            msg = f"boss drop tables {sorted(self._boss_pools)} must match boss monsters {sorted(boss_codes)}"
            raise ValueError(msg)

    def on_kill(
        self,
        monster: Monster,
        killer_id: str,
        now_ms: int,
        rng: random.Random,
    ) -> tuple[int, list[Drop]]:
        """F10 gold plus one kill's worth of ground drops, all at the monster's cell."""
        defn = monster.defn
        gold = roll_gold(rng, defn.gold_min, defn.gold_max)
        if defn.is_boss:
            entries, rolls = self._boss_pools[defn.code], ROLLS_BOSS
        else:
            entries, rolls = self._band_by_level[defn.level], ROLLS_NORMAL
        drops: list[Drop] = []
        for _ in range(rolls):
            entry = self._roll_pool(entries, rng)
            if entry is None:
                continue
            drops.append(self._make_drop(entry, monster.x, monster.y, killer_id, now_ms, rng))
        return gold, drops

    def tick(self, now_ms: int) -> list[Drop]:
        """Remove drops past their 120s lifespan; return the ones that vanished."""
        expired = [d for d in self._drops.values() if now_ms - d.created_ms >= DISAPPEAR_MS]
        for d in expired:
            del self._drops[d.id]
        return expired

    def pickup(self, player_id: str, drop: Drop, now_ms: int) -> bool:
        """True and the drop leaves the ground only if ``player_id`` may take it now."""
        active = self._drops.get(drop.id)
        if active is None:
            return False
        age = now_ms - active.created_ms
        if age >= DISAPPEAR_MS:
            return False
        if age < PROTECT_MS and player_id != active.killer_id:
            return False
        del self._drops[drop.id]
        return True

    def _make_drop(
        self,
        entry: DropEntry,
        x: int,
        y: int,
        killer_id: str,
        now_ms: int,
        rng: random.Random,
    ) -> Drop:
        item = self._items[entry.item]
        drop = Drop(
            id=f"drop-{self._id_counter:06d}",
            item_code=entry.item,
            qty=entry.qty,
            bonus=self._roll_bonus(item, rng),
            x=x,
            y=y,
            killer_id=killer_id,
            created_ms=now_ms,
        )
        self._id_counter += 1
        self._drops[drop.id] = drop
        return drop

    @staticmethod
    def _roll_pool(
        entries: tuple[DropEntry, ...], rng: random.Random
    ) -> DropEntry | None:
        r = rng.random() * 100.0
        acc = 0.0
        for entry in entries:
            acc += entry.weight
            if r < acc:
                return entry
        return None

    @staticmethod
    def _roll_bonus(item: Item, rng: random.Random) -> tuple[Bonus, ...]:
        if item.category not in SLOT_CODES:
            return ()
        if rng.random() >= RARE_CHANCE:
            return ()
        count = rng.randint(1, 3)  # 1~3 bonus stats
        return tuple(Bonus(rng.choice(BONUS_STATS), rng.randint(1, 3)) for _ in range(count))

    def _check_entries(self, entries: Sequence[DropEntry]) -> None:
        for entry in entries:
            item = self._items.get(entry.item)
            if item is None:
                msg = f"drop table references unknown item {entry.item}"
                raise ValueError(msg)
            if item.category in SLOT_CODES and entry.qty != 1:
                msg = f"equipment {entry.item} must drop qty 1, got {entry.qty}"
                raise ValueError(msg)
