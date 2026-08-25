"""消耗品使用 API（todo 26）：药品瞬回 / 神水限时 buff / 祝福油 / 三卷。

POST /api/inventory/use，body ``{item_instance_id: int}``，按 item_id/kind 分支：
- potion（stats 带 hp_restore/mp_restore）：瞬回结算（与挂机自动链同源数值，
  钳上限）；HP/MP 随本事务写 character 行（REST 写路径规则：DB 为准、
  flush 后内存跟随）。手动喝不写 potion_cooldowns——CD 是挂机自动链的防抖闸
  （REST 手动操作无连点抖动），见 docs/MVP/README.md §8；
- divine_water：加限时 buff EffectInstance（时长 = balance.DIVINE_WATER_
  DURATION_TICKS；效果模板取 effects.EFFECT_REGISTRY["divine_water"]，加值
  以物品 stats 的 ``*_bonus`` 键覆盖）。同款重复使用 = 刷新（替换旧实例）。
  buff_json 随本事务写 character 行；登出不清空、重登由 hydrate_buffs 按
  saved_at 墙钟折损离线时长（真实时间过期语义）；
- blessing_oil：装备武器 lucky < BLESSING_OIL_LUCK_MAX 时按 BLESSING_OIL_
  SUCCESS_RATE 概率 +1；已达上限 422 不扣油；概率失败扣油不升幸运；
- return_scroll：扣卷后（锁外）调 tick_loop.trigger_return_city 占位回城
  （其内部自带发布 zone_change，本端点不重复发）；
- random_scroll：MVP 无坐标网格——扣卷 + 掷 PORTAL_RANDOM_* 距离档日志播报；
- dungeon_escape_scroll：扣卷 + BFS 就近 NPC 图（洞穴入口城语义），无可达
  城镇回退绑定城。

校验链（全在锁内）：物品存在且属本人(404) → 定义存在(422) → kind 可用
(422) → stackable(422) → quantity>0(409)。并发纪律（todo 15 横切约束）：
锁临界区仅限 DB 事务 + flush_snapshot + 换图内存操作；SSE publish 与
trigger_return_city（内部自带取锁，不可在锁内调用）恒在锁外。
"""

from __future__ import annotations

import json
from collections import deque
from contextlib import suppress
from typing import cast

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPlayerId, DbSession
from app.api.inventory import (
    _character_of,
    _items,
    _load_equipment_state,
    _panel_for,
    _publish_stat,
)
from app.combat import balance
from app.combat.effects import EffectInstance, get_effect
from app.gamedata_loader import load_all
from app.gamedata_schema.items import ItemKind
from app.models.character import Character
from app.models.equipment_slot import EquipmentSlot, EquipmentSlotType
from app.models.item_instance import ItemInstance
from app.sim.session import get_or_create_player_session
from app.sim.tick_loop import (
    EV_LOG,
    EV_ZONE_CHANGE,
    buffs_to_payload,
    hydrate_buffs,
    trigger_return_city,
)

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

# 神水物品 id（docs/MVP/README.md §8 攻击神水；效果模板 divine_water）
DIVINE_WATER_ID = "divine_water"
# 可使用物品种类：药品 + 卷轴（祝福油/三卷均为 scroll kind）
USABLE_KINDS: frozenset[ItemKind] = frozenset({ItemKind.potion, ItemKind.scroll})


class UseIn(BaseModel):
    item_instance_id: int


class UseOut(BaseModel):
    item_instance_id: int
    item_id: str
    name: str
    # restored=药品瞬回 | buff_applied=神水上 buff | bless_success/bless_failed=
    # 祝福油 | returned=回城卷 | teleported=随机卷 | escaped=地牢逃脱卷
    result: str
    message: str
    quantity_left: int
    hp: int
    mp: int
    lucky_after: int | None = None
    map_id: str | None = None


def _escape_target(map_id: str) -> str | None:
    """BFS 就近找有 NPC 的地图（洞穴入口城语义）；起点不计入，无可达返回 None。"""
    data = load_all()
    maps = cast("dict[str, object]", data["maps"])
    npc_maps: set[str] = set()
    for npc in cast("dict[str, object]", data["npcs"]).values():
        npc_map = getattr(npc, "map_id", None)
        if isinstance(npc_map, str):
            npc_maps.add(npc_map)
    visited = {map_id}
    queue: deque[str] = deque([map_id])
    while queue:
        current = queue.popleft()
        map_def = maps.get(current)
        adjacent = getattr(map_def, "adjacent", None) or []
        for neighbor in adjacent:
            if neighbor in visited:
                continue
            visited.add(neighbor)
            if neighbor in npc_maps:
                return neighbor
            queue.append(neighbor)
    return None


async def _weapon_instance(db: AsyncSession, character_id: int) -> ItemInstance:
    """取武器槽引用的实例行；未装备武器 422（祝福油前置）。"""
    row = (
        await db.execute(
            select(EquipmentSlot).where(
                EquipmentSlot.character_id == character_id,
                EquipmentSlot.slot == EquipmentSlotType.weapon,
            )
        )
    ).scalar_one_or_none()
    if row is None or row.item_instance_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="未装备武器，无法使用祝福油",
        )
    weapon = await db.get(ItemInstance, row.item_instance_id)
    if weapon is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="武器槽引用的实例不存在",
        )
    return weapon


@router.post("/use")
async def use_consumable(
    payload: UseIn, db: DbSession, player_id: CurrentPlayerId
) -> UseOut:
    """使用背包消耗品（todo 26）：校验 → 分支结算 → 扣量 → commit → SSE 锁外。"""
    character = await _character_of(db, player_id)
    session = await get_or_create_player_session(db, character.id)
    items = _items()

    lucky_after: int | None = None
    map_after: str | None = None
    async with session.lock:
        char_row = await db.get(Character, character.id)
        if char_row is None:  # 建号后被删的极端竞态
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在"
            )
        await db.refresh(char_row)

        inst = await db.get(ItemInstance, payload.item_instance_id)
        if inst is None or inst.character_id != character.id:
            # 不区分"不存在"与"他人的"——不泄露物品存在性（equip 同款口径）。
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="物品不存在"
            )
        item_def = items.get(inst.item_id)
        if item_def is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"未知物品定义: {inst.item_id}",
            )
        if item_def.kind not in USABLE_KINDS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"物品类型 {item_def.kind.value} 无法使用",
            )
        if not item_def.stackable:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="不可堆叠物品无法使用",
            )
        if inst.quantity <= 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="物品数量不足"
            )

        used_item_id = inst.item_id

        # 神水路径先按 DB 快照恢复 buff（登出不清空语义；空列表才回填防覆盖）
        if used_item_id == DIVINE_WATER_ID and not session.buffs:
            await hydrate_buffs(session)

        snap = session.char_snapshot
        result = ""
        message = ""

        if used_item_id == DIVINE_WATER_ID:
            # 限时 buff：模板取注册表定义，加值以物品 stats 的 *_bonus 覆盖；
            # 时长用 balance 常量（600s），不复用注册表里的占位短时长。
            definition = get_effect(DIVINE_WATER_ID)
            params = {
                **definition.params,
                **{
                    k: v
                    for k, v in item_def.stats.items()
                    if k.endswith("_bonus")
                },
            }
            fresh = EffectInstance(
                definition_id=DIVINE_WATER_ID,
                owner_id=str(character.id),
                remaining_ticks=balance.DIVINE_WATER_DURATION_TICKS,
                params=params,
            )
            session.buffs = [
                b for b in session.buffs if b.definition_id != DIVINE_WATER_ID
            ]
            session.buffs.append(fresh)
            char_row.buff_json = buffs_to_payload(session.buffs)
            bonus_text = " ".join(f"{k}+{v}" for k, v in params.items())
            result = "buff_applied"
            message = (
                f"{item_def.name}生效（{bonus_text}），"
                f"持续 {balance.DIVINE_WATER_DURATION_TICKS} 秒"
            )
        elif (
            item_def.stats.get("hp_restore", 0) > 0
            or item_def.stats.get("mp_restore", 0) > 0
        ):
            # 药品瞬回（金创药/魔法药/太阳水族）：数值与挂机自动链同源（stats）。
            # HP/MP 同事务写行（REST 写路径规则：flush 后内存跟随 DB）。
            hp_restore = int(item_def.stats.get("hp_restore", 0))
            mp_restore = int(item_def.stats.get("mp_restore", 0))
            snap.hp = min(snap.max_hp, snap.hp + hp_restore)
            snap.mp = min(snap.max_mp, snap.mp + mp_restore)
            char_row.hp = snap.hp
            char_row.mp = snap.mp
            result = "restored"
            message = (
                f"喝下{item_def.name}"
                f"（HP+{hp_restore} MP+{mp_restore} → {snap.hp}/{snap.max_hp}）"
            )
        elif used_item_id == "blessing_oil":
            weapon = await _weapon_instance(db, character.id)
            if weapon.lucky >= balance.BLESSING_OIL_LUCK_MAX:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"武器幸运已达上限 {balance.BLESSING_OIL_LUCK_MAX}，"
                        "祝福油无法继续提升"
                    ),
                )
            if session.rng.random() < balance.BLESSING_OIL_SUCCESS_RATE:
                weapon.lucky += 1
                result = "bless_success"
                message = f"祝福成功！武器幸运升至 {weapon.lucky}"
            else:
                result = "bless_failed"
                message = f"祝福失败，幸运不变（当前 {weapon.lucky}）"
            lucky_after = weapon.lucky
        elif used_item_id == "return_scroll":
            result = "returned"
            message = f"使用回城卷，返回绑定主城 {snap.bind_city}"
            map_after = snap.bind_city  # 锁外由 trigger_return_city 切图
        elif used_item_id == "random_scroll":
            span = (
                balance.PORTAL_RANDOM_MAX_DIST - balance.PORTAL_RANDOM_MIN_DIST + 1
            )
            dist = balance.PORTAL_RANDOM_MIN_DIST + int(session.rng.random() * span)
            result = "teleported"
            message = f"随机传送到地图另一处（距离约 {dist} 步）"
            map_after = snap.map_id
        elif used_item_id == "dungeon_escape_scroll":
            target = _escape_target(snap.map_id) or snap.bind_city
            await db.execute(
                update(Character)
                .where(Character.id == character.id)
                .values(map_id=target)
            )
            old_zone = session.zone
            if old_zone is not None and old_zone.map_id != target:
                from app.sim.zone import leave_map

                with suppress(KeyError):
                    await leave_map(old_zone.map_id, character.id)
                session.zone = None
            if session.zone is None:
                from app.sim.zone import enter_map

                try:
                    session.zone = await enter_map(target, character.id)
                except KeyError:
                    pass  # 目标图不在 gamedata（测试假图）→ 无图模式继续
            result = "escaped"
            message = f"使用地牢逃脱卷，脱离洞穴回到 {target}"
            map_after = target
        else:  # pragma: no cover — USABLE_KINDS 内暂无其他 id，防御性兜底
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"未知消耗品: {used_item_id}",
            )

        # 扣量（所有成功分支共用）：归零删行，否则数量减一
        inst.quantity -= 1
        quantity_left = max(0, inst.quantity)
        if inst.quantity <= 0:
            await db.delete(inst)
        await db.commit()
        await session.flush_snapshot()
        if result == "escaped":
            # 换图后快照对齐目标图（flush 重读 map_id）
            map_after = session.char_snapshot.map_id

        slots, instances = await _load_equipment_state(db, character.id)
        panel = _panel_for(char_row, slots, instances, items)

    # --- 锁外：回城动作（自带取锁）+ SSE 推送 ---------------------------------
    if result == "returned":
        await trigger_return_city(session)
        map_after = session.char_snapshot.map_id
    session.sse_state.publish(
        EV_LOG, json.dumps({"kind": "use", "text": message}, ensure_ascii=False)
    )
    _publish_stat(session, reason="use", panel=panel)
    if result == "escaped" and map_after is not None:
        session.sse_state.publish(
            EV_ZONE_CHANGE,
            json.dumps({"map_id": map_after, "reason": result}, ensure_ascii=False),
        )
    return UseOut(
        item_instance_id=payload.item_instance_id,
        item_id=used_item_id,
        name=item_def.name,
        result=result,
        message=message,
        quantity_left=quantity_left,
        hp=session.char_snapshot.hp,
        mp=session.char_snapshot.mp,
        lucky_after=lucky_after,
        map_id=map_after,
    )
