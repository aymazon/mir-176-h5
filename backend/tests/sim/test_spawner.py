"""spawner 模块测试（todo 14）：周期补怪循环 + boss_respawn 表读写。

覆盖：spawner_loop 周期 refill（短 interval 让 tick 跑多次）、record_boss_kill
upsert（同 boss 二次写覆盖 respawn_at 且仍只一行）、load_pending_bosses 删过期行、
make_boss_kill_recorder 与 kill_monster 的端到端集成（真实 PG mir_dev）。

boss_respawn 行由写入用例自行收尾（先清历史 + finally 删除），防跨运行残留；
zone 注册表 shutdown_all() 清场；时间推进走注入假时钟，仅 loop 周期用真实短 sleep。
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.db import SessionLocal, engine
from app.gamedata_loader import load_all
from app.gamedata_schema.spawns import SpawnEntry
from app.models.boss_respawn import BossRespawn
from app.sim.spawner import (
    load_pending_bosses,
    make_boss_kill_recorder,
    record_boss_kill,
    spawn_target,
    spawner_loop,
)
from app.sim.zone import ZoneInstance, enter_map, shutdown_all

DEER_KEY = "biqi-wild:deer"
DEER_DENSITY = 6
DEER_RESPAWN = 60


@pytest.fixture(autouse=True)
async def _clean_runtime() -> AsyncIterator[None]:
    await shutdown_all()
    yield
    await shutdown_all()
    # 连接池连接绑定用例 event loop，不 dispose 会污染下一用例（conftest 约定）
    await engine.dispose()


@pytest.fixture(scope="module")
def gd() -> dict[str, object]:
    return cast("dict[str, object]", load_all())


async def _wipe_boss_rows(boss_ids: list[str]) -> None:
    async with SessionLocal() as db:
        await db.execute(delete(BossRespawn).where(BossRespawn.boss_id.in_(boss_ids)))
        await db.commit()


async def _fetch_rows(
    *, boss_id: str | None = None, map_id: str | None = None
) -> list[BossRespawn]:
    stmt = select(BossRespawn)
    if boss_id is not None:
        stmt = stmt.where(BossRespawn.boss_id == boss_id)
    if map_id is not None:
        stmt = stmt.where(BossRespawn.map_id == map_id)
    async with SessionLocal() as db:
        return list((await db.execute(stmt)).scalars().all())


def make_deer_zone(gd: dict[str, object], clock: Callable[[], float]) -> ZoneInstance:
    spawns = cast("dict[str, SpawnEntry]", gd["spawns"])
    return ZoneInstance(
        map_id="biqi-wild",
        static_spawn_table={
            k: e for k, e in spawns.items() if e.map_id == "biqi-wild"
        },
        monster_defs=cast("dict[str, object]", gd["monsters"]),
        clock=clock,
    )


# --- spawner_loop：周期 refill -------------------------------------------------


async def test_spawner_loop_periodic_refill(gd: dict[str, object]) -> None:
    box = [1000.0]
    zone = make_deer_zone(gd, lambda: box[0])
    zone.fill_initial()
    victim = next(m for m in zone.alive_monsters if m.spawn_key == DEER_KEY)
    zone.kill_monster(victim.instance_id)
    assert zone.alive_count(DEER_KEY) == DEER_DENSITY - 1

    # 计数包装（仍委托真 refill，不替换行为），观察 tick 次数
    ticks = 0
    real_refill = zone.refill

    def counting_refill() -> int:
        nonlocal ticks
        ticks += 1
        return real_refill()

    zone.refill = counting_refill  # type: ignore[method-assign]

    task = asyncio.create_task(spawner_loop(zone, interval=0.05))
    try:
        box[0] += DEER_RESPAWN + 1  # 推进假时钟让重生票到期
        deadline = asyncio.get_running_loop().time() + 3.0
        # 等 ≥4 个 tick（interval=0.05s），证明是周期循环而非一次性补怪
        while ticks < 4:
            assert asyncio.get_running_loop().time() < deadline, "spawner tick 次数不足"
            await asyncio.sleep(0.02)
        assert zone.alive_count(DEER_KEY) == DEER_DENSITY, "周期 refill 未还原 density"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_spawn_target_is_density(gd: dict[str, object]) -> None:
    entry = cast("dict[str, SpawnEntry]", gd["spawns"])[DEER_KEY]
    assert spawn_target(entry) == entry.density == DEER_DENSITY


# --- record_boss_kill：upsert ---------------------------------------------------


async def test_record_boss_kill_upsert_keeps_single_row() -> None:
    boss_id = f"test-boss-{uuid4().hex[:8]}"
    try:
        first_kill = datetime.now(UTC) - timedelta(hours=2)
        await record_boss_kill(
            SessionLocal,
            map_id="map-a",
            boss_id=boss_id,
            respawn_seconds=60,
            now=first_kill,
        )
        rows = await _fetch_rows(boss_id=boss_id)
        assert len(rows) == 1
        assert rows[0].map_id == "map-a"
        assert rows[0].killed_at == first_kill
        assert rows[0].respawn_at == first_kill + timedelta(seconds=60)

        # 同 boss 二次击杀 → 覆盖而非新增一行
        second_kill = datetime.now(UTC) - timedelta(seconds=30)
        await record_boss_kill(
            SessionLocal,
            map_id="map-b",
            boss_id=boss_id,
            respawn_seconds=3600,
            now=second_kill,
        )
        rows = await _fetch_rows(boss_id=boss_id)
        assert len(rows) == 1, "upsert 后必须仍只有一行"
        assert rows[0].map_id == "map-b"
        assert rows[0].killed_at == second_kill
        assert rows[0].respawn_at == second_kill + timedelta(seconds=3600)
    finally:
        await _wipe_boss_rows([boss_id])


# --- load_pending_bosses：未来保留 / 过期删除 -------------------------------------


async def test_load_pending_bosses_keeps_future_deletes_expired() -> None:
    map_id = f"test-load-{uuid4().hex[:8]}"
    future_boss = f"fb-{uuid4().hex[:8]}"
    past_boss = f"pb-{uuid4().hex[:8]}"
    now = datetime.now(UTC)
    try:
        async with SessionLocal() as db:
            db.add(
                BossRespawn(
                    boss_id=future_boss,
                    map_id=map_id,
                    killed_at=now - timedelta(minutes=10),
                    respawn_at=now + timedelta(minutes=30),
                )
            )
            db.add(
                BossRespawn(
                    boss_id=past_boss,
                    map_id=map_id,
                    killed_at=now - timedelta(hours=3),
                    respawn_at=now - timedelta(minutes=1),
                )
            )
            await db.commit()

        pending = await load_pending_bosses(SessionLocal, map_id=map_id)
        # 只返未来行（respawn_at > now）
        assert set(pending) == {future_boss}
        assert pending[future_boss] == now + timedelta(minutes=30)

        # 过期行被当场删除（commit 后再查）
        remaining = await _fetch_rows(map_id=map_id)
        assert [r.boss_id for r in remaining] == [future_boss]
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(BossRespawn).where(BossRespawn.map_id == map_id))
            await db.commit()


# --- make_boss_kill_recorder：kill_monster → 写库端到端 ---------------------------


async def test_boss_kill_recorder_writes_db_row(gd: dict[str, object]) -> None:
    # 先清历史行：否则 enter_map 会把旧计时种成票，导致场上无 champion 可杀
    await _wipe_boss_rows(["orc_champion"])
    try:
        zone = await enter_map("shouren-gu-3", 42, gd)
        champion = next(
            m for m in zone.alive_monsters if m.monster_id == "orc_champion"
        )
        assert champion.monster_def.boss is True

        assert zone.kill_monster(champion.instance_id) is champion
        await zone.drain_boss_hooks()  # 等在途写库钩子完成

        rows = await _fetch_rows(boss_id="orc_champion")
        assert len(rows) == 1
        row = rows[0]
        assert row.map_id == "shouren-gu-3"
        # 重生间隔取刷怪点定义（shouren-gu-3_orc_champion.json: 3600s）
        assert row.respawn_at - row.killed_at == timedelta(seconds=3600)
        assert row.respawn_at > datetime.now(UTC)
    finally:
        await _wipe_boss_rows(["orc_champion"])
