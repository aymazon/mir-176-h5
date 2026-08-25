"""技能学习与升级流程测试（todo 28）。

覆盖验收（计划 todo 28）：
- 7 级战士首次攻击自动学会基本剑术（skill_state 行出现，挂机 tick 路径）；
- 使用 N 次升 1 级（exp 累计到 threshold → level 0→1 + SSE skill_levelup）；
- 无书学烈火 → 拒绝（35 级未到 422 等级不足；到 35 级无书仍 422 必须技能书；
  有书走 learn_from_book 成功且书被消耗）；
- 书→技能映射按中文名：book_lightning(雷电术)→thunder_bolt 而非 lightning
  (疾光电影)；未开放技能的书（分身术）422；
- learn_from_book 门禁：职业不匹配 / 等级不足 / 非书物品 / 他人物品；
- 35 级道士召狗 e2e（进程内快速版）：书学召唤神兽 → REST 施放召狗 →
  simulate_tick 驱动狗攻击主人目标 → 击杀经验归主人 → 断线宽限超时 teardown
  解散狗（pet_dispersed）→ 重连新会话 pets 为空。

用 httpx.AsyncClient(ASGITransport) 直连真实 PG（mir_dev），与 test_equip 同一
约定：随机命名避撞、收尾删 player（FK CASCADE）+ shutdown 序 + engine.dispose。
"""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import cast
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api import skill as skill_api
from app.core.config import settings
from app.core.db import SessionLocal, engine
from app.gamedata_loader import load_all
from app.gamedata_schema import MonsterDef, SpawnEntry
from app.main import app
from app.sim.session import (
    get_or_create_player_session,
    max_hp_for,
    max_mp_for,
    register_session,
    release_session,
    session_for,
)
from app.sim.session import reset_sessions as reset_player_sessions
from app.sim.tick_loop import (
    _teardown_on_grace_timeout,
    persist_session_state,
    shutdown_all_sessions,
    simulate_tick,
)
from app.sim.zone import ZoneInstance, shutdown_all
from app.sse.session_state import get_or_create_session
from app.sse.session_state import reset_sessions as reset_sse_sessions


class _Harness:
    """一次用例的账号/角色/物品供给与 DB 断言助手（test_equip 同款）。"""

    def __init__(self, client: AsyncClient) -> None:
        self.client = client
        self.usernames: list[str] = []
        # character_id -> 注册凭据（双角色用例需重登录避开单 cookie jar 覆盖）
        self.creds_by_cid: dict[int, dict[str, str]] = {}

    async def create_character(
        self, class_: str = "warrior", level: int | None = None
    ) -> int:
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
        self.creds_by_cid[character_id] = creds
        if level is not None:
            # API 建号 hp/mp=0：施放类用例直接摆满，防 insufficient_mp 假失败
            await self.set_character(
                character_id, level=level, hp=max_hp_for(level), mp=max_mp_for(level)
            )
        return character_id

    async def switch_to(self, character_id: int) -> None:
        """重登录指定角色的账号（单 cookie jar：后登录者覆盖前者）。"""
        creds = self.creds_by_cid[character_id]
        r = await self.client.post("/api/auth/login", json=creds)
        assert r.status_code == 200, r.text

    async def set_character(self, character_id: int, **fields: object) -> None:
        sets = ", ".join(f"{col} = :{col}" for col in fields)
        async with SessionLocal() as db:
            await db.execute(
                text(f"UPDATE character SET {sets} WHERE id = :cid"),
                {**fields, "cid": character_id},
            )
            await db.commit()

    async def seed_item(self, character_id: int, item_id: str, qty: int = 1) -> int:
        async with SessionLocal() as db:
            row = await db.execute(
                text(
                    "INSERT INTO item_instance (character_id, item_id, quantity) "
                    "VALUES (:cid, :iid, :qty) RETURNING id"
                ),
                {"cid": character_id, "iid": item_id, "qty": qty},
            )
            row_id = int(row.scalar_one())
            await db.commit()
        return row_id

    async def skill_row(
        self, character_id: int, skill_id: str
    ) -> tuple[int, int] | None:
        async with SessionLocal() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT level, exp FROM skill_state "
                        "WHERE character_id = :cid AND skill_id = :sid"
                    ),
                    {"cid": character_id, "sid": skill_id},
                )
            ).first()
        return None if row is None else (int(row.level), int(row.exp))

    async def item_exists(self, item_instance_id: int) -> bool:
        async with SessionLocal() as db:
            row = await db.execute(
                text("SELECT 1 FROM item_instance WHERE id = :iid"),
                {"iid": item_instance_id},
            )
            return row.first() is not None


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
    await shutdown_all_sessions()
    await shutdown_all()
    reset_player_sessions()
    reset_sse_sessions()
    skill_api._rest_last_cast.clear()
    await engine.dispose()


# --- helpers --------------------------------------------------------------------


def _biqi_zone() -> ZoneInstance:
    """真实 gamedata 比奇野外 zone（test_tick_loop 同款手工装配）。"""
    gd = cast("dict[str, object]", load_all())
    spawns = cast("dict[str, SpawnEntry]", gd["spawns"])
    table = {k: v for k, v in spawns.items() if v.map_id == "biqi-wild"}
    zone = ZoneInstance(
        map_id="biqi-wild",
        static_spawn_table=table,
        monster_defs=cast("dict[str, MonsterDef]", gd["monsters"]),
    )
    zone.fill_initial()
    return zone


def _events(character_id: int) -> list[tuple[str, dict[str, object]]]:
    """该角色 SSE buffer 全事件的 (event, payload) 视图（会话注销后仍可读）。"""
    session = session_for(character_id)
    state = (
        session.sse_state
        if session is not None
        else get_or_create_session(character_id)
    )
    return [(event.event, json.loads(event.data)) for event in state.buffer.after(0)]


def _require_session(character_id: int):  # type: ignore[no-untyped-def]
    session = session_for(character_id)
    assert session is not None
    return session


# --- 自动习得（挂机出手路径）------------------------------------------------------


async def test_first_attack_auto_learns_basic_sword(harness: _Harness) -> None:
    """7 级战士首次攻击学会基本剑术：skill_state 行出现（内存 + 落库）。"""
    cid = await harness.create_character(class_="warrior", level=7)

    # 学前清单：basic_sword 在 available 且非书技；learned 为空
    r = await harness.client.get("/api/skill/list")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["learned"] == []
    available = {s["skill_id"]: s for s in body["available"]}
    assert available["basic_sword"]["requires_book"] is False
    assert available["basic_sword"]["learn_level"] == 7

    # 挂机出手：ATTACK_INTERVAL_TICKS=2 → 数 tick 内必触发自动习得
    session = _require_session(cid)
    session.zone = _biqi_zone()
    learned_seen = False
    for n in range(1, 11):
        events = simulate_tick(session, n)
        if any(
            e[0] == "log" and json.loads(e[1]).get("kind") == "skill_learn"
            for e in events
        ):
            learned_seen = True
            break
    assert learned_seen, "10 tick 内未触发首次施放自动习得"
    state = session.skill_states["basic_sword"]
    assert state.level == 0
    assert state.exp == 1  # 首次施放成功即 +1 熟练

    # 每 5 tick persist 批次把内存行 upsert 进 DB
    async with session.lock:
        await persist_session_state(session)
    assert await harness.skill_row(cid, "basic_sword") == (0, 1)


async def test_low_level_or_book_skill_not_auto_learned(harness: _Harness) -> None:
    """未到级不自动学；≥35 级书技（烈火剑法）挂机路径永不自动学。"""
    cid = await harness.create_character(class_="warrior", level=6)
    async with SessionLocal() as db:
        session = await get_or_create_player_session(db, cid)  # 无 API 调用也建会话
    session.zone = _biqi_zone()
    for n in range(1, 11):
        simulate_tick(session, n)
    assert "basic_sword" not in session.skill_states  # 6 级 < learn_level 7
    assert "liehuo" not in session.skill_states  # 书技任何等级都不自动学


# --- 使用次数驱动升级 -------------------------------------------------------------


async def test_use_n_times_levels_up(harness: _Harness) -> None:
    """使用 N 次升 1 级：basic_sword threshold[0]=100 → 第 100 次施放跨级。"""
    cid = await harness.create_character(class_="warrior", level=7)
    r = await harness.client.post("/api/skill/learn", json={"skill_id": "basic_sword"})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "learn"

    leveled_responses = 0
    last_body: dict[str, object] = {}
    for _ in range(100):
        r = await harness.client.post(
            "/api/skill/use", json={"skill_id": "basic_sword"}
        )
        assert r.status_code == 200, r.text
        last_body = r.json()
        assert last_body["success"] is True, last_body
        if last_body["leveled_up"]:
            leveled_responses += 1
    assert leveled_responses == 1, f"应恰好升级一次，实得 {leveled_responses}"
    assert last_body["level_before"] == 0
    assert last_body["level_after"] == 1

    session = _require_session(cid)
    state = session.skill_states["basic_sword"]
    assert state.level == 1
    assert state.exp == 0  # 100 - threshold[0]=100 余量滚存归零

    # SSE：skill_learn（显式习得）+ skill_levelup 各一条
    events = _events(cid)
    learns = [p for e, p in events if e == "skill_learn"]
    ups = [p for e, p in events if e == "skill_levelup"]
    assert len(learns) == 1 and learns[0]["skill_id"] == "basic_sword"
    assert len(ups) == 1 and ups[0]["level"] == 1

    # exp 值在 /use 不落库（DB 仍是习得时的 0/0），persist 批次覆盖写
    assert await harness.skill_row(cid, "basic_sword") == (0, 0)
    async with session.lock:
        await persist_session_state(session)
    assert await harness.skill_row(cid, "basic_sword") == (1, 0)


async def test_use_gates(harness: _Harness) -> None:
    """/use 三道闸人话化：未学（他职业）not_learned；冷却 cooldown。"""
    cid = await harness.create_character(class_="warrior", level=7)
    # 他职业技能无 state 且职业闸拦截自动习得 → not_learned（200 + success=False）
    r = await harness.client.post("/api/skill/use", json={"skill_id": "healing"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is False
    assert body["reason"] == "not_learned"
    assert "healing" not in _require_session(cid).skill_states

    # 未知技能 → 422
    r = await harness.client.post("/api/skill/use", json={"skill_id": "no_such_skill"})
    assert r.status_code == 422, r.text

    # 冷却：taoist healing(cd=10s) 双发验证 REST 墙钟冷却账本（换账号避开同角色账本）
    cid2 = await harness.create_character(class_="taoist", level=7)
    r = await harness.client.post("/api/skill/learn", json={"skill_id": "healing"})
    assert r.status_code == 200, r.text
    r = await harness.client.post("/api/skill/use", json={"skill_id": "healing"})
    assert r.json()["success"] is True, r.text
    r = await harness.client.post("/api/skill/use", json={"skill_id": "healing"})
    assert r.json()["success"] is False, r.text
    assert r.json()["reason"] == "cooldown"
    assert "healing" in _require_session(cid2).skill_states


# --- 烈火剑法：无书拒绝 / 有书习得 --------------------------------------------------


async def test_learn_liehuo_requires_book(harness: _Harness) -> None:
    """无书学烈火 → 拒绝：34 级 422 等级不足；35 级无书 422 必须技能书。"""
    cid = await harness.create_character(class_="warrior", level=34)
    r = await harness.client.post("/api/skill/learn", json={"skill_id": "liehuo"})
    assert r.status_code == 422, r.text
    assert "等级不足" in r.json()["detail"]

    await harness.set_character(cid, level=35)
    r = await harness.client.post("/api/skill/learn", json={"skill_id": "liehuo"})
    assert r.status_code == 422, r.text
    assert "技能书" in r.json()["detail"]
    assert await harness.skill_row(cid, "liehuo") is None

    # 有书：learn_from_book 成功，书消耗、行落库
    book_id = await harness.seed_item(cid, "book_fire_sword")
    r = await harness.client.post(
        "/api/skill/learn_from_book", json={"item_instance_id": book_id}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["skill_id"] == "liehuo"
    assert body["source"] == "book"
    assert await harness.skill_row(cid, "liehuo") == (0, 0)
    assert not await harness.item_exists(book_id), "书未被消耗"

    # 重复习得 → 409（书已删，同实例再试是 404 物品不存在）
    r = await harness.client.post(
        "/api/skill/learn_from_book", json={"item_instance_id": book_id}
    )
    assert r.status_code == 404, r.text


async def test_book_maps_by_skill_name_not_prefix(harness: _Harness) -> None:
    """book_lightning 名为「雷电术（书）」→ thunder_bolt（而非 lightning 疾光电影）。"""
    cid = await harness.create_character(class_="wizard", level=17)
    book_id = await harness.seed_item(cid, "book_lightning")
    r = await harness.client.post(
        "/api/skill/learn_from_book", json={"item_instance_id": book_id}
    )
    assert r.status_code == 200, r.text
    assert r.json()["skill_id"] == "thunder_bolt"

    # 未开放技能的书（分身术不在 32 技能集）→ 422
    cid2 = await harness.create_character(class_="wizard", level=41)
    clone_id = await harness.seed_item(cid2, "book_clone")
    r = await harness.client.post(
        "/api/skill/learn_from_book", json={"item_instance_id": clone_id}
    )
    assert r.status_code == 422, r.text
    assert "尚未开放" in r.json()["detail"]


async def test_learn_from_book_gates(harness: _Harness) -> None:
    """门禁矩阵：职业不匹配 / 等级不足 / 非书物品 / 他人物品 404。"""
    warrior_cid = await harness.create_character(class_="warrior", level=35)
    taoist_cid = await harness.create_character(class_="taoist", level=34)

    # 职业不匹配：道士书给战士（单 cookie jar：先切回战士身份再发请求）
    wrong_class_book = await harness.seed_item(warrior_cid, "book_summon_beast")
    await harness.switch_to(warrior_cid)
    r = await harness.client.post(
        "/api/skill/learn_from_book", json={"item_instance_id": wrong_class_book}
    )
    assert r.status_code == 422, r.text
    assert "职业不匹配" in r.json()["detail"]

    # 等级不足：34 级道士拿自己的书
    low_level_book = await harness.seed_item(taoist_cid, "book_summon_beast")
    await harness.switch_to(taoist_cid)
    r = await harness.client.post(
        "/api/skill/learn_from_book", json={"item_instance_id": low_level_book}
    )
    assert r.status_code == 422, r.text
    assert "等级不足" in r.json()["detail"]

    # 非书物品（切回战士身份）
    potion_id = await harness.seed_item(warrior_cid, "potion_hp_small")
    await harness.switch_to(warrior_cid)
    r = await harness.client.post(
        "/api/skill/learn_from_book", json={"item_instance_id": potion_id}
    )
    assert r.status_code == 422, r.text
    assert "不是技能书" in r.json()["detail"]

    # 他人物品 → 404（防存在性探测口径；道士的书以战士身份访问）
    r = await harness.client.post(
        "/api/skill/learn_from_book", json={"item_instance_id": low_level_book}
    )
    assert r.status_code == 404, r.text


# --- 35 级道士召狗 e2e（进程内快速版）----------------------------------------------


async def test_taoist_dog_e2e(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """书学召唤神兽 → 召狗 → 狗攻击主人目标击杀经验归主人 → 宽限超时狗解散。"""
    monkeypatch.setattr(settings, "SSE_GRACE_PERIOD_SECONDS", 0.05)
    cid = await harness.create_character(class_="taoist", level=35)
    book_id = await harness.seed_item(cid, "book_summon_beast")

    # 1. 技能书习得（35 级技能唯一入口）
    r = await harness.client.post(
        "/api/skill/learn_from_book", json={"item_instance_id": book_id}
    )
    assert r.status_code == 200, r.text
    assert r.json()["skill_id"] == "summon_beast"

    # 2. 上图 + REST 施放：扣蓝 45（185→140）、pet_summon、pets 入列
    session = _require_session(cid)
    session.zone = _biqi_zone()
    mp_before = session.char_snapshot.mp
    r = await harness.client.post("/api/skill/use", json={"skill_id": "summon_beast"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, body
    assert body["reason"] == "ok"
    assert body["mp_after"] == mp_before - 45
    assert body["pet_id"] is not None
    assert len(session.pets) == 1
    dog = session.pets[0]
    assert dog.pet_type.value == "dog"
    assert any(e == "pet_summon" for e, _ in _events(cid))

    # 3. 冷却闸：cd=8s 内立即二连放 → cooldown（墙钟账本）
    r = await harness.client.post("/api/skill/use", json={"skill_id": "summon_beast"})
    assert r.json()["success"] is False, r.text
    assert r.json()["reason"] == "cooldown"

    # 4. tick 驱动：狗攻击主人目标（alive_monsters[0]），击杀经验归主人
    exp_before = session.char_snapshot.exp
    pet_kill_seen = False
    for n in range(1, 121):
        simulate_tick(session, n)
        if any(e == "log" and p.get("kind") == "pet_kill" for e, p in _events(cid)):
            pet_kill_seen = True
            break
    assert pet_kill_seen, "120 tick 内狗未击杀任何怪物"
    assert dog.exp_yield_to_owner > 0
    assert session.char_snapshot.exp > exp_before, "击杀经验未归主人"
    by_pet = [p for e, p in _events(cid) if e == "exp" and p.get("by") == "pet"]
    assert by_pet, "缺少 by=pet 的 exp 事件"

    # 5. 断线宽限超时：teardown 解散狗 + 注销会话（重连后狗不存在）
    session.disconnected = True  # SSE close hook 等价（PlayerSession 断线旗标）
    session.grace_period_started_at = asyncio.get_running_loop().time() - 1.0
    await _teardown_on_grace_timeout(session)
    assert session.pets == [], "宽限超时后狗仍在"
    assert release_session(cid) is None, "会话未注销"
    assert any(e == "pet_dispersed" for e, _ in _events(cid))

    # 6. 重连：全新会话 pets 为空（召唤物不持久化红线）
    async with SessionLocal() as db:
        fresh = await get_or_create_player_session(db, cid)
    assert fresh.pets == []
    register_session(fresh)  # 复位注册表供 fixture 清理路径一致
