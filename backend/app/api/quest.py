"""任务系统 API（todo 29）：状态机 + 四类任务 + 奖励发放。

端点：
- GET  /api/quest/list     全部任务按状态分组（未接/进行/完成/已交付）+ 进度；
- POST /api/quest/accept   未接→进行：等级/职业门禁，boss_instance 任务接取即
  传送进独立竞技场（命运之刃降级实现，见 sim/quest.py）；
- POST /api/quest/progress 推进进度：kill=击杀悬赏 +1（与 tick 钩子同一账本）、
  collect=缴纳物品扣背包、dialog=NPC 对话（同图校验；profession 引导任务泛化为
  与任意 NPC 同图对话——gamedata 无职业导师 NPC 数据）、boss=竞技场分身击杀；
- POST /api/quest/complete 达标待交付→已交付（终态）：发奖励（金币/经验/物品/
  技能书——技能书即普通物品入包，习得走 todo 28 /api/skill/learn_from_book）。

状态机（quest_state.status 四态）：未接=无行 → in_progress(进行) → completed
(达标待交付) → delivered(已交付)。活跃会话期间内存账本（sim/quest.py）是事实源，
REST 写路径在 session.lock 内读写同一份内存并直写 DB 行；tick 杀怪的内存脏行由
persist_session_state 批次落库。

错误码：404 任务不存在；422 等级不符/职业不符/前置未达/进度未满/物品不足/
不在同图/不在竞技场/背包已满；409 已接取/已交付/已完成待交付。
并发纪律（todo 15 横切约束）：锁临界区仅限 DB 事务 + flush_snapshot + 换图
内存操作；SSE publish 恒在锁外。
"""

from __future__ import annotations

import json
from typing import Final, Literal, cast

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentPlayerId, DbSession
from app.api.inventory import (
    EV_BAG,
    _character_of,
    _items,
    _load_equipment_state,
    _panel_for,
    _publish_stat,
)
from app.gamedata_loader import load_all
from app.gamedata_schema.items import ItemDef
from app.gamedata_schema.quests import QuestClass, QuestDef, QuestKind
from app.models.character import Character
from app.models.item_instance import ItemInstance
from app.models.quest_state import QuestState, QuestStatus
from app.sim.quest import (
    EV_QUEST_ACCEPT,
    EV_QUEST_COMPLETE,
    EV_QUEST_PROGRESS,
    advance,
    arena_map_id,
    enter_arena,
    hydrate,
    leave_arena_to,
    states_of,
)
from app.sim.session import PlayerSession, get_or_create_player_session

router = APIRouter(prefix="/api/quest", tags=["quest"])


# --- 出入参模型 -----------------------------------------------------------------


class QuestOut(BaseModel):
    quest_id: str
    name: str
    kind: str
    status: str
    level_req: int
    required_class: str | None
    target_id: str
    target_count: int
    count: int
    need: int
    reward_gold: int
    reward_exp: int
    reward_items: list[str]
    dialogue: dict[str, str]
    # boss_instance 任务接取即进的竞技场图 id（前端展示用）
    arena_map_id: str | None = None


class QuestListOut(BaseModel):
    not_started: list[QuestOut]
    in_progress: list[QuestOut]
    completed: list[QuestOut]
    delivered: list[QuestOut]


class AcceptIn(BaseModel):
    quest_id: str


class AcceptOut(BaseModel):
    quest: QuestOut
    message: str
    # boss_instance 任务接取即传送：新所在图 id
    map_id: str | None = None


class ProgressIn(BaseModel):
    quest_id: str
    action: Literal["kill", "collect", "dialog", "boss"]


class RewardItemsOut(BaseModel):
    item_id: str
    name: str
    quantity: int


class CompleteOut(BaseModel):
    quest_id: str
    name: str
    status: str
    gold: int
    exp: int
    rewards: dict[str, object]


def _quests() -> dict[str, QuestDef]:
    return cast("dict[str, QuestDef]", load_all()["quests"])


def _npcs() -> dict[str, object]:
    return cast("dict[str, object]", load_all()["npcs"])


def _publish(session: PlayerSession, event: str, payload: dict[str, object]) -> None:
    """锁外 SSE 推送（json dumps 统一 ensure_ascii=False）。"""
    session.sse_state.publish(event, json.dumps(payload, ensure_ascii=False))


def _quest_out(qd: QuestDef, mq: object | None, *, status_: QuestStatus) -> QuestOut:
    """任务定义 + 内存进度 → 出参（未接任务 count=0）。"""
    from app.sim.quest import MemQuest

    mem = cast("MemQuest | None", mq)
    return QuestOut(
        quest_id=qd.id,
        name=qd.name,
        kind=qd.kind.value,
        status=status_.value,
        level_req=qd.level_req,
        required_class=(
            qd.required_class.value
            if qd.required_class is not None and qd.required_class is not QuestClass.all
            else None
        ),
        target_id=qd.target_id,
        target_count=qd.target_count,
        count=mem.count if mem is not None else 0,
        need=qd.target_count,
        reward_gold=qd.reward_gold,
        reward_exp=qd.reward_exp,
        reward_items=list(qd.reward_items),
        dialogue=dict(qd.dialogue),
        arena_map_id=arena_map_id(qd) if qd.boss_instance is not None else None,
    )


# --- GET /list -------------------------------------------------------------------


@router.get("/list")
async def quest_list(db: DbSession, player_id: CurrentPlayerId) -> QuestListOut:
    """全部任务按状态分组（读路径不取锁；内存账本缺账时先从 DB 恢复）。"""
    character = await _character_of(db, player_id)
    await hydrate(character.id)
    bucket = states_of(character.id)
    groups: dict[str, list[QuestOut]] = {
        "not_started": [],
        "in_progress": [],
        "completed": [],
        "delivered": [],
    }
    for qd in _quests().values():
        mq = bucket.get(qd.id)
        if mq is None:
            groups["not_started"].append(
                _quest_out(qd, None, status_=QuestStatus.not_started)
            )
        else:
            key = mq.status.value
            groups.setdefault(key, []).append(_quest_out(qd, mq, status_=mq.status))
    return QuestListOut(**groups)


# --- POST /accept ----------------------------------------------------------------


@router.post("/accept")
async def quest_accept(
    payload: AcceptIn, db: DbSession, player_id: CurrentPlayerId
) -> AcceptOut:
    """接任务：未接→进行。等级/职业门禁；boss_instance 任务接取即进竞技场。"""
    character = await _character_of(db, player_id)
    session = await get_or_create_player_session(db, character.id)

    qd = _quests().get(payload.quest_id)
    if qd is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    async with session.lock:
        char_row = await db.get(Character, character.id)
        if char_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在"
            )
        await db.refresh(char_row)

        await hydrate(character.id)
        bucket = states_of(character.id)
        if qd.id in bucket:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"任务已接取（状态 {bucket[qd.id].status.value}）",
            )
        if char_row.level < qd.level_req:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"等级不足：需要 {qd.level_req} 级（当前 {char_row.level} 级）",
            )
        if (
            qd.required_class is not None
            and qd.required_class is not QuestClass.all
            and qd.required_class.value != char_row.class_.value
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"职业不符：限 {qd.required_class.value}",
            )

        # 内存账本 + DB 行同事务落地（status=in_progress, progress={"count":0}）
        from app.sim.quest import MemQuest

        mq = MemQuest(quest_id=qd.id, status=QuestStatus.in_progress, count=0)
        bucket[qd.id] = mq
        db.add(
            QuestState(
                character_id=character.id,
                quest_id=qd.id,
                status=QuestStatus.in_progress,
                progress={"count": 0},
            )
        )
        await db.commit()
        await session.flush_snapshot()

        # boss_instance 任务：接取即传送进竞技场（换图内存操作允许在锁内）
        entered_map: str | None = None
        if qd.boss_instance is not None:
            entered_map = await enter_arena(session, qd)

    # --- 锁外：SSE 推送 -------------------------------------------------------
    _publish(
        session, EV_QUEST_ACCEPT, _quest_out(qd, mq, status_=mq.status).model_dump()
    )
    _publish(
        session,
        "log",
        {
            "kind": "quest_accept",
            "text": f"接受任务【{qd.name}】：{qd.dialogue.get('accept', '')}",
        },
    )
    if entered_map is not None:
        _publish(
            session,
            "zone_change",
            {"map_id": entered_map, "reason": "quest_arena"},
        )
    return AcceptOut(
        quest=_quest_out(qd, mq, status_=mq.status),
        message=f"接受任务【{qd.name}】",
        map_id=entered_map,
    )


# --- POST /progress --------------------------------------------------------------


@router.post("/progress")
async def quest_progress(
    payload: ProgressIn, db: DbSession, player_id: CurrentPlayerId
) -> dict[str, object]:
    """推进进度：kill/boss 计数、collect 扣背包、dialog 同图对话。

    kill 动作与 tick 击杀钩子写同一份内存账本（手动上报路径，MVP 信任客户端，
    与 dialog 同级信任模型）；collect 从 DB 行扣减后 flush 对齐内存。
    """
    character = await _character_of(db, player_id)
    session = await get_or_create_player_session(db, character.id)
    items = _items()

    qd = _quests().get(payload.quest_id)
    if qd is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    message = ""

    async with session.lock:
        char_row = await db.get(Character, character.id)
        if char_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在"
            )
        await db.refresh(char_row)

        await hydrate(character.id)
        mq = states_of(character.id).get(qd.id)
        if mq is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="尚未接取该任务",
            )
        if mq.status is QuestStatus.delivered:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="任务已交付"
            )
        if mq.status is QuestStatus.completed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="任务已完成，请交付领取奖励",
            )

        # action ↔ 任务类型映射校验（422 前置未达族）
        if qd.boss_instance is not None:
            if payload.action != "boss":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="该任务需在竞技场挑战分身（action=boss）",
                )
            if session.zone is None or session.zone.map_id != arena_map_id(qd):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"不在挑战区 {arena_map_id(qd)}，无法推进分身击杀",
                )
            message = (
                f"击败弱化的{qd.target_id}分身（{mq.count + 1}/{qd.target_count}）"
            )
        elif qd.kind is QuestKind.kill:
            if payload.action != "kill":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="击杀悬赏需 action=kill",
                )
            message = f"击杀进度 {mq.count + 1}/{qd.target_count}"
        elif qd.kind is QuestKind.collect:
            if payload.action != "collect":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="收集任务需 action=collect 缴纳物品",
                )
            item_def = items.get(qd.target_id)
            if item_def is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"未知收集物定义: {qd.target_id}",
                )
            rows = list(
                (
                    await db.execute(
                        select(ItemInstance).where(
                            ItemInstance.character_id == character.id,
                            ItemInstance.item_id == qd.target_id,
                        )
                    )
                ).scalars()
            )
            held = sum(r.quantity for r in rows)
            if held < qd.target_count:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"物品不足：需要 {item_def.name}×{qd.target_count}，"
                        f"身上只有 {held}"
                    ),
                )
            # 缴纳扣背包：贪心扣量，归零删行
            remaining = qd.target_count
            for r in rows:
                take = min(r.quantity, remaining)
                r.quantity -= take
                remaining -= take
                if r.quantity <= 0:
                    await db.delete(r)
                if remaining == 0:
                    break
            message = f"缴纳{item_def.name}×{qd.target_count}"
        elif qd.kind is QuestKind.dialogue:
            if payload.action != "dialog":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="对话任务需 action=dialog",
                )
            npc = _npcs().get(qd.target_id)
            npc_map = getattr(npc, "map_id", None) if npc is not None else None
            if not isinstance(npc_map, str):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"未知对话 NPC: {qd.target_id}",
                )
            if session.char_snapshot.map_id != npc_map:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"需与 {qd.target_id} 同图对话（其在 {npc_map}）",
                )
            message = "与 NPC 对话完成"
        else:  # QuestKind.profession —— 技能书引导（MVP 泛化：任意 NPC 同图对话）
            if payload.action != "dialog":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="职业引导任务需 action=dialog 与导师对话",
                )
            snap_map = session.char_snapshot.map_id
            on_npc_map = any(
                getattr(npc, "map_id", None) == snap_map for npc in _npcs().values()
            )
            if not on_npc_map:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="附近没有可对话的 NPC",
                )
            message = "与导师对话完成"

        # collect/dialog 缴纳/对话一次交割全部目标 → 计数直达目标值；
        # kill/boss 单次 +1（tick 钩子同款粒度）
        delta = (
            1
            if payload.action in ("kill", "boss")
            else qd.target_count
        )
        payload_out = advance(session, qd, mq, delta=delta)
        await db.commit()
        await session.flush_snapshot()

    # --- 锁外：SSE 推送 -------------------------------------------------------
    _publish(session, EV_QUEST_PROGRESS, payload_out)
    _publish(session, "log", {"kind": "quest_progress", "text": message})
    return {
        "quest_id": qd.id,
        "count": payload_out["count"],
        "need": payload_out["need"],
        "status": payload_out["status"],
        "message": message,
    }


# --- POST /complete --------------------------------------------------------------

_REWARD_LOG_KIND: Final[str] = "quest_reward"


@router.post("/complete")
async def quest_complete(
    payload: AcceptIn, db: DbSession, player_id: CurrentPlayerId
) -> CompleteOut:
    """交付任务：达标待交付→已交付（终态）；发奖励金币/经验/物品（含技能书）。

    奖励入账以内存快照为基数（tick 击杀经验/掉落金币可能尚未落库——内存恒为
    活跃会话事实源），commit 后 flush_snapshot 对齐 DB。重复交付 409。
    """
    character = await _character_of(db, player_id)
    session = await get_or_create_player_session(db, character.id)
    items = _items()

    qd = _quests().get(payload.quest_id)
    if qd is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    async with session.lock:
        char_row = await db.get(Character, character.id)
        if char_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在"
            )
        await db.refresh(char_row)

        await hydrate(character.id)
        mq = states_of(character.id).get(qd.id)
        if mq is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="尚未接取该任务",
            )
        if mq.status is QuestStatus.delivered:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="任务已交付"
            )
        if mq.status is not QuestStatus.completed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"进度未满：{mq.count}/{qd.target_count}，完成后方可交付",
            )

        # 背包容量预检：不可堆叠/无可合并行的奖励各占一格（失败不动状态可重试）
        rows = list(
            (
                await db.execute(
                    select(ItemInstance).where(
                        ItemInstance.character_id == character.id
                    )
                )
            ).scalars()
        )
        new_slots = 0
        for item_id in qd.reward_items:
            item_def = items.get(item_id)
            if item_def is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"未知奖励物品定义: {item_id}",
                )
            mergeable = item_def.stackable and any(r.item_id == item_id for r in rows)
            if not mergeable:
                new_slots += 1
        if len(rows) + new_slots > session.char_snapshot.bag_capacity:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(f"背包已满（需 {new_slots} 格空位），清理后再交付"),
            )

        # 发奖：金币/经验以内存快照为基数；物品入包（堆叠合并优先）
        snap = session.char_snapshot
        snap.gold += qd.reward_gold
        snap.exp += qd.reward_exp
        char_row.gold = snap.gold
        char_row.exp = snap.exp
        granted: list[RewardItemsOut] = []
        for item_id in qd.reward_items:
            item_def = cast("ItemDef", items[item_id])
            qty = 1  # 奖励物品一律 ×1（gamedata 契约：reward_items 无数量字段）
            target = (
                next((r for r in rows if r.item_id == item_id), None)
                if item_def.stackable
                else None
            )
            if target is not None:
                target.quantity += qty
                granted.append(
                    RewardItemsOut(
                        item_id=item_id, name=item_def.name, quantity=target.quantity
                    )
                )
            else:
                row = ItemInstance(
                    character_id=character.id, item_id=item_id, quantity=qty
                )
                db.add(row)
                rows.append(row)
                granted.append(
                    RewardItemsOut(item_id=item_id, name=item_def.name, quantity=qty)
                )

        # 状态机终态：delivered（内存 + DB 行同事务）
        mq.status = QuestStatus.delivered
        row = (
            await db.execute(
                select(QuestState).where(
                    QuestState.character_id == character.id,
                    QuestState.quest_id == qd.id,
                )
            )
        ).scalar_one_or_none()
        if row is None:  # 防御：accept 行缺失（历史脏数据）时补建
            row = QuestState(
                character_id=character.id,
                quest_id=qd.id,
                status=QuestStatus.delivered,
                progress={"count": mq.count},
            )
            db.add(row)
        else:
            row.status = QuestStatus.delivered
            row.progress = {"count": mq.count}

        await db.commit()
        await session.flush_snapshot()

        # 交付后离开竞技场回绑定城（换图内存操作允许在锁内）
        returned_map: str | None = None
        if (
            qd.boss_instance is not None
            and session.zone is not None
            and session.zone.map_id == arena_map_id(qd)
        ):
            returned_map = await leave_arena_to(session, snap.bind_city)

        slots, instances = await _load_equipment_state(db, character.id)
        panel = _panel_for(char_row, slots, instances, items)

    # --- 锁外：SSE 推送（quest_complete + 奖励事件 + stat/bag_update）-----------
    rewards: dict[str, object] = {
        "gold": qd.reward_gold,
        "exp": qd.reward_exp,
        "items": [g.model_dump() for g in granted],
    }
    _publish(
        session,
        EV_QUEST_COMPLETE,
        {
            "quest_id": qd.id,
            "name": qd.name,
            "status": QuestStatus.delivered.value,
            "rewards": rewards,
        },
    )
    _publish(
        session,
        "log",
        {
            "kind": _REWARD_LOG_KIND,
            "text": f"交付任务【{qd.name}】：金币+{qd.reward_gold} 经验+{qd.reward_exp}",
        },
    )
    for g in granted:
        _publish(
            session,
            EV_BAG,
            {
                "reason": "quest_reward",
                "item_id": g.item_id,
                "quantity": g.quantity,
                "gold": session.char_snapshot.gold,
            },
        )
    _publish_stat(session, reason="quest_reward", panel=panel)
    if returned_map is not None:
        _publish(
            session,
            "zone_change",
            {"map_id": returned_map, "reason": "quest_return"},
        )
    return CompleteOut(
        quest_id=qd.id,
        name=qd.name,
        status=QuestStatus.delivered.value,
        gold=session.char_snapshot.gold,
        exp=session.char_snapshot.exp,
        rewards=rewards,
    )
