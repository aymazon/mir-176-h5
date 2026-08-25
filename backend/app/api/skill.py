"""技能学习/升级 API（todo 28）：按级习得 / 技能书习得 / 施放与熟练度升级。

三条习得入口（docs/MVP/README.md §7 口径）：
- 按级自动习得：<35 级技能到 learn_level 后**首次施放即学会 0 级**（tick 出手
  与 REST /use 同口径）；POST /api/skill/learn 为显式提前习得入口；
- 技能书习得：≥35 级技能（烈火剑法/冰咆哮/召唤神兽）**必须**消耗对应技能书
  （35 级技能书掉落/任务奖励），绝不自动习得；
- 书→技能映射按**技能中文名**（书名去掉「（书）」后缀）而非 id 前缀——
  book_ice_roar→ice_thunder、book_fire_sword→liehuo、book_lightning(雷电术)
  →thunder_bolt，前缀映射会错配（真实数据实证）。

POST /api/skill/use：调 combat.skills.try_cast 三道闸（未习得/冷却/MP），
成功即 grant_cast_exp +1 熟练（可能跨级 → SSE skill_levelup）。伤害结算仍由
挂机 tick 出手路径承担（REST 只做施放/熟练度记账，MVP 距离档无手动选目标，
target_id 预留不消费）。召唤类 self 技能施放成功额外 summon_pet（pet_summon）。
冷却记账：REST 用墙钟整秒刻度（模块级 _rest_last_cast），与 tick 路径的
session.last_cast_ticks（tick 序号刻度）分账本隔离——try_cast 的冷却算术对
任意单调整数刻度成立，同路径内自洽即可（MVP 简化，跨路径 CD 不互斥）。

并发纪律（todo 15 横切约束）：锁临界区仅限 DB 事务 + flush_snapshot；SSE
publish 与 summon_pet（内部直接 publish）恒在锁外。exp 值在 /use 不落库——
随挂机每 PERSIST_EVERY_TICKS 批次覆盖写（persist_session_state upsert）。
"""

from __future__ import annotations

import json
import time
from functools import lru_cache
from typing import Final

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPlayerId, DbSession
from app.api.inventory import _character_of, _items
from app.combat.skills import (
    SkillNotFoundError,
    SkillRegistry,
    SkillState,
    load_registry,
    try_cast,
)
from app.gamedata_schema.items import ItemDef, ItemKind
from app.gamedata_schema.skills import SkillClass, SkillDef
from app.models.character import Character
from app.models.item_instance import ItemInstance
from app.models.skill_state import SkillState as SkillStateRow
from app.sim.pet import summon_pet
from app.sim.session import PlayerSession, get_or_create_player_session
from app.sim.tick_loop import BOOK_REQUIRED_LEVEL
from app.sim.travel import has_travel

router = APIRouter(prefix="/api/skill", tags=["skill"])

# SSE 事件名（与 api/inventory 的 repair/stat 并行推送同通道）
EV_SKILL_LEARN: Final[str] = "skill_learn"
EV_SKILL_LEVELUP: Final[str] = "skill_levelup"
EV_LOG: Final[str] = "log"

# 书名后缀（gamedata/items/book_*.json name 字段固定格式「<技能名>（书）」）
_BOOK_NAME_SUFFIX: Final[str] = "（书）"


@lru_cache(maxsize=1)
def _skills() -> SkillRegistry:
    """进程内缓存已校验技能注册表（tick_loop._skills 同款）。"""
    return load_registry()


# REST 施放冷却记账：(character_id, skill_id) → 上次施放的墙钟整秒刻度。
# 见模块 docstring「冷却记账」；进程生命周期内小字典（角色×技能上限），不清理。
_rest_last_cast: Final[dict[tuple[int, str], int]] = {}


# --- 出参模型 -------------------------------------------------------------------


class LearnedSkill(BaseModel):
    skill_id: str
    name: str
    level: int
    exp: int
    max_level: int
    # 从当前级升到下一级所需累计熟练经验；0 = 已满级（read_exp_threshold 哨兵）
    next_level_exp: int


class AvailableSkill(BaseModel):
    skill_id: str
    name: str
    learn_level: int
    mp_cost: int
    # True = ≥35 级技能书技能：/learn 拒绝，必须 learn_from_book
    requires_book: bool


class SkillListOut(BaseModel):
    learned: list[LearnedSkill]
    available: list[AvailableSkill]


class LearnIn(BaseModel):
    skill_id: str


class LearnOut(BaseModel):
    skill_id: str
    name: str
    level: int
    exp: int
    # learn=显式按级习得 | book=技能书习得
    source: str


class BookIn(BaseModel):
    item_instance_id: int


class UseIn(BaseModel):
    skill_id: str
    # MVP 距离档自动锁定场上第一只怪（D12），预留手动选目标不消费
    target_id: str | None = None


class UseOut(BaseModel):
    skill_id: str
    success: bool
    # ok | not_learned | cooldown | insufficient_mp（try_cast 三道闸原文）
    reason: str
    mp_after: int
    exp_gained: int
    level_before: int
    level_after: int
    leveled_up: bool
    # 召唤类技能成功时返回 pet_id（pet_summon 事件同步推送）
    pet_id: str | None = None


# --- helpers --------------------------------------------------------------------


def _get_skill(registry: SkillRegistry, skill_id: str) -> SkillDef:
    """registry.get 的 HTTP 化包装：未知 id → 422（fail-fast 人话化）。"""
    try:
        return registry.get(skill_id)
    except SkillNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"未知技能: {skill_id}",
        ) from None


def _requires_book(skill: SkillDef) -> bool:
    """≥35 级技能必须技能书习得（与 tick_loop 自动习得闸同一常量口径）。"""
    return skill.learn_level >= BOOK_REQUIRED_LEVEL


def _ids_for_class(registry: SkillRegistry, class_value: str) -> list[str]:
    """该职业可用技能 id（含 class=all 通用技；ids_by_class 不含 all 故自建）。"""
    out: list[str] = []
    for skill_id in registry.ids():
        skill = registry.get(skill_id)
        if skill.class_ is SkillClass.all or skill.class_.value == class_value:
            out.append(skill_id)
    return out


def _skill_id_for_book(registry: SkillRegistry, item_def: ItemDef) -> str | None:
    """技能书物品 → 对应技能 id；按技能中文名匹配（见模块 docstring 映射坑）。

    书名去「（书）」后缀在 32 技能名索引中查找；无对应技能（分身术/流星火雨等
    未录入技能）返回 None，调用方 422。
    """
    if not item_def.name.endswith(_BOOK_NAME_SUFFIX):
        return None
    skill_name = item_def.name[: -len(_BOOK_NAME_SUFFIX)]
    for skill_id in registry.ids():
        if registry.get(skill_id).name == skill_name:
            return skill_id
    return None


def _publish(session: PlayerSession, event: str, payload: dict[str, object]) -> None:
    """锁外 SSE 推送（json dumps 统一 ensure_ascii=False）。"""
    session.sse_state.publish(event, json.dumps(payload, ensure_ascii=False))


async def _locked_character(db: AsyncSession, character_id: int) -> Character:
    """锁内取最新角色行并 refresh（equip/consumable 同款；极端竞态 404）。"""
    char_row = await db.get(Character, character_id)
    if char_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在")
    await db.refresh(char_row)
    return char_row


# --- 端点 ------------------------------------------------------------------------


@router.get("/list")
async def list_skills(db: DbSession, player_id: CurrentPlayerId) -> SkillListOut:
    """已学技能（skill_state 行内存投影）+ 可学清单（职业匹配且到级、未学）。"""
    character = await _character_of(db, player_id)
    session = await get_or_create_player_session(db, character.id)
    registry = _skills()

    learned: list[LearnedSkill] = []
    for skill_id, state in sorted(session.skill_states.items()):
        try:
            skill = registry.get(skill_id)
        except SkillNotFoundError:
            continue  # 定义已下线的残留行不炸列表
        learned.append(
            LearnedSkill(
                skill_id=skill_id,
                name=skill.name,
                level=state.level,
                exp=state.exp,
                max_level=skill.max_level,
                next_level_exp=registry.read_exp_threshold(skill_id, state.level),
            )
        )

    available: list[AvailableSkill] = []
    for skill_id in _ids_for_class(registry, character.class_.value):
        if skill_id in session.skill_states:
            continue
        skill = registry.get(skill_id)
        available.append(
            AvailableSkill(
                skill_id=skill_id,
                name=skill.name,
                learn_level=skill.learn_level,
                mp_cost=skill.mp_cost,
                requires_book=_requires_book(skill),
            )
        )
    return SkillListOut(learned=learned, available=available)


@router.post("/learn")
async def learn_skill(
    payload: LearnIn, db: DbSession, player_id: CurrentPlayerId
) -> LearnOut:
    """显式按级习得：未学 + 到级 + 非书技（≥35 必须走技能书）→ 写 skill_state 行。"""
    character = await _character_of(db, player_id)
    session = await get_or_create_player_session(db, character.id)
    registry = _skills()
    skill = _get_skill(registry, payload.skill_id)

    async with session.lock:
        char_row = await _locked_character(db, character.id)
        if payload.skill_id in session.skill_states:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"已学会 {skill.name}",
            )
        if char_row.level < skill.learn_level:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"等级不足：{skill.name} 需要 {skill.learn_level} 级",
            )
        if _requires_book(skill):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{skill.name} 为 {BOOK_REQUIRED_LEVEL} 级技能，必须使用技能书学习",
            )
        db.add(
            SkillStateRow(
                character_id=character.id, skill_id=payload.skill_id, level=0, exp=0
            )
        )
        await db.commit()
        await session.flush_snapshot()
        session.skill_states[payload.skill_id] = SkillState(skill_id=payload.skill_id)

    _publish(
        session,
        EV_SKILL_LEARN,
        {"skill_id": skill.id, "name": skill.name, "level": 0, "source": "learn"},
    )
    return LearnOut(skill_id=skill.id, name=skill.name, level=0, exp=0, source="learn")


@router.post("/learn_from_book")
async def learn_from_book(
    payload: BookIn, db: DbSession, player_id: CurrentPlayerId
) -> LearnOut:
    """技能书习得：扣一本书 + 写 skill_state 行（35 级技能唯一习得入口）。"""
    character = await _character_of(db, player_id)
    session = await get_or_create_player_session(db, character.id)
    registry = _skills()
    items = _items()

    async with session.lock:
        char_row = await _locked_character(db, character.id)
        inst = await db.get(ItemInstance, payload.item_instance_id)
        if inst is None or inst.character_id != character.id:
            # 不区分"不存在"与"他人的"（equip/consumable 同款防探测口径）
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="物品不存在"
            )
        item_def = items.get(inst.item_id)
        if item_def is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"未知物品定义: {inst.item_id}",
            )
        if item_def.kind is not ItemKind.book:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"物品类型 {item_def.kind.value} 不是技能书",
            )
        mapped_id = _skill_id_for_book(registry, item_def)
        if mapped_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{item_def.name} 对应的技能尚未开放",
            )
        skill = registry.get(mapped_id)
        if (
            skill.class_ is not SkillClass.all
            and skill.class_.value != char_row.class_.value
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"职业不匹配：{skill.name} 限 {skill.class_.value}",
            )
        if char_row.level < skill.learn_level:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"等级不足：{skill.name} 需要 {skill.learn_level} 级",
            )
        if mapped_id in session.skill_states:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"已学会 {skill.name}",
            )
        if inst.quantity <= 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="物品数量不足"
            )

        # 扣书（归零删行）+ 写技能行，同事务提交
        inst.quantity -= 1
        if inst.quantity <= 0:
            await db.delete(inst)
        db.add(
            SkillStateRow(character_id=character.id, skill_id=mapped_id, level=0, exp=0)
        )
        await db.commit()
        await session.flush_snapshot()
        session.skill_states[mapped_id] = SkillState(skill_id=mapped_id)

    _publish(
        session,
        EV_SKILL_LEARN,
        {"skill_id": skill.id, "name": skill.name, "level": 0, "source": "book"},
    )
    return LearnOut(skill_id=skill.id, name=skill.name, level=0, exp=0, source="book")


@router.post("/use")
async def use_skill(
    payload: UseIn, db: DbSession, player_id: CurrentPlayerId
) -> UseOut:
    """施放技能：try_cast 三道闸 → 成功扣蓝 +1 熟练（升级推 skill_levelup）。

    - <35 级技能未学但到级 → 首次施放自动习得 0 级（行随本事务落库）；
    - MP 扣减同事务写 character 行（REST 写路径规则：flush 后内存跟随 DB）；
    - exp 值本端点不落库，随挂机每 5 tick persist 批次覆盖写；
    - 召唤类 self 技能成功后锁外 summon_pet（内部发布 pet_summon）。
    """
    character = await _character_of(db, player_id)
    session = await get_or_create_player_session(db, character.id)
    if has_travel(character.id):  # todo 30：行进中发起战斗 → 拒绝
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="行进中无法施放技能（跑图倒计时进行中）",
        )
    registry = _skills()
    skill = _get_skill(registry, payload.skill_id)

    auto_learned = False
    leveled_up = False
    cast_ok = False
    async with session.lock:
        char_row = await _locked_character(db, character.id)
        snap = session.char_snapshot
        state = session.skill_states.get(skill.id)
        class_ok = (
            skill.class_ is SkillClass.all
            or skill.class_.value == char_row.class_.value
        )
        if (
            state is None
            and class_ok
            and not _requires_book(skill)
            and snap.level >= skill.learn_level
        ):
            # 到级自动习得（首次施放即 0 级）；行随本事务落库
            state = SkillState(skill_id=skill.id)
            session.skill_states[skill.id] = state
            db.add(
                SkillStateRow(
                    character_id=character.id, skill_id=skill.id, level=0, exp=0
                )
            )
            auto_learned = True
        level_before = state.level if state is not None else 0

        now = int(time.monotonic())
        cd_key = (character.id, skill.id)
        attempt = try_cast(
            registry,
            skill.id,
            char_level=snap.level,
            mp=snap.mp,
            state=state,
            last_cast_tick=_rest_last_cast.get(cd_key),
            now_tick=now,
        )
        cast_ok = attempt.success
        if attempt.success:
            snap.mp = attempt.mp_after
            char_row.mp = snap.mp  # REST 内存变更同事务写行（task 26 规则）
            _rest_last_cast[cd_key] = now
            leveled_up = state is not None and state.level > level_before
        if attempt.success or auto_learned:
            # 施放成功（扣蓝）或发生了自动习得：本事务提交。习得独立于施放成败——
            # 失败也不回滚内存建行，防 session.skill_states 与 DB 分叉。
            await db.commit()
            await session.flush_snapshot()

    level_after = (
        session.skill_states[skill.id].level if skill.id in session.skill_states else 0
    )

    # --- 锁外：召唤结算 + SSE 推送 ---------------------------------------------
    pet_id: str | None = None
    if cast_ok and skill.target.value == "self":
        try:
            pet = summon_pet(session, skill.id)
        except ValueError:
            pet = None  # 非召唤类 self 技能（治愈/隐身族）→ 无宠物结算
        if pet is not None:
            pet_id = pet.pet_id
    if cast_ok:
        _publish(
            session,
            EV_LOG,
            {"kind": "skill", "text": f"你施放了{skill.name}"},
        )
        if auto_learned:
            _publish(
                session,
                EV_SKILL_LEARN,
                {
                    "skill_id": skill.id,
                    "name": skill.name,
                    "level": 0,
                    "source": "first_cast",
                },
            )
        if leveled_up:
            _publish(
                session,
                EV_SKILL_LEVELUP,
                {
                    "skill_id": skill.id,
                    "name": skill.name,
                    "level": level_after,
                    "exp": session.skill_states[skill.id].exp,
                },
            )
    return UseOut(
        skill_id=skill.id,
        success=attempt.success,
        reason=attempt.reason,
        mp_after=session.char_snapshot.mp,
        exp_gained=attempt.exp_gained,
        level_before=level_before,
        level_after=level_after,
        leveled_up=leveled_up,
        pet_id=pet_id,
    )


__all__ = ["EV_SKILL_LEARN", "EV_SKILL_LEVELUP", "router"]
