"""1Hz tick 循环（todo 15）：玩家出手→怪物 AI→regen→自动吃药→SSE→每 5 tick 落库。

管线顺序（每 tick）：
    1. buff/dot 结算（effects.tick_effect，伤害并入本 tick 承伤）
    2. 玩家出手（compute_panel 面板 → skills.try_cast 选技 → hit.hit_check
       → damage.physical_damage；击杀走 zone.kill_monster + drops.roll_drop）
    3. 怪物 AI 最小集：场上第一只活怪按 attack_speed 回击（D12 距离档模型，
       不做网格寻路）
    4. regen：本 tick 未承伤则 HP/MP 各回 1（MVP 占位节奏）
    5. 自动吃药（potion.plan_potion_use 纯函数出参直接落地）
    6. 每 tick 至少一条 stat 事件推 SSE（1Hz 连续性锚点）；**publish 在锁外**
    7. 每 PERSIST_EVERY_TICKS tick：``async with session.lock`` 内落库
       （HP/MP/exp/gold/last_active_at=now() + 背包增量 + buff_json 快照）→ flush_snapshot；

并发纪律（横切约束，todo 23–30 同守）：
- 战斗纯计算在锁外（防慢客户端堵 REST）；锁临界区仅限 DB 事务+flush/落库，
  不含 SSE 推送；flush_snapshot 方向 DB→内存、自身不取锁、由持锁方调用。
- 断线语义：SSE close 只标记 disconnected + 宽限期计时（session.on_disconnect）；
  宽限内重连无缝续跑（事件持续入 buffer 供 Last-Event-ID 补全），超时才由本
  模块取消 task + leave_map + release_session。心跳刷新为单行 UPDATE 豁免取锁。
- 抢占语义：start_tick_loop 发现旧 tick_task 在跑先 cancel + await 取消完成
  再启新 task（防半开连接双 task）。

MVP 边界：不做离线收益（_compensation_stub 空壳）；玩家 hp≤0 仅钳到 1 保命
（死亡/复活系统不在 MVP）；多 worker 禁用（单进程单例注册表的前提）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Final, cast
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.combat import balance
from app.combat.damage import physical_damage
from app.combat.drops import DroppedItem, roll_drop
from app.combat.effects import EffectInstance, get_effect, tick_effect
from app.combat.hit import hit_check
from app.combat.potion import PotionSpec, plan_potion_use
from app.combat.skills import SkillRegistry, SkillState, load_registry, try_cast
from app.combat.stats import ClassKey, StatPanel, compute_panel
from app.core.config import settings
from app.core.db import SessionLocal
from app.gamedata_loader import load_all
from app.gamedata_schema.skills import SkillTarget
from app.models.character import Character
from app.models.equipment_slot import EquipmentSlot
from app.models.item_instance import ItemInstance
from app.models.skill_state import SkillState as SkillStateRow
from app.sim.pet import disperse_all, tick_pets
from app.sim.quest import (
    flush_dirty as _flush_quest_rows,
)
from app.sim.quest import (
    hydrate as _hydrate_quests,
)
from app.sim.quest import (
    on_monster_killed as _quest_on_kill,
)
from app.sim.session import (
    PlayerSession,
    build_potion_catalog,
    release_session,
)
from app.sim.travel import (
    TravelState,
    clear_travel,
    has_travel,
    pop_due_travel,
)

if TYPE_CHECKING:
    from app.combat.drops import DropResult
    from app.gamedata_schema import DropEntry, ItemDef
    from app.sim.zone import MonsterInstance, ZoneInstance

LOG = logging.getLogger(__name__)

TICK_SECONDS: Final[float] = 1.0
PERSIST_EVERY_TICKS: Final[int] = 5

# SSE 事件名（与前端 sse.ts 监听清单对齐）
EV_LOG: Final[str] = "log"
EV_STAT: Final[str] = "stat"
EV_DROP: Final[str] = "drop"
EV_EXP: Final[str] = "exp"
EV_ZONE_CHANGE: Final[str] = "zone_change"
# 内部回城标记（todo 25）：simulate_tick 产出、tick_loop 拦截后调 trigger_return_city，
# 不对外发布（下划线前缀 = 非公开事件名，前端 sse.ts 监听清单不含）。
EV_RECALL: Final[str] = "_recall"

# ≥35 级技能必须技能书习得（docs/MVP/README.md §7：35 技能书掉落/引导任务）；
# 自动习得（首次施放即 0 级）只覆盖 <35 的按级技能。api/skill.py 同一常量口径。
BOOK_REQUIRED_LEVEL: Final[int] = 35

# 装备槽缓存：character_id → {slot.value: item_instance_id | None}（todo 24）。
# simulate_tick 是纯内存计算（无 DB），磨损需要知道哪件实例穿在武器/衣服槽——
# 映射由 start_tick_loop 启动时、persist_session_state 落库事务内、以及
# inventory equip/unequip 提交后（reload_equip_cache）刷新；测试可直接写本表
# 驱动纯内存用例。注销路径（shutdown/宽限超时）弹出防陈旧增长。
_EQUIP_CACHE: Final[dict[int, dict[str, int | None]]] = {}


@dataclass(frozen=True)
class GroundDrop:
    """地面掉落实例（todo 25）：未被拾取的掉落保留在地面池，供手动拾取 API 领取。

    instance_id 预留 todo 26/30：接通扣卷/传送员流程后，地面实例可能对应已持久化
    的 item_instance 行 id；当前 tick 掉落均为纯内存条目（恒 None）。
    """

    drop_id: str
    item_id: str
    qty: int
    tier: str = "normal"
    extra_json: dict[str, int] | None = None
    instance_id: int | None = None


# 地面掉落池：character_id → 未拾取掉落（进程内存；会话注销/宽限超时清空）。
# 同步增删在单事件循环内天然原子；REST 拾取在 session.lock 内 find→事务→pop 防双拾。
_GROUND_DROPS: Final[dict[int, list[GroundDrop]]] = {}


def retain_ground_drop(
    character_id: int,
    item_id: str,
    qty: int,
    *,
    tier: str = "normal",
    extra_json: dict[str, int] | None = None,
    instance_id: int | None = None,
) -> GroundDrop:
    """掉落落地：生成唯一 drop_id 入池并返回（tick 结算与测试共用入口）。"""
    drop = GroundDrop(
        drop_id=f"g{uuid4().hex[:12]}",
        item_id=item_id,
        qty=qty,
        tier=tier,
        extra_json=extra_json,
        instance_id=instance_id,
    )
    _GROUND_DROPS.setdefault(character_id, []).append(drop)
    return drop


def ground_drops(character_id: int) -> tuple[GroundDrop, ...]:
    """只读视图：该角色地面全部未拾取掉落。"""
    return tuple(_GROUND_DROPS.get(character_id, ()))


def find_ground_drop(
    character_id: int,
    *,
    drop_id: str | None = None,
    instance_id: int | None = None,
) -> GroundDrop | None:
    """按 drop_id 或 instance_id 定位一条地面掉落；都不匹配返回 None。"""
    for drop in _GROUND_DROPS.get(character_id, ()):
        if drop_id is not None and drop.drop_id == drop_id:
            return drop
        if instance_id is not None and drop.instance_id == instance_id:
            return drop
    return None


def pop_ground_drop(
    character_id: int,
    *,
    drop_id: str | None = None,
    instance_id: int | None = None,
) -> GroundDrop | None:
    """拾取成功后出池；不存在返回 None（REST 路径在锁内 find→事务→pop）。"""
    drops = _GROUND_DROPS.get(character_id)
    if drops is None:
        return None
    for idx, drop in enumerate(drops):
        if drop_id is not None and drop.drop_id == drop_id:
            return drops.pop(idx)
        if instance_id is not None and drop.instance_id == instance_id:
            return drops.pop(idx)
    return None


async def _refresh_equip_cache(db: AsyncSession, character_id: int) -> None:
    """DB→内存重读装备槽映射（调用方保证在 session.lock 临界区内）。"""
    rows = (
        (
            await db.execute(
                select(EquipmentSlot).where(EquipmentSlot.character_id == character_id)
            )
        )
        .scalars()
        .all()
    )
    _EQUIP_CACHE[character_id] = {r.slot.value: r.item_instance_id for r in rows}


async def reload_equip_cache(character_id: int) -> None:
    """独立连接重读装备槽缓存（equip/unequip 提交后调用，保持磨损记账新鲜）。"""
    async with SessionLocal() as db:
        await _refresh_equip_cache(db, character_id)


@lru_cache(maxsize=1)
def _gamedata() -> dict[str, object]:
    """进程内缓存一份已校验 gamedata（zone._load_gamedata_cached 同款）。"""
    return cast("dict[str, object]", load_all())


@lru_cache(maxsize=1)
def _bind_cities() -> frozenset[str]:
    """主城 id 集（todo 30 回城绑定）：来自 teleport.json bind_cities。

    数据缺失（测试假 gamedata）时回退出生城，保证绑定语义永不悬空。
    """
    try:
        table = cast("dict[str, object]", _gamedata()["teleport"])["table"]
        cities = getattr(table, "bind_cities", None)
        if isinstance(cities, (list, tuple)) and cities:
            return frozenset(str(c) for c in cities)
    except KeyError:
        pass
    return frozenset({balance.BIRTH_MAP_ID})


@lru_cache(maxsize=1)
def _skills() -> SkillRegistry:
    return load_registry()


@lru_cache(maxsize=1)
def _potion_catalog() -> dict[str, PotionSpec]:
    return build_potion_catalog()


# --- buff 持久化（todo 26）：character.buff_json 快照 + 真实时间过期 -----------------


def buffs_to_payload(buffs: list[EffectInstance]) -> dict[str, object]:
    """未过期效果实例 → buff_json 快照（saved_at 墙钟供离线时长折损）。

    已过期实例不入快照（落库即清理）；结构见 models/character.buff_json 注释。
    """
    records = [
        {
            "definition_id": inst.definition_id,
            "owner_id": inst.owner_id,
            "remaining_ticks": inst.remaining_ticks,
            "params": dict(inst.params),
            "blocks_targeting": inst.blocks_targeting,
            "blocks_movement": inst.blocks_movement,
            "absorb_remaining": inst.absorb_remaining,
        }
        for inst in buffs
        if not inst.expired
    ]
    return {"buffs": records, "saved_at": time.time()}


def payload_to_buffs(payload: object) -> list[EffectInstance]:
    """buff_json → 效果实例列表；按 saved_at 折损离线时长，过期/残缺记录丢弃。

    真实时间过期语义（计划红线）：登出不清空 buff，离线秒数从 remaining_ticks
    中扣除——重登时已到期的效果自然消失，未到期的带余量继续走 tick。
    """
    if not isinstance(payload, Mapping):
        return []
    raw = payload.get("buffs")
    if not isinstance(raw, list):
        return []
    saved_at = payload.get("saved_at")
    elapsed = (
        max(0, int(time.time() - float(saved_at)))
        if isinstance(saved_at, (int, float))
        else 0
    )
    out: list[EffectInstance] = []
    for record in raw:
        if not isinstance(record, Mapping):
            continue
        remaining_raw = record.get("remaining_ticks")
        definition_raw = record.get("definition_id")
        absorb_raw = record.get("absorb_remaining", 0)
        if (
            not isinstance(remaining_raw, int)
            or isinstance(remaining_raw, bool)
            or not isinstance(definition_raw, str)
        ):
            continue  # 残缺记录丢弃，不阻断其余 buff 恢复
        remaining = remaining_raw - elapsed
        absorb_remaining = (
            absorb_raw
            if isinstance(absorb_raw, int) and not isinstance(absorb_raw, bool)
            else 0
        )
        if remaining <= 0:
            continue  # 离线期间已自然到期
        params_raw = record.get("params")
        params: dict[str, int | float] = {}
        if isinstance(params_raw, Mapping):
            params = {
                str(k): v
                for k, v in params_raw.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            }
        out.append(
            EffectInstance(
                definition_id=definition_raw,
                owner_id=str(record.get("owner_id", "")),
                remaining_ticks=remaining,
                params=params,
                blocks_targeting=bool(record.get("blocks_targeting", False)),
                blocks_movement=bool(record.get("blocks_movement", False)),
                absorb_remaining=max(0, absorb_remaining),
            )
        )
    return out


async def hydrate_buffs(session: PlayerSession) -> int:
    """DB→内存重读 buff_json 并按真实时间折损（登录/用药前调用，幂等替换）。

    返回恢复的实例数；角色行不存在返回 0。调用方自行保证与写路径的并发安全
    （start_tick_loop 在会话注册前调用；REST 用药路径在 session.lock 内调用）。
    """
    async with SessionLocal() as db:
        row = await db.get(Character, session.character_id)
        if row is None:
            return 0
        payload = row.buff_json
    restored = payload_to_buffs(payload)
    session.buffs = restored
    return len(restored)


# --- 生命周期 ------------------------------------------------------------------


async def start_tick_loop(
    session: PlayerSession, *, interval: float | None = None
) -> asyncio.Task[None]:
    """为会话启动 tick task；已有在跑 task 先抢占取消（防半开连接双 task）。"""
    old = session.tick_task
    if old is not None and not old.done():
        old.cancel()
        with suppress(asyncio.CancelledError):
            await old
    if session.zone is None:
        from app.sim.zone import enter_map

        try:
            session.zone = await enter_map(
                session.char_snapshot.map_id, session.character_id
            )
        except KeyError:
            # 测试注入的假地图不在 gamedata → 无图模式（战斗阶段自动跳过）
            LOG.warning(
                "map %s not in gamedata; tick runs headless (character %s)",
                session.char_snapshot.map_id,
                session.character_id,
            )
    try:  # 装备槽缓存预加载（磨损记账依据）；失败不阻断挂机启动
        await reload_equip_cache(session.character_id)
    except Exception:
        LOG.exception("equip cache load failed for character %s", session.character_id)
    try:  # buff 恢复（todo 26）：DB 快照 + 离线时长折损；失败不阻断挂机启动
        await hydrate_buffs(session)
    except Exception:
        LOG.exception("buff hydrate failed for character %s", session.character_id)
    try:  # 任务进度恢复（todo 29）：DB→内存账本；失败不阻断挂机启动
        await _hydrate_quests(session.character_id)
    except Exception:
        LOG.exception("quest hydrate failed for character %s", session.character_id)
    session.mark_connected()
    task = asyncio.create_task(
        tick_loop(session, interval=interval),
        name=f"tick:{session.character_id}",
    )
    session.tick_task = task
    return task


async def cancel_tick_loop(session: PlayerSession) -> bool:
    """优雅取消 tick task（cancel + 等取消完成）。返回是否确有任务被取消。"""
    task = session.tick_task
    session.tick_task = None
    if task is None or task.done():
        return False
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    return True


async def shutdown_session(character_id: int) -> bool:
    """显式登出路径：取消 tick → 离图 → 心跳刷新（豁免取锁）→ 注销会话。"""
    from app.sim.zone import leave_map

    session = release_session(character_id)
    if session is None:
        return False
    await cancel_tick_loop(session)
    disperse_all(session)  # 召唤物随会话注销解散（todo 28 接通生命周期钩子）
    zone = session.zone
    if zone is not None:
        with suppress(KeyError):
            await leave_map(zone.map_id, character_id)
        session.zone = None
    _EQUIP_CACHE.pop(character_id, None)  # 磨损记账缓存随会话注销清理（todo 24）
    _GROUND_DROPS.pop(character_id, None)  # 地面掉落池随会话注销清理（todo 25）
    clear_travel(character_id)  # 行进态随会话注销取消（todo 30）
    async with SessionLocal() as db:
        await db.execute(
            update(Character)
            .where(Character.id == character_id)
            .values(last_active_at=func.now())
        )
        await db.commit()
    return True


async def shutdown_all_sessions() -> None:
    """关停全部会话（lifespan shutdown / 测试隔离用）：取消 tick + 离图 + 注销。

    不刷心跳（进程都要停了，落库态以最后一次 5-tick 落库为准）。
    """
    from app.sim.session import all_character_ids, reset_sessions
    from app.sim.zone import leave_map

    for character_id in all_character_ids():
        session = release_session(character_id)
        if session is None:
            continue
        await cancel_tick_loop(session)
        disperse_all(session)  # 召唤物随会话注销解散（todo 28）
        zone = session.zone
        if zone is not None:
            with suppress(KeyError):
                await leave_map(zone.map_id, character_id)
            session.zone = None
        _EQUIP_CACHE.pop(character_id, None)
        clear_travel(character_id)  # 行进态随会话注销取消（todo 30）
    reset_sessions()


async def _teardown_on_grace_timeout(session: PlayerSession) -> None:
    """宽限期耗尽（tick task 自身视角）：离图 + 注销会话，task 自然返回。"""
    from app.sim.zone import leave_map

    zone = session.zone
    if zone is not None:
        with suppress(KeyError):
            await leave_map(zone.map_id, session.character_id)
        session.zone = None
    disperse_all(session)  # 宽限超时：召唤物解散（重连后 pets 为空）
    release_session(session.character_id)
    _EQUIP_CACHE.pop(session.character_id, None)
    _GROUND_DROPS.pop(session.character_id, None)
    clear_travel(session.character_id)  # 行进态随宽限超时取消（todo 30）
    session.tick_task = None
    LOG.info("grace period expired; tick loop stopped for %s", session.character_id)


def _grace_expired(session: PlayerSession) -> bool:
    """断线且宽限期已耗尽（settings 动态读取，测试可 monkeypatch 调快）。"""
    if not session.disconnected or session.grace_period_started_at is None:
        return False
    elapsed = time.monotonic() - session.grace_period_started_at
    return elapsed >= settings.SSE_GRACE_PERIOD_SECONDS


# --- 主循环 --------------------------------------------------------------------


async def tick_loop(session: PlayerSession, *, interval: float | None = None) -> None:
    """1Hz 主循环；被 cancel 即退出。每 5 tick 锁内落库，SSE 推送恒在锁外。"""
    tick_seconds = TICK_SECONDS if interval is None else interval
    n = 0
    LOG.info("tick loop started for character %s", session.character_id)
    try:
        while True:
            await asyncio.sleep(tick_seconds)
            if _grace_expired(session):
                await _teardown_on_grace_timeout(session)
                return
            n += 1
            # 锁外战斗纯计算 + 内存变更（防慢客户端堵 REST 的镜像面：不堵 tick）
            # 锁外战斗纯计算 + 内存变更（防慢客户端堵 REST 的镜像面：不堵 tick）；
            # 行进中（todo 30）simulate_tick 内部跳过战斗/技能/吃药，仅 stat 心跳。
            events = simulate_tick(session, n)
            # 行进到点（todo 30）：切 zone_id + 清 traveling_until + 主城绑定更新；
            # 失败不阻断主循环（下 tick 重试——注册表未消费仍会命中）。
            arrival = pop_due_travel(session.character_id)
            if arrival is not None:
                try:
                    await complete_travel(session, arrival)
                except Exception:
                    LOG.exception(
                        "travel arrival failed for character %s",
                        session.character_id,
                    )
            # 自动回城触发器（todo 25）：拦截内部 recall 标记 → 调回城动作
            # （占位实现直读 bind_city 切 zone_id；TODO(todo-26) 扣卷 / TODO(todo-30)
            # 传送员就绪后在 trigger_return_city 接通）。标记事件不对外发布。
            if any(name == EV_RECALL for name, _ in events):
                events = [(nm, d) for nm, d in events if nm != EV_RECALL]
                try:
                    await trigger_return_city(session)
                except Exception:
                    LOG.exception(
                        "auto recall failed for character %s", session.character_id
                    )
            if n % PERSIST_EVERY_TICKS == 0:
                try:
                    async with session.lock:
                        await persist_session_state(session)
                except Exception:
                    LOG.exception(
                        "tick persist failed for character %s", session.character_id
                    )
            # SSE 推送在锁外（慢客户端不得阻塞 REST 写路径取锁）
            for event_name, data in events:
                session.sse_state.publish(event_name, data)
    except asyncio.CancelledError:
        LOG.info("tick loop cancelled for character %s", session.character_id)
        raise


async def persist_session_state(session: PlayerSession) -> None:
    """增量落库：character 行（HP/MP/exp/gold/last_active_at）+ 背包增删改 + 耐久。

    **必须在 ``session.lock`` 临界区内调用**（与 REST 写事务同一把锁串行化，
    防 commit 与 flush 之间的调度窗口被以旧快照覆盖）；commit 后锁内调
    flush_snapshot（DB→内存重读）。buff_json 随本批次一并快照落库（todo 26，
    未过期实例 + saved_at 墙钟；重登时按真实时间折损）。
    耐久（todo 24）：装备槽引用的实例随每 PERSIST_EVERY_TICKS 批次一并 UPDATE
    （磨损每 tick 只改内存，不逐 tick 写库），同事务刷新装备槽缓存。
    """
    snap = session.char_snapshot
    async with SessionLocal() as db:
        await db.execute(
            update(Character)
            .where(Character.id == session.character_id)
            .values(
                hp=snap.hp,
                mp=snap.mp,
                exp=snap.exp,
                gold=snap.gold,
                last_active_at=func.now(),
                buff_json=buffs_to_payload(session.buffs),
            )
        )
        for row_id in session.removed_item_ids:
            await db.execute(delete(ItemInstance).where(ItemInstance.id == row_id))
        for row_id in session.dirty_quantities:
            row = next((r for r in session.inventory if r.id == row_id), None)
            if row is not None and row.id not in session.removed_item_ids:
                await db.execute(
                    update(ItemInstance)
                    .where(ItemInstance.id == row_id)
                    .values(quantity=row.quantity)
                )
        for row in session.pending_items:
            db.add(row)
        # 耐久落库（todo 24）：只写装备槽引用的实例（≤10 行），背包物不涉及。
        equipped_ids = {
            iid
            for iid in _EQUIP_CACHE.get(session.character_id, {}).values()
            if iid is not None
        }
        for row in session.inventory:
            if (
                row.id is not None
                and row.id in equipped_ids
                and row.durability is not None
            ):
                await db.execute(
                    update(ItemInstance)
                    .where(ItemInstance.id == row.id)
                    .values(durability=row.durability)
                )
        # 同事务刷新装备槽映射（穿戴变更由 equip/unequip 提交后即时刷新兜底）
        await _refresh_equip_cache(db, session.character_id)
        # 技能熟练度落库（todo 28）：全量 upsert（REST /use 只记内存，本批次覆盖写）
        for state in session.skill_states.values():
            stmt = (
                pg_insert(SkillStateRow)
                .values(
                    character_id=session.character_id,
                    skill_id=state.skill_id,
                    level=state.level,
                    exp=state.exp,
                )
                .on_conflict_do_update(
                    constraint="uq_skill_state_character_skill",
                    set_={"level": state.level, "exp": state.exp},
                )
            )
            await db.execute(stmt)
        # 任务进度落库（todo 29）：tick 杀怪的内存账本脏行随本批次 upsert
        await _flush_quest_rows(db, session.character_id)
        await db.commit()
    await session.flush_snapshot()


async def trigger_return_city(session: PlayerSession) -> str:
    """自动回城动作（todo 25 占位实现）：直读 bind_city 切 zone_id。

    锁内单行 UPDATE character.map_id + 换图（离旧图 → 懒进绑定城）→ flush_snapshot；
    SSE zone_change 在锁外发布。已在绑定城时幂等跳过换图。

    TODO(todo-26 回城卷): 回城卷道具就绪后在此接通扣卷流程——消耗 return_scroll
    一张才允许触发（当前占位不消耗任何道具）。
    TODO(todo-30 传送员): 传送员 NPC 就绪后接通「记录出发地 → 付费传送/返回」流程，
    届时本函数降级为共享底层动作。
    """
    from app.sim.zone import enter_map, leave_map

    target = session.char_snapshot.bind_city
    async with session.lock:
        async with SessionLocal() as db:
            await db.execute(
                update(Character)
                .where(Character.id == session.character_id)
                .values(map_id=target)
            )
            await db.commit()
        old_zone = session.zone
        if old_zone is not None and old_zone.map_id != target:
            with suppress(KeyError):
                await leave_map(old_zone.map_id, session.character_id)
            session.zone = None
        if session.zone is None:
            try:
                session.zone = await enter_map(target, session.character_id)
            except KeyError:
                # 绑定城不在 gamedata（测试假城市名）→ 无图模式继续跑
                LOG.warning(
                    "bind city %s not in gamedata; recall headless (%s)",
                    target,
                    session.character_id,
                )
        await session.flush_snapshot()
    session.sse_state.publish(
        EV_ZONE_CHANGE,
        json.dumps({"map_id": target, "reason": "recall"}, ensure_ascii=False),
    )
    return target


async def complete_travel(session: PlayerSession, state: TravelState) -> str:
    """行进到点（todo 30）：锁内切 zone_id + 清 traveling_until + 主城绑定更新。

    与 trigger_return_city 同构：单事务 UPDATE character（map_id/traveling_until/
    bind_city）→ 离旧图 → 懒进目标图 → flush_snapshot；SSE zone_change 锁外。
    目标为主城时自动 bind_city=target（最近访问主城绑定，回城卷语义）。
    """
    from app.sim.zone import enter_map, leave_map

    target = state.target_map_id
    async with session.lock:
        async with SessionLocal() as db:
            row = await db.get(Character, session.character_id)
            if row is not None:
                row.map_id = target
                row.traveling_until = None
                if target in _bind_cities():
                    row.bind_city = target  # 最近访问主城自动绑定
            await db.commit()
        old_zone = session.zone
        if old_zone is not None and old_zone.map_id != target:
            with suppress(KeyError):
                await leave_map(old_zone.map_id, session.character_id)
            session.zone = None
        if session.zone is None:
            try:
                session.zone = await enter_map(target, session.character_id)
            except KeyError:
                # 目标图不在 gamedata（测试假图）→ 无图模式继续跑
                LOG.warning(
                    "travel target %s not in gamedata; headless (%s)",
                    target,
                    session.character_id,
                )
        await session.flush_snapshot()
    session.sse_state.publish(
        EV_ZONE_CHANGE,
        json.dumps({"map_id": target, "reason": "travel"}, ensure_ascii=False),
    )
    return target


# --- 单 tick 战斗结算（纯内存变更，无锁无 DB）-------------------------------------


def simulate_tick(session: PlayerSession, tick_index: int) -> list[tuple[str, str]]:
    """推进一 tick 并产出 SSE 事件序列（(event_name, json_data) 列表）。

    只做内存变更；落库与推送由 tick_loop 编排。公开给测试直接驱动。
    """
    snap = session.char_snapshot
    events: list[tuple[str, str]] = []
    if has_travel(session.character_id):
        # 行进中（todo 30）：倒计时期间无战斗/技能/吃药结算，仅 stat 心跳；
        # 到点切图由 tick_loop 编排（pop_due_travel → complete_travel）。
        events.append(_stat_event(session, tick_index))
        return events
    panel = compute_panel(char_class=ClassKey(snap.class_), level=snap.level)
    dot_damage = _tick_buffs(session, events)
    _player_attack(session, panel, tick_index, events)
    monster_damage = _monster_ai(session, panel, tick_index, events)
    _tick_pets(session)  # todo 28：召唤物回合（攻主人目标，击杀经验归主人）

    damage_taken = dot_damage + monster_damage
    if damage_taken > 0:
        snap.hp -= damage_taken
        if snap.hp <= 0:
            # MVP 无死亡/复活系统：钳到 1 保命（计划外玩法，后续 todo 另立）
            snap.hp = 1
            events.append(_log_event("survive", "你已重伤倒地，侥幸保住性命"))
    else:
        # regen：未承伤的 tick 自然回复 1 点（MVP 占位节奏）
        snap.hp = min(snap.max_hp, snap.hp + 1)
        snap.mp = min(snap.max_mp, snap.mp + 1)

    _auto_potion(session, tick_index, events)
    events.append(_stat_event(session, tick_index))
    return events


def _stat_event(session: PlayerSession, tick_index: int) -> tuple[str, str]:
    """stat 心跳事件（正常 tick 与行进中分支共用同一形状）。"""
    snap = session.char_snapshot
    return (
        EV_STAT,
        json.dumps(
            {
                "tick": tick_index,
                "hp": snap.hp,
                "max_hp": snap.max_hp,
                "mp": snap.mp,
                "max_mp": snap.max_mp,
                "exp": snap.exp,
                "gold": snap.gold,
            },
            ensure_ascii=False,
        ),
    )


def _log_event(kind: str, text: str) -> tuple[str, str]:
    return (EV_LOG, json.dumps({"kind": kind, "text": text}, ensure_ascii=False))


def _tick_buffs(session: PlayerSession, events: list[tuple[str, str]]) -> int:
    """推进会话 buff 实例一 tick，返回 dot 造成的承伤合计。

    限时 buff（kind=buff）到期时追加 log 事件播报面板加值回落；stat 事件由
    simulate_tick 每 tick 恒发一条，面板数值随之下行（todo 26 过期推送语义）。
    """
    total = 0
    for inst in session.buffs:
        outcome = tick_effect(inst, get_effect(inst.definition_id))
        total += sum(event.amount for event in outcome.damage_events)
        if outcome.expired and get_effect(inst.definition_id).kind == "buff":
            events.append(
                _log_event("buff_expire", f"{inst.definition_id} 效果已消退，加值回落")
            )
    session.buffs = [inst for inst in session.buffs if not inst.expired]
    return total


def _tick_pets(session: PlayerSession) -> None:
    """召唤物回合（todo 28 接通）：pet 攻主人目标、击杀经验归主人。

    pet.py 内部直接 publish（pet_miss/pet_kill/exp 等事件）；本包装吞异常不阻断
    主循环。无宠物零开销（挂机主流路径不进 try）。
    """
    if not session.pets:
        return
    try:
        tick_pets(session)
    except Exception:
        LOG.exception("pet tick failed for character %s", session.character_id)


def _wear_equipment(session: PlayerSession, slot_key: str, amount: int) -> int | None:
    """扣指定槽位装备耐久（todo 24，纯内存；落库随每 5 tick 批次）.

    返回扣减后的耐久值；无装备/物品无耐久概念/已破碎返回 None（不重复扣减、
    不重复播报）。durability 为 NULL 的存量实例按满耐久初始化（seed 兼容）。
    """
    iid = _EQUIP_CACHE.get(session.character_id, {}).get(slot_key)
    if iid is None:
        return None
    row = next((r for r in session.inventory if r.id == iid), None)
    if row is None:
        return None
    item_def = cast("dict[str, ItemDef]", _gamedata()["items"]).get(row.item_id)
    max_durability = item_def.max_durability if item_def is not None else 0
    if max_durability <= 0:
        return None  # 无耐久概念的物品不磨损
    if row.durability is None:
        row.durability = max_durability
    if row.durability <= 0:
        return None
    row.durability = max(0, row.durability - amount)
    return row.durability


def _player_attack(
    session: PlayerSession,
    panel: StatPanel,
    tick_index: int,
    events: list[tuple[str, str]],
) -> None:
    """玩家出手：攻速节流 → 选技能 → 命中 roll → 伤害结算 → 击杀/掉落。"""
    zone = session.zone
    if zone is None or not zone.alive_monsters:
        return
    if tick_index % balance.ATTACK_INTERVAL_TICKS != 0:
        return
    target = zone.alive_monsters[0]  # MVP 距离档：场上第一只即目标（D12）
    multiplier = _cast_skill(session, panel, tick_index, events)
    if not hit_check(session.rng, panel.accuracy, target.monster_def.agility):
        events.append(_log_event("miss", f"你攻击{target.monster_def.name}未命中"))
        return
    # 武器磨损（todo 24）：出手命中即扣（未命中不扣）；归零时播报属性失效。
    weapon_durability = _wear_equipment(
        session, "weapon", balance.DURABILITY_LOSS_PER_ATTACK
    )
    if weapon_durability == 0:
        events.append(
            _log_event("durability", "你的武器耐久已耗尽，属性失效（商店可修理）")
        )
    dmg = physical_damage(
        session.rng,
        attack_min=panel.dc_min,
        attack_max=panel.dc_max,
        target_defense=target.monster_def.defense,
        luck=panel.luck,
        skill_multiplier=multiplier,
    )
    remaining = _monster_hp(session, target) - dmg
    if remaining <= 0:
        _kill_monster(session, zone, target, events)
    else:
        session.monster_hp[target.instance_id] = remaining
        events.append(
            _log_event(
                "attack",
                f"你命中{target.monster_def.name}，造成 {dmg} 点伤害"
                f"（剩余 {remaining}）",
            )
        )


def _cast_skill(
    session: PlayerSession,
    panel: StatPanel,
    tick_index: int,
    events: list[tuple[str, str]],
) -> float:
    """尝试施放第一个可用的敌方目标职业技能；成功返回其倍率，否则平砍基数。

    todo 28 自动习得：<35 级技能到 learn_level 后首次出手即学会 0 级（内存建行，
    随每 5 tick persist 批次落库）；≥35 级技能必须技能书（api/skill 同一口径），
    挂机路径永不自动学。升级时追加 skill_levelup 日志事件。
    """
    registry = _skills()
    snap = session.char_snapshot
    for skill_id in registry.ids_by_class(snap.class_):
        skill = registry.get(skill_id)
        if skill.target != SkillTarget.enemy:
            continue
        state = session.skill_states.get(skill_id)
        if state is None:
            if (
                snap.level < skill.learn_level
                or skill.learn_level >= BOOK_REQUIRED_LEVEL
            ):
                continue  # 未到级 / 书技不自动学
            state = SkillState(skill_id=skill_id)
            session.skill_states[skill_id] = state
            events.append(
                _log_event("skill_learn", f"初窥门径，你学会了{skill.name}（Lv0）")
            )
        level_before = state.level
        attempt = try_cast(
            registry,
            skill_id,
            char_level=snap.level,
            mp=snap.mp,
            state=state,
            last_cast_tick=session.last_cast_ticks.get(skill_id),
            now_tick=tick_index,
        )
        if not attempt.success:
            continue
        snap.mp = attempt.mp_after
        session.last_cast_ticks[skill_id] = tick_index
        events.append(_log_event("skill", f"你施放了{registry.get(skill_id).name}"))
        if state.level > level_before:
            events.append(
                _log_event(
                    "skill_levelup",
                    f"{skill.name} 熟练度提升至 Lv{state.level}",
                )
            )
        return registry.get_multiplier(skill_id, state.level)
    return balance.SKILL_MULTIPLIER_BASE


def _monster_hp(session: PlayerSession, monster: MonsterInstance) -> int:
    """怪当前 HP 记账（首见按 def.hp 初始化；MonsterInstance 不携带血量）。"""
    return session.monster_hp.setdefault(monster.instance_id, monster.monster_def.hp)


def _kill_monster(
    session: PlayerSession,
    zone: ZoneInstance,
    target: MonsterInstance,
    events: list[tuple[str, str]],
) -> None:
    """击杀结算：移除活怪 → 经验入账 → 掉落 roll → 自动拾取。"""
    killed = zone.kill_monster(target.instance_id)
    if killed is None:
        return
    session.monster_hp.pop(target.instance_id, None)
    exp_gain = killed.monster_def.exp
    session.char_snapshot.exp += exp_gain
    events.append(
        (
            EV_EXP,
            json.dumps(
                {
                    "amount": exp_gain,
                    "total": session.char_snapshot.exp,
                    "monster_id": killed.monster_id,
                },
                ensure_ascii=False,
            ),
        )
    )
    events.append(
        _log_event("kill", f"你击杀了{killed.monster_def.name}，获得 {exp_gain} 经验")
    )
    # 任务进度挂钩（todo 29）：击杀悬赏/分身挑战在此同步推进 quest_state（锁外内存）
    _quest_on_kill(session, killed)
    table = cast("dict[str, DropEntry]", _gamedata()["drops"]).get(killed.monster_id)
    if table is None:
        return
    drop = roll_drop(
        table,
        cast("dict[str, ItemDef]", _gamedata()["items"]),
        session.rng,
    )
    _apply_drop(session, drop, events)


def _apply_drop(
    session: PlayerSession, drop: DropResult, events: list[tuple[str, str]]
) -> None:
    """掉落入账：金币直加（自动入袋）；物品按 auto_pickup 决定拾取或落地保留。

    拾取失败（背包满 / 自动拾取关闭）→ 掉落保留进地面池（retain_ground_drop），
    供 POST /api/inventory/pick 手动拾取；背包满额外日志提示（todo 25）。
    """
    picked: list[dict[str, object]] = []
    if drop.gold > 0:
        session.char_snapshot.gold += drop.gold
        picked.append({"gold": drop.gold})
    items = cast("dict[str, ItemDef]", _gamedata()["items"])
    for item in drop.items:
        ok = _pickup_item(session, item) if session.auto_pickup else False
        if ok:
            picked.append({"item_id": item.item_id, "qty": item.qty, "picked": True})
            continue
        ground = retain_ground_drop(
            session.character_id,
            item.item_id,
            item.qty,
            tier=item.tier,
            extra_json=item.extra_json,
        )
        if session.auto_pickup:
            # 开着自动拾取仍失败 = 背包满（_pickup_item 唯一失败原因）→ 日志提示
            name = items[item.item_id].name if item.item_id in items else item.item_id
            events.append(
                _log_event(
                    "bag_full",
                    f"背包已满，{name} 留在地面，请手动拾取或清理背包",
                )
            )
        picked.append(
            {
                "item_id": item.item_id,
                "qty": item.qty,
                "picked": False,
                "drop_id": ground.drop_id,
            }
        )
    events.append(
        (
            EV_DROP,
            json.dumps(
                {"monster_id": drop.monster_id, "loot": picked}, ensure_ascii=False
            ),
        )
    )


def _pickup_item(session: PlayerSession, dropped: DroppedItem) -> bool:
    """拾取入包：可堆叠优先合并既有行（合并不占新格，背包满也可并入）。

    新行（首个可堆叠物 / 装备）需有空格；装备永远 quantity=1 单独成行
    （不可堆叠红线，todo 25）。背包满返回 False，由调用方落地保留+日志。
    """
    items = cast("dict[str, ItemDef]", _gamedata()["items"])
    item_def = items.get(dropped.item_id)
    stackable = bool(getattr(item_def, "stackable", False))
    if stackable:
        row = next((r for r in session.inventory if r.item_id == dropped.item_id), None)
        if row is not None:
            row.quantity += dropped.qty
            if row.id is not None:
                session.dirty_quantities.add(row.id)
            return True
    if len(session.inventory) >= session.char_snapshot.bag_capacity:
        return False
    new_row = ItemInstance(
        character_id=session.character_id,
        item_id=dropped.item_id,
        quantity=dropped.qty if stackable else 1,
    )
    session.inventory.append(new_row)
    session.pending_items.append(new_row)
    return True


def _monster_ai(
    session: PlayerSession,
    panel: StatPanel,
    tick_index: int,
    events: list[tuple[str, str]],
) -> int:
    """怪物最小 AI：场上第一只活怪按自身攻速回击玩家，返回造成的伤害。"""
    zone = session.zone
    if zone is None or not zone.alive_monsters:
        return 0
    monster = zone.alive_monsters[0]
    if tick_index % max(1, monster.monster_def.attack_speed) != 0:
        return 0
    if not hit_check(session.rng, monster.monster_def.accuracy, panel.agility):
        events.append(_log_event("dodge", f"{monster.monster_def.name}攻击你未命中"))
        return 0
    # 防具磨损（todo 24）：被命中即扣衣服槽耐久（未命中不扣）；归零播报失效。
    armor_durability = _wear_equipment(
        session, "armor", balance.DURABILITY_LOSS_PER_HIT
    )
    if armor_durability == 0:
        events.append(
            _log_event("durability", "你的衣服耐久已耗尽，属性失效（商店可修理）")
        )
    dmg = physical_damage(
        session.rng,
        attack_min=monster.monster_def.attack,
        attack_max=monster.monster_def.attack,
        target_defense=panel.ac_max,
    )
    events.append(
        _log_event("defend", f"{monster.monster_def.name}命中你，造成 {dmg} 点伤害")
    )
    return dmg


def _auto_potion(
    session: PlayerSession, tick_index: int, events: list[tuple[str, str]]
) -> None:
    """自动吃药：plan_potion_use 出参直接落地（背包扣减/冷却/HP·MP 回复）。"""
    snap = session.char_snapshot
    bag = _bag_counts(session)
    plan = plan_potion_use(
        hp=snap.hp,
        max_hp=snap.max_hp,
        mp=snap.mp,
        max_mp=snap.max_mp,
        bag=bag,
        catalog=_potion_catalog(),
        now_tick=tick_index,
        cooldowns=session.potion_cooldowns,
        # 空格 < BAG_FREE_SLOTS_RECALL 时 plan 追加 recall 动作（todo 25 自动回城）；
        # policy 仍可显式传 free_slots=None 关闭（R6：实参非 None 时优先于 policy）。
        free_slots=max(0, snap.bag_capacity - len(session.inventory)),
        policy=session.potion_policy,
    )
    session.potion_cooldowns = dict(plan.cooldowns_after)
    catalog = _potion_catalog()
    for action in plan.actions:
        if action.kind == "recall":
            _request_recall(session, events)  # todo 25：内部标记，tick_loop 拦截执行
            continue
        if action.kind != "drink" or action.item_id is None:
            continue
        spec = catalog.get(action.item_id)
        row = _take_potion_row(session, action.item_id)
        if row is None:
            continue
        hp_restore = spec.hp_restore if spec is not None else 0
        mp_restore = spec.mp_restore if spec is not None else 0
        snap.hp = min(snap.max_hp, snap.hp + hp_restore)
        snap.mp = min(snap.max_mp, snap.mp + mp_restore)
        events.append(
            _log_event(
                "potion",
                f"自动喝下{action.item_id}"
                f"（HP+{hp_restore} MP+{mp_restore} → {snap.hp}/{snap.max_hp}）",
            )
        )


def _request_recall(session: PlayerSession, events: list[tuple[str, str]]) -> None:
    """空格不足的自动回城请求（todo 25）：日志 + 内部标记事件。

    标记事件（EV_RECALL）由 tick_loop 拦截后调 trigger_return_city，不对外发布；
    已在绑定城时不重复触发（防每 tick 刷屏）。
    """
    snap = session.char_snapshot
    if snap.map_id == snap.bind_city:
        return
    events.append(
        _log_event(
            "recall",
            f"背包空格不足 {balance.BAG_FREE_SLOTS_RECALL} 格，自动回城",
        )
    )
    events.append(
        (EV_RECALL, json.dumps({"target": snap.bind_city}, ensure_ascii=False))
    )


def _bag_counts(session: PlayerSession) -> dict[str, int]:
    """背包聚合视图（item_id → 总数量），供 plan_potion_use 只读消费。"""
    counts: dict[str, int] = {}
    for row in session.inventory:
        counts[row.item_id] = counts.get(row.item_id, 0) + row.quantity
    return counts


def _take_potion_row(session: PlayerSession, item_id: str) -> ItemInstance | None:
    """从背包扣一瓶药：数量减一，归零删行并记脏标记（落库时 DELETE）。"""
    row = next(
        (r for r in session.inventory if r.item_id == item_id and r.quantity > 0),
        None,
    )
    if row is None:
        return None
    row.quantity -= 1
    if row.quantity <= 0:
        session.inventory.remove(row)
        if row.id is not None:
            session.removed_item_ids.add(row.id)
            session.dirty_quantities.discard(row.id)
    elif row.id is not None:
        session.dirty_quantities.add(row.id)
    return row
