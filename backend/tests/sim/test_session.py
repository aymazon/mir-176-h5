"""PlayerSession 单例注册表 + flush_snapshot/断线钩子测试（todo 15）。

覆盖：session_for/register/release 单例语义、on_disconnect 只标记不取消
（幂等）、mark_connected 清标记、get_or_create_player_session 从库装配
（快照/背包/技能）与并发去重、flush_snapshot 的 DB→内存方向（含脏标记清零）、
心跳刷新落库。DB 用例走真实 PG（mir_dev），收尾删 player（FK CASCADE）并
dispose engine 防跨 loop 复用（conftest 约定）。
"""

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.db import SessionLocal, engine
from app.sim.session import (
    CharacterSnapshot,
    PlayerSession,
    all_character_ids,
    get_or_create_player_session,
    max_hp_for,
    max_mp_for,
    on_disconnect,
    register_session,
    release_session,
    session_for,
)
from app.sse.session_state import get_or_create_session, reset_sessions


@pytest.fixture(autouse=True)
async def _clean_runtime() -> AsyncIterator[None]:
    reset_sessions()
    yield
    reset_sessions()
    await engine.dispose()


@pytest.fixture
async def seeded_char() -> AsyncIterator[tuple[str, int]]:
    """建 player+character（warrior Lv8，心跳置 120s 前），返回 (username, character_id)。"""
    username = f"t15_{uuid4().hex[:10]}"
    char_name = f"c15_{uuid4().hex[:10]}"
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
                "80, 40, 0, 'biqi-wild', 'yin-xing-cun', now() - make_interval(secs => 120)) "
                "RETURNING id"
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
        await db.execute(
            text(
                "INSERT INTO skill_state (character_id, skill_id, level, exp) "
                "VALUES (:cid, 'basic_sword', 0, 0)"
            ),
            {"cid": character_id},
        )
        await db.commit()
    yield username, character_id
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM player WHERE username = :u"), {"u": username})
        await db.commit()


# --- 注册表单例语义 -----------------------------------------------------------


def _dummy_snapshot(cid: int) -> CharacterSnapshot:
    return CharacterSnapshot(
        name=f"t{cid}", class_="warrior", level=1, exp=0, hp=24, mp=15,
        gold=0, map_id="yin-xing-cun", bind_city="yin-xing-cun", bag_capacity=40,
    )


def test_register_for_release_roundtrip() -> None:
    session = PlayerSession(character_id=1, char_snapshot=_dummy_snapshot(1))
    assert session_for(1) is None
    register_session(session)
    assert session_for(1) is session
    assert all_character_ids() == [1]
    assert release_session(1) is session
    assert session_for(1) is None
    assert release_session(1) is None  # 幂等


def test_on_disconnect_marks_grace_without_cancelling(monkeypatch: pytest.MonkeyPatch) -> None:
    """断线钩子：只标记 + 宽限期起表（幂等），绝不取消 tick task。"""
    session = PlayerSession(
        character_id=2,
        char_snapshot=_dummy_snapshot(2),
        sse_state=get_or_create_session(2),
    )
    register_session(session)

    async def noop_heartbeat(cid: int) -> None:  # 避免测试里打真库
        return None

    monkeypatch.setattr("app.sim.session._touch_heartbeat", noop_heartbeat)
    assert session.disconnected is False
    assert on_disconnect(2) is True
    assert session.disconnected is True
    assert session.grace_period_started_at is not None
    first_stamp = session.grace_period_started_at
    assert on_disconnect(2) is False  # 幂等：时间戳不动
    assert session.grace_period_started_at == first_stamp
    assert session.tick_task is None  # 从未取消任何 task（本来就没有）
    assert session_for(2) is not None  # 也不注销会话

    session.mark_connected()
    assert session.disconnected is False
    assert session.grace_period_started_at is None


def test_max_hp_placeholder_formulas() -> None:
    """MVP 占位公式锚点（待 todo 20 校准）：L8 → 80/50，L1 → 24/15。"""
    assert max_hp_for(8) == 80
    assert max_mp_for(8) == 50
    assert max_hp_for(1) == 24
    assert max_mp_for(1) == 15


# --- 从库装配 / flush_snapshot（真实 PG）----------------------------------------


async def test_get_or_create_player_session_loads_from_db(
    seeded_char: tuple[str, int],
) -> None:
    _, character_id = seeded_char
    async with SessionLocal() as db:
        session = await get_or_create_player_session(db, character_id)
    assert session_for(character_id) is session  # 已注册单例
    snap = session.char_snapshot
    assert (snap.name.startswith("c15_"), snap.class_, snap.level) == (True, "warrior", 8)
    assert snap.max_hp == 80
    assert [(r.item_id, r.quantity) for r in session.inventory] == [
        ("potion_hp_small", 30)
    ]
    assert "basic_sword" in session.skill_states
    assert session.sse_state is get_or_create_session(character_id)

    # 二次获取：复用同一单例（并发去重语义）
    async with SessionLocal() as db:
        again = await get_or_create_player_session(db, character_id)
    assert again is session


async def test_flush_snapshot_reloads_db_state_over_memory(
    seeded_char: tuple[str, int],
) -> None:
    """方向契约：DB→内存。外部直接改库后 flush，内存快照被替换、脏标记清零。"""
    _, character_id = seeded_char
    async with SessionLocal() as db:
        session = await get_or_create_player_session(db, character_id)

    # 内存侧制造脏态：喝药 + 待入库新行
    session.inventory[0].quantity -= 5
    session.dirty_quantities.add(session.inventory[0].id)
    session.char_snapshot.gold = 999

    # 外部（模拟另一事务）改库：hp=77、金币=55、再塞一件物品
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE character SET hp = 77, gold = 55 WHERE id = :cid"),
            {"cid": character_id},
        )
        await db.execute(
            text(
                "INSERT INTO item_instance (character_id, item_id, quantity) "
                "VALUES (:cid, 'meat', 2)"
            ),
            {"cid": character_id},
        )
        await db.commit()

    async with session.lock:  # 契约：持锁方在锁内调用
        await session.flush_snapshot()

    snap = session.char_snapshot
    assert (snap.hp, snap.gold) == (77, 55)  # DB 值覆盖内存值
    assert sorted((r.item_id, r.quantity) for r in session.inventory) == [
        ("meat", 2),
        # 内存侧的 25 被库里的 30 覆盖——方向契约即 DB→内存（未落库的内存变更不存活）
        ("potion_hp_small", 30),
    ]
    assert session.dirty_quantities == set()  # 重读后脏标记清零
    assert session.pending_items == []


async def test_on_disconnect_refreshes_heartbeat_in_db(
    seeded_char: tuple[str, int],
) -> None:
    """断线心跳刷新：单行 UPDATE last_active_at=now()（豁免取锁的豁免项验证）。"""
    _, character_id = seeded_char
    async with SessionLocal() as db:
        await get_or_create_player_session(db, character_id)
    assert on_disconnect(character_id) is True
    age = 999.0  # 等 fire-and-forget 心跳任务落地（最多 ~2s）
    for _ in range(100):
        await asyncio.sleep(0.02)
        async with SessionLocal() as db:
            age = (
                await db.execute(
                    text(
                        "SELECT EXTRACT(EPOCH FROM (now() - last_active_at)) "
                        "FROM character WHERE id = :cid"
                    ),
                    {"cid": character_id},
                )
            ).scalar_one()
        if float(age) < 60:
            break
    assert float(age) < 60, f"心跳未刷新：age={age}"


