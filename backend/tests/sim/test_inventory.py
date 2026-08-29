"""背包系统测试（todo 25）：堆叠合并 / 容量约束 / 拾取 / 自动回城触发器。

覆盖验收（计划 todo 25）：
- 买 20 瓶中药（金创药·中量）quantity=20 单行而非 20 行（堆叠合并，拾取路径）；
- 背包满时掉落装备保留地面实例并日志提示（bag_full 日志 + 地面池保留）；
- 空格 < BAG_FREE_SLOTS_RECALL 触发自动回城：断言 recall 触发且 zone 切到 bind_city
  （trigger_return_city 占位实现：直读 bind_city 切 zone_id + UPDATE character.map_id）。

纯内存用例直接驱动 tick_loop._apply_drop/_pickup_item/simulate_tick（test_durability
同款）；DB/API 用例直连真实 PG（mir_dev），随机命名避撞、收尾删 player（FK CASCADE）
并 dispose engine 防跨 event loop 复用。
"""

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.combat.balance import BAG_FREE_SLOTS_RECALL
from app.combat.drops import DroppedItem, DropResult
from app.core.db import SessionLocal, engine
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
)
from app.sim.tick_loop import (
    _GROUND_DROPS,
    _apply_drop,
    cancel_tick_loop,
    ground_drops,
    retain_ground_drop,
    simulate_tick,
    start_tick_loop,
    trigger_return_city,
)
from app.sim.zone import shutdown_all
from app.sse.session_state import get_or_create_session
from app.sse.session_state import reset_sessions as reset_sse

BIND_CITY = "yin-xing-cun"
MID_HP_POTION = "potion_hp_medium"  # 止血草药（中）（旧名金创药·中量），stackable=True

# --- 公共脚手架 ------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _clean_runtime() -> AsyncIterator[None]:
    """会话/zone/装备槽缓存/地面掉落池隔离 + engine dispose（防跨 loop 复用）。"""
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


_next_id = [30000]


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


def fill_bag(session: PlayerSession, *, free_slots: int) -> None:
    """把内存背包填到只剩 free_slots 个空格（假 id 行，不碰 DB）。"""
    cap = session.char_snapshot.bag_capacity
    for _ in range(cap - free_slots):
        row = ItemInstance(
            character_id=session.character_id, item_id="potion_hp_small", quantity=1
        )
        row.id = next_id()
        session.inventory.append(row)


def logs_of_kind(events: list[tuple[str, str]], kind: str) -> list[dict[str, object]]:
    out = []
    for name, data in events:
        if name != "log":
            continue
        payload = json.loads(data)
        if payload.get("kind") == kind:
            out.append(payload)
    return out


# --- A. 堆叠合并：20 瓶中药单行（真实 PG + 拾取 API 全链路）-------------------------


class _Harness:
    """账号/角色/物品供给与 DB 断言助手（test_durability 同款裁剪）。"""

    def __init__(self, client: AsyncClient) -> None:
        self.client = client
        self.usernames: list[str] = []

    async def create_character(self) -> int:
        creds = {"username": f"u_{uuid4().hex[:12]}", "password": "p@ssw0rd"}
        r = await self.client.post("/api/auth/register", json=creds)
        assert r.status_code == 201, r.text
        r = await self.client.post("/api/auth/login", json=creds)
        assert r.status_code == 200, r.text
        r = await self.client.post(
            "/api/character", json={"name": f"c_{uuid4().hex[:10]}", "class": "warrior"}
        )
        assert r.status_code == 201, r.text
        cid = int(r.json()["id"])
        self.usernames.append(creds["username"])
        return cid

    async def set_character(self, character_id: int, **fields: object) -> None:
        sets = ", ".join(f"{col} = :{col}" for col in fields)
        async with SessionLocal() as db:
            await db.execute(
                text(f"UPDATE character SET {sets} WHERE id = :cid"),
                {**fields, "cid": character_id},
            )
            await db.commit()

    async def seed_item(self, character_id: int, item_id: str, *, quantity: int = 1) -> None:
        async with SessionLocal() as db:
            await db.execute(
                text(
                    "INSERT INTO item_instance (character_id, item_id, quantity) "
                    "VALUES (:cid, :iid, :qty)"
                ),
                {"cid": character_id, "iid": item_id, "qty": quantity},
            )
            await db.commit()


@pytest.fixture
async def harness() -> AsyncIterator[_Harness]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        h = _Harness(c)
        yield h
    async with SessionLocal() as db:
        for username in h.usernames:
            await db.execute(text("DELETE FROM player WHERE username = :u"), {"u": username})
        await db.commit()
    from app.sim.tick_loop import shutdown_all_sessions

    await shutdown_all_sessions()
    await shutdown_all()


async def test_pick_merges_stack_into_single_row_20(harness: _Harness) -> None:
    """买 20 瓶中药语义：既有 12 瓶堆叠 + 拾取 8 瓶 → 单行 quantity=20 而非 20 行。"""
    cid = await harness.create_character()
    await harness.seed_item(cid, MID_HP_POTION, quantity=12)
    drop = retain_ground_drop(cid, MID_HP_POTION, 8)

    r = await harness.client.post(
        "/api/inventory/pick", json={"item_drop_id": drop.drop_id}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["item_id"] == MID_HP_POTION
    assert body["quantity"] == 20
    assert body["name"] == "止血草药（中）"

    async with SessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT quantity FROM item_instance "
                    "WHERE character_id = :c AND item_id = :i"
                ),
                {"c": cid, "i": MID_HP_POTION},
            )
        ).all()
    assert len(rows) == 1, f"应合并为单行，实际 {len(rows)} 行"
    assert rows[0].quantity == 20


async def test_bag_endpoint_shape(harness: _Harness) -> None:
    """GET /api/inventory/bag：{items, gold, capacity, used} 且 used=行数。"""
    cid = await harness.create_character()
    await harness.seed_item(cid, MID_HP_POTION, quantity=12)
    await harness.set_character(cid, gold=777)

    r = await harness.client.get("/api/inventory/bag")
    assert r.status_code == 200, r.text
    bag = r.json()
    assert bag["gold"] == 777
    assert bag["capacity"] == 40
    assert bag["used"] == 1
    entry = next(i for i in bag["items"] if i["item_id"] == MID_HP_POTION)
    assert entry["quantity"] == 12
    assert entry["name"] == "止血草药（中）"
    assert entry["kind"] == "potion"


async def test_pick_by_instance_id_form(harness: _Harness) -> None:
    """item_instance_id 表单：按地面条目预留的实例行 id 定位（todo 26/30 接口位）。"""
    cid = await harness.create_character()
    drop = retain_ground_drop(cid, MID_HP_POTION, 3, instance_id=987654)
    r = await harness.client.post("/api/inventory/pick", json={"item_instance_id": 987654})
    assert r.status_code == 200, r.text
    assert r.json()["item_drop_id"] == drop.drop_id
    assert r.json()["quantity"] == 3


async def test_pick_unknown_drop_404_and_empty_body_422(harness: _Harness) -> None:
    """未知掉落 id → 404；两个标识都缺省 → 422。"""
    await harness.create_character()
    r = await harness.client.post(
        "/api/inventory/pick", json={"item_drop_id": "g_no_such_drop"}
    )
    assert r.status_code == 404, r.text
    r = await harness.client.post("/api/inventory/pick", json={})
    assert r.status_code == 422, r.text


# --- B. 容量约束：装满后拾取失败 422（stackable 合并豁免）---------------------------


async def test_pick_full_bag_new_item_422_and_retained(harness: _Harness) -> None:
    """背包满再拾新物 → 422 不入袋；地面条目保留（可清理后再拾）。"""
    cid = await harness.create_character()
    await harness.set_character(cid, bag_capacity=2)
    await harness.seed_item(cid, "bronze_sword")
    await harness.seed_item(cid, "light_armor")
    drop = retain_ground_drop(cid, "potion_mp_medium", 3)

    r = await harness.client.post(
        "/api/inventory/pick", json={"item_drop_id": drop.drop_id}
    )
    assert r.status_code == 422, r.text
    assert "背包已满" in r.json()["detail"]
    # 失败不出池：地面实例仍在，可清背包后再拾
    assert len(ground_drops(cid)) == 1

    async with SessionLocal() as db:
        count = (
            await db.execute(
                text("SELECT count(*) FROM item_instance WHERE character_id = :c"),
                {"c": cid},
            )
        ).scalar_one()
    assert count == 2, "422 后不得写入任何新行"


async def test_pick_stackable_merge_allowed_when_full(harness: _Harness) -> None:
    """背包满但 stackable 可合并既有行 → 不占新格，拾取成功（容量豁免规则）。"""
    cid = await harness.create_character()
    await harness.set_character(cid, bag_capacity=2)
    await harness.seed_item(cid, "bronze_sword")
    await harness.seed_item(cid, MID_HP_POTION, quantity=15)
    drop = retain_ground_drop(cid, MID_HP_POTION, 5)

    r = await harness.client.post(
        "/api/inventory/pick", json={"item_drop_id": drop.drop_id}
    )
    assert r.status_code == 200, r.text
    assert r.json()["quantity"] == 20

    r = await harness.client.get("/api/inventory/bag")
    assert r.json()["used"] == 2, "合并不新增格数"


# --- C. 背包满掉落：装备保留地面实例 + 日志提示（纯内存）----------------------------


def test_bag_full_equipment_retained_on_ground_with_log() -> None:
    """背包满时掉落装备：不入包、保留地面实例、bag_full 日志、drop 事件带 drop_id。"""
    session = make_session(32)
    row = ItemInstance(character_id=32, item_id="light_armor", quantity=1)
    row.id = next_id()
    session.inventory.append(row)
    session.char_snapshot.bag_capacity = 1

    events: list[tuple[str, str]] = []
    _apply_drop(
        session,
        DropResult(monster_id="skeleton", gold=50, items=(DroppedItem("bronze_sword", 1),)),
        events,
    )

    # 金币自动入袋（与物品拾取无关）；装备未入包
    assert session.char_snapshot.gold == 50
    assert [r.item_id for r in session.inventory] == ["light_armor"]

    # 地面实例保留
    drops = ground_drops(32)
    assert len(drops) == 1
    assert drops[0].item_id == "bronze_sword"
    assert drops[0].qty == 1

    # 日志提示 + drop 事件携带 drop_id 供手动拾取
    full_logs = logs_of_kind(events, "bag_full")
    assert len(full_logs) == 1
    assert "背包已满" in str(full_logs[0]["text"])
    assert "留在地面" in str(full_logs[0]["text"])
    drop_events = [json.loads(d) for n, d in events if n == "drop"]
    loot = drop_events[0]["loot"]
    item_entry = next(e for e in loot if "item_id" in e)
    assert item_entry["picked"] is False
    assert item_entry["drop_id"] == drops[0].drop_id


def test_gold_always_picked_and_manual_mode_retains_items_silently() -> None:
    """auto_pickup=False：金币仍自动入袋；物品静默落地（无 bag_full 日志）。"""
    session = make_session(33)
    session.auto_pickup = False

    events: list[tuple[str, str]] = []
    _apply_drop(
        session,
        DropResult(monster_id="skeleton", gold=7, items=(DroppedItem("bronze_sword", 1),)),
        events,
    )

    assert session.char_snapshot.gold == 7
    assert len(session.inventory) == 0
    drops = ground_drops(33)
    assert len(drops) == 1 and drops[0].item_id == "bronze_sword"
    assert logs_of_kind(events, "bag_full") == []


def test_auto_pickup_stacks_and_equipment_never_stacks() -> None:
    """自动拾取：可叠物并入既有行；装备永远 quantity=1 单独成行（不可堆叠红线）。"""
    session = make_session(34)
    potion = ItemInstance(character_id=34, item_id=MID_HP_POTION, quantity=17)
    potion.id = next_id()
    session.inventory.append(potion)

    events: list[tuple[str, str]] = []
    _apply_drop(
        session,
        DropResult(
            monster_id="skeleton",
            items=(DroppedItem(MID_HP_POTION, 3), DroppedItem("bronze_sword", 1)),
        ),
        events,
    )

    assert potion.quantity == 20  # 堆叠合并
    sword = next(r for r in session.inventory if r.item_id == "bronze_sword")
    assert sword.quantity == 1  # 装备不可堆叠
    drop_event = next(json.loads(d) for n, d in events if n == "drop")
    loot = drop_event["loot"]
    assert all(entry["picked"] is True for entry in loot)


# --- D. 自动回城触发器：空格 < N → recall → trigger_return_city 占位 ----------------


@pytest.fixture
async def db_char() -> AsyncIterator[tuple[str, int]]:
    username = f"t25r_{uuid4().hex[:10]}"
    char_name = f"c25r_{uuid4().hex[:10]}"
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
                "80, 50, 0, 'biqi-wild', :bind, now()) RETURNING id"
            ),
            {"u": username, "n": char_name, "bind": BIND_CITY},
        )
        character_id = row.scalar_one()
        await db.commit()
    yield username, character_id
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM player WHERE username = :u"), {"u": username})
        await db.commit()


async def test_free_slots_below_threshold_triggers_auto_recall(
    db_char: tuple[str, int],
) -> None:
    """空格 < BAG_FREE_SLOTS_RECALL：simulate_tick 产出 recall 标记；回城动作切 zone。"""
    _, character_id = db_char
    async with SessionLocal() as db:
        session = await get_or_create_player_session(db, character_id)
    assert session.char_snapshot.map_id == "biqi-wild"

    # 造 36 行内存背包 → 空格 4 < BAG_FREE_SLOTS_RECALL=5
    fill_bag(session, free_slots=BAG_FREE_SLOTS_RECALL - 1)

    events = simulate_tick(session, 1)
    recalls = [(n, d) for n, d in events if n == "_recall"]
    assert len(recalls) == 1, "空格不足必须产出内部 recall 标记"
    assert json.loads(recalls[0][1])["target"] == BIND_CITY
    recall_logs = logs_of_kind(events, "recall")
    assert len(recall_logs) == 1 and "自动回城" in str(recall_logs[0]["text"])

    # 回城动作（占位）：直读 bind_city 切 zone_id + UPDATE character.map_id
    target = await trigger_return_city(session)
    assert target == BIND_CITY
    assert session.char_snapshot.map_id == BIND_CITY
    assert session.zone is not None
    assert session.zone.map_id == BIND_CITY

    async with SessionLocal() as db:
        map_id = (
            await db.execute(
                text("SELECT map_id FROM character WHERE id = :c"), {"c": character_id}
            )
        ).scalar_one()
    assert map_id == BIND_CITY

    zone_events = [
        json.loads(e.data)
        for e in session.sse_state.buffer.after(0)
        if e.event == "zone_change"
    ]
    assert zone_events and zone_events[-1]["map_id"] == BIND_CITY


async def test_recall_not_triggered_when_free_slots_enough(
    db_char: tuple[str, int],
) -> None:
    """空格充足（≥N）→ 无 recall 标记（R6 阈值严格小于语义）。"""
    _, character_id = db_char
    async with SessionLocal() as db:
        session = await get_or_create_player_session(db, character_id)
    fill_bag(session, free_slots=BAG_FREE_SLOTS_RECALL)  # 恰好 = N 不触发

    events = simulate_tick(session, 1)
    assert not any(name == "_recall" for name, _ in events)


async def test_tick_loop_routes_recall_marker_to_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tick_loop 拦截内部标记调回城动作，且 "_recall" 不泄漏进 SSE buffer。"""
    session = make_session(77, map_id="biqi-wild")
    fill_bag(session, free_slots=BAG_FREE_SLOTS_RECALL - 1)

    calls: list[int] = []

    async def spy(s: PlayerSession) -> str:
        calls.append(s.character_id)
        return s.char_snapshot.bind_city

    monkeypatch.setattr("app.sim.tick_loop.trigger_return_city", spy)

    task = await start_tick_loop(session, interval=0.01)
    try:
        await asyncio.sleep(0.08)  # ~7 tick，每 tick 都满足触发条件
    finally:
        await cancel_tick_loop(session)
        _ = task

    assert calls and calls[0] == session.character_id
    published = {e.event for e in session.sse_state.buffer.after(0)}
    assert "_recall" not in published
