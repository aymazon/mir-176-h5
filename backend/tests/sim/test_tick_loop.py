"""tick_loop 生命周期 + 单 tick 结算 + 落库/锁序列化测试（todo 15）。

覆盖：start/cancel/抢占（无双 task）、断线宽限期超时取消且注册表无残留、
宽限内重连续跑、simulate_tick 各阶段（buff dot / 玩家出手击杀掉落 / 怪物
回击 / regen / 自动吃药）、每 5 tick 增量落库 + flush_snapshot、锁临界区
force-inject 序列化保证（commit 后 flush 前让出，intruder 无法插入）。

时间相关用例经 settings.SSE_GRACE_PERIOD_SECONDS 与 interval 注入调快，
不真实 sleep 秒级。DB 用例走真实 PG；收尾删 player 并 dispose engine。
"""

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.core.db import SessionLocal, engine
from app.gamedata_loader import load_all
from app.gamedata_schema import MonsterDef, SpawnEntry
from app.models.item_instance import ItemInstance
from app.sim.session import (
    CharacterSnapshot,
    PlayerSession,
    get_or_create_player_session,
    max_hp_for,
    max_mp_for,
    on_disconnect,
    register_session,
    release_session,
    session_for,
)
from app.sim.tick_loop import (
    EV_SCENE,
    PERSIST_EVERY_TICKS,
    cancel_tick_loop,
    persist_session_state,
    simulate_tick,
    start_tick_loop,
)
from app.sim.travel import TravelState, clear_travel, start_travel
from app.sim.zone import ZoneInstance, shutdown_all
from app.sse.session_state import get_or_create_session, reset_sessions


@pytest.fixture(autouse=True)
async def _clean_runtime() -> AsyncIterator[None]:
    reset_sessions()
    await shutdown_all()
    yield
    reset_sessions()
    await shutdown_all()
    await engine.dispose()


def make_session(
    character_id: int = 1,
    *,
    level: int = 8,
    hp: int | None = None,
    mp: int | None = None,
    map_id: str = "no-such-map",  # 缺省假地图 → start_tick_loop 无图模式，不碰 DB
) -> PlayerSession:
    snap = CharacterSnapshot(
        name=f"t{character_id}",
        class_="warrior",
        level=level,
        exp=0,
        hp=max_hp_for(level) if hp is None else hp,
        mp=max_mp_for(level) if mp is None else mp,
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





# --- 生命周期：start / cancel / 抢占 ---------------------------------------------


async def test_start_creates_named_task_and_cancel_awaits() -> None:
    session = make_session(10)
    task = await start_tick_loop(session, interval=0.05)
    assert task is session.tick_task
    assert task.get_name() == "tick:10"
    assert not task.done()
    assert await cancel_tick_loop(session) is True
    assert task.cancelled()
    assert session.tick_task is None
    assert await cancel_tick_loop(session) is False  # 幂等


async def test_second_start_preempts_old_task_no_double_task() -> None:
    """新连接抢占：旧 tick task 先 cancel+await 完成再启新 task（无双 task）。"""
    session = make_session(11)
    first = await start_tick_loop(session, interval=0.05)
    await asyncio.sleep(0.02)
    second = await start_tick_loop(session, interval=0.05)
    assert first is not second
    assert first.done() and first.cancelled()  # 旧的已被等完
    assert session.tick_task is second and not second.done()
    running = [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and t.get_name().startswith("tick:11")
    ]
    assert running == [second]
    await cancel_tick_loop(session)


async def test_grace_timeout_cancels_and_releases_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """断网超过宽限期：task 自然结束、会话出注册表、无残留 tick task。"""
    monkeypatch.setattr(settings, "SSE_GRACE_PERIOD_SECONDS", 0.3)

    async def noop_heartbeat(cid: int) -> None:
        return None

    monkeypatch.setattr("app.sim.session._touch_heartbeat", noop_heartbeat)
    session = make_session(12)
    task = await start_tick_loop(session, interval=0.05)
    assert on_disconnect(12) is True
    done, _ = await asyncio.wait({task}, timeout=3)
    assert task in done and not task.cancelled()  # 正常 return，非异常取消
    assert session_for(12) is None  # 注册表已清理
    running = [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and t.get_name().startswith("tick:12")
    ]
    assert running == []


async def test_reconnect_within_grace_keeps_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """宽限内重连：task 未被取消，mark_connected 清标记后续跑。"""
    monkeypatch.setattr(settings, "SSE_GRACE_PERIOD_SECONDS", 5.0)

    async def noop_heartbeat(cid: int) -> None:
        return None

    monkeypatch.setattr("app.sim.session._touch_heartbeat", noop_heartbeat)
    session = make_session(13)
    task = await start_tick_loop(session, interval=0.02)
    on_disconnect(13)
    await asyncio.sleep(0.1)  # 远小于宽限期
    assert not task.done()
    session.mark_connected()
    await asyncio.sleep(0.1)
    assert not task.done()
    assert session_for(13) is session
    await cancel_tick_loop(session)


async def test_events_accumulate_in_buffer_while_disconnected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """断线期间 tick 继续产事件入 buffer（重连 Last-Event-ID 补全的依据）。"""
    session = make_session(14)
    await start_tick_loop(session, interval=0.02)
    on_disconnect(14)
    seq_before = session.sse_state.last_seq
    await asyncio.sleep(0.1)
    assert session.sse_state.last_seq > seq_before
    await cancel_tick_loop(session)


# --- simulate_tick 各阶段 -------------------------------------------------------


def _events_of_kind(events: list[tuple[str, str]], kind: str) -> list[dict[str, object]]:
    import json

    out = []
    for name, data in events:
        if name != "log":
            continue
        payload = json.loads(data)
        if payload.get("kind") == kind:
            out.append(payload)
    return out


def test_potion_triggers_when_hp_below_threshold() -> None:
    """HP < 30% 上限自动喝药：HP 回复、背包扣减、冷却记账、potion 事件。"""
    session = make_session(20, hp=10)
    session.inventory.append(
        ItemInstance(character_id=20, item_id="potion_hp_small", quantity=2)
    )
    events = simulate_tick(session, tick_index=3)
    snap = session.char_snapshot
    assert snap.hp == 41  # 10 + regen 1 + 30（potion_hp_small hp_restore）
    assert [(r.item_id, r.quantity) for r in session.inventory] == [
        ("potion_hp_small", 1)
    ]
    assert session.potion_cooldowns["potion_hp_small"] == 3 + 5
    potions = _events_of_kind(events, "potion")
    assert len(potions) == 1
    # HP 已回到阈值上方 → 下个 tick 不再喝
    events2 = simulate_tick(session, tick_index=4)
    assert _events_of_kind(events2, "potion") == []


def test_stat_event_published_every_tick() -> None:
    """1Hz 连续性锚点：每 tick 恰好一条 stat 事件。"""
    session = make_session(21)
    events = simulate_tick(session, tick_index=9)
    stats = [e for e in events if e[0] == "stat"]
    assert len(stats) == 1
    import json

    payload = json.loads(stats[0][1])
    assert payload["tick"] == 9
    assert payload["max_hp"] == max_hp_for(8)


def test_kill_yields_exp_drop_and_pickup_with_real_gamedata() -> None:
    """真实 gamedata 场上怪：命中→击杀→经验入账→掉落 roll→自动拾取入包。"""
    gd = cast("dict[str, object]", load_all())
    spawns = cast("dict[str, SpawnEntry]", gd["spawns"])
    table = {k: v for k, v in spawns.items() if v.map_id == "biqi-wild"}
    zone = ZoneInstance(
        map_id="biqi-wild",
        static_spawn_table=table,
        monster_defs=cast("dict[str, MonsterDef]", gd["monsters"]),
    )
    zone.fill_initial()
    session = make_session(22, map_id="biqi-wild")
    session.zone = zone

    exp_seen = False
    drop_seen = False
    for n in range(1, 61):
        events = simulate_tick(session, n)
        if any(e[0] == "exp" for e in events):
            exp_seen = True
        if any(e[0] == "drop" for e in events):
            drop_seen = True
        if exp_seen and drop_seen:
            break
    assert exp_seen, "60 tick 内未产生击杀经验"
    assert drop_seen, "击杀后未产生掉落事件"
    assert session.char_snapshot.exp > 0
    assert session.char_snapshot.gold > 0 or session.inventory  # 金币或物品入账


def test_monster_retaliates_and_regen_only_when_untouched() -> None:
    """怪物按攻速回击扣血；未承伤 tick 触发 regen 回 1 点。"""
    gd = cast("dict[str, object]", load_all())
    spawns = cast("dict[str, SpawnEntry]", gd["spawns"])
    table = {k: v for k, v in spawns.items() if v.map_id == "biqi-wild"}
    zone = ZoneInstance(
        map_id="biqi-wild",
        static_spawn_table=table,
        monster_defs=cast("dict[str, MonsterDef]", gd["monsters"]),
    )
    zone.fill_initial()
    session = make_session(23, map_id="biqi-wild")
    session.zone = zone
    full_hp = session.char_snapshot.max_hp

    # 无图对照：无怪 → 承伤 0 → regen 生效
    headless = make_session(24)
    headless.char_snapshot.hp = full_hp - 5
    simulate_tick(headless, 1)
    assert headless.char_snapshot.hp == full_hp - 4

    # 有怪：60 tick 内必然被咬（deer accuracy=5 vs warrior L8 agility=4）
    damaged = False
    for n in range(1, 61):
        before = session.char_snapshot.hp
        simulate_tick(session, n)
        if session.char_snapshot.hp < before:
            damaged = True
            break
    assert damaged, "60 tick 内怪物从未命中玩家"


# --- 落库 + 锁序列化（真实 PG）----------------------------------------------------


@pytest.fixture
async def db_char() -> AsyncIterator[tuple[str, int]]:
    username = f"t15l_{uuid4().hex[:10]}"
    char_name = f"c15l_{uuid4().hex[:10]}"
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
        await db.execute(
            text(
                "INSERT INTO item_instance (character_id, item_id, quantity) "
                "VALUES (:cid, 'potion_hp_small', 30)"
            ),
            {"cid": character_id},
        )
        await db.commit()
    yield username, character_id
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM player WHERE username = :u"), {"u": username})
        await db.commit()


async def test_persist_writes_character_and_inventory(db_char: tuple[str, int]) -> None:
    """落库契约：HP/MP/exp/gold/last_active_at + 背包增删改，commit 后 flush 重读。"""
    _, character_id = db_char
    async with SessionLocal() as db:
        session = await get_or_create_player_session(db, character_id)

    # 内存变更：战斗消耗 + 拾取新物 + 喝空一瓶药
    session.char_snapshot.hp = 33
    session.char_snapshot.exp = 40
    session.char_snapshot.gold = 12
    potion_row = next(r for r in session.inventory if r.item_id == "potion_hp_small")
    potion_row.quantity -= 1
    session.dirty_quantities.add(potion_row.id)
    new_item = ItemInstance(character_id=character_id, item_id="meat", quantity=2)
    session.inventory.append(new_item)
    session.pending_items.append(new_item)

    async with session.lock:  # 契约：persist 必须在锁内调用
        await persist_session_state(session)

    async with SessionLocal() as db:
        row = (
            await db.execute(
                text("SELECT hp, exp, gold FROM character WHERE id = :cid"),
                {"cid": character_id},
            )
        ).one()
        items = sorted(
            (
                await db.execute(
                    text(
                        "SELECT item_id, quantity FROM item_instance "
                        "WHERE character_id = :cid ORDER BY item_id"
                    ),
                    {"cid": character_id},
                )
            ).all()
        )
        age = (
            await db.execute(
                text(
                    "SELECT EXTRACT(EPOCH FROM (now() - last_active_at)) "
                    "FROM character WHERE id = :cid"
                ),
                {"cid": character_id},
            )
        ).scalar_one()
    assert (row.hp, row.exp, row.gold) == (33, 40, 12)
    assert items == [("meat", 2), ("potion_hp_small", 29)]
    assert float(age) < 5  # last_active_at=now() 已刷新
    # flush 后内存与库一致、脏标记清零、pending 行已获得持久化 id
    assert sorted((r.item_id, r.quantity) for r in session.inventory) == items
    assert new_item.id is not None
    assert session.dirty_quantities == set() and session.pending_items == []


async def test_lock_serializes_commit_flush_window(db_char: tuple[str, int]) -> None:
    """force-inject：tick 落库在 commit 后 flush 前让出事件循环，
    并发 REST 写必须等整个临界区（含 flush）结束才能进入——DB 不出现中间覆盖态。
    """
    _, character_id = db_char
    order: list[str] = []

    class InjectingSession(PlayerSession):
        """flush_snapshot 前强制让出 3 个调度槽——若锁没覆盖窗口，intruder 会插队。"""

        flush_tag: str = "tick"

        async def flush_snapshot(self) -> None:
            order.append(f"{self.flush_tag}:flush-start")
            for _ in range(3):
                await asyncio.sleep(0)
            await super().flush_snapshot()
            order.append(f"{self.flush_tag}:flush-end")

    async with SessionLocal() as db:
        loaded = await get_or_create_player_session(db, character_id)
    release_session(character_id)
    session = InjectingSession(
        character_id=character_id,
        char_snapshot=loaded.char_snapshot,
        inventory=list(loaded.inventory),
        skill_states=dict(loaded.skill_states),
        sse_state=get_or_create_session(character_id),
    )
    register_session(session)
    session.flush_tag = "tick"
    session.char_snapshot.hp = 111  # tick 侧要落库的值

    async def tick_side() -> None:
        async with session.lock:
            order.append("tick:tx-start")
            await persist_session_state(session)
            order.append("tick:tx-end")

    async def rest_writer() -> None:
        order.append("rest:want-lock")
        async with session.lock:
            session.flush_tag = "rest"
            order.append("rest:tx-start")
            async with SessionLocal() as db:
                await db.execute(
                    text("UPDATE character SET hp = 222 WHERE id = :cid"),
                    {"cid": character_id},
                )
                await db.commit()
            order.append("rest:commit")
            await session.flush_snapshot()
            order.append("rest:flush")

    tick_task = asyncio.create_task(tick_side())
    await asyncio.sleep(0.001)  # 让 tick 侧先持锁进入临界区
    rest_task = asyncio.create_task(rest_writer())
    with suppress(Exception):
        await asyncio.gather(tick_task, rest_task)

    # 关键断言：REST 写的整个事务严格排在 tick 的 flush-end 之后——
    # 即使 tick 在 commit 后让出，intruder 也进不了临界区（无中间覆盖态）。
    assert order.index("rest:tx-start") > order.index("tick:flush-end")
    assert order.index("rest:want-lock") < order.index("tick:flush-start")
    # 最终内存 = 后写者（REST）的值：flush 方向 DB→内存未被旧快照覆盖
    assert session.char_snapshot.hp == 222


def test_persist_every_constant_is_five() -> None:
    """计划红线：每 5 tick 落库。"""
    assert PERSIST_EVERY_TICKS == 5


# --- scene 事件 on-change diff（battle-scene-ui todo 2 通道 A）-------------------


def _scene_events(events: list[tuple[str, str]]) -> list[str]:
    return [data for name, data in events if name == EV_SCENE]


def test_scene_published_once_when_roster_unchanged() -> None:
    """无变化不重复发：两连 tick 场上无变化 → 仅首 tick 一条 scene。"""
    session = make_session(30)  # 无图模式：scene 恒为空帧
    first = simulate_tick(session, 1)
    second = simulate_tick(session, 2)
    assert len(_scene_events(first)) == 1
    assert _scene_events(second) == []
    payload = json.loads(_scene_events(first)[0])
    assert set(payload) == {"map_id", "monsters", "pets"}


def test_scene_published_on_kill_tick() -> None:
    """击杀改变场上 roster → 击杀当 tick 末尾 diff 触发 scene 发布。"""
    gd = cast("dict[str, object]", load_all())
    spawns = cast("dict[str, SpawnEntry]", gd["spawns"])
    table = {k: v for k, v in spawns.items() if v.map_id == "biqi-wild"}
    zone = ZoneInstance(
        map_id="biqi-wild",
        static_spawn_table=table,
        monster_defs=cast("dict[str, MonsterDef]", gd["monsters"]),
    )
    zone.fill_initial()
    session = make_session(31, map_id="biqi-wild")
    session.zone = zone

    killed = False
    for n in range(1, 61):
        events = simulate_tick(session, n)
        if any(e[0] == "exp" for e in events):
            killed = True
            assert _scene_events(events), "击杀 tick 必须发布 scene（roster 已变）"
            break
    assert killed, "60 tick 内未产生击杀"


def test_travel_branch_does_not_publish_scene() -> None:
    """行进中早退分支：zone 未切 → 不发 scene，且不写 diff 基线。"""
    session = make_session(32)
    start_travel(
        32, TravelState(target_map_id="biqi-wild", until=time.time() + 999, fee=100)
    )
    try:
        events = simulate_tick(session, 1)
    finally:
        clear_travel(32)
    assert _scene_events(events) == []
    assert session.last_scene_json is None
    assert [e for e in events if e[0] == "stat"]  # stat 心跳仍在
