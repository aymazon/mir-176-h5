"""装备穿戴/卸下 API（todo 23）：门槛校验 + 面板即时重算 + SSE stat 事件。

槽位模型：equipment_slot 表 10 槽位（weapon/armor/helmet/necklace/bracelet_l/
bracelet_r/ring_l/ring_r/belt/boots），唯一约束 (character_id, slot)。
item_instance 行即背包内容："穿戴" = 槽位行引用该实例，"卸下" = 引用清空
（实例行仍在背包——装备不可堆叠、无新行，故无容量/堆叠合并问题）。

校验链（全在锁内）：物品存在且属本人(404) → kind↔slot 映射(422) →
同一实例未占他槽(409) → 等级(422) → 腕力/魔法力/道术（compute_panel 派生值，
含当前已穿装备，176 契约按总面板计）(422) → 职业限制(422，见下)。

职业限制：ItemDef 目前无 class 字段——176 数据以属性门槛表达职业门禁
（法师腕力恒 1，永远够不到黑铁头盔 need_strength=46）。此处保留 class_
前置检查位（getattr 缺省 None=通用），数据侧一旦扩展即生效。

并发纪律（todo 15 横切约束）：锁临界区仅限 DB 事务 + flush_snapshot
（DB→内存只读重读，自身不取锁）；SSE publish 恒在锁外。
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Final, cast

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPlayerId, DbSession
from app.combat.stats import (
    PANEL_KEYS,
    ClassKey,
    EquipmentInput,
    StatPanel,
    compute_panel,
)
from app.gamedata_loader import load_all
from app.gamedata_schema.items import ItemDef, ItemKind
from app.models.character import Character
from app.models.equipment_slot import EquipmentSlot, EquipmentSlotType
from app.models.item_instance import ItemInstance
from app.sim.session import PlayerSession, get_or_create_player_session
from app.sim.tick_loop import EV_STAT

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

# 槽位 → 接受的物品 kind（bracelet/ring 左右槽可互换，其余一对一固定映射）。
SLOT_ACCEPTS: Final[dict[EquipmentSlotType, frozenset[ItemKind]]] = {
    EquipmentSlotType.weapon: frozenset({ItemKind.weapon}),
    EquipmentSlotType.armor: frozenset({ItemKind.armor}),
    EquipmentSlotType.helmet: frozenset({ItemKind.helmet}),
    EquipmentSlotType.necklace: frozenset({ItemKind.necklace}),
    EquipmentSlotType.bracelet_l: frozenset({ItemKind.bracelet}),
    EquipmentSlotType.bracelet_r: frozenset({ItemKind.bracelet}),
    EquipmentSlotType.ring_l: frozenset({ItemKind.ring}),
    EquipmentSlotType.ring_r: frozenset({ItemKind.ring}),
    EquipmentSlotType.belt: frozenset({ItemKind.belt}),
    EquipmentSlotType.boots: frozenset({ItemKind.boots}),
}


class EquipIn(BaseModel):
    item_instance_id: int
    slot: EquipmentSlotType


class UnequipIn(BaseModel):
    slot: EquipmentSlotType


class EquipOut(BaseModel):
    slot: EquipmentSlotType
    item_instance_id: int
    item_id: str
    # 被换下的旧装备实例（旧槽位装备自动回背包）；空槽位穿戴为 None
    replaced_item_instance_id: int | None
    panel: StatPanel


class UnequipOut(BaseModel):
    slot: EquipmentSlotType
    item_instance_id: int
    panel: StatPanel


@lru_cache(maxsize=1)
def _items() -> dict[str, ItemDef]:
    """进程内缓存已校验物品目录（tick_loop._gamedata 同款）。"""
    return cast("dict[str, ItemDef]", load_all()["items"])


def _equipment_inputs(
    slots: list[EquipmentSlot],
    instances: dict[int, ItemInstance],
    items: dict[str, ItemDef],
) -> list[EquipmentInput]:
    """槽位行 + 实例行 → compute_panel 入参（stats 白名单过滤非面板键）。

    真实数据里白色虎齿项链带 magic_resist（非 StatPanel 键）——StatDict 白名单
    会 fail-fast 拒未知键，穿戴路径是用户触发热路径，过滤优于 500。
    """
    inputs: list[EquipmentInput] = []
    for row in slots:
        if row.item_instance_id is None:
            continue
        inst = instances.get(row.item_instance_id)
        if inst is None:
            continue
        item_def = items.get(inst.item_id)
        if item_def is None:
            continue
        stats = {k: v for k, v in item_def.stats.items() if k in PANEL_KEYS}
        extra = {
            k: v
            for k, v in (inst.extra_json or {}).items()
            if k in PANEL_KEYS and isinstance(v, int) and not isinstance(v, bool)
        }
        inputs.append(
            EquipmentInput(
                slot=row.slot.value,
                stats=stats,
                lucky=inst.lucky,
                curse=inst.curse,
                extra_json=extra,
            )
        )
    return inputs


def _panel_for(
    char_row: Character,
    slots: list[EquipmentSlot],
    instances: dict[int, ItemInstance],
    items: dict[str, ItemDef],
) -> StatPanel:
    """裸装基线 + 当前装备 → 最终面板（stats.py 唯一计算点）。"""
    return compute_panel(
        char_class=ClassKey(char_row.class_.value),
        level=char_row.level,
        equipment=_equipment_inputs(slots, instances, items),
    )


def _publish_stat(session: PlayerSession, *, reason: str, panel: StatPanel) -> None:
    """锁外推 SSE stat 事件（键位与 tick_loop 的 stat 事件对齐 + 面板）。"""
    snap = session.char_snapshot
    session.sse_state.publish(
        EV_STAT,
        json.dumps(
            {
                "reason": reason,
                "hp": snap.hp,
                "max_hp": snap.max_hp,
                "mp": snap.mp,
                "max_mp": snap.max_mp,
                "exp": snap.exp,
                "gold": snap.gold,
                "panel": panel.model_dump(),
            },
            ensure_ascii=False,
        ),
    )


async def _character_of(db: AsyncSession, player_id: int) -> Character:
    character = (
        await db.execute(select(Character).where(Character.player_id == player_id))
    ).scalar_one_or_none()
    if character is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="尚未建号"
        )
    return character


async def _load_equipment_state(
    db: AsyncSession, character_id: int
) -> tuple[list[EquipmentSlot], dict[int, ItemInstance]]:
    slots = list(
        (
            await db.execute(
                select(EquipmentSlot).where(EquipmentSlot.character_id == character_id)
            )
        )
        .scalars()
        .all()
    )
    instances = {
        row.id: row
        for row in (
            await db.execute(
                select(ItemInstance).where(ItemInstance.character_id == character_id)
            )
        )
        .scalars()
        .all()
    }
    return slots, instances


@router.post("/equip")
async def equip(
    payload: EquipIn, db: DbSession, player_id: CurrentPlayerId
) -> EquipOut:
    character = await _character_of(db, player_id)
    session = await get_or_create_player_session(db, character.id)
    items = _items()

    async with session.lock:
        # 锁内 refresh 取 DB 最新角色行（tick 落库与本事务由同一把锁串行化）。
        char_row = await db.get(Character, character.id)
        if char_row is None:  # 建号后被删的极端竞态
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在"
            )
        await db.refresh(char_row)

        inst = await db.get(ItemInstance, payload.item_instance_id)
        if inst is None or inst.character_id != character.id:
            # 不区分"不存在"与"他人的"——不泄露物品存在性（不允许穿他人装备）。
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="物品不存在"
            )
        item_def = items.get(inst.item_id)
        if item_def is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"未知物品定义: {inst.item_id}",
            )
        if item_def.kind not in SLOT_ACCEPTS[payload.slot]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"物品类型 {item_def.kind} 不能穿戴到槽位 {payload.slot.value}",
            )

        slots, instances = await _load_equipment_state(db, character.id)
        # 同一实例不可占两个槽位（含本槽重复穿戴）。
        if any(row.item_instance_id == inst.id for row in slots):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="该物品已穿戴"
            )

        if char_row.level < item_def.need_level:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"等级不足：需要 {item_def.need_level} 级",
            )
        # 腕力/魔法力/道术门槛按当前总面板（含已穿装备）判定。
        panel = _panel_for(char_row, slots, instances, items)
        if panel.strength < item_def.need_strength:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"腕力不足：需要 {item_def.need_strength}（当前 {panel.strength}）",
            )
        if panel.magic_power < item_def.need_magic:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"魔法力不足：需要 {item_def.need_magic}（当前 {panel.magic_power}）",
            )
        if panel.taoism < item_def.need_taoism:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"道术不足：需要 {item_def.need_taoism}（当前 {panel.taoism}）",
            )
        # 职业限制：数据暂无 class 字段（176 以属性门槛表达）；getattr 缺省 None=通用。
        required_class = cast("str | None", getattr(item_def, "class_", None))
        if required_class is not None and required_class != char_row.class_.value:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"职业不匹配：限 {required_class}",
            )

        # 穿戴：占用槽位；旧装备被替换即自动回背包（引用清空，实例行不动）。
        target = next((row for row in slots if row.slot == payload.slot), None)
        replaced_id: int | None = None
        if target is None:
            target = EquipmentSlot(
                character_id=character.id,
                slot=payload.slot,
                item_instance_id=inst.id,
            )
            db.add(target)
            slots.append(target)
        else:
            replaced_id = target.item_instance_id
            target.item_instance_id = inst.id
        await db.commit()
        await session.flush_snapshot()
        new_panel = _panel_for(char_row, slots, instances, items)

    _publish_stat(session, reason="equip", panel=new_panel)
    return EquipOut(
        slot=payload.slot,
        item_instance_id=inst.id,
        item_id=inst.item_id,
        replaced_item_instance_id=replaced_id,
        panel=new_panel,
    )


@router.post("/unequip")
async def unequip(
    payload: UnequipIn, db: DbSession, player_id: CurrentPlayerId
) -> UnequipOut:
    character = await _character_of(db, player_id)
    session = await get_or_create_player_session(db, character.id)
    items = _items()

    async with session.lock:
        char_row = await db.get(Character, character.id)
        if char_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在"
            )
        await db.refresh(char_row)

        row = (
            await db.execute(
                select(EquipmentSlot).where(
                    EquipmentSlot.character_id == character.id,
                    EquipmentSlot.slot == payload.slot,
                )
            )
        ).scalar_one_or_none()
        if row is None or row.item_instance_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="槽位未装备"
            )
        item_instance_id = row.item_instance_id
        # 卸下 = 清空槽位引用；实例行留在背包（装备不可堆叠，无合并）。
        row.item_instance_id = None
        await db.commit()
        await session.flush_snapshot()
        slots, instances = await _load_equipment_state(db, character.id)
        new_panel = _panel_for(char_row, slots, instances, items)

    _publish_stat(session, reason="unequip", panel=new_panel)
    return UnequipOut(
        slot=payload.slot, item_instance_id=item_instance_id, panel=new_panel
    )
