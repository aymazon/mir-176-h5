"""PlayerSession——按 character_id 单例的内存会话容器（todo 15）。

职责边界（计划红线）：
- **纯内存容器**：角色快照 / 背包 / buff / 吃药策略 / 自动拾取开关 / 写序锁 /
  tick task 引用 / 断线宽限期标记。自己**不**创建 asyncio.Task（task 由
  tick_loop 创建并把引用挂到 ``tick_task``），不持有长生命周期 DB 连接。
- **flush_snapshot 方向 = DB→内存**：对刚 commit 的事务结果做一次只读 SELECT
  并重读快照替换内存。自身**绝不取锁**（防不可重入自死锁），必须由持锁方在
  ``session.lock`` 临界区内调用——REST 写路径规则（todo 23–30 同样遵守）：
  同一 DB 事务写完涉及表 → commit → 锁内 flush_snapshot；锁临界区仅限
  DB 事务+flush，不含 SSE 推送。
- **断线语义**：``on_disconnect`` 只标记 disconnected + 启动宽限期计时
  （60s，settings.SSE_GRACE_PERIOD_SECONDS），**不取消 tick task**——宽限内
  重连无缝续跑；超时由 tick_loop 负责取消 + release。断线/登出的心跳刷新为
  单行 UPDATE，豁免取锁（seed 判活依据 character.last_active_at）。

MVP 占位：max_hp/max_mp 公式自创（character 表无上限列、CLASS_GROWTH 无
hp/mp 键），待 todo 20 全量数据录入时校准；离线收益/挂机补偿只有空壳
``_compensation_stub``（计划红线：不做离线收益）。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.combat.effects import EffectInstance
from app.combat.potion import PotionSpec
from app.combat.skills import SkillState
from app.combat.potion import PotionSpec
from app.core.db import SessionLocal
from app.models.character import Character
from app.models.item_instance import ItemInstance
from app.sse.session_state import SessionState, get_or_create_session

if TYPE_CHECKING:
    from app.sim.pet import Pet
    from app.sim.zone import ZoneInstance

LOG = logging.getLogger(__name__)

# MVP 占位成长公式（自创占位待 todo 20 校准）：max = base + per × level。
MAX_HP_BASE: Final[int] = 16
MAX_HP_PER_LEVEL: Final[int] = 8
MAX_MP_BASE: Final[int] = 10
MAX_MP_PER_LEVEL: Final[int] = 5


def max_hp_for(level: int) -> int:
    """角色 HP 上限（MVP 占位公式，与职业无关）。"""
    return MAX_HP_BASE + MAX_HP_PER_LEVEL * level


def max_mp_for(level: int) -> int:
    """角色 MP 上限（MVP 占位公式，与职业无关）。"""
    return MAX_MP_BASE + MAX_MP_PER_LEVEL * level


@dataclass(slots=True)
class CharacterSnapshot:
    """character 行的战斗内存投影（tick 直接读写，落库时写回）。"""

    name: str
    class_: str
    level: int
    exp: int
    hp: int
    mp: int
    gold: int
    map_id: str
    bind_city: str
    bag_capacity: int

    @property
    def max_hp(self) -> int:
        return max_hp_for(self.level)

    @property
    def max_mp(self) -> int:
        return max_mp_for(self.level)


@dataclass(slots=True)
class PlayerSession:
    """单个角色的会话容器（进程内按 character_id 单例，见 _sessions）。"""

    character_id: int
    char_snapshot: CharacterSnapshot
    # 背包（ItemInstance ORM 实例的内存投影；expire_on_commit=False 可安全离线读属性）
    inventory: list[ItemInstance] = field(default_factory=list)
    # 未过期效果实例（sim 层持有值对象；todo 26 接入 buff_json 持久化）
    buffs: list[EffectInstance] = field(default_factory=list)
    # 已学技能熟练度状态（skill_state 行的内存投影；try_cast 就地推进）
    skill_states: dict[str, SkillState] = field(default_factory=dict)
    # 技能冷却记账：skill_id → 上次施放 tick
    last_cast_ticks: dict[str, int] = field(default_factory=dict)
    # 场上怪当前 HP 记账：instance_id → 剩余 HP（MonsterInstance 不携带血量）
    monster_hp: dict[str, int] = field(default_factory=dict)
    # 召唤物列表（todo 16 内存实体；不入库，session 注销时由 pet.disperse_all 清空）
    pets: list[Pet] = field(default_factory=list)
    # 吃药策略覆盖键（hp_pct/mp_pct/urgent_hp_pct/free_slots），空 dict = 全默认
    potion_policy: dict[str, object] = field(default_factory=dict)
    auto_pickup: bool = True
    # 写序串行化锁：REST 写事务+flush 与 tick 落库共用同一把（横切约束）
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # 由 tick_loop.start_tick_loop 创建并持有引用；本类不创建 task
    tick_task: asyncio.Task[None] | None = None
    disconnected: bool = False
    grace_period_started_at: float | None = None
    last_active_at: float = field(default_factory=time.monotonic)
    # SSE 会话状态（事件 buffer 的唯一事实源，publish 在锁外调用）
    sse_state: SessionState = field(default_factory=lambda: get_or_create_session(0))
    # 所在地图实例（start_tick_loop 时懒进图；登出/宽限超时 leave_map）
    zone: ZoneInstance | None = None
    # 随机源（可注入固定 seed 供测试复现）
    rng: random.Random = field(default_factory=random.Random)
    # 吃药冷却：item_id → 到期 tick（绝对 tick 记账，消费 plan_potion_use 出参）
    potion_cooldowns: dict[str, int] = field(default_factory=dict)
    # 背包脏标记（增量落库用；flush_snapshot 重读后清零）
    pending_items: list[ItemInstance] = field(default_factory=list)
    dirty_quantities: set[int] = field(default_factory=set)
    removed_item_ids: set[int] = field(default_factory=set)

    def mark_connected(self) -> None:
        """连接建立/重连：清除断线标记与宽限期计时起点。"""
        self.disconnected = False
        self.grace_period_started_at = None
        self.last_active_at = time.monotonic()

    async def flush_snapshot(self) -> None:
        """DB→内存只读重读（角色行 + 背包行），替换内存快照并清脏标记。

        **自身不取锁**——必须由持锁方在 ``session.lock`` 内调用（REST 写事务
        commit 后 / tick 落库 commit 后），防 commit 与 flush 之间的调度窗口
        被 tick 落库以旧快照覆盖。角色行消失（被删号）→ 保留内存旧值仅告警。
        """
        async with SessionLocal() as db:
            row = await db.get(Character, self.character_id)
            if row is None:
                LOG.warning(
                    "flush_snapshot: character %s 不存在（已删号？）保留内存快照",
                    self.character_id,
                )
                return
            items = (
                (
                    await db.execute(
                        select(ItemInstance).where(
                            ItemInstance.character_id == self.character_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            self.char_snapshot = _snapshot_from(row)
            self.inventory = list(items)
        self.pending_items.clear()
        self.dirty_quantities.clear()
        self.removed_item_ids.clear()
        self.last_active_at = time.monotonic()


def _snapshot_from(row: Character) -> CharacterSnapshot:
    """ORM 行 → 内存快照（唯一组装点，方向 DB→内存）。"""
    return CharacterSnapshot(
        name=row.name,
        class_=row.class_.value,
        level=row.level,
        exp=row.exp,
        hp=row.hp,
        mp=row.mp,
        gold=row.gold,
        map_id=row.map_id,
        bind_city=row.bind_city,
        bag_capacity=row.bag_capacity,
    )


# --- 进程内单例注册表 ---------------------------------------------------------


_sessions: Final[dict[int, PlayerSession]] = {}


def session_for(character_id: int) -> PlayerSession | None:
    """按 character_id 取会话单例；不存在返回 None。"""
    return _sessions.get(character_id)


def register_session(session: PlayerSession) -> None:
    """登记会话（同 id 覆盖——抢占语义由 tick_loop 的旧 task 取消保证）。"""
    _sessions[session.character_id] = session


def release_session(character_id: int) -> PlayerSession | None:
    """注销会话并返回被移除的实例（不存在返回 None）；不取消 task（调用方负责）。"""
    return _sessions.pop(character_id, None)


def all_character_ids() -> list[int]:
    """当前在册全部 character_id（关停巡检/测试隔离用）。"""
    return list(_sessions.keys())


def on_disconnect(character_id: int) -> bool:
    """SSE close 钩子（api/stream.py client_close_handler 调用）。

    只标记 disconnected + 启动宽限期计时（幂等），**不取消 tick task**；
    顺带把 last_active_at 心跳刷进库（单行 UPDATE，豁免取锁——横切约束明文
    豁免项）。返回是否本次调用真正置位（已是断线态返回 False）。
    """
    session = _sessions.get(character_id)
    if session is None or session.disconnected:
        return False
    session.disconnected = True
    session.grace_period_started_at = time.monotonic()
    try:  # 无运行中 loop（纯单测）时跳过心跳 task——生产路径恒在 loop 内
        asyncio.get_running_loop().create_task(
            _touch_heartbeat(character_id),
            name=f"heartbeat:{character_id}",
        )
    except RuntimeError:
        pass
    return True


async def _touch_heartbeat(character_id: int) -> None:
    """断线/登出心跳刷新：单行 UPDATE last_active_at=now()（豁免取锁）。"""
    try:
        async with SessionLocal() as db:
            await db.execute(
                update(Character)
                .where(Character.id == character_id)
                .values(last_active_at=func.now())
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — 心跳失败不阻断断线主流程
        LOG.exception("heartbeat refresh failed for character %s", character_id)


async def get_or_create_player_session(
    db: AsyncSession, character_id: int
) -> PlayerSession:
    """取会话单例；缺失则从库加载快照+背包建会话并注册（SSE 连接路径）。

    并发去重：加载期间可能已有并发连接注册同 id 会话——注册前复查，
    命中则丢弃本次加载结果复用既有单例（无双 session）。
    """
    existing = session_for(character_id)
    if existing is not None:
        return existing
    row = await db.get(Character, character_id)
    if row is None:
        raise LookupError(f"character {character_id} 不存在")
    items = (
        (
            await db.execute(
                select(ItemInstance).where(
                    ItemInstance.character_id == character_id
                )
            )
        )
        .scalars()
        .all()
    )
    skill_rows = (
        (
            await db.execute(
                text("SELECT skill_id, exp, level FROM skill_state WHERE character_id = :cid"),
                {"cid": character_id},
            )
        )
        .mappings()
        .all()
    )
    session = PlayerSession(
        character_id=character_id,
        char_snapshot=_snapshot_from(row),
        inventory=list(items),
        skill_states={
            r["skill_id"]: SkillState(skill_id=r["skill_id"], exp=r["exp"], level=r["level"])
            for r in skill_rows
        },
        sse_state=get_or_create_session(character_id),
    )
    existing = session_for(character_id)
    if existing is not None:
        return existing
    register_session(session)
    return session


def build_potion_catalog() -> dict[str, PotionSpec]:
    """从 gamedata items 派生吃药目录（kind=potion 且带恢复属性的物品）。

    priority：双恢复（太阳水类）= 2 > 单恢复 = 1——危险线 R2 优先高级药、
    常规线省着用的替代链顺序依据。
    """
    from app.gamedata_loader import load_all

    items = load_all()["items"]
    catalog: dict[str, PotionSpec] = {}
    for item_id, item in sorted(items.items()):  # type: ignore[union-attr]
        stats = getattr(item, "stats", {})
        hp_restore = int(stats.get("hp_restore", 0))
        mp_restore = int(stats.get("mp_restore", 0))
        if hp_restore <= 0 and mp_restore <= 0:
            continue
        catalog[item_id] = PotionSpec(
            hp_restore=hp_restore,
            mp_restore=mp_restore,
            priority=2 if (hp_restore > 0 and mp_restore > 0) else 1,
        )
    return catalog


def reset_sessions() -> None:
    """测试专用：清空会话注册表（生产路径永不调用）。"""
    _sessions.clear()


def _compensation_stub(character_id: int) -> None:  # noqa: ARG001
    """离线收益/挂机补偿占位（计划红线：不做离线收益，永不去实现）。"""
    raise NotImplementedError("离线收益不在 MVP 范围（计划红线）")
