"""ZoneInstance：按需区域实例（懒创建 / 无玩家销毁）+ 进程内注册表。

架构锚点（docs/MVP/README.md:70-71）::

    PlayerSession(内存) → ZoneInstance(懒创建) → 静态刷怪表 + 存活列表 + 重生计时

- **懒创建**：首个玩家进图时 :func:`enter_map` 创建实例（消费 gamedata/maps +
  gamedata/spawns；悬空引用已在启动期被 gamedata_loader.load_all 拒绝）。
- **销毁**：最后一名玩家离图后 :func:`leave_map` 销毁——取消 spawner task、
  丢弃全部内存态。普通怪物状态不持久化；唯一例外是 BOSS 重生计时落
  ``boss_respawn`` 表跨实例存活（读写封装在 spawner.py）。
- **距离档模型（draft D12）**：本层不做网格寻路；怪物只带静态坐标，
  追击/攻击/脱离的 AI 结算由 todo 15 的 tick loop 接入（effects/AI 计算）。

内存态字段（计划规格命名）：
- ``static_spawn_table``: ``{spawn_key: SpawnEntry}``，直接引用 gamedata spawns 值
- ``alive_monsters``: ``list[MonsterInstance]``
- ``respawn_timers``: ``{monster_instance_id: 到期时刻(单调秒)}``
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import TYPE_CHECKING, Final, cast
from uuid import uuid4

from app.core.db import SessionLocal
from app.gamedata_loader import load_all
from app.gamedata_schema.monsters import MonsterDef
from app.gamedata_schema.spawns import SpawnEntry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

LOG = logging.getLogger(__name__)

# BOSS 被杀时的持久化钩子签名（由 spawner.make_boss_kill_recorder 提供）。
BossKillHook = Callable[["ZoneInstance", "MonsterInstance"], Awaitable[None]]


@dataclass
class MonsterInstance:
    """场上一只怪的最小内存态（todo 15 战斗结算在此基础上扩展）。"""

    instance_id: str
    monster_id: str
    spawn_key: str
    x: int
    y: int
    monster_def: MonsterDef


class ZoneInstance:
    """一张地图的运行时实例：静态刷怪表引用 + 存活怪 + 重生计时 + 玩家引用。"""

    def __init__(
        self,
        *,
        map_id: str,
        static_spawn_table: dict[str, SpawnEntry],
        monster_defs: dict[str, MonsterDef] | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_boss_killed: BossKillHook | None = None,
    ) -> None:
        self.map_id: str = map_id
        # 直接引用 gamedata spawns 字典的值（loader 已校验 map/monster 引用存在）。
        self.static_spawn_table: dict[str, SpawnEntry] = static_spawn_table
        self.alive_monsters: list[MonsterInstance] = []
        # 怪物实例 id → 重生到期时刻（clock 单调秒）。同怪多死多票，逐实例回补。
        self.respawn_timers: dict[str, float] = {}
        self.players: set[int] = set()
        self.clock: Callable[[], float] = clock
        self.spawner_task: asyncio.Task[None] | None = None
        self._on_boss_killed: BossKillHook | None = on_boss_killed
        self._monster_defs: dict[str, MonsterDef] = (
            monster_defs if monster_defs is not None else {}
        )
        # 死亡待重生快照：instance_id → 死亡时的实例（提供 respawn 所需 def/entry）。
        self._awaiting: dict[str, MonsterInstance] = {}
        # 刷怪点位轮转游标：spawn_key → 下一次使用的 locations 下标。
        self._loc_cursor: dict[str, int] = {}
        self._boss_hook_tasks: set[asyncio.Task[None]] = set()

    # --- 玩家引用计数 -------------------------------------------------------

    def enter(self, character_id: int) -> None:
        """玩家进图：登记引用（重复 enter 幂等）。"""
        self.players.add(character_id)

    def leave(self, character_id: int) -> bool:
        """玩家离图。返回 True 表示这是最后一名玩家（实例应销毁）。"""
        self.players.discard(character_id)
        return not self.players

    # --- 怪物增删 -----------------------------------------------------------

    def alive_count(self, spawn_key: str) -> int:
        return sum(1 for m in self.alive_monsters if m.spawn_key == spawn_key)

    def pending_count(self, spawn_key: str) -> int:
        """该刷怪点当前挂着重生票（含 BOSS 跨实例种子票）的数量。"""
        return sum(1 for m in self._awaiting.values() if m.spawn_key == spawn_key)

    def _next_location(self, entry: SpawnEntry) -> tuple[int, int]:
        key = f"{entry.map_id}:{entry.monster_id}"
        idx = self._loc_cursor.get(key, 0)
        self._loc_cursor[key] = (idx + 1) % len(entry.locations)
        loc = entry.locations[idx]
        return loc.x, loc.y

    def spawn_monster(
        self, monster_def: MonsterDef, spawn_entry: SpawnEntry
    ) -> MonsterInstance:
        """按刷怪点定义生成一只活怪（新实例 id，点位 round-robin）。"""
        inst = MonsterInstance(
            instance_id=uuid4().hex,
            monster_id=monster_def.id,
            spawn_key=f"{spawn_entry.map_id}:{spawn_entry.monster_id}",
            x=0,
            y=0,
            monster_def=monster_def,
        )
        inst.x, inst.y = self._next_location(spawn_entry)
        self.alive_monsters.append(inst)
        return inst

    def kill_monster(self, monster_instance_id: str) -> MonsterInstance | None:
        """移除活怪并启动重生计时；BOSS 触发持久化钩子。未找到返回 None。"""
        inst = next(
            (m for m in self.alive_monsters if m.instance_id == monster_instance_id),
            None,
        )
        if inst is None:
            return None
        self.alive_monsters.remove(inst)
        entry = self.static_spawn_table[inst.spawn_key]
        # 重生间隔以刷怪点定义为准（boss 的长间隔已编码在 spawns 数据里，
        # 如 orc_champion respawn_seconds=3600）。
        self.respawn_timers[inst.instance_id] = self.clock() + entry.respawn_seconds
        self._awaiting[inst.instance_id] = inst
        if inst.monster_def.boss and self._on_boss_killed is not None:
            self._schedule_boss_hook(inst)
        return inst

    # --- 重生补怪 -----------------------------------------------------------

    def collect_due(self) -> list[MonsterInstance]:
        """弹出所有到期的重生票（按到期时刻排序），返回对应死亡快照。"""
        now = self.clock()
        due = sorted((ts, iid) for iid, ts in self.respawn_timers.items() if ts <= now)
        dead: list[MonsterInstance] = []
        for _, iid in due:
            self.respawn_timers.pop(iid, None)
            snapshot = self._awaiting.pop(iid, None)
            if snapshot is not None:
                dead.append(snapshot)
        return dead

    def refill(self) -> int:
        """spawner tick 动作：把到期票换成新怪（不超过 density 上限）。"""
        spawned = 0
        for dead in self.collect_due():
            entry = self.static_spawn_table[dead.spawn_key]
            if self.alive_count(dead.spawn_key) >= entry.density:
                continue  # 防御分支：容量已满则弃票，保不变量 alive ≤ density
            monster_def = self._monster_defs.get(dead.monster_id)
            if monster_def is None:
                continue
            self.spawn_monster(monster_def, entry)
            spawned += 1
        return spawned

    def fill_initial(self) -> int:
        """进图初始化：每个刷怪点补到 density（有重生票的槽位——如离线 BOSS——跳过）。"""
        spawned = 0
        for entry in self.static_spawn_table.values():
            key = f"{entry.map_id}:{entry.monster_id}"
            deficit = entry.density - self.alive_count(key) - self.pending_count(key)
            monster_def = self._monster_defs.get(entry.monster_id)
            if monster_def is None:
                continue
            for _ in range(max(0, deficit)):
                self.spawn_monster(monster_def, entry)
                spawned += 1
        return spawned

    def seed_boss_timers(self, pending: dict[str, datetime], wall_now: datetime) -> int:
        """把 boss_respawn 表里的未来计时种成重生票（跨实例存活语义）。

        pending: {boss_id: respawn_at(墙钟)}；wall_now 为读库时刻的墙钟。
        返回种子票数。剩余等待时间换算到本实例的 monotonic 时钟上。
        """
        seeded = 0
        for boss_id, respawn_at in pending.items():
            entry = next(
                (
                    e
                    for e in self.static_spawn_table.values()
                    if e.monster_id == boss_id
                ),
                None,
            )
            monster_def = self._monster_defs.get(boss_id)
            if entry is None or monster_def is None:
                continue  # 数据变更后的陈旧行；load_pending_bosses 已负责删过期行
            remaining = max(0.0, (respawn_at - wall_now).total_seconds())
            placeholder = MonsterInstance(
                instance_id=f"boss-seed-{boss_id}",
                monster_id=boss_id,
                spawn_key=f"{entry.map_id}:{entry.monster_id}",
                x=entry.locations[0].x,
                y=entry.locations[0].y,
                monster_def=monster_def,
            )
            self._awaiting[placeholder.instance_id] = placeholder
            self.respawn_timers[placeholder.instance_id] = self.clock() + remaining
            seeded += 1
        return seeded

    # --- BOSS 持久化钩子 ------------------------------------------------------

    def _schedule_boss_hook(self, monster: MonsterInstance) -> None:
        task = asyncio.create_task(self._run_boss_hook(monster))
        self._boss_hook_tasks.add(task)
        task.add_done_callback(self._boss_hook_tasks.discard)

    async def _run_boss_hook(self, monster: MonsterInstance) -> None:
        assert self._on_boss_killed is not None
        try:
            await self._on_boss_killed(self, monster)
        except Exception:  # 持久化失败不阻断战斗主流程
            LOG.exception("boss_respawn write failed for %s", monster.monster_id)

    async def drain_boss_hooks(self) -> None:
        """等待在途 BOSS 写库钩子完成（测试与 todo 15 关停顺序用）。"""
        while self._boss_hook_tasks:
            await asyncio.gather(*set(self._boss_hook_tasks))

    async def aclose(self) -> None:
        """销毁前置清理：取消 spawner task 与在途钩子。"""
        if self.spawner_task is not None:
            self.spawner_task.cancel()
            try:
                await self.spawner_task
            except asyncio.CancelledError:
                pass
            self.spawner_task = None
        for task in set(self._boss_hook_tasks):
            task.cancel()
        await asyncio.gather(*self._boss_hook_tasks, return_exceptions=True)
        self._boss_hook_tasks.clear()


# --- 进程内 zone 注册表：{map_id: ZoneInstance} --------------------------------

_zones: Final[dict[str, ZoneInstance]] = {}


def get_zone(map_id: str) -> ZoneInstance | None:
    return _zones.get(map_id)


async def shutdown_all() -> None:
    """销毁全部实例（测试隔离 / todo 15 优雅关停用）。"""
    for map_id in list(_zones):
        await destroy_zone(map_id)


@lru_cache(maxsize=1)
def _load_gamedata_cached() -> dict[str, object]:
    """进程内缓存一份已校验 gamedata（启动校验失败的进程不该跑到这里）。"""
    return cast("dict[str, object]", load_all())


def _spawn_table_for(
    gamedata: dict[str, object], map_id: str
) -> dict[str, SpawnEntry]:
    """从已校验 gamedata 里取该图的刷怪点子表（键即 loader 的 spawn_key）。"""
    spawns = cast("dict[str, SpawnEntry]", gamedata["spawns"])
    return {key: entry for key, entry in spawns.items() if entry.map_id == map_id}


async def enter_map(
    map_id: str,
    character_id: int,
    gamedata: dict[str, object] | None = None,
    *,
    session_factory: "async_sessionmaker | None" = None,
    clock: Callable[[], float] = time.monotonic,
    spawner_interval: float | None = None,
) -> ZoneInstance:
    """进图：懒创建（首个玩家触发）或复用既有实例。

    - 懒创建：过滤该图 spawns → 读 boss_respawn 种跨实例计时 → 初始填充 →
      起 spawner background task（周期默认 SPAWNER_TICK_SECONDS=1000ms）。
    - 抛 KeyError 当 map_id 不存在于 gamedata/maps（拼错 / 数据被删 → 显式报错；
      spawns 引用不存在的怪物 id 在更早的 load_all 启动校验就被拒绝）。
    """
    from app.sim.spawner import (
        load_pending_bosses,
        make_boss_kill_recorder,
        spawner_loop,
    )

    data = gamedata if gamedata is not None else _load_gamedata_cached()
    maps = cast("dict[str, object]", data["maps"])
    if map_id not in maps:
        raise KeyError(f"unknown map_id '{map_id}' (not in gamedata/maps)")

    zone = _zones.get(map_id)
    if zone is not None:
        zone.enter(character_id)
        return zone

    factory = session_factory if session_factory is not None else SessionLocal
    monsters = cast("dict[str, MonsterDef]", data["monsters"])
    zone = ZoneInstance(
        map_id=map_id,
        static_spawn_table=_spawn_table_for(data, map_id),
        monster_defs=monsters,
        clock=clock,
        on_boss_killed=make_boss_kill_recorder(factory),
    )
    pending = await load_pending_bosses(factory, map_id=map_id)
    zone.seed_boss_timers(pending, datetime.now(UTC))
    zone.fill_initial()
    zone.enter(character_id)
    zone.spawner_task = asyncio.create_task(
        spawner_loop(zone, interval=spawner_interval),
        name=f"spawner:{map_id}",
    )
    _zones[map_id] = zone
    return zone


async def leave_map(map_id: str, character_id: int) -> bool:
    """离图：减引用；引用归零即销毁实例。返回是否发生了销毁。"""
    zone = _zones.get(map_id)
    if zone is None:
        return False
    if not zone.leave(character_id):
        return False
    await destroy_zone(map_id)
    return True


async def destroy_zone(map_id: str) -> bool:
    """强制销毁实例：出注册表 + 取消 spawner task。返回是否确有实例被销毁。"""
    zone = _zones.pop(map_id, None)
    if zone is None:
        return False
    await zone.aclose()
    return True
