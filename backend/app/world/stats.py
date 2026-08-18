"""Pure stat computation: class growth base + equipment + premium bonus."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final

from app.content.models import GameClass, Item

__all__ = [
    "BONUS_ATTRS",
    "BonusError",
    "CharacterStats",
    "apply_item_stats",
    "class_base_stats",
    "parse_bonus",
]

#: Premium bonus attr names accepted in ``item_instance.bonus`` (schema 8.2:
#: ``[{"attr": "atk_max", "value": 2}, ...]``). The drop generator must emit
#: only these keys; anything else is a data error.
BONUS_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "atk_min",
        "atk_max",
        "mag_min",
        "mag_max",
        "dao_min",
        "dao_max",
        "def_min",
        "def_max",
        "mdef_min",
        "mdef_max",
        "hp",
        "mp",
    }
)


class BonusError(ValueError):
    """``item_instance.bonus`` JSONB does not match the ``[{"attr", "value"}]`` shape."""


@dataclass(frozen=True, slots=True)
class CharacterStats:
    """Aggregated combat stats: ``hp``/``mp`` are max values; combat stats are min-max."""

    hp: int
    mp: int
    atk_min: int = 0
    atk_max: int = 0
    mag_min: int = 0
    mag_max: int = 0
    dao_min: int = 0
    dao_max: int = 0
    def_min: int = 0
    def_max: int = 0
    mdef_min: int = 0
    mdef_max: int = 0
    accuracy: int = 0
    dodge: int = 0


def parse_bonus(raw: object) -> dict[str, int]:
    """Parse the ``item_instance.bonus`` JSONB payload into an attr -> total map.

    Duplicate attr entries are summed. Raises :class:`BonusError` on any
    malformed shape or unknown attr so bad rows fail loudly, not silently.
    """
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise BonusError(f"bonus must be a list of {{attr, value}} entries, got {type(raw).__name__}")
    totals: dict[str, int] = {}
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {"attr", "value"}:
            raise BonusError(f"malformed bonus entry: {entry!r}")
        attr, value = entry["attr"], entry["value"]
        if not isinstance(attr, str) or attr not in BONUS_ATTRS:
            raise BonusError(f"unknown bonus attr: {attr!r}")
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise BonusError(f"bonus value must be a positive int, got {value!r}")
        totals[attr] = totals.get(attr, 0) + value
    return totals


def class_base_stats(cls: GameClass, level: int) -> CharacterStats:
    """Bare stats for ``cls`` at ``level`` (no equipment), per the doc 4.2 growth table."""
    if not 1 <= level <= 40:
        raise ValueError(f"level out of range 1..40: {level}")
    levels = level - 1
    growth = cls.growth
    step = growth.main_stat_step
    main_gain = levels // step.every_n_levels
    gain_pair = (main_gain * step.min_step, main_gain * step.max_step)
    return CharacterStats(
        hp=cls.initial_hp + growth.hp_per_level * levels,
        mp=cls.initial_mp + growth.mp_per_level * levels,
        atk_min=cls.attack.min + (gain_pair[0] if growth.main_stat == "attack" else 0),
        atk_max=cls.attack.max + (gain_pair[1] if growth.main_stat == "attack" else 0),
        mag_min=cls.magic.min + (gain_pair[0] if growth.main_stat == "magic" else 0),
        mag_max=cls.magic.max + (gain_pair[1] if growth.main_stat == "magic" else 0),
        dao_min=cls.taoist.min + (gain_pair[0] if growth.main_stat == "taoist" else 0),
        dao_max=cls.taoist.max + (gain_pair[1] if growth.main_stat == "taoist" else 0),
        def_min=cls.defense,
        def_max=cls.defense,
        mdef_min=cls.magic_def,
        mdef_max=cls.magic_def,
        accuracy=cls.accuracy + levels // growth.accuracy_step.every_n_levels * growth.accuracy_step.step,
        dodge=cls.dodge + levels // growth.dodge_step.every_n_levels * growth.dodge_step.step,
    )


_ITEM_RANGES: Final[tuple[tuple[str, str, str], ...]] = (
    ("attack", "atk_min", "atk_max"),
    ("magic", "mag_min", "mag_max"),
    ("taoist", "dao_min", "dao_max"),
    ("defense", "def_min", "def_max"),
    ("magic_def", "mdef_min", "mdef_max"),
)


def apply_item_stats(
    stats: CharacterStats,
    item: Item,
    bonus: Mapping[str, int],
) -> CharacterStats:
    """Add one equipment item's fixed stats plus its premium bonus to ``stats``."""
    delta: dict[str, int] = {
        "hp": item.hp_bonus + bonus.get("hp", 0),
        "mp": item.mp_bonus + bonus.get("mp", 0),
    }
    for attr, value in bonus.items():
        if attr not in ("hp", "mp"):
            delta[attr] = value
    for field, lo_attr, hi_attr in _ITEM_RANGES:
        rng = getattr(item, field)
        if rng is not None:
            delta[lo_attr] = delta.get(lo_attr, 0) + rng.min
            delta[hi_attr] = delta.get(hi_attr, 0) + rng.max
    updates = {key: value for key, value in delta.items() if value}
    return replace(stats, **updates) if updates else stats
