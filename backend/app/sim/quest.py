"""任务运行时（todo 29）：内存进度账本 + tick 击杀钩子 + 命运之刃竞技场。

状态机（quest_state.status，四态）：未接=无行/无内存条目 → in_progress(进行)
→ completed(达标待交付) → delivered(已交付，终态)。

并发模型与 exp/gold 同源：tick 杀怪在锁外**纯内存**推进账本并推 SSE（战斗
路径不取锁、不做 DB 往返），脏行由 persist_session_state 批次在同一事务落库；
REST 端点在 session.lock 内先 hydrate 再读写同一份内存——活跃会话期间内存
账本是事实源，DB 行是持久化投影。进程重启后首次访问从 DB 恢复（hydrate）。

命运之刃降级实现：boss_instance 任务接取即传送进独立 zone 实例（复用
ZoneInstance 机制，不改 sim/zone.py），场内一只弱化分身：
- 全属性 ×70%（WEAK_CLONE_RATIO）；
- boss=False → 不触发 boss_respawn 持久化钩子、不种子跨实例计时；
- id 加 ``_weak_clone`` 后缀 → 不命中本体掉落表（BossInstanceSpec.weak_clone
  契约「不掉落本体掉落表」），击杀匹配按 clone id 别名识别。
竞技场不启动 spawner task（单怪一次性挑战，respawn_seconds=0 的票无人消费），
实例登记进 zone._zones 使 leave_map/destroy_zone 与宽限超时 teardown 复用。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.gamedata_loader import load_all
from app.gamedata_schema.quests import QuestDef, QuestKind
from app.models.quest_state import QuestState, QuestStatus

if TYPE_CHECKING:
    from app.sim.session import PlayerSession
    from app.sim.zone import MonsterDef, MonsterInstance

LOG = logging.getLogger(__name__)

# SSE 事件名（api/quest.py 与 tick 钩子共用；前端按需监听）
EV_QUEST_ACCEPT: Final[str] = "quest_accept"
EV_QUEST_PROGRESS: Final[str] = "quest_progress"
EV_QUEST_COMPLETE: Final[str] = "quest_complete"

# 弱化分身参数（命运之刃降级实现；docs/MVP/README.md §9 职业专属）
WEAK_CLONE_RATIO: Final[float] = 0.7
WEAK_CLONE_SUFFIX: Final[str] = "_weak_clone"

# 竞技场命名：默认 f"{quest_id}-arena"；命运之刃按任务规格固定名。
ARENA_SUFFIX: Final[str] = "-arena"
_ARENA_NAMES: Final[dict[str, str]] = {"warrior_fate_blade": "fate-blade-arena"}


@dataclass(slots=True)
class MemQuest:
    """单个任务的内存进度条目（quest_state 行的战斗侧投影）。"""

    quest_id: str
    status: QuestStatus
    count: int = 0


# character_id → {quest_id: MemQuest}；_hydrated 防 DB 重复装载；_dirty 待落库集合。
_mem: Final[dict[int, dict[str, MemQuest]]] = {}
_hydrated: Final[set[int]] = set()
_dirty: Final[set[tuple[int, str]]] = set()


def reset_registry() -> None:
    """测试专用：清空内存账本（生产路径永不调用）。"""
    _mem.clear()
    _hydrated.clear()
    _dirty.clear()


def _quests() -> dict[str, QuestDef]:
    return cast("dict[str, QuestDef]", load_all()["quests"])


def states_of(character_id: int) -> dict[str, MemQuest]:
    """只读视图：该角色内存任务账本（未 hydrate 时为空 dict）。"""
    return _mem.get(character_id, {})


async def hydrate(character_id: int) -> None:
    """DB→内存装载该角色全部 quest_state 行（幂等；进程内首次访问触发）。"""
    if character_id in _hydrated:
        return
    async with SessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(QuestState).where(QuestState.character_id == character_id)
                )
            )
            .scalars()
            .all()
        )
    bucket = _mem.setdefault(character_id, {})
    for row in rows:
        raw = row.progress.get("count") if isinstance(row.progress, dict) else None
        bucket[row.quest_id] = MemQuest(
            quest_id=row.quest_id,
            status=row.status,
            count=raw if isinstance(raw, int) and not isinstance(raw, bool) else 0,
        )
    _hydrated.add(character_id)


def mark_dirty(character_id: int, quest_id: str) -> None:
    """tick 路径内存变更后标记待落库（persist 批次消费；REST 直写不用）。"""
    _dirty.add((character_id, quest_id))


async def flush_dirty(db: AsyncSession, character_id: int) -> int:
    """把该角色脏任务行 upsert 进 DB（persist_session_state 同事务调用）。

    必须在 session.lock 临界区内调用（与 REST 写事务串行化）。返回落库行数。
    """
    bucket = _mem.get(character_id)
    if not bucket:
        return 0
    n = 0
    for quest_id, mq in bucket.items():
        if (character_id, quest_id) not in _dirty:
            continue
        progress: dict[str, object] = {"count": mq.count}
        row = (
            await db.execute(
                select(QuestState).where(
                    QuestState.character_id == character_id,
                    QuestState.quest_id == quest_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            db.add(
                QuestState(
                    character_id=character_id,
                    quest_id=quest_id,
                    status=mq.status,
                    progress=progress,
                )
            )
        else:
            row.status = mq.status
            row.progress = progress
        _dirty.discard((character_id, quest_id))
        n += 1
    return n


# --- 击杀钩子（tick 结算事件挂钩）---------------------------------------------


def _publish(session: PlayerSession, event: str, payload: dict[str, object]) -> None:
    session.sse_state.publish(event, json.dumps(payload, ensure_ascii=False))


def _progress_payload(qd: QuestDef, mq: MemQuest) -> dict[str, object]:
    return {
        "quest_id": qd.id,
        "name": qd.name,
        "kind": qd.kind.value,
        "count": mq.count,
        "need": qd.target_count,
        "status": mq.status.value,
    }


def advance(
    session: PlayerSession, qd: QuestDef, mq: MemQuest, *, delta: int = 1
) -> dict[str, object]:
    """推进计数并维护状态机；返回 quest_progress payload（调用方锁外发布）。

    达目标值 → completed（待 complete 端点发奖）；计数钳到目标值防溢出。
    REST 调用方持 session.lock 时只做内存变更，SSE 在锁外发布（横切约束）。
    """
    mq.count = min(mq.count + delta, qd.target_count)
    became_ready = False
    if mq.count >= qd.target_count and mq.status is QuestStatus.in_progress:
        mq.status = QuestStatus.completed
        became_ready = True
    mark_dirty(session.character_id, qd.id)
    payload = _progress_payload(qd, mq)
    if became_ready:
        _publish(
            session,
            "log",
            {
                "kind": "quest_ready",
                "text": f"任务【{qd.name}】目标达成，回去交付吧",
            },
        )
    return payload


def on_monster_killed(session: PlayerSession, killed: MonsterInstance) -> None:
    """tick 击杀结算钩子（_kill_monster 末尾调用；锁外纯内存 + SSE）。

    只推进 kind=kill 的进行中任务：普通悬赏任意地图计数；boss_instance 任务
    仅在对应竞技场内认分身击杀（分身 id 带后缀，天然不匹配野外本体）。
    未接任务 / 无内存账本时静默返回（常态热路径零开销）。
    """
    bucket = _mem.get(session.character_id)
    if not bucket:
        return
    zone_map = session.zone.map_id if session.zone is not None else None
    quests = _quests()
    for mq in bucket.values():
        if mq.status is not QuestStatus.in_progress:
            continue
        qd = quests.get(mq.quest_id)
        if qd is None or qd.kind is not QuestKind.kill:
            continue
        # 分身挑战认 _weak_clone 别名 id；普通悬赏认本体 id
        expected_id = (
            qd.target_id + WEAK_CLONE_SUFFIX
            if qd.boss_instance is not None
            else qd.target_id
        )
        if killed.monster_id != expected_id:
            continue
        if qd.boss_instance is not None and zone_map != arena_map_id(qd):
            # 分身挑战：只在对应竞技场内计数（野外杀本体不算）
            continue
        payload = advance(session, qd, mq)
        _publish(session, EV_QUEST_PROGRESS, payload)


# --- 命运之刃竞技场（复用 zone 机制的降级实现）---------------------------------


def arena_map_id(qd: QuestDef) -> str:
    """任务竞技场地图 id（fate-blade-arena 等固定名优先，否则 {quest_id}-arena）。"""
    return _ARENA_NAMES.get(qd.id, f"{qd.id}{ARENA_SUFFIX}")


def weak_clone_def(target: MonsterDef) -> MonsterDef:
    """弱化分身定义：全属性 ×70%；boss=False 不写 boss_respawn；id 加后缀不掉本体掉落。"""

    def _scaled(value: int, *, floor: int = 0) -> int:
        return max(floor, int(value * WEAK_CLONE_RATIO))

    return target.__class__(
        id=target.id + WEAK_CLONE_SUFFIX,
        name=f"弱化的{target.name}",
        level=max(1, _scaled(target.level)),
        hp=max(1, _scaled(target.hp)),
        attack=_scaled(target.attack),
        defense=_scaled(target.defense),
        magic_defense=_scaled(target.magic_defense),
        accuracy=_scaled(target.accuracy),
        agility=_scaled(target.agility),
        exp=max(1, _scaled(target.exp)),
        attack_speed=target.attack_speed,
        move_speed=target.move_speed,
        undead=target.undead,
        boss=False,
        respawn_seconds=None,
    )


async def enter_arena(session: PlayerSession, qd: QuestDef) -> str:
    """把会话切进任务竞技场（锁内换图内存操作；返回 arena map id）。

    首个玩家触发懒创建：弱化分身 ×density 1，无 spawner task（一次性挑战）。
    实例登记进 zone._zones —— leave_map/destroy_zone/宽限超时 teardown 全部复用。
    """
    from app.gamedata_schema.spawns import SpawnEntry, SpawnLocation
    from app.sim import zone as zone_mod
    from app.sim.zone import ZoneInstance

    arena_id = arena_map_id(qd)
    old = session.zone
    if old is not None and old.map_id != arena_id:
        await zone_mod.leave_map(old.map_id, session.character_id)

    z = zone_mod.get_zone(arena_id)
    if z is None:
        monsters = cast("dict[str, MonsterDef]", load_all()["monsters"])
        target = monsters[qd.target_id]
        clone = weak_clone_def(target)
        entry = SpawnEntry(
            map_id=arena_id,
            monster_id=clone.id,
            density=1,
            respawn_seconds=0,
            count=1,
            locations=[SpawnLocation(x=1, y=1)],
        )
        z = ZoneInstance(
            map_id=arena_id,
            static_spawn_table={f"{arena_id}:{clone.id}": entry},
            monster_defs={clone.id: clone},
        )
        z.fill_initial()
        zone_mod._zones[arena_id] = z
    z.enter(session.character_id)
    session.zone = z
    LOG.info(
        "character %s entered quest arena %s (quest=%s)",
        session.character_id,
        arena_id,
        qd.id,
    )
    return arena_id


async def leave_arena_to(session: PlayerSession, map_id: str) -> str | None:
    """离开竞技场回到指定地图（交付完成回城用）；返回实际所在图 id。"""
    from app.sim import zone as zone_mod

    old = session.zone
    if old is not None and old.map_id != map_id:
        await zone_mod.leave_map(old.map_id, session.character_id)
    try:
        session.zone = await zone_mod.enter_map(map_id, session.character_id)
    except KeyError:
        # 目标图不在 gamedata（测试假图）→ 无图模式继续
        session.zone = None
    return session.zone.map_id if session.zone is not None else None


__all__ = [
    "EV_QUEST_ACCEPT",
    "EV_QUEST_COMPLETE",
    "EV_QUEST_PROGRESS",
    "WEAK_CLONE_RATIO",
    "MemQuest",
    "advance",
    "arena_map_id",
    "enter_arena",
    "flush_dirty",
    "hydrate",
    "leave_arena_to",
    "mark_dirty",
    "on_monster_killed",
    "reset_registry",
    "states_of",
    "weak_clone_def",
]
