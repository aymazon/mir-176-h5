"""消耗品效果测试（todo 26）：神水限时 buff / 祝福油两分支 / 三卷 / 药品瞬回。

覆盖验收（计划 todo 26）：
- 神水后面板 +N 且 600 tick 后回落（buff_expire 日志 + 面板加值归零）；
- 登出不清空 buff：重登按 saved_at 墙钟折损离线时长（真实时间过期语义）；
- 祝福油成功（lucky 6→7）/概率失败（耗油不升）/达上限 422 不扣油；
- 回城卷切 bind_city、随机卷同图距离档、地牢逃脱卷回就近 NPC 城镇；
- 药品瞬回钳上限；校验链 404/422。

API 用例直连真实 PG（mir_dev），随机命名避撞、收尾删 player（FK CASCADE）
并 dispose engine 防跨 event loop 复用（test_inventory 同款脚手架）。
"""

import json
import random
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.combat.balance import (
    BLESSING_OIL_LUCK_MAX,
    DIVINE_WATER_DURATION_TICKS,
    PORTAL_RANDOM_MAX_DIST,
    PORTAL_RANDOM_MIN_DIST,
)
from app.combat.effects import active_stat_bonus
from app.core.db import SessionLocal, engine
from app.main import app
from app.sim.session import (
    CharacterSnapshot,
    PlayerSession,
    get_or_create_player_session,
    max_hp_for,
    max_mp_for,
    register_session,
    release_session,
    reset_sessions,
)
from app.sim.tick_loop import (
    _GROUND_DROPS,
    cancel_tick_loop,
    hydrate_buffs,
    payload_to_buffs,
    simulate_tick,
    start_tick_loop,
)
from app.sim.zone import shutdown_all
from app.sse.session_state import get_or_create_session
from app.sse.session_state import reset_sessions as reset_sse

BIND_CITY = "yin-xing-cun"
DIVINE_WATER = "divine_water"
OIL = "blessing_oil"

# --- 公共脚手架 ------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _clean_runtime() -> AsyncIterator[None]:
    """会话/zone/地面掉落池隔离 + engine dispose（防跨 loop 复用）。"""
    reset_sessions()
    reset_sse()
    _GROUND_DROPS.clear()
    await shutdown_all()
    yield
    reset_sessions()
    reset_sse()
    _GROUND_DROPS.clear()
    await shutdown_all()
    await engine.dispose()


class _Harness:
    """账号/角色/物品供给与 DB 断言助手（test_inventory 同款）。"""

    def __init__(self, client: AsyncClient) -> None:
        self.client = client
        self.usernames: list[str] = []

    async def create_character(
        self, *, map_id: str | None = None, bind_city: str = BIND_CITY
    ) -> int:
        creds = {"username": f"u_{uuid_hex()}", "password": "p@ssw0rd"}
        r = await self.client.post("/api/auth/register", json=creds)
        assert r.status_code == 201, r.text
        r = await self.client.post("/api/auth/login", json=creds)
        assert r.status_code == 200, r.text
        r = await self.client.post(
            "/api/character", json={"name": f"c_{uuid_hex()}", "class": "warrior"}
        )
        assert r.status_code == 201, r.text
        cid = int(r.json()["id"])
        self.usernames.append(creds["username"])
        if map_id is not None or bind_city != BIND_CITY:
            await self.set_character(
                cid, map_id=map_id or "no-such-map", bind_city=bind_city
            )
        return cid

    async def set_character(self, character_id: int, **fields: object) -> None:
        sets = ", ".join(f"{col} = :{col}" for col in fields)
        async with SessionLocal() as db:
            await db.execute(
                text(f"UPDATE character SET {sets} WHERE id = :cid"),
                {**fields, "cid": character_id},
            )
            await db.commit()

    async def seed_item(
        self, character_id: int, item_id: str, *, quantity: int = 1, lucky: int = 0
    ) -> int:
        async with SessionLocal() as db:
            row = await db.execute(
                text(
                    "INSERT INTO item_instance (character_id, item_id, quantity, lucky) "
                    "VALUES (:cid, :iid, :qty, :lucky) RETURNING id"
                ),
                {"cid": character_id, "iid": item_id, "qty": quantity, "lucky": lucky},
            )
            instance_id = int(row.scalar_one())
            await db.commit()
        return instance_id

    async def equip_weapon(self, character_id: int, instance_id: int) -> None:
        """直插武器槽行（绕过穿戴门槛校验，专注祝福油分支本身）。"""
        async with SessionLocal() as db:
            await db.execute(
                text(
                    "INSERT INTO equipment_slot (character_id, slot, item_instance_id) "
                    "VALUES (:cid, 'weapon', :iid)"
                ),
                {"cid": character_id, "iid": instance_id},
            )
            await db.commit()

    async def db_scalar(self, sql: str, params: dict[str, object]) -> object:
        async with SessionLocal() as db:
            return (
                await db.execute(text(sql), params)
            ).scalar_one_or_none()

    async def backdate_buff_saved_at(self, character_id: int, seconds: int) -> None:
        """把 buff_json.saved_at 回拨 N 秒（模拟离线时长，真实时间过期验证）。"""
        async with SessionLocal() as db:
            await db.execute(
                text(
                    "UPDATE character SET buff_json = jsonb_set(buff_json, "
                    "'{saved_at}', to_jsonb(extract(epoch from now()) - :secs)) "
                    "WHERE id = :cid"
                ),
                {"secs": seconds, "cid": character_id},
            )
            await db.commit()


def uuid_hex() -> str:
    import uuid

    return uuid.uuid4().hex[:12]


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


async def _session_of(character_id: int) -> PlayerSession:
    async with SessionLocal() as db:
        return await get_or_create_player_session(db, character_id)


def make_headless_session(character_id: int) -> PlayerSession:
    """纯内存会话（假地图无图模式，供 tick 推进类断言）。"""
    snap = CharacterSnapshot(
        name=f"t{character_id}",
        class_="warrior",
        level=8,
        exp=0,
        hp=max_hp_for(8),
        mp=max_mp_for(8),
        gold=0,
        map_id="no-such-map",
        bind_city=BIND_CITY,
        bag_capacity=40,
    )
    session = PlayerSession(
        character_id=character_id,
        char_snapshot=snap,
        sse_state=get_or_create_session(character_id),
    )
    register_session(session)
    return session


# --- A. 神水：面板 +N、600 tick 回落、buff_json 持久化 ------------------------------


async def test_divine_water_applies_buff_persists_and_refreshes(
    harness: _Harness,
) -> None:
    """使用神水：session.buffs 挂 600 tick 实例、面板加值 +5、buff_json 落库；

    重复使用 = 刷新（替换旧实例而非叠加）。
    """
    cid = await harness.create_character()
    inst_id = await harness.seed_item(cid, DIVINE_WATER, quantity=2)

    r = await harness.client.post(
        "/api/inventory/use", json={"item_instance_id": inst_id}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "buff_applied"
    assert body["quantity_left"] == 1

    session = await _session_of(cid)
    assert len(session.buffs) == 1
    buff = session.buffs[0]
    assert buff.definition_id == DIVINE_WATER
    assert buff.remaining_ticks == DIVINE_WATER_DURATION_TICKS
    assert not buff.expired
    # 面板贡献：攻击神水 attack_bonus +5（effects.active_stat_bonus 唯一计算点）
    assert active_stat_bonus(session.buffs, "attack_bonus") == 5

    # buff_json 已随事务落库（REST 写路径规则：flush 后内存=DB）
    saved = await harness.db_scalar(
        "SELECT buff_json FROM character WHERE id = :c", {"c": cid}
    )
    assert isinstance(saved, dict)
    records = saved["buffs"]
    assert isinstance(records, list) and len(records) == 1
    assert records[0]["definition_id"] == DIVINE_WATER  # type: ignore[index]
    assert "saved_at" in saved

    # 重复使用：刷新时长（仍单实例），不叠加
    r = await harness.client.post(
        "/api/inventory/use", json={"item_instance_id": inst_id}
    )
    assert r.status_code == 200, r.text
    assert len(session.buffs) == 1
    assert session.buffs[0].remaining_ticks == DIVINE_WATER_DURATION_TICKS


async def test_divine_water_falls_back_after_600_ticks(harness: _Harness) -> None:
    """神水后面板 +N，600 tick 后到期回落：buff 移除 + buff_expire 日志。"""
    cid = await harness.create_character()
    inst_id = await harness.seed_item(cid, DIVINE_WATER, quantity=1)
    r = await harness.client.post(
        "/api/inventory/use", json={"item_instance_id": inst_id}
    )
    assert r.status_code == 200, r.text
    session = await _session_of(cid)

    expire_logs: list[dict[str, object]] = []
    for i in range(1, DIVINE_WATER_DURATION_TICKS + 1):
        events = simulate_tick(session, i)
        for name, data in events:
            if name == "log":
                payload = json.loads(data)
                if payload.get("kind") == "buff_expire":
                    expire_logs.append(payload)

    assert session.buffs == [], "600 tick 后神水 buff 必须到期移除"
    assert active_stat_bonus(session.buffs, "attack_bonus") == 0, "面板加值回落"
    assert len(expire_logs) == 1, "到期时恰好播报一次 buff_expire"


async def test_buff_survives_logout_and_decays_by_realtime(
    harness: _Harness,
) -> None:
    """登出不清空（计划红线）：重登按真实时间折损——离线超时消失、未超时带余量。"""
    cid = await harness.create_character()
    inst_id = await harness.seed_item(cid, DIVINE_WATER, quantity=1)
    r = await harness.client.post(
        "/api/inventory/use", json={"item_instance_id": inst_id}
    )
    assert r.status_code == 200, r.text
    release_session(cid)  # 模拟登出（内存会话销毁，buff_json 留在库）

    # 离线 601s > 600s 时长 → 重登后 buff 已自然过期
    await harness.backdate_buff_saved_at(cid, 601)
    session = await _session_of(cid)
    restored = await hydrate_buffs(session)
    assert restored == 0
    assert session.buffs == []

    # 离线 300s < 600s → 余量约 300 tick 继续走
    inst2 = await harness.seed_item(cid, DIVINE_WATER, quantity=1)
    r = await harness.client.post(
        "/api/inventory/use", json={"item_instance_id": inst2}
    )
    assert r.status_code == 200, r.text
    release_session(cid)
    await harness.backdate_buff_saved_at(cid, 300)
    session = await _session_of(cid)
    restored = await hydrate_buffs(session)
    assert restored == 1
    remaining = session.buffs[0].remaining_ticks
    assert 290 <= remaining <= DIVINE_WATER_DURATION_TICKS - 300


def test_payload_roundtrip_skips_expired_and_corrupt_records() -> None:
    """序列化往返：已过期实例不入快照；残缺记录恢复时丢弃不炸。"""
    from app.combat.effects import EffectInstance, expire_effect
    from app.sim.tick_loop import buffs_to_payload

    live = EffectInstance(
        definition_id="divine_water",
        owner_id="9001",
        remaining_ticks=42,
        params={"attack_bonus": 5},
    )
    dead = expire_effect(
        EffectInstance(
            definition_id="divine_water", owner_id="9001", remaining_ticks=0
        )
    )
    payload = buffs_to_payload([live, dead])
    records = payload["buffs"]
    assert isinstance(records, list) and len(records) == 1

    restored = payload_to_buffs(payload)
    assert len(restored) == 1
    assert restored[0].remaining_ticks <= 42

    corrupt = payload_to_buffs({"buffs": [{"remaining_ticks": "x"}, {"a": 1}], "saved_at": 0})
    assert corrupt == []


# --- B. 祝福油：成功 / 概率失败 / 达上限 --------------------------------------------


async def test_blessing_oil_success_upgrades_weapon_lucky(
    harness: _Harness,
) -> None:
    """幸运 6 武器 + 必成 seed → lucky 7（上限内成功分支），油被消耗。"""
    cid = await harness.create_character()
    weapon_id = await harness.seed_item(cid, "bronze_sword", lucky=6)
    await harness.equip_weapon(cid, weapon_id)
    oil_id = await harness.seed_item(cid, OIL, quantity=1)

    session = await _session_of(cid)
    session.rng = random.Random(1)  # Random(1).random() ≈ 0.134 < 0.6 → 成功
    r = await harness.client.post("/api/inventory/use", json={"item_instance_id": oil_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "bless_success"
    assert body["lucky_after"] == BLESSING_OIL_LUCK_MAX
    assert body["quantity_left"] == 0

    lucky = await harness.db_scalar(
        "SELECT lucky FROM item_instance WHERE id = :i", {"i": weapon_id}
    )
    assert lucky == BLESSING_OIL_LUCK_MAX


async def test_blessing_oil_probability_fail_consumes_without_upgrade(
    harness: _Harness,
) -> None:
    """幸运 6 武器 + 必败 seed → 200 但 bless_failed：油照耗、幸运不升。"""
    cid = await harness.create_character()
    weapon_id = await harness.seed_item(cid, "bronze_sword", lucky=6)
    await harness.equip_weapon(cid, weapon_id)
    oil_id = await harness.seed_item(cid, OIL, quantity=2)

    session = await _session_of(cid)
    session.rng = random.Random(0)  # Random(0).random() ≈ 0.844 ≥ 0.6 → 失败
    r = await harness.client.post("/api/inventory/use", json={"item_instance_id": oil_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "bless_failed"
    assert body["lucky_after"] == 6
    assert body["quantity_left"] == 1

    lucky = await harness.db_scalar(
        "SELECT lucky FROM item_instance WHERE id = :i", {"i": weapon_id}
    )
    assert lucky == 6


async def test_blessing_oil_at_max_lucky_422_and_not_consumed(
    harness: _Harness,
) -> None:
    """幸运已达 +7 上限再用药 → 422 失败提示且不扣油（验收 failure 场景）。"""
    cid = await harness.create_character()
    weapon_id = await harness.seed_item(cid, "bronze_sword", lucky=BLESSING_OIL_LUCK_MAX)
    await harness.equip_weapon(cid, weapon_id)
    oil_id = await harness.seed_item(cid, OIL, quantity=3)

    r = await harness.client.post("/api/inventory/use", json={"item_instance_id": oil_id})
    assert r.status_code == 422, r.text
    assert "上限" in r.json()["detail"]

    qty = await harness.db_scalar(
        "SELECT quantity FROM item_instance WHERE id = :i", {"i": oil_id}
    )
    assert qty == 3, "达上限失败分支不得扣油"


# --- C. 三卷：回城 / 随机 / 地牢逃脱 ------------------------------------------------


async def test_return_scroll_switches_zone_to_bind_city(harness: _Harness) -> None:
    """回城卷：扣卷 + 切 zone_id=bind_city（trigger_return_city 占位接通）。"""
    cid = await harness.create_character(map_id="biqi-wild")
    scroll_id = await harness.seed_item(cid, "return_scroll", quantity=2)

    r = await harness.client.post(
        "/api/inventory/use", json={"item_instance_id": scroll_id}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "returned"
    assert body["map_id"] == BIND_CITY
    assert body["quantity_left"] == 1

    session = await _session_of(cid)
    assert session.char_snapshot.map_id == BIND_CITY
    assert session.zone is not None and session.zone.map_id == BIND_CITY

    map_id = await harness.db_scalar(
        "SELECT map_id FROM character WHERE id = :c", {"c": cid}
    )
    assert map_id == BIND_CITY

    zone_events = [
        json.loads(e.data)
        for e in session.sse_state.buffer.after(0)
        if e.event == "zone_change"
    ]
    assert zone_events and zone_events[-1]["map_id"] == BIND_CITY


async def test_random_scroll_stays_on_map_with_distance_in_band(
    harness: _Harness,
) -> None:
    """随机传送卷：同图随机距离档（MVP 无坐标网格 → 日志播报距离）。"""
    cid = await harness.create_character(map_id="biqi-wild")
    scroll_id = await harness.seed_item(cid, "random_scroll", quantity=1)

    session = await _session_of(cid)
    seed_value = 42
    session.rng = random.Random(seed_value)
    expected_dist = PORTAL_RANDOM_MIN_DIST + int(
        random.Random(seed_value).random()
        * (PORTAL_RANDOM_MAX_DIST - PORTAL_RANDOM_MIN_DIST + 1)
    )

    r = await harness.client.post(
        "/api/inventory/use", json={"item_instance_id": scroll_id}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "teleported"
    assert body["map_id"] == "biqi-wild", "随机卷不出图"
    assert body["quantity_left"] == 0
    assert f"距离约 {expected_dist} 步" in body["message"]
    assert PORTAL_RANDOM_MIN_DIST <= expected_dist <= PORTAL_RANDOM_MAX_DIST


async def test_dungeon_escape_returns_to_nearest_npc_town(
    harness: _Harness,
) -> None:
    """地牢逃脱卷：兽人墓二层 → 就近 NPC 城镇 = 兽人墓一层（guide 所在）。"""
    cid = await harness.create_character(map_id="shouren-gu-2")
    scroll_id = await harness.seed_item(cid, "dungeon_escape_scroll", quantity=1)

    r = await harness.client.post(
        "/api/inventory/use", json={"item_instance_id": scroll_id}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "escaped"
    assert body["map_id"] == "shouren-gu-1"
    assert body["quantity_left"] == 0

    session = await _session_of(cid)
    assert session.char_snapshot.map_id == "shouren-gu-1"
    assert session.zone is not None and session.zone.map_id == "shouren-gu-1"

    map_id = await harness.db_scalar(
        "SELECT map_id FROM character WHERE id = :c", {"c": cid}
    )
    assert map_id == "shouren-gu-1"


async def test_dungeon_escape_falls_back_to_bind_city_when_isolated(
    harness: _Harness,
) -> None:
    """无可达 NPC 城镇（假图/headless）→ 回退绑定城。"""
    cid = await harness.create_character(map_id="no-such-map")
    scroll_id = await harness.seed_item(cid, "dungeon_escape_scroll", quantity=1)

    r = await harness.client.post(
        "/api/inventory/use", json={"item_instance_id": scroll_id}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "escaped"
    assert body["map_id"] == BIND_CITY


# --- D. 药品瞬回 + 校验链 ------------------------------------------------------------


async def test_hp_potion_instant_restore_clamped(harness: _Harness) -> None:
    """金创药（小）瞬回 HP+30 并随事务落库（REST 写路径规则）。"""
    cid = await harness.create_character()
    await harness.set_character(cid, level=8, hp=30, mp=50)
    potion_id = await harness.seed_item(cid, "potion_hp_small", quantity=5)

    r = await harness.client.post(
        "/api/inventory/use", json={"item_instance_id": potion_id}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "restored"
    assert body["hp"] == 60  # 30 + 30（金创药小 hp_restore=30）
    assert body["quantity_left"] == 4

    hp = await harness.db_scalar("SELECT hp FROM character WHERE id = :c", {"c": cid})
    assert hp == 60


async def test_sun_water_restores_both_hp_and_mp(harness: _Harness) -> None:
    """太阳水双恢复瞬回（HP+70/MP+110），钳上限不溢出。"""
    cid = await harness.create_character()
    await harness.set_character(cid, level=8, hp=10, mp=5)
    potion_id = await harness.seed_item(cid, "sun_water", quantity=1)

    r = await harness.client.post(
        "/api/inventory/use", json={"item_instance_id": potion_id}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hp"] == 80  # 10 + 70 ≤ max_hp_for(8)=80
    assert body["mp"] == 50  # 5 + 110 → 钳到 max_mp_for(8)=10+5×8=50


async def test_use_validation_chain(harness: _Harness) -> None:
    """校验链：未知实例 404 / 他人物品 404 / 装备类不可用 422。"""
    owner_cid = await harness.create_character()
    foreign_inst = await harness.seed_item(owner_cid, "sun_water", quantity=1)

    # 换登录态到另一个号（httpx 单 cookie jar：后登录者覆盖会话）
    intruder_cid = await harness.create_character()
    assert intruder_cid != owner_cid

    r = await harness.client.post(
        "/api/inventory/use", json={"item_instance_id": 999999999}
    )
    assert r.status_code == 404, r.text

    r = await harness.client.post(
        "/api/inventory/use", json={"item_instance_id": foreign_inst}
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "物品不存在"

    own_equipment = await harness.seed_item(intruder_cid, "bronze_sword", quantity=1)
    r = await harness.client.post(
        "/api/inventory/use", json={"item_instance_id": own_equipment}
    )
    assert r.status_code == 422, r.text
    assert "无法使用" in r.json()["detail"]


async def test_start_tick_loop_hydrates_buffs_from_db(harness: _Harness) -> None:
    """start_tick_loop 启动时从 buff_json 恢复（挂机入口的持久化接入点）。"""
    cid = await harness.create_character()
    inst_id = await harness.seed_item(cid, DIVINE_WATER, quantity=1)
    r = await harness.client.post(
        "/api/inventory/use", json={"item_instance_id": inst_id}
    )
    assert r.status_code == 200, r.text
    release_session(cid)

    fresh = make_headless_session(cid)
    assert fresh.buffs == []
    _ = await start_tick_loop(fresh, interval=0.01)
    try:
        assert len(fresh.buffs) == 1
        assert fresh.buffs[0].definition_id == DIVINE_WATER
        assert fresh.buffs[0].remaining_ticks <= DIVINE_WATER_DURATION_TICKS
    finally:
        await cancel_tick_loop(fresh)
