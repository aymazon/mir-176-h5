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
       （HP/MP/exp/gold/last_active_at=now() + 背包增量）→ flush_snapshot；
       **buff_json 列待 todo 26 迁移加入后再纳入落库集合（W2 阶段跳过）**

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
from contextlib import suppress
from functools import lru_cache
from typing import TYPE_CHECKING, Final, cast

from sqlalchemy import delete, func, update

from app.combat import balance
from app.combat.damage import physical_damage
from app.combat.drops import DroppedItem, roll_drop
from app.combat.effects import get_effect, tick_effect
from app.combat.hit import hit_check
from app.combat.potion import plan_potion_use
from app.combat.skills import SkillRegistry, load_registry, try_cast
from app.combat.stats import ClassKey, StatPanel, compute_panel
from app.core.config import settings
from app.core.db import SessionLocal
from app.gamedata_loader import load_all
from app.gamedata_schema.skills import SkillTarget
from app.models.character import Character
from app.models.item_instance import ItemInstance
from app.sim.session import (
    PlayerSession,
    build_potion_catalog,
    release_session,
)
from app.combat.potion import PotionSpec

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


@lru_cache(maxsize=1)
def _gamedata() -> dict[str, object]:
    """进程内缓存一份已校验 gamedata（zone._load_gamedata_cached 同款）。"""
    return cast("dict[str, object]", load_all())


@lru_cache(maxsize=1)
def _skills() -> SkillRegistry:
    return load_registry()


@lru_cache(maxsize=1)
def _potion_catalog() -> dict[str, PotionSpec]:
    return build_potion_catalog()


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
    zone = session.zone
    if zone is not None:
        with suppress(KeyError):
            await leave_map(zone.map_id, character_id)
        session.zone = None
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
        zone = session.zone
        if zone is not None:
            with suppress(KeyError):
                await leave_map(zone.map_id, character_id)
            session.zone = None
    reset_sessions()



async def _teardown_on_grace_timeout(session: PlayerSession) -> None:
    """宽限期耗尽（tick task 自身视角）：离图 + 注销会话，task 自然返回。"""
    from app.sim.zone import leave_map

    zone = session.zone
    if zone is not None:
        with suppress(KeyError):
            await leave_map(zone.map_id, session.character_id)
        session.zone = None
    release_session(session.character_id)
    session.tick_task = None
    LOG.info("grace period expired; tick loop stopped for %s", session.character_id)


def _grace_expired(session: PlayerSession) -> bool:
    """断线且宽限期已耗尽（settings 动态读取，测试可 monkeypatch 调快）。"""
    if not session.disconnected or session.grace_period_started_at is None:
        return False
    elapsed = time.monotonic() - session.grace_period_started_at
    return elapsed >= settings.SSE_GRACE_PERIOD_SECONDS


# --- 主循环 --------------------------------------------------------------------


async def tick_loop(
    session: PlayerSession, *, interval: float | None = None
) -> None:
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
            events = simulate_tick(session, n)
            if n % PERSIST_EVERY_TICKS == 0:
                try:
                    async with session.lock:
                        await persist_session_state(session)
                except Exception:  # noqa: BLE001 — 单次落库失败不终止挂机
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
    """增量落库：character 行（HP/MP/exp/gold/last_active_at）+ 背包增删改。

    **必须在 ``session.lock`` 临界区内调用**（与 REST 写事务同一把锁串行化，
    防 commit 与 flush 之间的调度窗口被以旧快照覆盖）；commit 后锁内调
    flush_snapshot（DB→内存重读）。buff_json 待 todo 26 接入（列尚未迁移）。
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
        await db.commit()
    await session.flush_snapshot()


# --- 单 tick 战斗结算（纯内存变更，无锁无 DB）-------------------------------------


def simulate_tick(session: PlayerSession, tick_index: int) -> list[tuple[str, str]]:
    """推进一 tick 并产出 SSE 事件序列（(event_name, json_data) 列表）。

    只做内存变更；落库与推送由 tick_loop 编排。公开给测试直接驱动。
    """
    snap = session.char_snapshot
    panel = compute_panel(char_class=ClassKey(snap.class_), level=snap.level)
    events: list[tuple[str, str]] = []

    dot_damage = _tick_buffs(session)
    _player_attack(session, panel, tick_index, events)
    monster_damage = _monster_ai(session, panel, tick_index, events)

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
    events.append(
        (
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
    )
    return events


def _log_event(kind: str, text: str) -> tuple[str, str]:
    return (EV_LOG, json.dumps({"kind": kind, "text": text}, ensure_ascii=False))


def _tick_buffs(session: PlayerSession) -> int:
    """推进会话 buff 实例一 tick，返回 dot 造成的承伤合计。"""
    total = 0
    for inst in session.buffs:
        outcome = tick_effect(inst, get_effect(inst.definition_id))
        total += sum(event.amount for event in outcome.damage_events)
    session.buffs = [inst for inst in session.buffs if not inst.expired]
    return total


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
        events.append(
            _log_event("miss", f"你攻击{target.monster_def.name}未命中")
        )
        return
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
    """尝试施放第一个可用的敌方目标职业技能；成功返回其倍率，否则平砍基数。"""
    registry = _skills()
    snap = session.char_snapshot
    for skill_id in registry.ids_by_class(snap.class_):
        state = session.skill_states.get(skill_id)
        if state is None or registry.get(skill_id).target != SkillTarget.enemy:
            continue
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
        events.append(
            _log_event("skill", f"你施放了{registry.get(skill_id).name}")
        )
        return registry.get_multiplier(skill_id, state.level)
    return balance.SKILL_MULTIPLIER_BASE


def _monster_hp(
    session: PlayerSession, monster: MonsterInstance
) -> int:
    """怪当前 HP 记账（首见按 def.hp 初始化；MonsterInstance 不携带血量）。"""
    return session.monster_hp.setdefault(
        monster.instance_id, monster.monster_def.hp
    )


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
    """掉落入账：金币直加；物品按 auto_pickup 决定拾取或散落。"""
    picked: list[dict[str, object]] = []
    if drop.gold > 0:
        session.char_snapshot.gold += drop.gold
        picked.append({"gold": drop.gold})
    for item in drop.items:
        ok = _pickup_item(session, item) if session.auto_pickup else False
        picked.append(
            {"item_id": item.item_id, "qty": item.qty, "picked": ok}
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
    """拾取入包：可堆叠合并既有行，否则新行；背包满则拾取失败。"""
    if len(session.inventory) >= session.char_snapshot.bag_capacity:
        return False
    items = cast("dict[str, ItemDef]", _gamedata()["items"])
    item_def = items.get(dropped.item_id)
    stackable = getattr(item_def, "stackable", False)
    if stackable:
        row = next(
            (r for r in session.inventory if r.item_id == dropped.item_id), None
        )
        if row is not None:
            row.quantity += dropped.qty
            if row.id is not None:
                session.dirty_quantities.add(row.id)
            return True
    new_row = ItemInstance(
        character_id=session.character_id,
        item_id=dropped.item_id,
        quantity=dropped.qty,
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
        events.append(
            _log_event("dodge", f"{monster.monster_def.name}攻击你未命中")
        )
        return 0
    dmg = physical_damage(
        session.rng,
        attack_min=monster.monster_def.attack,
        attack_max=monster.monster_def.attack,
        target_defense=panel.ac_max,
    )
    events.append(
        _log_event(
            "defend", f"{monster.monster_def.name}命中你，造成 {dmg} 点伤害"
        )
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
        free_slots=None,  # 挂机回城未开启（R6 显式关闭信号）
        policy=session.potion_policy,
    )
    session.potion_cooldowns = dict(plan.cooldowns_after)
    catalog = _potion_catalog()
    for action in plan.actions:
        if action.kind != "drink" or action.item_id is None:
            continue  # recall 动作归 todo 25 回城流程
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
