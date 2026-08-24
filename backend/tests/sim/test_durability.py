"""耐久系统测试（todo 24）：磨损 / 失效 / 普修。

覆盖验收（计划 todo 24）：
- 100 次攻击后武器耐久下降（tick_loop 磨损，锁内持久化另测）；
- 耐久 0 后面板失效（compute_panel 输入侧过滤 + API 穿戴路径端到端）；
- 修理后恢复且扣费正确（cost = (max - current) × REPAIR_PRICE_PER_DURABILITY）；
- 金币不足拒绝（422 且耐久不变）；"all" 遍历多槽；空槽 409。

纯内存用例直接写 tick_loop._EQUIP_CACHE 驱动磨损记账（生产路径由
start_tick_loop / persist_session_state / equip API 刷新该缓存）；
DB 用例直连真实 PG（mir_dev），与 test_equip/test_tick_loop 同一约定：
随机命名避撞、收尾删 player（FK CASCADE）并 dispose engine。
"""

import json
import random
from collections.abc import AsyncIterator
from typing import cast
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.combat.balance import (
    DURABILITY_LOSS_PER_ATTACK,
    DURABILITY_LOSS_PER_HIT,
    REPAIR_PRICE_PER_DURABILITY,
)
from app.combat.stats import ClassKey, EquipmentInput, compute_panel
from app.core.db import SessionLocal, engine
from app.gamedata_loader import load_all
from app.gamedata_schema import MonsterDef, SpawnEntry
from app.main import app
from app.models.item_instance import ItemInstance
from app.sim.session import (
    CharacterSnapshot,
    PlayerSession,
    get_or_create_player_session,
    max_hp_for,
    max_mp_for,
    register_session,
    reset_sessions,
    session_for,
)
from app.sim.tick_loop import (
    _EQUIP_CACHE,
    persist_session_state,
    reload_equip_cache,
    simulate_tick,
)
from app.sim.zone import ZoneInstance, shutdown_all
from app.sse.session_state import get_or_create_session
from app.sse.session_state import reset_sessions as reset_sse

# --- 公共脚手架 ------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _clean_runtime() -> AsyncIterator[None]:
    """会话/zone/装备槽缓存隔离 + engine dispose（防跨 event loop 复用）。"""
    reset_sessions()
    reset_sse()
    _EQUIP_CACHE.clear()
    await shutdown_all()
    yield
    reset_sessions()
    reset_sse()
    _EQUIP_CACHE.clear()
    await shutdown_all()
    await engine.dispose()


_next_id = [9000]


def next_id() -> int:
    _next_id[0] += 1
    return _next_id[0]


def make_session(
    character_id: int = 1,
    *,
    level: int = 8,
    map_id: str = "no-such-map",  # 缺省假地图 → 无图模式，不碰 DB
) -> PlayerSession:
    snap = CharacterSnapshot(
        name=f"t{character_id}",
        class_="warrior",
        level=level,
        exp=0,
        hp=max_hp_for(level),
        mp=max_mp_for(level),
        gold=0,
        map_id=map_id,
        bind_city="yin-xing-cun",
        bag_capacity=40,
    )
    session = PlayerSession(
        character_id=character_id,
        char_snapshot=snap,
        sse_state=get_or_create_session(character_id),
        rng=random.Random(42),
    )
    register_session(session)
    return session


def make_biqi_zone() -> PlayerSession:
    """真实 gamedata 的 biqi-wild 场景：返回挂上 zone 的会话。"""
    gd = cast("dict[str, object]", load_all())
    spawns = cast("dict[str, SpawnEntry]", gd["spawns"])
    table = {k: v for k, v in spawns.items() if v.map_id == "biqi-wild"}
    zone = ZoneInstance(
        map_id="biqi-wild",
        static_spawn_table=table,
        monster_defs=cast("dict[str, MonsterDef]", gd["monsters"]),
    )
    zone.fill_initial()
    session = make_session(30, map_id="biqi-wild")
    session.zone = zone
    return session


def equip_in_memory(
    session: PlayerSession, item_id: str, slot: str, *, durability: int | None
) -> ItemInstance:
    """塞一件装备进内存背包并登记进装备槽缓存（绕过 DB 的纯内存穿戴）。"""
    row = ItemInstance(
        character_id=session.character_id,
        item_id=item_id,
        quantity=1,
        durability=durability,
    )
    row.id = next_id()  # 内存用例需要非 None id 才能被缓存定位
    session.inventory.append(row)
    _EQUIP_CACHE.setdefault(session.character_id, {})[slot] = row.id
    return row


def events_of_kind(events: list[tuple[str, str]], kind: str) -> list[dict[str, object]]:
    out = []
    for name, data in events:
        if name != "log":
            continue
        payload = json.loads(data)
        if payload.get("kind") == kind:
            out.append(payload)
    return out


# --- A1. 磨损：出手扣武器 / 受击扣防具 ----------------------------------------------


def test_100_attacks_wear_weapon_by_100(monkeypatch: pytest.MonkeyPatch) -> None:
    """100 次出手命中 → 武器耐久恰好降 100（DURABILITY_LOSS_PER_ATTACK=1/次）。"""
    session = make_biqi_zone()
    assert session.zone is not None and session.zone.alive_monsters
    weapon = equip_in_memory(session, "bronze_sword", "weapon", durability=1000)

    # 确定性化：命中恒真、伤害恒 0（怪不死 → 场上始终有目标、每 2 tick 出手一次）
    monkeypatch.setattr("app.sim.tick_loop.hit_check", lambda *a, **k: True)
    monkeypatch.setattr("app.sim.tick_loop.physical_damage", lambda *a, **k: 0)

    for n in range(1, 201):  # ATTACK_INTERVAL_TICKS=2 → 恰好 100 次出手
        simulate_tick(session, n)

    assert weapon.durability == 1000 - 100 * DURABILITY_LOSS_PER_ATTACK == 900


def test_weapon_break_event_fires_once_and_clamps_at_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """耐久归零：播报一次 durability 事件；后续出手不再负扣、不再重复播报。"""
    session = make_biqi_zone()
    weapon = equip_in_memory(session, "bronze_sword", "weapon", durability=1)
    monkeypatch.setattr("app.sim.tick_loop.hit_check", lambda *a, **k: True)
    monkeypatch.setattr("app.sim.tick_loop.physical_damage", lambda *a, **k: 0)

    all_events: list[tuple[str, str]] = []
    for n in range(1, 11):  # 5 次出手：第 1 次打光，后 4 次已破碎
        all_events.extend(simulate_tick(session, n))

    assert weapon.durability == 0
    breaks = events_of_kind(all_events, "durability")
    assert len(breaks) == 1
    assert "武器" in str(breaks[0]["text"])


def test_armor_wears_on_each_hit_taken(monkeypatch: pytest.MonkeyPatch) -> None:
    """每次被怪物命中 → 衣服槽耐久降 DURABILITY_LOSS_PER_HIT（未命中不扣）。"""
    session = make_biqi_zone()
    armor = equip_in_memory(session, "light_armor", "armor", durability=500)
    monkeypatch.setattr("app.sim.tick_loop.hit_check", lambda *a, **k: True)
    monkeypatch.setattr("app.sim.tick_loop.physical_damage", lambda *a, **k: 0)

    hits_taken = 0
    for n in range(1, 41):
        events = simulate_tick(session, n)
        hits_taken += len(events_of_kind(events, "defend"))

    assert hits_taken > 0, "40 tick 内怪物从未命中（前置失败）"
    assert armor.durability == 500 - hits_taken * DURABILITY_LOSS_PER_HIT


def test_wear_skips_without_equipped_or_durability_concept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无槽位引用/物品无耐久概念 → 不磨损；durability NULL 存量首触按满初始化。"""
    session = make_biqi_zone()
    no_slot_row = ItemInstance(character_id=30, item_id="bronze_sword", quantity=1)
    no_slot_row.id = next_id()
    session.inventory.append(no_slot_row)  # 在背包但不在任何槽位 → 不磨损
    potion_row = ItemInstance(character_id=30, item_id="potion_hp_small", quantity=3)
    potion_row.id = next_id()
    session.inventory.append(potion_row)
    _EQUIP_CACHE[30] = {"weapon": potion_row.id}  # 药品穿在武器槽（无耐久概念）
    seeded = equip_in_memory(session, "xiuluo_blade", "armor", durability=None)

    monkeypatch.setattr("app.sim.tick_loop.hit_check", lambda *a, **k: True)
    monkeypatch.setattr("app.sim.tick_loop.physical_damage", lambda *a, **k: 0)
    for n in range(1, 7):  # 3 次出手
        simulate_tick(session, n)

    assert no_slot_row.durability is None
    assert potion_row.durability is None
    # NULL 存量 → 首次磨损触达时初始化为满耐久 25000，此后随受击磨损只降不升
    assert seeded.durability is not None
    assert 25000 - 40 <= seeded.durability <= 25000


# --- A2. 失效：compute_panel 输入侧过滤 -------------------------------------------


def test_zero_durability_excluded_from_panel() -> None:
    """耐久 0 的装备不计入面板；None/正数正常计入（输入侧过滤，stats 本体不动）。"""
    bare = compute_panel(char_class=ClassKey.warrior, level=22)
    broken = compute_panel(
        char_class=ClassKey.warrior,
        level=22,
        equipment=[
            EquipmentInput(slot="weapon", stats={"dc_max": 20}, durability=0),
            EquipmentInput(slot="armor", stats={"ac_max": 5}, durability=0),
        ],
    )
    assert broken.dc_max == bare.dc_max
    assert broken.ac_max == bare.ac_max

    worn = compute_panel(
        char_class=ClassKey.warrior,
        level=22,
        equipment=[EquipmentInput(slot="weapon", stats={"dc_max": 20}, durability=1)],
    )
    assert worn.dc_max == bare.dc_max + 20

    legacy = compute_panel(  # durability 缺省 None（存量调用方兼容）
        char_class=ClassKey.warrior,
        level=22,
        equipment=[EquipmentInput(slot="weapon", stats={"dc_max": 20})],
    )
    assert legacy.dc_max == bare.dc_max + 20


# --- B. 磨损的锁内持久化（真实 PG）--------------------------------------------------


@pytest.fixture
async def db_char() -> AsyncIterator[tuple[str, int]]:
    username = f"t24l_{uuid4().hex[:10]}"
    char_name = f"c24l_{uuid4().hex[:10]}"
    async with SessionLocal() as db:
        await db.execute(
            text("INSERT INTO player (username, password_hash) VALUES (:u, :p)"),
            {"u": username, "p": "not-a-login"},
        )
        row = await db.execute(
            text(
                "INSERT INTO character "
                "(player_id, name, class, level, hp, mp, gold, map_id, bind_city, last_active_at) "
                "VALUES ((SELECT id FROM player WHERE username = :u), :n, 'warrior', 8, "
                "80, 50, 0, 'biqi-wild', 'yin-xing-cun', now()) RETURNING id"
            ),
            {"u": username, "n": char_name},
        )
        character_id = row.scalar_one()
        inst = await db.execute(
            text(
                "INSERT INTO item_instance (character_id, item_id, quantity, durability) "
                "VALUES (:cid, 'bronze_sword', 1, 6000) RETURNING id"
            ),
            {"cid": character_id},
        )
        instance_id = inst.scalar_one()
        await db.execute(
            text(
                "INSERT INTO equipment_slot (character_id, slot, item_instance_id) "
                "VALUES (:cid, 'weapon', :iid)"
            ),
            {"cid": character_id, "iid": instance_id},
        )
        await db.commit()
    yield username, character_id
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM player WHERE username = :u"), {"u": username})
        await db.commit()


async def test_persist_writes_durability_within_lock(
    db_char: tuple[str, int],
) -> None:
    """磨损落库契约：装备实例耐久随每 5 tick 批次 UPDATE（必须在 session.lock 内）。"""
    _, character_id = db_char
    async with SessionLocal() as db:
        session = await get_or_create_player_session(db, character_id)
    await reload_equip_cache(character_id)
    assert _EQUIP_CACHE[character_id] == {"weapon": session.inventory[0].id}

    weapon = session.inventory[0]
    weapon.durability = 5997  # 模拟 3 次出手磨损后的内存态

    async with session.lock:  # 契约：persist 必须在锁内调用
        await persist_session_state(session)

    async with SessionLocal() as db:
        dur = (
            await db.execute(
                text("SELECT durability FROM item_instance WHERE character_id = :cid"),
                {"cid": character_id},
            )
        ).scalar_one()
    assert dur == 5997


# --- C. 商店普修 API（真实 PG，全链路）----------------------------------------------


class _Harness:
    """账号/角色/物品供给与 DB 断言助手（test_equip 同款 + durability 扩展）。"""

    def __init__(self, client: AsyncClient) -> None:
        self.client = client
        self.usernames: list[str] = []

    async def create_character(self, class_: str = "warrior", level: int | None = None) -> int:
        creds = {"username": f"u_{uuid4().hex[:12]}", "password": "p@ssw0rd"}
        r = await self.client.post("/api/auth/register", json=creds)
        assert r.status_code == 201, r.text
        r = await self.client.post("/api/auth/login", json=creds)
        assert r.status_code == 200, r.text
        r = await self.client.post(
            "/api/character", json={"name": f"c_{uuid4().hex[:10]}", "class": class_}
        )
        assert r.status_code == 201, r.text
        character_id = int(r.json()["id"])
        self.usernames.append(creds["username"])
        if level is not None:
            await self.set_character(character_id, level=level)
        return character_id

    async def set_character(self, character_id: int, **fields: object) -> None:
        sets = ", ".join(f"{col} = :{col}" for col in fields)
        async with SessionLocal() as db:
            await db.execute(
                text(f"UPDATE character SET {sets} WHERE id = :cid"),
                {**fields, "cid": character_id},
            )
            await db.commit()

    async def seed_item(
        self, character_id: int, item_id: str, *, durability: int | None = None
    ) -> int:
        async with SessionLocal() as db:
            row = await db.execute(
                text(
                    "INSERT INTO item_instance (character_id, item_id, quantity, durability) "
                    "VALUES (:cid, :iid, 1, :dur) RETURNING id"
                ),
                {"cid": character_id, "iid": item_id, "dur": durability},
            )
            row_id = int(row.scalar_one())
            await db.commit()
        return row_id

    async def equip(self, character_id: int, item_instance_id: int, slot: str) -> None:
        r = await self.client.post(
            "/api/inventory/equip",
            json={"item_instance_id": item_instance_id, "slot": slot},
        )
        assert r.status_code == 200, r.text

    async def durability_of(self, item_instance_id: int) -> int | None:
        async with SessionLocal() as db:
            return (
                await db.execute(
                    text("SELECT durability FROM item_instance WHERE id = :iid"),
                    {"iid": item_instance_id},
                )
            ).scalar_one()

    async def gold_of(self, character_id: int) -> int:
        async with SessionLocal() as db:
            return (
                await db.execute(
                    text("SELECT gold FROM character WHERE id = :cid"),
                    {"cid": character_id},
                )
            ).scalar_one()


@pytest.fixture
async def harness() -> AsyncIterator[_Harness]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        h = _Harness(c)
        yield h
    async with SessionLocal() as db:
        for username in h.usernames:
            await db.execute(
                text("DELETE FROM player WHERE username = :u"), {"u": username}
            )
        await db.commit()
    from app.sim.tick_loop import shutdown_all_sessions

    await shutdown_all_sessions()
    await shutdown_all()


def _sse_events(character_id: int, event_name: str) -> list[dict[str, object]]:
    session = session_for(character_id)
    assert session is not None
    return [
        json.loads(event.data)
        for event in session.sse_state.buffer.after(0)
        if event.event == event_name
    ]


async def test_repair_single_slot_restores_and_charges(harness: _Harness) -> None:
    """单槽普修：cost=(max-current)×单价；金币扣减、耐久回满、stat+repair 双事件。"""
    cid = await harness.create_character(level=22)
    iid = await harness.seed_item(cid, "xiuluo_blade", durability=24500)  # 缺 500
    await harness.equip(cid, iid, "weapon")
    await harness.set_character(cid, gold=1_000_000)

    r = await harness.client.post("/api/inventory/repair", json={"slot": "weapon"})
    assert r.status_code == 200, r.text
    body = r.json()
    expected_cost = 500 * REPAIR_PRICE_PER_DURABILITY
    assert body["cost"] == expected_cost == 50_000
    assert body["gold_after"] == 1_000_000 - expected_cost
    assert [(i["slot"], i["item_instance_id"]) for i in body["repaired"]] == [
        ("weapon", iid)
    ]
    assert body["repaired"][0]["durability_before"] == 24500
    assert body["repaired"][0]["durability_after"] == 25000

    # DB 落位：耐久回满、金币扣减
    assert await harness.durability_of(iid) == 25000
    assert await harness.gold_of(cid) == 950_000

    # SSE：stat(reason=repair) + repair 明细各一条
    stats = [e for e in _sse_events(cid, "stat") if e.get("reason") == "repair"]
    assert len(stats) == 1
    repairs = _sse_events(cid, "repair")
    assert len(repairs) == 1 and repairs[0]["cost"] == expected_cost


async def test_repair_insufficient_gold_422_and_no_change(harness: _Harness) -> None:
    """修理费 > 身上金币 → 422，且不扣金币、不改耐久。"""
    cid = await harness.create_character(level=22)
    iid = await harness.seed_item(cid, "xiuluo_blade", durability=24500)
    await harness.equip(cid, iid, "weapon")
    await harness.set_character(cid, gold=49_999)  # < 需 50_000

    r = await harness.client.post("/api/inventory/repair", json={"slot": "weapon"})
    assert r.status_code == 422, r.text
    assert "金币不足" in r.json()["detail"]
    assert await harness.durability_of(iid) == 24500
    assert await harness.gold_of(cid) == 49_999


async def test_repair_all_slots_multi(harness: _Harness) -> None:
    """"all" 全修：遍历所有已穿戴槽位累加费用并全部回满。"""
    cid = await harness.create_character(level=11)
    sword = await harness.seed_item(cid, "bronze_sword", durability=5900)  # 缺 100
    armor = await harness.seed_item(cid, "light_armor", durability=7900)  # 缺 100
    await harness.equip(cid, sword, "weapon")
    await harness.equip(cid, armor, "armor")
    await harness.set_character(cid, gold=500_000)

    r = await harness.client.post("/api/inventory/repair", json={"slot": "all"})
    assert r.status_code == 200, r.text
    body = r.json()
    expected_cost = 200 * REPAIR_PRICE_PER_DURABILITY
    assert body["cost"] == expected_cost == 20_000
    assert sorted(i["slot"] for i in body["repaired"]) == ["armor", "weapon"]
    assert await harness.durability_of(sword) == 6000
    assert await harness.durability_of(armor) == 8000
    assert await harness.gold_of(cid) == 480_000


async def test_repair_empty_slot_409_and_all_empty_409(harness: _Harness) -> None:
    """单槽未装备 → 409；一件没穿时 "all" → 409。"""
    _cid = await harness.create_character(level=11)
    r = await harness.client.post("/api/inventory/repair", json={"slot": "weapon"})
    assert r.status_code == 409, r.text
    r = await harness.client.post("/api/inventory/repair", json={"slot": "all"})
    assert r.status_code == 409, r.text


async def test_repair_invalid_slot_422(harness: _Harness) -> None:
    _cid = await harness.create_character(level=11)
    r = await harness.client.post("/api/inventory/repair", json={"slot": "cape"})
    assert r.status_code == 422, r.text


async def test_equip_broken_weapon_panel_excludes_stats(harness: _Harness) -> None:
    """端到端失效：耐久 0 的修罗穿戴上，面板 dc_max 不含其 +20（输入侧过滤）。"""
    cid = await harness.create_character(level=22)
    iid = await harness.seed_item(cid, "xiuluo_blade", durability=0)
    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": iid, "slot": "weapon"}
    )
    assert r.status_code == 200, r.text
    bare = compute_panel(char_class=ClassKey.warrior, level=22)
    assert r.json()["panel"]["dc_max"] == bare.dc_max

    # 修理后面板恢复计入
    await harness.set_character(cid, gold=10_000_000)
    r = await harness.client.post("/api/inventory/repair", json={"slot": "weapon"})
    assert r.status_code == 200, r.text
    assert r.json()["panel"]["dc_max"] == bare.dc_max + 20
