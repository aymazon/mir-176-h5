"""ZoneInstance 全生命周期测试（todo 14）。

覆盖计划验收：进图懒创建（首个玩家触发 / 已有实例复用）、杀怪后按重生
间隔回补（kill_monster → collect_due + refill 还原）、最后玩家离开实例销毁、
边界（空 spawns 表 / 容量上限 / 时钟跳变）与跨实例 BOSS 计时种子。

gamedata 一律用 load_all() 真实数据（不造假字典）；DB 走 conftest 约定的
真实 PG（mir_dev）；zone 注册表用 shutdown_all() 在用例前后清场防跨用例
残留。时间相关断言全部走注入的假时钟，不 sleep。
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from app.core.db import engine
from app.gamedata_loader import load_all
from app.gamedata_schema.spawns import SpawnEntry
from app.sim.zone import (
    ZoneInstance,
    destroy_zone,
    enter_map,
    get_zone,
    leave_map,
    shutdown_all,
)

# 真实 gamedata 关键常量（biqi-wild_deer.json / shouren-gu-3_orc_champion.json）
DEER_KEY = "biqi-wild:deer"
DEER_DENSITY = 6
DEER_RESPAWN = 60
CHAMPION_KEY = "shouren-gu-3:orc_champion"
CHAMPION_RESPAWN = 3600


@pytest.fixture(autouse=True)
async def _clean_zones() -> AsyncIterator[None]:
    """zone 注册表清场：防上一用例残留实例 + 收尾取消 spawner task。"""
    await shutdown_all()
    yield
    await shutdown_all()
    # 连接池连接绑定用例 event loop，不 dispose 会污染下一用例（conftest 约定）
    await engine.dispose()


@pytest.fixture(scope="module")
def gd() -> dict[str, object]:
    return cast("dict[str, object]", load_all())


def make_clock(start: float = 1000.0) -> tuple[Callable[[], float], list[float]]:
    """可控假时钟：返回 (clock, now_box)；推进/跳变 = 改 box[0]。"""
    box = [start]
    return (lambda: box[0]), box


def spawn_table_for(gd: dict[str, object], map_id: str) -> dict[str, SpawnEntry]:
    spawns = cast("dict[str, SpawnEntry]", gd["spawns"])
    return {key: entry for key, entry in spawns.items() if entry.map_id == map_id}


def make_zone(
    gd: dict[str, object],
    map_id: str,
    clock: Callable[[], float],
) -> ZoneInstance:
    """直构 ZoneInstance（不经 enter_map，不依赖 DB / spawner task）。"""
    return ZoneInstance(
        map_id=map_id,
        static_spawn_table=spawn_table_for(gd, map_id),
        monster_defs=cast("dict[str, object]", gd["monsters"]),
        clock=clock,
    )


# --- 进图懒创建 / 复用 -------------------------------------------------------


async def test_enter_map_lazy_create_and_reuse(gd: dict[str, object]) -> None:
    zone = await enter_map("biqi-wild", 1, gd)
    assert get_zone("biqi-wild") is zone
    assert zone.players == {1}
    # 初始填充：每个刷怪点补到 density
    assert zone.alive_count(DEER_KEY) == DEER_DENSITY
    # spawner 后台任务已起且在跑
    assert zone.spawner_task is not None and not zone.spawner_task.done()

    # 第二名玩家进图 → 复用同一实例，不重建
    again = await enter_map("biqi-wild", 2, gd)
    assert again is zone
    assert zone.players == {1, 2}


async def test_enter_map_unknown_map_raises(gd: dict[str, object]) -> None:
    with pytest.raises(KeyError, match="no-such-map"):
        await enter_map("no-such-map", 1, gd)


# --- 最后玩家离开销毁 --------------------------------------------------------


async def test_last_leave_destroys_zone(gd: dict[str, object]) -> None:
    zone = await enter_map("biqi-wild", 1, gd)
    await enter_map("biqi-wild", 2, gd)

    # 非在册玩家离图：引用未归零 → 不销毁
    assert await leave_map("biqi-wild", 999) is False
    assert get_zone("biqi-wild") is zone

    # 还有其他玩家 → 不销毁
    assert await leave_map("biqi-wild", 1) is False
    assert get_zone("biqi-wild") is zone

    spawner_task = zone.spawner_task
    assert spawner_task is not None and not spawner_task.done()

    # 最后一名 → 返回 True 且实例出注册表、spawner task 被取消（aclose 置 None）
    assert await leave_map("biqi-wild", 2) is True
    assert get_zone("biqi-wild") is None
    assert zone.spawner_task is None
    assert spawner_task.cancelled()

    # 幂等：再离图 / 再强拆都返 False
    assert await leave_map("biqi-wild", 2) is False
    assert await destroy_zone("biqi-wild") is False


async def test_shutdown_all_clears_registry(gd: dict[str, object]) -> None:
    await enter_map("biqi-wild", 1, gd)
    await enter_map("shouren-gu-3", 2, gd)
    await shutdown_all()
    assert get_zone("biqi-wild") is None
    assert get_zone("shouren-gu-3") is None


async def test_player_reference_counting_on_direct_instance() -> None:
    clock, _ = make_clock()
    zone = ZoneInstance(map_id="ref-count-map", static_spawn_table={}, clock=clock)
    zone.enter(1)
    zone.enter(1)  # 重复 enter 幂等
    zone.enter(2)
    assert zone.players == {1, 2}
    assert zone.leave(1) is False
    assert zone.leave(1) is False  # discard 幂等
    assert zone.leave(2) is True  # 最后一人 → 应销毁


# --- 杀怪回补（纯内存，不依赖 DB）---------------------------------------------


async def test_kill_then_refill_after_respawn_interval(gd: dict[str, object]) -> None:
    clock, now = make_clock()
    zone = make_zone(gd, "biqi-wild", clock)
    zone.fill_initial()
    victim = next(m for m in zone.alive_monsters if m.spawn_key == DEER_KEY)

    killed = zone.kill_monster(victim.instance_id)
    assert killed is victim
    assert zone.alive_count(DEER_KEY) == DEER_DENSITY - 1
    # 重生票按刷怪点 respawn_seconds 记账（clock 单调秒）
    assert zone.respawn_timers[victim.instance_id] == now[0] + DEER_RESPAWN

    # 未到期：refill 不动（refill 内部 collect_due 无票可弹）
    now[0] += DEER_RESPAWN - 1
    assert zone.collect_due() == []
    assert zone.refill() == 0
    assert zone.alive_count(DEER_KEY) == DEER_DENSITY - 1

    # 到期：refill 一次完成 collect_due + 回补，还原到 density
    now[0] += 1
    assert zone.refill() == 1
    assert zone.alive_count(DEER_KEY) == DEER_DENSITY
    # 回补的是新实例（新 id），不是复活旧对象
    assert all(m.instance_id != victim.instance_id for m in zone.alive_monsters)


async def test_collect_due_pops_tickets_and_clears_state(
    gd: dict[str, object],
) -> None:
    clock, now = make_clock()
    zone = make_zone(gd, "biqi-wild", clock)
    zone.fill_initial()
    victim = next(m for m in zone.alive_monsters if m.spawn_key == DEER_KEY)
    zone.kill_monster(victim.instance_id)

    now[0] += DEER_RESPAWN + 1
    due = zone.collect_due()
    # 弹出的是死亡快照，且票与待重生快照一并清掉
    assert [m.instance_id for m in due] == [victim.instance_id]
    assert victim.instance_id not in zone.respawn_timers
    assert zone.pending_count(DEER_KEY) == 0
    # 幂等：再收一次为空
    assert zone.collect_due() == []


async def test_kill_unknown_instance_returns_none(gd: dict[str, object]) -> None:
    clock, _ = make_clock()
    zone = make_zone(gd, "biqi-wild", clock)
    assert zone.kill_monster("no-such-instance-id") is None


# --- 边界：时钟跳变 -----------------------------------------------------------


async def test_clock_jump_backward_delays_respawn(gd: dict[str, object]) -> None:
    clock, now = make_clock()
    zone = make_zone(gd, "biqi-wild", clock)
    zone.fill_initial()
    victim = next(m for m in zone.alive_monsters if m.spawn_key == DEER_KEY)
    zone.kill_monster(victim.instance_id)

    now[0] -= 500  # 时钟回拨：票远未到期
    assert zone.collect_due() == []
    assert zone.refill() == 0
    assert zone.alive_count(DEER_KEY) == DEER_DENSITY - 1


async def test_clock_jump_forward_flushes_tickets(gd: dict[str, object]) -> None:
    clock, now = make_clock()
    zone = make_zone(gd, "biqi-wild", clock)
    zone.fill_initial()
    victims = [m for m in zone.alive_monsters if m.spawn_key == DEER_KEY][:2]
    for v in victims:
        zone.kill_monster(v.instance_id)
    assert zone.alive_count(DEER_KEY) == DEER_DENSITY - 2

    now[0] += 10_000  # 大幅前跳：所有票一次性到期
    # refill 内部一次收完两张到期票并回补
    assert zone.refill() == 2
    assert zone.alive_count(DEER_KEY) == DEER_DENSITY


# --- 边界：空 spawns 表 / 容量上限 ----------------------------------------------


async def test_empty_spawn_table_fills_nothing(gd: dict[str, object]) -> None:
    clock, _ = make_clock()
    zone = ZoneInstance(
        map_id="empty-map",
        static_spawn_table={},
        monster_defs=cast("dict[str, object]", gd["monsters"]),
        clock=clock,
    )
    assert zone.fill_initial() == 0
    assert zone.refill() == 0
    assert zone.alive_monsters == []


async def test_refill_never_exceeds_density(gd: dict[str, object]) -> None:
    clock, now = make_clock()
    zone = make_zone(gd, "biqi-wild", clock)
    zone.fill_initial()

    # 人为超容：alive > density（防御分支的前置态）；不假设 alive_monsters[0] 是鹿
    # （todo 37 起 biqi-wild 首个刷怪点是 chicken）——显式按 spawn_key 取鹿。
    deer_inst = next(m for m in zone.alive_monsters if m.spawn_key == DEER_KEY)
    deer_def = deer_inst.monster_def
    entry = zone.static_spawn_table[DEER_KEY]
    zone.spawn_monster(deer_def, entry)
    assert zone.alive_count(DEER_KEY) == DEER_DENSITY + 1

    zone.kill_monster(deer_inst.instance_id)  # alive 回到 density，但挂了一张重生票
    now[0] += DEER_RESPAWN + 1

    # 容量已满 → 弃票，保不变量 alive ≤ density
    assert zone.refill() == 0
    assert zone.alive_count(DEER_KEY) == DEER_DENSITY


# --- 跨实例 BOSS 计时种子 ------------------------------------------------------


async def test_seed_boss_timers_then_refill_after_wait(gd: dict[str, object]) -> None:
    clock, now = make_clock()
    zone = make_zone(gd, "shouren-gu-3", clock)

    wall_now = datetime.now(UTC)
    pending = {"orc_champion": wall_now + timedelta(seconds=120)}
    assert zone.seed_boss_timers(pending, wall_now) == 1
    assert zone.pending_count(CHAMPION_KEY) == 1

    # 初始填充跳过有票的 boss 槽位；其余刷怪点正常补满
    filled = zone.fill_initial()
    assert zone.alive_count(CHAMPION_KEY) == 0
    assert filled > 0

    # 时钟跳过剩余等待 → refill 出新 boss
    now[0] += 121
    assert zone.refill() == 1
    assert zone.alive_count(CHAMPION_KEY) == 1


async def test_seed_boss_timers_skips_unknown_boss(gd: dict[str, object]) -> None:
    clock, _ = make_clock()
    zone = make_zone(gd, "shouren-gu-3", clock)
    wall_now = datetime.now(UTC)
    # 数据变更后的陈旧行（spawns 里已无该 boss）→ 不入账
    seeded = zone.seed_boss_timers(
        {"ghost-boss": wall_now + timedelta(seconds=60)}, wall_now
    )
    assert seeded == 0
    assert zone.pending_count(CHAMPION_KEY) == 0


async def test_seed_boss_timers_expired_row_is_immediately_due(
    gd: dict[str, object],
) -> None:
    """过期计时（load_pending_bosses 正常不会喂进来）被钳到 0 → 立即可回补。"""
    clock, _ = make_clock()
    zone = make_zone(gd, "shouren-gu-3", clock)
    wall_now = datetime.now(UTC)
    seeded = zone.seed_boss_timers(
        {"orc_champion": wall_now - timedelta(seconds=30)}, wall_now
    )
    assert seeded == 1
    assert zone.refill() == 1
    assert zone.alive_count(CHAMPION_KEY) == 1


# --- 收尾兜底：确保没有泄漏的后台 task ------------------------------------------


async def test_no_leaked_spawner_tasks_after_shutdown(gd: dict[str, object]) -> None:
    await enter_map("biqi-wild", 1, gd)
    await shutdown_all()
    await asyncio.sleep(0)  # 让 cancel 回调落地
    running = [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and "spawner" in (t.get_name())
    ]
    assert running == []
