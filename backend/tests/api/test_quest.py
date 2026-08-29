"""任务系统 API 全链路测试（todo 29）。

用 httpx.AsyncClient(transport=ASGITransport(app=app)) 直连真实 PG（mir_dev），
与 test_shop 同一约定：随机用户名/角色名避撞唯一约束，fixture 收尾删 player
（FK CASCADE 连带删 character/item_instance/quest_state）并按 todo 15 纪律
先 shutdown 后台 task 再 dispose engine 防连接池跨 loop。

覆盖验收：接任务→杀 20 只骷髅（tick 击杀结算钩子驱动）→交付→金币+经验入账；
命运之刃接取进 fate-blade-arena 弱化分身（70% 属性、boss=False、不掉本体掉落）
→击杀交付获得 fate_blade 武器；收集任务缴纳扣背包（不足 422）；对话任务同图
门禁；法/道技能书引导（profession 泛化 dialog）；门禁矩阵（404/422/409）与
重复交付拒绝。
"""

import json
from collections.abc import AsyncIterator
from typing import cast
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.combat import balance
from app.combat.drops import DropResult
from app.core.db import SessionLocal, engine
from app.gamedata_loader import load_all
from app.gamedata_schema.monsters import MonsterDef
from app.gamedata_schema.spawns import SpawnEntry, SpawnLocation
from app.main import app
from app.sim import tick_loop
from app.sim.quest import arena_map_id, reset_registry, states_of
from app.sim.session import PlayerSession, session_for
from app.sim.tick_loop import _kill_monster, shutdown_all_sessions
from app.sim.zone import ZoneInstance, shutdown_all
from app.sse.session_state import reset_sessions

# 真实数据锚点（gamedata/quests + monsters + items）
SKELETON_BOUNTY = "bounty_skeleton_20"  # L10 杀 20 骷髅，500 金 + 1500 经验
FATE_BLADE_QUEST = "warrior_fate_blade"  # 战士 35 级，奖励 fate_blade
WIZARD_GUIDE = "wizard_ice_roar_guide"  # 法师 35 级引导，奖励 book_ice_roar
COLLECT_EGG = "collect_maggot_egg"  # L12 缴纳蛆卵×5，150 金 + 400 经验
DIALOG_CHIEF = "newbie_meet_chief"  # L1 与村长对话，50 金 + 30 经验


class _Harness:
    """一次用例的账号/角色供给与 DB 断言助手（test_shop 同款扩展）。"""

    def __init__(self, client: AsyncClient) -> None:
        self.client = client
        self.usernames: list[str] = []

    async def create_character(
        self, *, class_: str = "warrior", level: int | None = None
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

    async def seed_stack(self, character_id: int, item_id: str, quantity: int) -> int:
        async with SessionLocal() as db:
            row = await db.execute(
                text(
                    "INSERT INTO item_instance (character_id, item_id, quantity) "
                    "VALUES (:cid, :iid, :qty) RETURNING id"
                ),
                {"cid": character_id, "iid": item_id, "qty": quantity},
            )
            row_id = int(row.scalar_one())
            await db.commit()
        return row_id

    async def item_rows(self, character_id: int, item_id: str) -> list[tuple[int, int]]:
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT id, quantity FROM item_instance "
                        "WHERE character_id = :cid AND item_id = :iid ORDER BY id"
                    ),
                    {"cid": character_id, "iid": item_id},
                )
            ).all()
        return [(int(r[0]), int(r[1])) for r in rows]

    async def char_field(self, character_id: int, col: str) -> int:
        async with SessionLocal() as db:
            value = (
                await db.execute(
                    text(f"SELECT {col} FROM character WHERE id = :cid"),
                    {"cid": character_id},
                )
            ).scalar_one()
        return int(value)

    async def quest_row(self, character_id: int, quest_id: str) -> dict[str, object]:
        async with SessionLocal() as db:
            row = (
                (
                    await db.execute(
                        text(
                            "SELECT status, progress FROM quest_state "
                            "WHERE character_id = :cid AND quest_id = :qid"
                        ),
                        {"cid": character_id, "qid": quest_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row is not None else {}

    def session(self, character_id: int) -> PlayerSession:
        sess = session_for(character_id)
        assert sess is not None, "REST 写路径应已建立 PlayerSession"
        return sess


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
    reset_sessions()
    reset_registry()
    await engine.dispose()


@pytest.fixture(autouse=True)
def _no_drops(monkeypatch: pytest.MonkeyPatch) -> None:
    """掉落归零：金币/物品断言只反映任务奖励与怪物经验（确定性）。"""
    monkeypatch.setattr(
        tick_loop,
        "roll_drop",
        lambda table, items, rng: DropResult(monster_id="test", gold=0, items=()),
    )


def _events(character_id: int, name: str) -> list[dict[str, object]]:
    """该角色 SSE buffer 里指定事件的全部 JSON payload。"""
    sess = session_for(character_id)
    assert sess is not None
    out: list[dict[str, object]] = [
        cast("dict[str, object]", json.loads(event.data))
        for event in sess.sse_state.buffer.after(0)
        if event.event == name
    ]
    return out


def _monster_def(monster_id: str) -> MonsterDef:
    return cast("MonsterDef", load_all()["monsters"][monster_id])


def make_zone(map_id: str, monster_def: MonsterDef, count: int = 1) -> ZoneInstance:
    """测试用 zone rig：单刷怪点 ×count 只怪（respawn 票无人消费，不启动 spawner）。"""
    entry = SpawnEntry(
        map_id=map_id,
        monster_id=monster_def.id,
        density=count,
        respawn_seconds=0,
        count=count,
        locations=[SpawnLocation(x=0, y=0)],
    )
    zone = ZoneInstance(
        map_id=map_id,
        static_spawn_table={f"{map_id}:{monster_def.id}": entry},
        monster_defs={monster_def.id: monster_def},
    )
    zone.fill_initial()
    return zone


def drive_kills(sess: PlayerSession, zone: ZoneInstance, n: int) -> None:
    """走真实击杀结算路径（zone.kill_monster → 经验 → 掉落 → 任务钩子）杀 n 只。

    _kill_monster 是 fire-and-forget（恒返回 None）：以存活数递减为结算证明。
    """
    events: list[tuple[str, str]] = []
    for _ in range(n):
        target = zone.alive_monsters[0]
        before = len(zone.alive_monsters)
        _kill_monster(sess, zone, target, events)
        assert len(zone.alive_monsters) == before - 1, "击杀未生效（实例不在场上）"


# --- 门禁矩阵 --------------------------------------------------------------------


async def test_accept_gates_404_422_409(harness: _Harness) -> None:
    """未知任务 404；等级不足 422；职业不符 422；重复接取 409。"""
    cid = await harness.create_character(level=1)

    r = await harness.client.post(
        "/api/quest/accept", json={"quest_id": "no_such_quest"}
    )
    assert r.status_code == 404, r.text

    # bounty_skeleton_20 需 L10 → 422 等级不足
    r = await harness.client.post(
        "/api/quest/accept", json={"quest_id": SKELETON_BOUNTY}
    )
    assert r.status_code == 422, r.text
    assert "等级不足" in r.json()["detail"]
    assert await harness.quest_row(cid, SKELETON_BOUNTY) == {}  # 未落行

    # 命运之刃限战士：道士 35 级接 → 422 职业不符
    await harness.create_character(class_="taoist", level=35)
    r = await harness.client.post(
        "/api/quest/accept", json={"quest_id": FATE_BLADE_QUEST}
    )
    assert r.status_code == 422, r.text
    assert "职业不符" in r.json()["detail"]

    # 正常接取后重复接取 → 409
    await harness.set_character(cid, level=12)
    r = await harness.client.post(
        "/api/quest/accept", json={"quest_id": SKELETON_BOUNTY}
    )
    assert r.status_code == 200, r.text
    r = await harness.client.post(
        "/api/quest/accept", json={"quest_id": SKELETON_BOUNTY}
    )
    assert r.status_code == 409, r.text
    assert "已接取" in r.json()["detail"]


async def test_progress_and_complete_before_ready(harness: _Harness) -> None:
    """未接取推进 422；未达标交付 422 进度未满。"""
    await harness.create_character(level=12)

    r = await harness.client.post(
        "/api/quest/progress", json={"quest_id": SKELETON_BOUNTY, "action": "kill"}
    )
    assert r.status_code == 422, r.text
    assert "尚未接取" in r.json()["detail"]

    r = await harness.client.post(
        "/api/quest/complete", json={"quest_id": SKELETON_BOUNTY}
    )
    assert r.status_code == 422, r.text

    await harness.client.post("/api/quest/accept", json={"quest_id": SKELETON_BOUNTY})
    r = await harness.client.post(
        "/api/quest/complete", json={"quest_id": SKELETON_BOUNTY}
    )
    assert r.status_code == 422, r.text
    assert "进度未满" in r.json()["detail"]

    # action ↔ 类型映射：击杀悬赏不接受 collect 动作
    r = await harness.client.post(
        "/api/quest/progress",
        json={"quest_id": SKELETON_BOUNTY, "action": "collect"},
    )
    assert r.status_code == 422, r.text


# --- 击杀悬赏主链路（验收场景）----------------------------------------------------


async def test_kill_bounty_full_loop(harness: _Harness) -> None:
    """接任务→杀 20 只骷髅（tick 结算钩子）→交付→金币+经验入账→重复交付 409。"""
    cid = await harness.create_character(level=12)
    gold_before = await harness.char_field(cid, "gold")
    exp_before = await harness.char_field(cid, "exp")

    r = await harness.client.post(
        "/api/quest/accept", json={"quest_id": SKELETON_BOUNTY}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["quest"]["status"] == "in_progress"
    assert body["quest"]["count"] == 0
    row = await harness.quest_row(cid, SKELETON_BOUNTY)
    assert row["status"] == "in_progress"

    # tick 结算钩子驱动：真实 _kill_monster 路径杀 20 只骷髅
    sess = harness.session(cid)
    skeleton = _monster_def("skeleton")
    zone = make_zone("shouren-gu-2", skeleton, count=25)
    sess.zone = zone
    drive_kills(sess, zone, 20)

    mq = states_of(cid)[SKELETON_BOUNTY]
    from app.models.quest_state import QuestStatus

    assert mq.count == 20
    assert mq.status is QuestStatus.completed  # 达标待交付

    # SSE：quest_accept 1 条 + quest_progress ≥20 条（末条 completed）
    assert len(_events(cid, "quest_accept")) == 1
    progresses = _events(cid, "quest_progress")
    assert len(progresses) >= 20
    assert progresses[-1]["status"] == "completed"
    assert progresses[-1]["count"] == 20

    # 交付：奖励入账（内存快照为基数——含 20 杀的怪物经验 140×20=2800）
    r = await harness.client.post(
        "/api/quest/complete", json={"quest_id": SKELETON_BOUNTY}
    )
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["rewards"]["gold"] == 500
    assert done["rewards"]["exp"] == 1500
    assert done["status"] == "delivered"
    assert await harness.char_field(cid, "gold") == gold_before + 500
    # 击杀经验随 balance.EXP_GAIN_RATE 缩放（todo 37 校准总闸）——断言符号化跟随系数。
    kill_exp = 20 * int(skeleton.exp * balance.EXP_GAIN_RATE)
    assert await harness.char_field(cid, "exp") == exp_before + kill_exp + 1500

    # DB 行终态 delivered；重复交付 409
    row = await harness.quest_row(cid, SKELETON_BOUNTY)
    assert row["status"] == "delivered"
    r = await harness.client.post(
        "/api/quest/complete", json={"quest_id": SKELETON_BOUNTY}
    )
    assert r.status_code == 409, r.text
    assert "已交付" in r.json()["detail"]

    completes = _events(cid, "quest_complete")
    assert len(completes) == 1
    rewards = cast("dict[str, object]", completes[0]["rewards"])
    assert rewards["gold"] == 500
    assert completes[0]["status"] == "delivered"


async def test_tick_attack_path_advances_quest(harness: _Harness) -> None:
    """simulate_tick 攻击路径集成：出手→命中→击杀结算→任务钩子推进。"""
    cid = await harness.create_character(level=15)
    await harness.client.post("/api/quest/accept", json={"quest_id": SKELETON_BOUNTY})
    sess = harness.session(cid)
    # 1 HP 无反击靶怪（id 保持 skeleton 以匹配悬赏目标）
    weak = _monster_def("skeleton").model_copy(update={"hp": 1, "attack": 0})
    zone = make_zone("shouren-gu-2", weak, count=3)
    sess.zone = zone

    advanced = False
    for tick_index in range(1, 60):
        tick_loop.simulate_tick(sess, tick_index * balance.ATTACK_INTERVAL_TICKS)
        if states_of(cid)[SKELETON_BOUNTY].count >= 1:
            advanced = True
            break
    assert advanced, "simulate_tick 攻击路径未推进任务进度"


# --- 命运之刃（降级实现：独立竞技场 + 弱化分身）-----------------------------------


async def test_fate_blade_arena_and_weapon_reward(harness: _Harness) -> None:
    """战士 35 级：接取即进 fate-blade-arena；分身 70% 属性；交付得 fate_blade。"""
    cid = await harness.create_character(level=35)
    exp_before = await harness.char_field(cid, "exp")

    r = await harness.client.post(
        "/api/quest/accept", json={"quest_id": FATE_BLADE_QUEST}
    )
    assert r.status_code == 200, r.text
    assert r.json()["map_id"] == "fate-blade-arena"

    sess = harness.session(cid)
    assert sess.zone is not None
    assert sess.zone.map_id == "fate-blade-arena"
    assert len(sess.zone.alive_monsters) == 1
    clone = sess.zone.alive_monsters[0].monster_def
    origin = _monster_def("wild_boar_white")
    assert clone.id == "wild_boar_white_weak_clone"
    assert clone.name == "弱化的白野猪"
    assert clone.hp == int(origin.hp * 0.7)  # 2000 → 1400
    assert clone.attack == int(origin.attack * 0.7)  # 55 → 38
    assert clone.boss is False  # 分身不写 boss_respawn
    assert clone.respawn_seconds is None

    # 竞技场外杀本体不计入（boss_instance 任务只在竞技场认分身）
    outside = make_zone("shimen", origin, count=1)
    sess.zone = outside
    drive_kills(sess, outside, 1)
    assert states_of(cid)[FATE_BLADE_QUEST].count == 0

    # 回到竞技场杀分身 → 达标
    from app.gamedata_schema.quests import QuestDef
    from app.sim.quest import enter_arena

    fate_def = cast("dict[str, QuestDef]", load_all()["quests"])[FATE_BLADE_QUEST]
    await enter_arena(sess, fate_def)
    assert sess.zone is not None and sess.zone.map_id == "fate-blade-arena"
    drive_kills(sess, sess.zone, 1)
    from app.models.quest_state import QuestStatus

    assert states_of(cid)[FATE_BLADE_QUEST].status is QuestStatus.completed

    # 分身 id 不在掉落表 → 不掉本体掉落（weak_clone 契约）
    # 经验构成：野外本体 3600 + 分身 2520（70%）已入快照；交付再发 20000
    r = await harness.client.post(
        "/api/quest/complete", json={"quest_id": FATE_BLADE_QUEST}
    )
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["rewards"]["items"] == [
        {"item_id": "fate_blade", "name": "命运之刃", "quantity": 1}
    ]
    assert done["rewards"]["exp"] == 20000
    assert await harness.item_rows(cid, "fate_blade") != []
    assert await harness.char_field(cid, "exp") == (
        exp_before
        # 两路击杀经验均随 balance.EXP_GAIN_RATE 缩放（todo 37），符号化跟随
        + int(origin.exp * balance.EXP_GAIN_RATE)
        + int(clone.exp * balance.EXP_GAIN_RATE)
        + done["rewards"]["exp"]
    )

    # 交付后离开竞技场回绑定城，竞技场实例销毁
    assert sess.zone is not None
    assert sess.zone.map_id == "yin-xing-cun"
    from app.sim import zone as zone_mod

    assert zone_mod.get_zone(arena_map_id(fate_def)) is None


# --- 收集 / 对话 / 职业引导 --------------------------------------------------------


async def test_collect_quest_deducts_bag(harness: _Harness) -> None:
    """收集任务：物品不足 422；足量缴纳扣背包并达标；交付发奖。"""
    cid = await harness.create_character(level=12)
    await harness.seed_stack(cid, "maggot_egg", 3)
    gold_before = await harness.char_field(cid, "gold")

    r = await harness.client.post("/api/quest/accept", json={"quest_id": COLLECT_EGG})
    assert r.status_code == 200, r.text

    # 只有 3 枚 < 5 → 422 物品不足
    r = await harness.client.post(
        "/api/quest/progress", json={"quest_id": COLLECT_EGG, "action": "collect"}
    )
    assert r.status_code == 422, r.text
    assert "物品不足" in r.json()["detail"]
    assert len(await harness.item_rows(cid, "maggot_egg")) == 1  # 未扣

    # 补足 5 枚（UPDATE 既有行——partial unique 索引禁同 item 双行 quantity>1）
    async with SessionLocal() as db:
        await db.execute(
            text(
                "UPDATE item_instance SET quantity = 5 "
                "WHERE character_id = :cid AND item_id = 'maggot_egg'"
            ),
            {"cid": cid},
        )
        await db.commit()
    r = await harness.client.post(
        "/api/quest/progress", json={"quest_id": COLLECT_EGG, "action": "collect"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"
    assert await harness.item_rows(cid, "maggot_egg") == []  # 归零删行

    r = await harness.client.post("/api/quest/complete", json={"quest_id": COLLECT_EGG})
    assert r.status_code == 200, r.text
    assert await harness.char_field(cid, "gold") == gold_before + 150


async def test_dialog_quest_same_map_gate(harness: _Harness) -> None:
    """对话任务：不在 NPC 同图 422；同图对话达标；交付发奖。"""
    cid = await harness.create_character(level=5)  # 出生图 yin-xing-cun（村长所在）

    r = await harness.client.post("/api/quest/accept", json={"quest_id": DIALOG_CHIEF})
    assert r.status_code == 200, r.text

    # 跑去比奇野外 → 422 不同图
    await harness.set_character(cid, map_id="biqi-wild")
    sess = harness.session(cid)
    await sess.flush_snapshot()  # SQL 改图后对齐内存快照
    r = await harness.client.post(
        "/api/quest/progress", json={"quest_id": DIALOG_CHIEF, "action": "dialog"}
    )
    assert r.status_code == 422, r.text
    assert "同图" in r.json()["detail"]

    # 回村对话 → 达标 → 交付
    await harness.set_character(cid, map_id="yin-xing-cun")
    await sess.flush_snapshot()
    r = await harness.client.post(
        "/api/quest/progress", json={"quest_id": DIALOG_CHIEF, "action": "dialog"}
    )
    assert r.status_code == 200, r.text
    r = await harness.client.post("/api/quest/complete", json={"quest_id": DIALOG_CHIEF})
    assert r.status_code == 200, r.text
    assert r.json()["rewards"]["gold"] == 50


async def test_wizard_guide_book_reward(harness: _Harness) -> None:
    """法师 35 级引导（profession）：任意 NPC 同图对话达标；交付得 book_ice_roar。"""
    cid = await harness.create_character(class_="wizard", level=35)

    r = await harness.client.post("/api/quest/accept", json={"quest_id": WIZARD_GUIDE})
    assert r.status_code == 200, r.text

    # profession 不接受 kill 动作
    r = await harness.client.post(
        "/api/quest/progress", json={"quest_id": WIZARD_GUIDE, "action": "kill"}
    )
    assert r.status_code == 422, r.text

    # yin-xing-cun 有 healer_npc 等 NPC → dialog 达标
    r = await harness.client.post(
        "/api/quest/progress", json={"quest_id": WIZARD_GUIDE, "action": "dialog"}
    )
    assert r.status_code == 200, r.text
    r = await harness.client.post("/api/quest/complete", json={"quest_id": WIZARD_GUIDE})
    assert r.status_code == 200, r.text
    assert r.json()["rewards"]["items"][0]["item_id"] == "book_ice_roar"
    assert await harness.item_rows(cid, "book_ice_roar") != []


# --- list 分组 -------------------------------------------------------------------


async def test_quest_list_groups_by_status(harness: _Harness) -> None:
    """list 按四态分组：全量任务默认未接；接取后移入进行组。"""
    await harness.create_character(level=35)

    r = await harness.client.get("/api/quest/list")
    assert r.status_code == 200, r.text
    groups = r.json()
    all_ids = {
        q["quest_id"]
        for group in groups.values()
        for q in group
    }
    assert len(all_ids) == 13  # gamedata/quests 全量
    assert len(groups["not_started"]) == 13
    assert groups["in_progress"] == []
    assert groups["delivered"] == []

    await harness.client.post("/api/quest/accept", json={"quest_id": FATE_BLADE_QUEST})
    r = await harness.client.get("/api/quest/list")
    groups = r.json()
    assert [q["quest_id"] for q in groups["in_progress"]] == [FATE_BLADE_QUEST]
    assert len(groups["not_started"]) == 12
    active = groups["in_progress"][0]
    assert active["arena_map_id"] == "fate-blade-arena"
    assert active["required_class"] == "warrior"
