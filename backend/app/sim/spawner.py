"""异步 spawner：周期补怪循环 + BOSS 重生计时落 boss_respawn 表。

职责边界（todo 14）：
- **准备怪**：按刷怪点 ``density`` 维持场上数量；被杀的怪按刷怪点
  ``respawn_seconds`` 到期回补（boss 的长间隔已编码在 spawns 数据里，
  如 orc_champion 3600s）。追击/攻击/脱离的 AI 结算不在本层——todo 15 的
  tick loop 接 effects/AI 后调用 :meth:`ZoneInstance.kill_monster` 即可。
- **唯一持久化**：BOSS 被杀 upsert 一行 ``boss_respawn``（boss_id 主键，
  跨实例/跨会话存活）；普通怪物状态一律不落库。
- 怪物状态不持久化 ⇒ 进程重启后普通怪全量重刷，BOSS 按 boss_respawn 行恢复。
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.boss_respawn import BossRespawn

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.gamedata_schema.spawns import SpawnEntry
    from app.sim.zone import MonsterInstance, ZoneInstance

LOG = logging.getLogger(__name__)

# spawner 周期：1000ms 默认（计划规格）。todo 37 数值校准时的统一调参入口是
# combat/balance.py——届时把本常量挪过去即可（balance 的 grep 防回归纪律只约束
# app/combat/ 下模块，sim/ 本地常量不违反系数唯一来源原则）。
SPAWNER_TICK_SECONDS: Final[float] = 1.0


def spawn_target(entry: "SpawnEntry") -> int:
    """刷怪点容量 = density（计划规格：按 density 决定最大怪物数）。"""
    return entry.density


async def spawner_loop(
    zone: "ZoneInstance",
    *,
    interval: float | None = None,
) -> None:
    """每 interval 秒补一次怪；task 被 cancel 即退出（destroy_zone 负责）。"""
    tick = SPAWNER_TICK_SECONDS if interval is None else interval
    LOG.debug("spawner loop started for %s (interval=%.3fs)", zone.map_id, tick)
    try:
        while True:
            await asyncio.sleep(tick)
            zone.refill()
    except asyncio.CancelledError:
        LOG.debug("spawner loop cancelled for %s", zone.map_id)
        raise


SessionFactory = "async_sessionmaker[AsyncSession]"


async def record_boss_kill(
    session_factory: "SessionFactory",
    *,
    map_id: str,
    boss_id: str,
    respawn_seconds: int,
    now: datetime | None = None,
) -> None:
    """BOSS 被杀 → upsert boss_respawn 一行（唯一跨会话世界状态，README:71）。"""
    killed_at = now if now is not None else datetime.now(UTC)
    respawn_at = killed_at + timedelta(seconds=respawn_seconds)
    stmt = (
        pg_insert(BossRespawn)
        .values(
            boss_id=boss_id,
            map_id=map_id,
            killed_at=killed_at,
            respawn_at=respawn_at,
        )
        .on_conflict_do_update(
            index_elements=[BossRespawn.boss_id],
            set_={
                BossRespawn.map_id: map_id,
                BossRespawn.killed_at: killed_at,
                BossRespawn.respawn_at: respawn_at,
            },
        )
    )
    async with session_factory() as db:
        await db.execute(stmt)
        await db.commit()


async def load_pending_bosses(
    session_factory: "SessionFactory",
    *,
    map_id: str,
    now: datetime | None = None,
) -> dict[str, datetime]:
    """读该图仍在等待重生的 BOSS 计时（boss_id → respawn_at 墙钟）。

    已过期行当场删除：离线期间到期的 BOSS 视为立即可刷（zone 初始填充会补上）。
    """
    moment = now if now is not None else datetime.now(UTC)
    pending: dict[str, datetime] = {}
    async with session_factory() as db:
        rows = (
            (await db.execute(select(BossRespawn).where(BossRespawn.map_id == map_id)))
            .scalars()
            .all()
        )
        for row in rows:
            if row.respawn_at > moment:
                pending[row.boss_id] = row.respawn_at
            else:
                await db.delete(row)
        await db.commit()
    return pending


def make_boss_kill_recorder(
    session_factory: "SessionFactory",
) -> "Callable[[ZoneInstance, MonsterInstance], Awaitable[None]]":
    """构造 ZoneInstance.on_boss_killed 钩子：把 BOSS 死亡写成 boss_respawn 行。

    重生间隔取刷怪点定义（数据缺失时退回怪物定义的 respawn_seconds）。
    """

    async def _record(zone: "ZoneInstance", monster: "MonsterInstance") -> None:
        entry = zone.static_spawn_table.get(monster.spawn_key)
        if entry is not None:
            seconds = entry.respawn_seconds
        else:
            seconds = monster.monster_def.respawn_seconds or 0
        await record_boss_kill(
            session_factory,
            map_id=zone.map_id,
            boss_id=monster.monster_id,
            respawn_seconds=seconds,
        )

    return _record
