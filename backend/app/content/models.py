"""Canonical pydantic v2 schemas for the core numeric tables.

Covers the four data-only tables under ``backend/game_data/``:

- ``exp_curve.json`` (doc 4.1)        -> :class:`ExpCurve`
- ``classes.json``   (doc 4.2)        -> :class:`ClassesFile`
- ``skills.json``    (doc 4.5)        -> :class:`SkillsFile`
- ``items.json``     (doc 4.7 + 4.8)  -> :class:`ItemsFile`
- ``maps.json``      (doc 5.1)        -> :class:`MapsFile`
- ``monsters.json``  (doc 5.3)        -> :class:`MonstersFile`
- ``spawns.json``    (doc 5.4)        -> :class:`SpawnsFile`
- ``map_connections.json`` (doc 5.2)  -> :class:`MapConnectionsFile`

W2T1's ``app/content/loader.py`` reuses these models to validate the full
def-table set. Keep this module dependency-free (pydantic only).

Schema notes (doc ambiguities resolved, see .omo notepad):
- ``class_req`` is either the string ``"all"`` or a list of class codes
  (needed because doc 4.7-B 重盔甲 is 战/道).
- Passive skills (doc 4.5) have ``mp_cost``/``cast_ms``/``cooldown_ms`` = null.
- Regen is stored losslessly as ``base + level / level_divisor`` per second.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------
ClassCode = Literal["warrior", "mage", "taoist"]
CLASS_CODES: tuple[str, ...] = ("warrior", "mage", "taoist")

SlotCode = Literal["weapon", "armor", "helmet", "necklace", "bracelet", "ring"]
SLOT_CODES: tuple[str, ...] = (
    "weapon", "armor", "helmet", "necklace", "bracelet", "ring",
)

SkillType = Literal[
    "passive", "single", "line", "fan", "aoe_3x3", "self_aoe",
    "dash", "buff", "summon", "dot", "control", "heal",
]

ItemCategory = Literal[
    "weapon", "armor", "helmet", "necklace", "bracelet", "ring", "potion", "scroll",
]

CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Base map code, e.g. ``m1``.
MAP_CODE_RE = re.compile(r"^m\d+$$")
# World-instance code: base maps use their bare code, multi-layer maps use
# ``{code}-{layer}`` (doc 5.1). Spawn/portal references may use either form.
INSTANCE_CODE_RE = re.compile(r"^m\d+(-\d+)?$")
LEVEL_BAND_RE = re.compile(r"^(\d+)(?:-(\d+)|\+)$")


def _check_code(kind: str, value: str) -> str:
    if not CODE_RE.match(value):
        msg = f"{kind} code must be snake_case english, got {value!r}"
        raise ValueError(msg)
    return value


class StatRange(BaseModel):
    """A ``下限-上限`` stat (doc 4.6: all combat stats are min-max)."""

    min: int = Field(ge=0)
    max: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_order(self) -> StatRange:
        if self.min > self.max:
            msg = f"stat min {self.min} exceeds max {self.max}"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# exp_curve.json (doc 4.1)
# ---------------------------------------------------------------------------
class ExpEntry(BaseModel):
    """EXP needed to go from ``level`` to ``level + 1``. Level 40 is max: no entry."""

    level: int = Field(ge=1, le=39)
    exp: int = Field(ge=1)


class ExpCurve(BaseModel):
    levels: list[ExpEntry]

    @model_validator(mode="after")
    def _check_complete(self) -> ExpCurve:
        if [e.level for e in self.levels] != list(range(1, 40)):
            msg = "exp_curve must contain exactly levels 1..39 in order"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# classes.json (doc 4.2)
# ---------------------------------------------------------------------------
class RegenFormula(BaseModel):
    """Natural regen per second = ``base + level / level_divisor`` (doc 4.2)."""

    base: int = Field(ge=0)
    level_divisor: int = Field(ge=1)


class ClassRegen(BaseModel):
    hp_per_second: RegenFormula
    mp_per_second: RegenFormula


class MainStatStep(BaseModel):
    """攻/魔/道 growth: every N levels both bounds +step (doc 4.2: every 2, +1)."""

    every_n_levels: int = Field(ge=1)
    min_step: int = Field(ge=0)
    max_step: int = Field(ge=0)


class CountStep(BaseModel):
    """Accuracy/dodge growth: every N levels +step (doc 4.2: 4 / 5)."""

    every_n_levels: int = Field(ge=1)
    step: int = Field(ge=0)


class ClassGrowth(BaseModel):
    hp_per_level: int = Field(ge=0)
    mp_per_level: int = Field(ge=0)
    main_stat: Literal["attack", "magic", "taoist"]
    main_stat_step: MainStatStep
    accuracy_step: CountStep
    dodge_step: CountStep


class Level40Reference(BaseModel):
    """Doc 4.2 sanity reference at level 40 (bare, no equipment)."""

    hp: int = Field(ge=1)
    mp: int = Field(ge=0)
    attack: StatRange | None = None
    magic: StatRange | None = None
    taoist: StatRange | None = None
    accuracy: int = Field(ge=0)
    dodge: int = Field(ge=0)


class GameClass(BaseModel):
    code: ClassCode
    name: str  # Chinese display name
    initial_hp: int = Field(ge=1)
    initial_mp: int = Field(ge=0)
    attack: StatRange
    magic: StatRange
    taoist: StatRange
    defense: int = Field(ge=0)
    magic_def: int = Field(ge=0)
    accuracy: int = Field(ge=0)
    dodge: int = Field(ge=0)
    growth: ClassGrowth
    regen: ClassRegen
    base_attack_ms: int = Field(ge=1)
    base_move_ms: int = Field(ge=1)
    level_40_reference: Level40Reference


class ClassesFile(BaseModel):
    classes: list[GameClass]

    @model_validator(mode="after")
    def _check_complete(self) -> ClassesFile:
        codes = [c.code for c in self.classes]
        if len(codes) != len(set(codes)) or set(codes) != set(CLASS_CODES):
            msg = "classes.json must contain exactly warrior/mage/taoist"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# skills.json (doc 4.5)
# ---------------------------------------------------------------------------
class Skill(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    code: str
    name: str  # Chinese display name (doc original wording)
    game_class: ClassCode = Field(alias="class")
    learn_level: int = Field(ge=1, le=40)
    mp_cost: int | None = Field(default=None, ge=0)
    cast_ms: int | None = Field(default=None, ge=1)
    cooldown_ms: int | None = Field(default=None, ge=0)
    type: SkillType
    formula_params: dict[str, Any] = Field(default_factory=dict)
    passive_bonus: dict[str, int] | None = None
    # doc 4.5-D: poison (施毒术) is material-free — MP only, no talisman item.
    material_free: bool = False

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        return _check_code("skill", v)

    @model_validator(mode="after")
    def _check_passive_rules(self) -> Skill:
        if self.type == "passive":
            if (
                self.mp_cost is not None
                or self.cast_ms is not None
                or self.cooldown_ms is not None
                or not self.passive_bonus
            ):
                msg = f"passive skill {self.code} needs null mp/cast/cooldown and passive_bonus"
                raise ValueError(msg)
        else:
            if (
                self.mp_cost is None
                or self.cast_ms is None
                or self.cooldown_ms is None
                or self.passive_bonus is not None
            ):
                msg = f"active skill {self.code} needs mp_cost/cast_ms/cooldown_ms and no passive_bonus"
                raise ValueError(msg)
        return self


class SkillsFile(BaseModel):
    skills: list[Skill]

    @model_validator(mode="after")
    def _check_unique(self) -> SkillsFile:
        codes = [s.code for s in self.skills]
        if len(codes) != len(set(codes)):
            msg = "duplicate skill codes"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# items.json (doc 4.7 + 4.8)
# ---------------------------------------------------------------------------
class ItemEffect(BaseModel):
    """Consumable effect (doc 4.8)."""

    hp_heal: int | None = Field(default=None, ge=0)
    mp_heal: int | None = Field(default=None, ge=0)
    teleport: Literal["return", "random_teleport"] | None = None


# "all" = every class; otherwise a non-empty list of class codes (doc 4.7-B
# 重盔甲 is 战/道, so a single string value is not expressive enough).
ClassReq = Literal["all"] | list[ClassCode]


class Item(BaseModel):
    code: str
    name: str  # Chinese display name (doc original wording)
    category: ItemCategory
    slot: SlotCode | None = None  # equipment only; consumables have no slot
    class_req: ClassReq
    level_req: int = Field(ge=1, le=40)
    attack: StatRange | None = None
    magic: StatRange | None = None
    taoist: StatRange | None = None
    defense: StatRange | None = None
    magic_def: StatRange | None = None
    hp_bonus: int = Field(default=0, ge=0)
    mp_bonus: int = Field(default=0, ge=0)
    # doc 4.6: weapons/armor durability 50; everything else 0 (none).
    durability: int = Field(default=0, ge=0)
    # doc 4.8: potions/scrolls stack to 99; equipment stack 1.
    stack_max: int = Field(default=1, ge=1)
    buy_price: int | None = Field(default=None, ge=0)  # null = not sold (—)
    sell_price: int | None = Field(default=None, ge=0)
    effect: ItemEffect | None = None

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        return _check_code("item", v)

    @field_validator("class_req")
    @classmethod
    def _validate_class_req(cls, v: ClassReq) -> ClassReq:
        if isinstance(v, list) and (not v or len(v) != len(set(v))):
            msg = f"class_req list must be non-empty and unique, got {v!r}"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _check_category_rules(self) -> Item:
        if self.category in SLOT_CODES:  # equipment
            if self.slot != self.category:
                msg = f"{self.code}: slot must equal category for equipment"
                raise ValueError(msg)
            if self.stack_max != 1:
                msg = f"{self.code}: equipment stack_max must be 1"
                raise ValueError(msg)
            expected_durability = 50 if self.category in ("weapon", "armor") else 0
            if self.durability != expected_durability:
                msg = f"{self.code}: {self.category} durability must be {expected_durability}"
                raise ValueError(msg)
            if self.effect is not None:
                msg = f"{self.code}: equipment must not have an effect"
                raise ValueError(msg)
            if any(s is not None for s in (self.attack, self.magic, self.taoist)):
                pass  # valid: necklaces/bracelets/rings/helmets/armor can grant attack/magic/taoist
        else:  # potion / scroll
            if self.slot is not None:
                msg = f"{self.code}: consumables have no slot"
                raise ValueError(msg)
            if self.durability != 0 or self.stack_max != 99:
                msg = f"{self.code}: consumables need durability 0 and stack_max 99"
                raise ValueError(msg)
            if self.attack or self.magic or self.taoist or self.defense or self.magic_def:
                msg = f"{self.code}: consumables grant no stats"
                raise ValueError(msg)
            assert self.effect is not None, f"{self.code}: consumable needs an effect"
            if self.category == "potion":
                if self.effect.teleport is not None or not (
                    self.effect.hp_heal or self.effect.mp_heal
                ):
                    msg = f"{self.code}: potion effect needs hp_heal and/or mp_heal"
                    raise ValueError(msg)
            else:  # scroll
                if self.effect.teleport is None or self.effect.hp_heal or self.effect.mp_heal:
                    msg = f"{self.code}: scroll effect must be a teleport"
                    raise ValueError(msg)
        return self


class ItemsFile(BaseModel):
    items: list[Item]

    @model_validator(mode="after")
    def _check_unique(self) -> ItemsFile:
        codes = [i.code for i in self.items]
        if len(codes) != len(set(codes)):
            msg = "duplicate item codes"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# maps.json (doc 5.1)
# ---------------------------------------------------------------------------
class MapDef(BaseModel):
    """One base map (doc 5.1); multi-layer maps expand to ``{code}-{layer}`` instances."""

    code: str
    name: str  # Chinese display name
    width: int = Field(ge=2)
    height: int = Field(ge=2)
    layers: int = Field(ge=1)
    level_band: str
    safe_zone: tuple[int, int, int, int] | None = None
    safe_all: bool = False
    note: str = ""

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        if not MAP_CODE_RE.match(v):
            msg = f"map code must look like m1..m13, got {v!r}"
            raise ValueError(msg)
        return v

    @field_validator("level_band")
    @classmethod
    def _validate_level_band(cls, v: str) -> str:
        match = LEVEL_BAND_RE.match(v)
        if not match:
            msg = f"level_band must look like '1-4' or '22+', got {v!r}"
            raise ValueError(msg)
        lo = int(match.group(1))
        hi = int(match.group(2)) if match.group(2) else 40
        if not (1 <= lo <= hi <= 40):
            msg = f"level_band out of 1..40: {v!r}"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _check_safe_zone(self) -> MapDef:
        if self.safe_all and self.safe_zone is not None:
            msg = f"{self.code}: safe_all and safe_zone are mutually exclusive"
            raise ValueError(msg)
        zone = self.safe_zone
        if zone is not None:
            x0, y0, x1, y1 = zone
            if not (0 <= x0 < x1 < self.width and 0 <= y0 < y1 < self.height):
                msg = f"{self.code}: safe_zone {zone} not inside the {self.width}x{self.height} grid"
                raise ValueError(msg)
        return self


class MapsFile(BaseModel):
    maps: list[MapDef]

    @model_validator(mode="after")
    def _check_unique(self) -> MapsFile:
        codes = [m.code for m in self.maps]
        if len(codes) != len(set(codes)):
            msg = "duplicate map codes"
            raise ValueError(msg)
        return self

    def instance_codes(self) -> tuple[str, ...]:
        """All world instances: bare code for single-layer maps, ``{code}-{i}`` per layer otherwise."""
        codes: list[str] = []
        for m in self.maps:
            if m.layers == 1:
                codes.append(m.code)
            else:
                codes.extend(f"{m.code}-{i}" for i in range(1, m.layers + 1))
        return tuple(codes)


# ---------------------------------------------------------------------------
# monsters.json (doc 5.3)
# ---------------------------------------------------------------------------
class MonsterDef(BaseModel):
    code: str
    name: str  # Chinese display name
    level: int = Field(ge=1, le=40)
    hp: int = Field(ge=1)
    attack_min: int = Field(ge=1)
    attack_max: int = Field(ge=1)
    defense: int = Field(ge=0)
    magic_def: int = Field(ge=0)
    exp: int = Field(ge=1)
    gold_min: int = Field(ge=0)
    gold_max: int = Field(ge=0)
    aggressive: bool = False
    map_codes: list[str]
    is_boss: bool = False
    # doc 5.3/N20: attack interval 800..1200 ms, default 1000 (per-monster override).
    attack_ms: int = Field(ge=800, le=1200)
    move_ms: int = Field(ge=1)
    attack_range: int = Field(ge=1)
    undead: bool = False

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        return _check_code("monster", v)

    @field_validator("map_codes")
    @classmethod
    def _validate_map_codes(cls, v: list[str]) -> list[str]:
        if not v or len(v) != len(set(v)):
            msg = f"map_codes must be a non-empty unique list, got {v!r}"
            raise ValueError(msg)
        for code in v:
            if not INSTANCE_CODE_RE.match(code):
                msg = f"map code must look like m1 or m4-2, got {code!r}"
                raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _check_orders(self) -> MonsterDef:
        if self.attack_min > self.attack_max:
            msg = f"{self.code}: attack_min exceeds attack_max"
            raise ValueError(msg)
        if self.gold_min > self.gold_max:
            msg = f"{self.code}: gold_min exceeds gold_max"
            raise ValueError(msg)
        return self


class MonstersFile(BaseModel):
    monsters: list[MonsterDef]

    @model_validator(mode="after")
    def _check_unique(self) -> MonstersFile:
        codes = [m.code for m in self.monsters]
        if len(codes) != len(set(codes)):
            msg = "duplicate monster codes"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# spawns.json (doc 5.4)
# ---------------------------------------------------------------------------
class SpawnDef(BaseModel):
    map_code: str
    monster_code: str
    max_alive: int = Field(ge=1)
    respawn_s: int = Field(ge=1)

    @field_validator("map_code")
    @classmethod
    def _validate_map_code(cls, v: str) -> str:
        if not INSTANCE_CODE_RE.match(v):
            msg = f"map code must look like m1 or m4-2, got {v!r}"
            raise ValueError(msg)
        return v

    @field_validator("monster_code")
    @classmethod
    def _validate_monster_code(cls, v: str) -> str:
        return _check_code("monster", v)


class SpawnsFile(BaseModel):
    spawns: list[SpawnDef]

    @model_validator(mode="after")
    def _check_unique(self) -> SpawnsFile:
        pairs = [(s.map_code, s.monster_code) for s in self.spawns]
        if len(pairs) != len(set(pairs)):
            msg = "duplicate (map_code, monster_code) spawn entries"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# map_connections.json (doc 5.2)
# ---------------------------------------------------------------------------
class ConnectionDef(BaseModel):
    """One directed walk-through: stepping on (from_map, from_x, from_y) lands on (to_map, to_x, to_y)."""

    from_map: str
    from_x: int = Field(ge=0)
    from_y: int = Field(ge=0)
    to_map: str
    to_x: int = Field(ge=0)
    to_y: int = Field(ge=0)
    note: str = ""

    @field_validator("from_map", "to_map")
    @classmethod
    def _validate_map_code(cls, v: str) -> str:
        if not INSTANCE_CODE_RE.match(v):
            msg = f"map code must look like m1 or m4-2, got {v!r}"
            raise ValueError(msg)
        return v


class MapConnectionsFile(BaseModel):
    connections: list[ConnectionDef]

    @model_validator(mode="after")
    def _check_unique_cells(self) -> MapConnectionsFile:
        cells = [(c.from_map, c.from_x, c.from_y) for c in self.connections]
        if len(cells) != len(set(cells)):
            msg = "duplicate portal cells (one walk-through per cell)"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# drops.json (doc 5.5)
# ---------------------------------------------------------------------------
# Drop pools are weight tables per roll (doc 5.5 note: 实现时用权重表). The "none"
# outcome is implicit with weight 100 - sum(entries), so each item's marginal
# per-roll probability equals its table weight. Normal monsters roll their
# level-band pool twice; each boss rolls its own table three times.
# ---------------------------------------------------------------------------
class DropEntry(BaseModel):
    item: str
    weight: float = Field(gt=0)  # percent; the implicit none bucket is 100 - sum(weights)
    qty: int = Field(default=1, ge=1)  # 1 for equipment; BOSS potion stacks use 3

    @field_validator("item")
    @classmethod
    def _validate_item(cls, v: str) -> str:
        return _check_code("drop item", v)


def _check_pool_entries(kind: str, entries: list[DropEntry]) -> None:
    if not entries:
        msg = f"{kind}: drop pool needs at least one entry"
        raise ValueError(msg)
    total = sum(e.weight for e in entries)
    if total > 100.0 + 1e-9:
        msg = f"{kind}: drop weights sum {total:.3f} exceeds 100 (none bucket would be negative)"
        raise ValueError(msg)


class LevelBandPool(BaseModel):
    level_lo: int = Field(ge=1, le=40)
    level_hi: int = Field(ge=1, le=40)
    entries: list[DropEntry]

    @model_validator(mode="after")
    def _check_band(self) -> LevelBandPool:
        if self.level_lo > self.level_hi:
            msg = f"level band {self.level_lo}-{self.level_hi} is inverted"
            raise ValueError(msg)
        _check_pool_entries(f"band {self.level_lo}-{self.level_hi}", self.entries)
        return self


class BossPool(BaseModel):
    boss: str
    entries: list[DropEntry]

    @field_validator("boss")
    @classmethod
    def _validate_boss(cls, v: str) -> str:
        return _check_code("boss", v)

    @model_validator(mode="after")
    def _check_pool(self) -> BossPool:
        _check_pool_entries(f"boss {self.boss}", self.entries)
        return self


class DropsFile(BaseModel):
    normal_bands: list[LevelBandPool]
    boss_tables: list[BossPool]

    @model_validator(mode="after")
    def _check(self) -> DropsFile:
        if not self.normal_bands:
            msg = "drops.json needs at least one normal band"
            raise ValueError(msg)
        _check_bands_tile(self.normal_bands)
        boss_codes = [b.boss for b in self.boss_tables]
        if len(boss_codes) != len(set(boss_codes)):
            msg = f"duplicate boss drop tables: {boss_codes}"
            raise ValueError(msg)
        return self


def _check_bands_tile(bands: list[LevelBandPool]) -> None:
    ranges = sorted((b.level_lo, b.level_hi) for b in bands)
    expected_lo = 1
    for lo, hi in ranges:
        if lo != expected_lo:
            msg = f"normal bands must tile 1..40 with no gap/overlap; gap or overlap at {lo}, expected {expected_lo}"
            raise ValueError(msg)
        expected_lo = hi + 1
    if expected_lo - 1 != 40:
        msg = f"normal bands must cover through level 40, they stop at {expected_lo - 1}"
        raise ValueError(msg)
