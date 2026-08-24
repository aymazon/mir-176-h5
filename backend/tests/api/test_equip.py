"""装备穿戴/卸下 API 全链路测试（todo 23）。

用 httpx.AsyncClient(transport=ASGITransport(app=app)) 直连真实 PG（mir_dev），
与 test_auth 同一约定：随机用户名/角色名避撞唯一约束，fixture 收尾删 player
（FK CASCADE 连带删 character/equipment_slot/item_instance）并 dispose engine
防连接池跨 event loop 复用。

覆盖验收：正常穿戴/卸下、槽位互斥（左手镯不冲右、同实例不可占两槽、旧装回背包）、
等级/腕力/职业门槛 422、物品不存在 404（含他人装备）、非法槽位 422、
空槽卸下 409、穿戴后面板 dc 上升、SSE stat 事件、flush_snapshot 方向 DB→内存、
锁临界区 force-inject 序列化（commit 后 flush 前让出，intruder 无法插入）。
"""

import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api import inventory as inv
from app.combat.stats import ClassKey, compute_panel
from app.core.db import SessionLocal, engine
from app.gamedata_schema.items import ItemKind
from app.main import app
from app.sim.session import (
    PlayerSession,
    get_or_create_player_session,
    register_session,
    release_session,
    session_for,
)
from app.sim.tick_loop import shutdown_all_sessions
from app.sim.zone import shutdown_all
from app.sse.session_state import get_or_create_session, reset_sessions


class _Harness:
    """一次用例的账号/角色/物品供给与 DB 断言助手。"""

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

    async def seed_item(self, character_id: int, item_id: str) -> int:
        """塞一件装备进背包（quantity=1 独立实例行），返回 item_instance.id。"""
        async with SessionLocal() as db:
            row = await db.execute(
                text(
                    "INSERT INTO item_instance (character_id, item_id, quantity) "
                    "VALUES (:cid, :iid, 1) RETURNING id"
                ),
                {"cid": character_id, "iid": item_id},
            )
            row_id = int(row.scalar_one())
            await db.commit()
        return row_id

    async def slot_ref(self, character_id: int, slot: str) -> int | None:
        """该槽位当前引用的 item_instance_id；无行或 NULL 均为 None。"""
        async with SessionLocal() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT item_instance_id FROM equipment_slot "
                        "WHERE character_id = :cid AND slot = :slot"
                    ),
                    {"cid": character_id, "slot": slot},
                )
            ).first()
        return None if row is None else row[0]

    async def item_owner(self, item_instance_id: int) -> int | None:
        async with SessionLocal() as db:
            return (
                await db.execute(
                    text("SELECT character_id FROM item_instance WHERE id = :iid"),
                    {"iid": item_instance_id},
                )
            ).scalar_one_or_none()


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
    await engine.dispose()


def _stat_events(character_id: int) -> list[dict[str, object]]:
    """该角色 SSE buffer 里全部 stat 事件的 JSON payload。"""
    session = session_for(character_id)
    assert session is not None, "装备写路径应已建立 PlayerSession"
    return [
        json.loads(event.data)
        for event in session.sse_state.buffer.after(0)
        if event.event == "stat"
    ]


async def test_equip_success_panel_dc_rises(harness: _Harness) -> None:
    """正常穿戴：22 级战士穿修罗（need_level=22, dc_max+20）→ 面板 dc 上升。"""
    cid = await harness.create_character(level=22)
    iid = await harness.seed_item(cid, "xiuluo_blade")

    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": iid, "slot": "weapon"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slot"] == "weapon"
    assert body["item_instance_id"] == iid
    assert body["item_id"] == "xiuluo_blade"
    assert body["replaced_item_instance_id"] is None

    bare = compute_panel(char_class=ClassKey.warrior, level=22)
    assert body["panel"]["dc_max"] == bare.dc_max + 20
    assert body["panel"]["dc_max"] > bare.dc_max

    # DB 落位：weapon 槽引用该实例
    assert await harness.slot_ref(cid, "weapon") == iid

    # SSE stat 事件：reason=equip，携带即时重算面板
    events = _stat_events(cid)
    assert len(events) == 1
    assert events[0]["reason"] == "equip"
    equip_panel = cast("dict[str, int]", events[0]["panel"])
    assert equip_panel["dc_max"] == bare.dc_max + 20


async def test_equip_level_gate_then_success_after_levelup(harness: _Harness) -> None:
    """1 级穿修罗 → 422 等级不足；升到 22 级后再穿 → 200。"""
    cid = await harness.create_character()
    iid = await harness.seed_item(cid, "xiuluo_blade")

    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": iid, "slot": "weapon"}
    )
    assert r.status_code == 422, r.text
    assert "等级不足" in r.json()["detail"]
    assert await harness.slot_ref(cid, "weapon") is None

    await harness.set_character(cid, level=22)
    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": iid, "slot": "weapon"}
    )
    assert r.status_code == 200, r.text
    assert await harness.slot_ref(cid, "weapon") == iid


async def test_equip_strength_insufficient_422(harness: _Harness) -> None:
    """黑铁头盔 need_strength=46；22 级战士面板腕力 20 → 422。"""
    cid = await harness.create_character(level=22)
    iid = await harness.seed_item(cid, "black_iron_helmet")

    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": iid, "slot": "helmet"}
    )
    assert r.status_code == 422, r.text
    assert "腕力不足" in r.json()["detail"]
    assert await harness.slot_ref(cid, "helmet") is None


async def test_equip_class_restriction_422(harness: _Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """职业限制：ItemDef 暂无 class 字段，monkeypatch 目录注入 class_=wizard 验证检查位。"""
    cid = await harness.create_character(level=30)
    iid = await harness.seed_item(cid, "bronze_sword")

    fake_wizard_only = SimpleNamespace(
        id="bronze_sword",
        kind=ItemKind.weapon,
        need_level=0,
        need_strength=0,
        need_magic=0,
        need_taoism=0,
        stats={"dc_min": 3, "dc_max": 7},
        class_="wizard",
    )
    monkeypatch.setattr(inv, "_items", lambda: {"bronze_sword": fake_wizard_only})
    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": iid, "slot": "weapon"}
    )
    assert r.status_code == 422, r.text
    assert "职业不匹配" in r.json()["detail"]

    fake_warrior = SimpleNamespace(**{**vars(fake_wizard_only), "class_": "warrior"})
    monkeypatch.setattr(inv, "_items", lambda: {"bronze_sword": fake_warrior})
    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": iid, "slot": "weapon"}
    )
    assert r.status_code == 200, r.text


async def test_equip_item_not_found_404(harness: _Harness) -> None:
    """不存在的实例 → 404；他人实例同样 404（不泄露存在性，不允许穿他人装备）。"""
    # 先建他人号并塞物品（每次建号会登录新账号、覆盖 cookie jar）
    other_cid = await harness.create_character(level=10)
    other_iid = await harness.seed_item(other_cid, "bronze_sword")
    # 再建本号——此后请求均以本号身份发出
    _ = await harness.create_character(level=10)
    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": 999_999_999, "slot": "weapon"}
    )
    assert r.status_code == 404, r.text

    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": other_iid, "slot": "weapon"}
    )
    assert r.status_code == 404, r.text


async def test_equip_invalid_slot_422(harness: _Harness) -> None:
    """槽位不在 10 槽枚举内 → pydantic 422。"""
    cid = await harness.create_character(level=10)
    iid = await harness.seed_item(cid, "bronze_sword")
    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": iid, "slot": "cape"}
    )
    assert r.status_code == 422, r.text


async def test_equip_kind_slot_mismatch_422(harness: _Harness) -> None:
    """武器塞头盔槽 → 422（kind↔slot 固定映射）。"""
    cid = await harness.create_character(level=10)
    iid = await harness.seed_item(cid, "bronze_sword")
    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": iid, "slot": "helmet"}
    )
    assert r.status_code == 422, r.text
    assert "不能穿戴到槽位" in r.json()["detail"]


async def test_bracelet_slots_independent_and_replace(harness: _Harness) -> None:
    """槽位互斥：左手镯不冲右；同槽换新旧装回背包；同一实例不可占两槽。"""
    cid = await harness.create_character(level=9)
    a = await harness.seed_item(cid, "big_bracelet")
    b = await harness.seed_item(cid, "big_bracelet")
    c = await harness.seed_item(cid, "big_bracelet")

    # A → 左手镯
    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": a, "slot": "bracelet_l"}
    )
    assert r.status_code == 200, r.text
    # B → 右手镯：不冲左
    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": b, "slot": "bracelet_r"}
    )
    assert r.status_code == 200, r.text
    assert await harness.slot_ref(cid, "bracelet_l") == a
    assert await harness.slot_ref(cid, "bracelet_r") == b

    # 同一实例不可占两槽：A 已在左手镯，再戴 → 409
    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": a, "slot": "bracelet_l"}
    )
    assert r.status_code == 409, r.text

    # C → 左手镯：A 被换下回背包，右手镯 B 不动
    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": c, "slot": "bracelet_l"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["replaced_item_instance_id"] == a
    assert await harness.slot_ref(cid, "bracelet_l") == c
    assert await harness.slot_ref(cid, "bracelet_r") == b
    # A 仍在背包（实例行归属未变）
    assert await harness.item_owner(a) == cid


async def test_unequip_success_and_empty_409(harness: _Harness) -> None:
    """卸下：200 + 槽位清空 + 实例留背包 + 面板回落 + SSE stat；空槽卸下 → 409。"""
    cid = await harness.create_character(level=8)
    iid = await harness.seed_item(cid, "bronze_sword")

    # 空槽卸下 → 409
    r = await harness.client.post("/api/inventory/unequip", json={"slot": "weapon"})
    assert r.status_code == 409, r.text

    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": iid, "slot": "weapon"}
    )
    assert r.status_code == 200, r.text
    bare = compute_panel(char_class=ClassKey.warrior, level=8)
    assert r.json()["panel"]["dc_max"] == bare.dc_max + 7

    r = await harness.client.post("/api/inventory/unequip", json={"slot": "weapon"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slot"] == "weapon"
    assert body["item_instance_id"] == iid
    # 面板回落到裸装基线
    assert body["panel"]["dc_max"] == bare.dc_max
    assert body["panel"]["dc_min"] == bare.dc_min

    # 槽位已清、实例仍在背包
    assert await harness.slot_ref(cid, "weapon") is None
    assert await harness.item_owner(iid) == cid

    # 卸下后再卸 → 409
    r = await harness.client.post("/api/inventory/unequip", json={"slot": "weapon"})
    assert r.status_code == 409, r.text

    # SSE：equip + unequip 各一条 stat 事件
    events = _stat_events(cid)
    assert [e["reason"] for e in events] == ["equip", "unequip"]
    unequip_panel = cast("dict[str, int]", events[-1]["panel"])
    assert unequip_panel["dc_max"] == bare.dc_max


async def test_unequip_invalid_slot_422(harness: _Harness) -> None:
    _ = await harness.create_character(level=8)
    r = await harness.client.post("/api/inventory/unequip", json={"slot": "cape"})
    assert r.status_code == 422, r.text


async def test_no_cookie_401(harness: _Harness) -> None:
    harness.client.cookies.clear()
    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": 1, "slot": "weapon"}
    )
    assert r.status_code == 401, r.text
    r = await harness.client.post("/api/inventory/unequip", json={"slot": "weapon"})
    assert r.status_code == 401, r.text


async def test_flush_snapshot_direction_db_to_memory(harness: _Harness) -> None:
    """flush 方向验证：外挂 SQL 改 gold 后，equip 的锁内 flush 把 DB 值带进内存快照。"""
    cid = await harness.create_character(level=8)
    iid = await harness.seed_item(cid, "bronze_sword")

    # 先发一次必败请求建立 PlayerSession（物品不存在 → 404，但会话已注册）
    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": 999_999_999, "slot": "weapon"}
    )
    assert r.status_code == 404, r.text
    session = session_for(cid)
    assert session is not None
    assert session.char_snapshot.gold == 0

    # 绕过会话直接改库
    await harness.set_character(cid, gold=777)

    r = await harness.client.post(
        "/api/inventory/equip", json={"item_instance_id": iid, "slot": "weapon"}
    )
    assert r.status_code == 200, r.text
    # flush_snapshot 后内存 = DB 最新值（方向 DB→内存）
    assert session.char_snapshot.gold == 777


async def test_lock_serializes_commit_flush_window(harness: _Harness) -> None:
    """force-inject：equip 在 commit 后 flush 前让出事件循环，

    并发写方必须等整个临界区（含 flush）结束才能取锁——DB 不出现中间覆盖态。
    """
    cid = await harness.create_character(level=8)
    await harness.set_character(cid, hp=80)
    iid = await harness.seed_item(cid, "bronze_sword")
    order: list[str] = []
    iid = await harness.seed_item(cid, "bronze_sword")
    order: list[str] = []

    class InjectingSession(PlayerSession):
        """flush_snapshot 前强制让出 3 个调度槽——若锁没覆盖窗口，intruder 会插队。"""

        async def flush_snapshot(self) -> None:
            order.append("equip:flush-start")
            for _ in range(3):
                await asyncio.sleep(0)
            await super().flush_snapshot()
            order.append("equip:flush-end")

    async with SessionLocal() as db:
        loaded = await get_or_create_player_session(db, cid)
    release_session(cid)
    session = InjectingSession(
        character_id=cid,
        char_snapshot=loaded.char_snapshot,
        inventory=list(loaded.inventory),
        skill_states=dict(loaded.skill_states),
        sse_state=get_or_create_session(cid),
    )
    register_session(session)

    equip_task = asyncio.create_task(
        harness.client.post(
            "/api/inventory/equip", json={"item_instance_id": iid, "slot": "weapon"}
        )
    )
    # 等 equip 进入 commit→flush 窗口（flush-start 已追加）后再派 intruder；
    # sleep(0) 不推进墙钟，DB 往返需真实毫秒（task 15 同款教训）。
    for _ in range(2000):
        if "equip:flush-start" in order:
            break
        await asyncio.sleep(0.001)
    assert "equip:flush-start" in order, "equip 未进入 flush 窗口（前置校验失败？）"
    for _ in range(1000):
        if "equip:flush-start" in order:
            break
        await asyncio.sleep(0)
    assert "equip:flush-start" in order, "equip 未进入 flush 窗口（前置校验失败？）"

    async def intruder() -> None:
        order.append("intruder:want-lock")
        async with session.lock:
            order.append("intruder:tx-start")
            async with SessionLocal() as db:
                await db.execute(
                    text("UPDATE character SET hp = 222 WHERE id = :cid"), {"cid": cid}
                )
                await db.commit()

    intruder_task = asyncio.create_task(intruder())
    equip_resp, _ = await asyncio.gather(equip_task, intruder_task)

    assert equip_resp.status_code == 200, equip_resp.text
    # 关键断言：intruder 的事务严格排在 equip 的 flush-end 之后——锁覆盖全窗口
    assert order.index("intruder:tx-start") > order.index("equip:flush-end")
    assert order.index("intruder:want-lock") < order.index("equip:flush-end")
    # 最终 DB = 后写者（intruder）的值
    async with SessionLocal() as db:
        hp = (
            await db.execute(
                text("SELECT hp FROM character WHERE id = :cid"), {"cid": cid}
            )
        ).scalar_one()
    assert hp == 222
