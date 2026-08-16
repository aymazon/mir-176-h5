"""Idempotent seed script: schema + content def tables + demo data (plan task 13).

Run from ``backend/``::

    python -m seed

Every step is idempotent, so running the seed twice leaves the database
identical (same rows, same values). Steps:

1. ``ensure_schema`` -- apply ``schema.sql`` (all statements are
   ``IF NOT EXISTS``).
2. ``upsert_defs`` -- upsert ``item_def`` for every ``items.json`` entry
   (``ON CONFLICT (code) DO UPDATE``). The remaining def tables
   (map / monster / spawn / drop / npc / quest) are seeded by later
   waves (W2T15 / W3) -- deliberately NOT stubbed here.
3. ``ensure_demo_account`` -- create account ``demo`` / ``demo123``
   (bcrypt cost 12, D22) when absent.
4. ``ensure_demo_characters`` -- upsert the three level-40 demo
   characters (one per class; D23: 土城 m10 center (150,150),
   bound_city ``tucheng``) plus their equipment and supplies.

Data mapping (``items.json`` -> ``item_def``):
- ``class_req`` VARCHAR(8) is encoded compactly: ``all`` or the letters
  ``w``/``m``/``t`` (warrior/mage/taoist) joined by ``|`` in JSON order
  (e.g. ``["warrior", "taoist"]`` -> ``w|t``).
- ``def`` / ``mdef`` are single columns while the JSON stores min-max
  ranges; the seed stores the MAX bound (the headline value).
- ``buy_price`` / ``sell_price``: JSON ``null`` (not sold) maps to 0.
- ``stackable`` = ``stack_max > 1``; ``max_durability`` = ``durability``.

Demo characters (level 40, gold 100,000 each):
- ``exp`` = sum of the exp_curve 1..39 entries -- the doc 4.1 1->40
  cumulative (~52,897,000); level 40 is max, so the curve has no row
  for it.
- ``hp`` / ``mp`` = the class ``level_40_reference`` from
  ``classes.json``.
- Equipment slots per doc 8.2: 100 weapon, 101 armor, 102 helmet,
  103 necklace, 104/105 bracelet, 106/107 ring; inventory 0..39.

Idempotency notes:
- ``character.name`` is globally unique including soft-deleted rows, so
  demo characters are upserted by name (never re-inserted).
- ``item_instance`` has no unique constraint; a row is inserted only
  when the ``(character_id, def_id, slot)`` combination is absent.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Final, TypeAlias

import asyncpg
import bcrypt
from asyncpg.pool import PoolConnectionProxy

from app.config import Settings
from app.content.loader import Content, ContentError, load_all_content
from app.content.models import ClassReq, Item, StatRange

#: Either a direct ``asyncpg.connect()`` handle or a ``pool.acquire()``
#: proxy; both expose the same query interface.
ConnectionLike: TypeAlias = asyncpg.Connection | PoolConnectionProxy

BACKEND_DIR: Final[Path] = Path(__file__).resolve().parent
DATA_DIR: Final[Path] = BACKEND_DIR / "game_data"
SCHEMA_PATH: Final[Path] = BACKEND_DIR / "schema.sql"

DEMO_USERNAME: Final[str] = "demo"
DEMO_PASSWORD: Final[str] = "demo123"
BCRYPT_ROUNDS: Final[int] = 12  # D22

DEMO_LEVEL: Final[int] = 40
DEMO_GOLD: Final[int] = 100_000
DEMO_BOUND_CITY: Final[str] = "tucheng"  # D23
DEMO_MAP_ID: Final[str] = "m10"  # D23
DEMO_X: Final[int] = 150
DEMO_Y: Final[int] = 150

#: Doc 8.2 equipment slot numbers; bracelet and ring each own two slots.
EQUIP_SLOT_NUMBERS: Final[Mapping[str, tuple[int, ...]]] = {
    "weapon": (100,),
    "armor": (101,),
    "helmet": (102,),
    "necklace": (103,),
    "bracelet": (104, 105),
    "ring": (106, 107),
}

DEMO_CHARACTER_NAMES: Final[Mapping[str, str]] = {
    "warrior": "demo_warrior",
    "mage": "demo_mage",
    "taoist": "demo_taoist",
}

#: The six equipment codes per class (plan task 13 / doc 10.2).
DEMO_EQUIPMENT: Final[Mapping[str, tuple[str, ...]]] = {
    "warrior": (
        "judgement_rod",
        "heavenly_demon_armor",
        "holy_war_helmet",
        "holy_war_necklace",
        "holy_war_bracelet",
        "holy_war_ring",
    ),
    "mage": (
        "bone_jade_staff",
        "fa_shen_cloak",
        "fa_shen_helmet",
        "fa_shen_necklace",
        "fa_shen_bracelet",
        "fa_shen_ring",
    ),
    "taoist": (
        "dragon_pattern_sword",
        "tian_zun_robe",
        "tian_zun_helmet",
        "tian_zun_necklace",
        "tian_zun_bracelet",
        "tian_zun_ring",
    ),
}

#: (code, quantity, inventory slot) supplies shared by all three classes.
DEMO_CONSUMABLES: Final[tuple[tuple[str, int, int], ...]] = (
    ("large_hp_potion", 50, 0),
    ("large_mp_potion", 50, 1),
    ("sun_water", 20, 2),
)

_CLASS_LETTERS: Final[Mapping[str, str]] = {"warrior": "w", "mage": "m", "taoist": "t"}

_CHARACTER_UPSERT = """
    INSERT INTO character (
        account_id, name, class, level, exp, hp, mp, gold, bound_city, map_id, x, y
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
    ON CONFLICT (name) DO UPDATE SET
        level = EXCLUDED.level,
        exp = EXCLUDED.exp,
        hp = EXCLUDED.hp,
        mp = EXCLUDED.mp,
        gold = EXCLUDED.gold,
        bound_city = EXCLUDED.bound_city,
        map_id = EXCLUDED.map_id,
        x = EXCLUDED.x,
        y = EXCLUDED.y,
        deleted_at = NULL
    RETURNING id
"""

_ITEM_INSERT_IF_ABSENT = """
    INSERT INTO item_instance (character_id, def_id, slot, quantity, durability)
    SELECT $1::bigint, $2::varchar, $3::smallint, $4::int, $5::smallint
    WHERE NOT EXISTS (
        SELECT 1 FROM item_instance
        WHERE character_id = $1 AND def_id = $2 AND slot = $3
    )
"""


def _class_req_code(class_req: ClassReq) -> str:
    """Encode ``class_req`` for the VARCHAR(8) column (see module docstring)."""
    if class_req == "all":
        return "all"
    return "|".join(_CLASS_LETTERS[class_code] for class_code in class_req)


def _bounds(stat: StatRange | None) -> tuple[int, int]:
    return (stat.min, stat.max) if stat is not None else (0, 0)


def _item_def_row(item: Item) -> tuple[object, ...]:
    attack = _bounds(item.attack)
    magic = _bounds(item.magic)
    taoist = _bounds(item.taoist)
    return (
        item.code,
        item.name,
        item.category,
        _class_req_code(item.class_req),
        item.level_req,
        attack[0],
        attack[1],
        magic[0],
        magic[1],
        taoist[0],
        taoist[1],
        item.defense.max if item.defense is not None else 0,
        item.magic_def.max if item.magic_def is not None else 0,
        item.hp_bonus,
        item.mp_bonus,
        item.buy_price if item.buy_price is not None else 0,
        item.sell_price if item.sell_price is not None else 0,
        item.durability,
        item.stack_max > 1,
    )


async def ensure_schema(conn: ConnectionLike) -> None:
    """Apply ``schema.sql``; every statement is ``IF NOT EXISTS``."""
    await conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


async def upsert_defs(conn: ConnectionLike, content: Content) -> int:
    """Upsert ``item_def`` for every ``items.json`` entry.

    Map / monster / spawn / drop / npc / quest def tables are added by
    later waves (W2T15 / W3); this wave seeds ``item_def`` only. Returns
    the number of upserted rows.
    """
    sql = """
        INSERT INTO item_def (
            code, name, kind, class_req, level_req,
            atk_min, atk_max, mag_min, mag_max, dao_min, dao_max,
            "def", mdef, hp_bonus, mp_bonus, buy_price, sell_price,
            max_durability, stackable
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)
        ON CONFLICT (code) DO UPDATE SET
            name = EXCLUDED.name,
            kind = EXCLUDED.kind,
            class_req = EXCLUDED.class_req,
            level_req = EXCLUDED.level_req,
            atk_min = EXCLUDED.atk_min,
            atk_max = EXCLUDED.atk_max,
            mag_min = EXCLUDED.mag_min,
            mag_max = EXCLUDED.mag_max,
            dao_min = EXCLUDED.dao_min,
            dao_max = EXCLUDED.dao_max,
            "def" = EXCLUDED."def",
            mdef = EXCLUDED.mdef,
            hp_bonus = EXCLUDED.hp_bonus,
            mp_bonus = EXCLUDED.mp_bonus,
            buy_price = EXCLUDED.buy_price,
            sell_price = EXCLUDED.sell_price,
            max_durability = EXCLUDED.max_durability,
            stackable = EXCLUDED.stackable
    """
    for item in content.items.items:
        await conn.execute(sql, *_item_def_row(item))
    return len(content.items.items)


async def ensure_demo_account(conn: ConnectionLike) -> None:
    """Create the ``demo`` account (bcrypt cost 12) when absent."""
    password_hash = bcrypt.hashpw(
        DEMO_PASSWORD.encode("utf-8"),
        bcrypt.gensalt(rounds=BCRYPT_ROUNDS),
    ).decode("ascii")
    await conn.execute(
        "INSERT INTO account (username, password_hash) VALUES ($1, $2) "
        "ON CONFLICT (username) DO NOTHING",
        DEMO_USERNAME,
        password_hash,
    )


def _level40_exp(content: Content) -> int:
    """Doc 4.1: the 1->40 cumulative EXP (~52,897,000), from the curve."""
    return sum(entry.exp for entry in content.exp_curve.levels)


def _require_demo_equip(items_by_code: Mapping[str, Item], code: str) -> tuple[Item, tuple[int, ...]]:
    item = items_by_code.get(code)
    if item is None or item.slot is None:
        raise ContentError(f"demo equipment {code!r} is missing from items.json or has no equipment slot")
    slots = EQUIP_SLOT_NUMBERS.get(item.slot)
    if slots is None:
        raise ContentError(f"demo equipment {code!r} has unsupported equipment slot {item.slot!r}")
    return item, slots


def _require_demo_consumable(items_by_code: Mapping[str, Item], code: str) -> Item:
    item = items_by_code.get(code)
    if item is None or item.slot is not None:
        raise ContentError(f"demo consumable {code!r} is missing from items.json or is equipment")
    return item


async def ensure_demo_characters(conn: ConnectionLike, content: Content) -> None:
    """Upsert the three level-40 demo characters and their inventories."""
    items_by_code = {item.code: item for item in content.items.items}
    exp = _level40_exp(content)
    account_id = await conn.fetchval(
        "SELECT id FROM account WHERE username = $1", DEMO_USERNAME
    )
    for game_class in content.classes.classes:
        reference = game_class.level_40_reference
        character_id = await conn.fetchval(
            _CHARACTER_UPSERT,
            account_id,
            DEMO_CHARACTER_NAMES[game_class.code],
            game_class.code,
            DEMO_LEVEL,
            exp,
            reference.hp,
            reference.mp,
            DEMO_GOLD,
            DEMO_BOUND_CITY,
            DEMO_MAP_ID,
            DEMO_X,
            DEMO_Y,
        )
        for code in DEMO_EQUIPMENT[game_class.code]:
            item, slots = _require_demo_equip(items_by_code, code)
            durability = item.durability if item.category in ("weapon", "armor") else None
            for slot in slots:
                await conn.execute(_ITEM_INSERT_IF_ABSENT, character_id, code, slot, 1, durability)
        for code, quantity, slot in DEMO_CONSUMABLES:
            _require_demo_consumable(items_by_code, code)
            await conn.execute(_ITEM_INSERT_IF_ABSENT, character_id, code, slot, quantity, None)


async def run_seed(conn: ConnectionLike) -> dict[str, int]:
    """Run the full seed on an open connection; return a row-count summary."""
    content = load_all_content(DATA_DIR)
    async with conn.transaction():
        await ensure_schema(conn)
        await upsert_defs(conn, content)
        await ensure_demo_account(conn)
        await ensure_demo_characters(conn, content)
    tables = ("account", "character", "item_def", "item_instance")
    summary: dict[str, int] = {}
    for table in tables:
        count: int | None = await conn.fetchval(f"SELECT count(*) FROM {table}")
        if count is None:
            raise ContentError(f"row count for {table!r} came back NULL (internal error)")
        summary[table] = int(count)
    return summary


async def main() -> int:
    conn = await asyncpg.connect(Settings().database_url)
    try:
        summary = await run_seed(conn)
    finally:
        await conn.close()
    print("seed complete (idempotent; safe to re-run):")
    for table, count in summary.items():
        print(f"  {table}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
