"""Seed script integration tests (plan task 13): idempotency + demo data.

Runs the real seed logic (``seed.run_seed``) against the live local database
(the live-DB pattern of ``tests/test_persist.py``). Demo rows are permanent
seed content, so no test cleans them up; every assertion must hold across
repeated runs of the suite.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Final

import asyncpg
import bcrypt
import httpx
import pytest

from app.db.pool import close_pool, init_pool
from app.main import app
from seed import run_seed

DATA_DIR = Path(__file__).resolve().parents[1] / "game_data"

DEMO_CHARACTERS: Final[dict[str, str]] = {
    "demo_warrior": "warrior",
    "demo_mage": "mage",
    "demo_taoist": "taoist",
}

_CLASS_LETTERS: Final[dict[str, str]] = {"warrior": "w", "mage": "m", "taoist": "t"}

_COUNTED_TABLES: Final[tuple[str, ...]] = ("account", "character", "item_def", "item_instance")

# Doc 8.2 slot numbers: 100 weapon, 101 armor, 102 helmet, 103 necklace,
# 104/105 bracelet, 106/107 ring. Only weapon/armor carry durability (50,
# doc 4.6); every other slot is NULL.
_EQUIPMENT: Final[dict[str, tuple[tuple[str, int, int | None], ...]]] = {
    "demo_warrior": (
        ("judgement_rod", 100, 50),
        ("heavenly_demon_armor", 101, 50),
        ("holy_war_helmet", 102, None),
        ("holy_war_necklace", 103, None),
        ("holy_war_bracelet", 104, None),
        ("holy_war_bracelet", 105, None),
        ("holy_war_ring", 106, None),
        ("holy_war_ring", 107, None),
    ),
    "demo_mage": (
        ("bone_jade_staff", 100, 50),
        ("fa_shen_cloak", 101, 50),
        ("fa_shen_helmet", 102, None),
        ("fa_shen_necklace", 103, None),
        ("fa_shen_bracelet", 104, None),
        ("fa_shen_bracelet", 105, None),
        ("fa_shen_ring", 106, None),
        ("fa_shen_ring", 107, None),
    ),
    "demo_taoist": (
        ("dragon_pattern_sword", 100, 50),
        ("tian_zun_robe", 101, 50),
        ("tian_zun_helmet", 102, None),
        ("tian_zun_necklace", 103, None),
        ("tian_zun_bracelet", 104, None),
        ("tian_zun_bracelet", 105, None),
        ("tian_zun_ring", 106, None),
        ("tian_zun_ring", 107, None),
    ),
}

# (code, inventory slot, quantity): one row each, stacked quantity.
_CONSUMABLES: Final[tuple[tuple[str, int, int], ...]] = (
    ("large_hp_potion", 0, 50),
    ("large_mp_potion", 1, 50),
    ("sun_water", 2, 20),
)


def _raw(name: str) -> dict[str, Any]:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def _expected_rows(character_name: str) -> Counter[tuple[str, int, int, int | None]]:
    rows: Counter[tuple[str, int, int, int | None]] = Counter(
        (code, slot, 1, durability) for code, slot, durability in _EQUIPMENT[character_name]
    )
    for code, slot, quantity in _CONSUMABLES:
        rows[(code, slot, quantity, None)] += 1
    return rows


async def _counts(pool: asyncpg.Pool) -> dict[str, int]:
    return {
        table: int(await pool.fetchval(f"SELECT count(*) FROM {table}")) for table in _COUNTED_TABLES
    }


@pytest.fixture
async def db() -> AsyncIterator[asyncpg.Pool]:
    pool = await init_pool()
    try:
        yield pool
    finally:
        await close_pool()


@pytest.fixture
async def client(db: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


async def _demo_token(client: httpx.AsyncClient) -> str:
    response = await client.post("/api/auth/login", json={"username": "demo", "password": "demo123"})
    assert response.status_code == 200, response.text
    return str(response.json()["token"])


async def test_seed_is_idempotent(db: asyncpg.Pool) -> None:
    async with db.acquire() as conn:
        summary_1 = await run_seed(conn)
    counts_1 = await _counts(db)
    hash_run_1 = await db.fetchval("SELECT password_hash FROM account WHERE username = 'demo'")

    async with db.acquire() as conn:
        summary_2 = await run_seed(conn)
    counts_2 = await _counts(db)
    hash_run_2 = await db.fetchval("SELECT password_hash FROM account WHERE username = 'demo'")

    assert summary_1 == summary_2
    assert counts_1 == counts_2
    assert counts_1["item_def"] == len(_raw("items.json")["items"])
    assert hash_run_1 is not None
    assert hash_run_1 == hash_run_2
    duplicates = await db.fetchval(
        "SELECT count(*) FROM (SELECT name FROM character GROUP BY name HAVING count(*) > 1) AS dupes"
    )
    assert duplicates == 0


async def test_demo_account_credentials_and_character_listing(
    db: asyncpg.Pool, client: httpx.AsyncClient
) -> None:
    row = await db.fetchrow("SELECT password_hash FROM account WHERE username = 'demo'")
    assert row is not None, "seed must create the demo account"
    assert bcrypt.checkpw(b"demo123", row["password_hash"].encode("ascii"))

    token = await _demo_token(client)
    listing = await client.get("/api/characters", headers={"Authorization": f"Bearer {token}"})
    assert listing.status_code == 200, listing.text
    names = sorted(str(char["name"]) for char in listing.json())
    assert names == sorted(DEMO_CHARACTERS)


async def test_demo_characters_carry_level_40_reference_state(client: httpx.AsyncClient) -> None:
    token = await _demo_token(client)
    listing = await client.get("/api/characters", headers={"Authorization": f"Bearer {token}"})
    assert listing.status_code == 200, listing.text
    characters = {str(char["name"]): char for char in listing.json()}

    expected_exp = sum(int(entry["exp"]) for entry in _raw("exp_curve.json")["levels"])
    reference = {cls["code"]: cls["level_40_reference"] for cls in _raw("classes.json")["classes"]}

    for name, class_code in DEMO_CHARACTERS.items():
        char = characters[name]
        assert char["level"] == 40
        assert char["exp"] == expected_exp
        assert char["hp"] == reference[class_code]["hp"]
        assert char["mp"] == reference[class_code]["mp"]
        assert char["gold"] == 100_000
        assert char["bound_city"] == "tucheng"
        assert char["map_id"] == "m10"
        assert (char["x"], char["y"]) == (150, 150)


async def test_demo_equipment_and_supplies_are_exact(db: asyncpg.Pool) -> None:
    rows = await db.fetch(
        "SELECT c.name AS name, i.def_id, i.slot, i.quantity, i.durability "
        "FROM item_instance i JOIN character c ON c.id = i.character_id "
        "WHERE c.name IN ($1, $2, $3)",
        "demo_warrior",
        "demo_mage",
        "demo_taoist",
    )
    per_character: dict[str, Counter[tuple[str, int, int, int | None]]] = {}
    for row in rows:
        key = (str(row["def_id"]), int(row["slot"]), int(row["quantity"]), row["durability"])
        per_character.setdefault(str(row["name"]), Counter())[key] += 1

    for name in DEMO_CHARACTERS:
        assert per_character.get(name) == _expected_rows(name), f"inventory mismatch for {name}"


async def test_item_def_rows_match_items_json_mapping(db: asyncpg.Pool) -> None:
    items = _raw("items.json")["items"]
    for item in items:
        row = await db.fetchrow("SELECT * FROM item_def WHERE code = $1", item["code"])
        assert row is not None, f"item_def missing for {item['code']}"
        class_req = item["class_req"]
        expected_req = "all" if class_req == "all" else "|".join(_CLASS_LETTERS[code] for code in class_req)
        attack = item.get("attack")
        magic = item.get("magic")
        taoist = item.get("taoist")
        defense = item.get("defense")
        magic_def = item.get("magic_def")
        assert row["kind"] == item["category"]
        assert row["class_req"] == expected_req
        assert row["level_req"] == item["level_req"]
        assert (int(row["atk_min"]), int(row["atk_max"])) == (
            (attack["min"], attack["max"]) if attack else (0, 0)
        )
        assert (int(row["mag_min"]), int(row["mag_max"])) == (
            (magic["min"], magic["max"]) if magic else (0, 0)
        )
        assert (int(row["dao_min"]), int(row["dao_max"])) == (
            (taoist["min"], taoist["max"]) if taoist else (0, 0)
        )
        assert int(row["def"]) == (defense["max"] if defense else 0)
        assert int(row["mdef"]) == (magic_def["max"] if magic_def else 0)
        assert int(row["hp_bonus"]) == item.get("hp_bonus", 0)
        assert int(row["mp_bonus"]) == item.get("mp_bonus", 0)
        assert int(row["buy_price"]) == (item["buy_price"] if item["buy_price"] is not None else 0)
        assert int(row["sell_price"]) == (item["sell_price"] if item["sell_price"] is not None else 0)
        assert int(row["max_durability"]) == item.get("durability", 0)
        assert bool(row["stackable"]) == (item["stack_max"] > 1)
