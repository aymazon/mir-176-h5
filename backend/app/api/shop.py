"""NPC 商店买卖 API（todo 27）：清单 / 买入 / 卖出。

GET ``/api/shop/list?npc_id=X``：该 NPC 的可售清单。城市等级过滤按 task 20
录入口径（docs/MVP/README.md §8「高级药只在高级城」）：
``npc.city_level >= entry.min_city_level``——maps/ 无 city_level 字段，玩家侧
约束即「角色当前在 NPC 所在地图」（buy 同款校验）；``stock=None`` 表示不限量。

POST ``/api/shop/buy`` body ``{npc_id, item_id, quantity}``：扣金币 + 入背包。
买入单价 = 条目价（price=0 回退物品 buy_price）× balance.SHOP_BUY_MULTIPLIER；
可堆叠物品合并既有行（不占新格、豁免容量校验），否则占 1 格（task 25 pick 同款）。

POST ``/api/shop/sell`` body ``{item_instance_id, quantity}``：扣物品 + 加金币。
卖出单价 = 买入单价 × balance.SHOP_SELL_MULTIPLIER（回收折扣 < 1 防倒卖套利；
四舍五入取整）。卖出不要求在 NPC 旁（回收走任意商店柜台语义，MVP 简化）。

错误码：404（NPC 不存在 / 物品实例不存在或非本人）、422（不在售卖清单 /
不在 NPC 所在地图 / 城市等级不符 / 库存不足 / 金币不足 / 背包已满 / 数量不足）、
401（未登录，deps 统一）。

并发纪律（todo 15 横切约束）：写路径锁临界区仅限 DB 事务 + flush_snapshot
（DB→内存只读重读，自身不取锁）；SSE publish 恒在锁外。buy 同时写
character.gold + item_instance 于同一事务（单 commit，无跨锁窗口）。
不做玩家间交易、不做限购刷新（stock 为静态目录属性，MVP 不落库递减）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import cast

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
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
from app.combat import balance
from app.gamedata_loader import load_all
from app.gamedata_schema.items import ItemDef
from app.gamedata_schema.npcs import NpcDef, ShopEntry
from app.models.character import Character
from app.models.item_instance import ItemInstance
from app.sim.session import get_or_create_player_session

router = APIRouter(prefix="/api/shop", tags=["shop"])


@lru_cache(maxsize=1)
def _npcs() -> dict[str, NpcDef]:
    """进程内缓存已校验 NPC 目录（inventory._items 同款；测试可 monkeypatch）。"""
    return cast("dict[str, NpcDef]", load_all()["npcs"])


# --- 出入参模型 -----------------------------------------------------------------


class ShopEntryOut(BaseModel):
    item_id: str
    name: str
    kind: str
    price: int  # 实际买入单价（条目价缺省回退 buy_price × SHOP_BUY_MULTIPLIER）
    stock: int | None = None  # None = 不限量（MVP 无限购刷新）
    min_city_level: int


class ShopListOut(BaseModel):
    npc_id: str
    npc_name: str
    map_id: str
    city_level: int | None
    entries: list[ShopEntryOut]


class BuyIn(BaseModel):
    npc_id: str
    item_id: str
    quantity: int = Field(ge=1)


class BuyOut(BaseModel):
    npc_id: str
    item_id: str
    name: str
    quantity_bought: int
    unit_price: int
    total_cost: int
    gold_after: int
    # 入包后的实例行 id（堆叠合并即既有行 id）
    item_instance_id: int
    quantity_in_bag: int


class SellIn(BaseModel):
    item_instance_id: int
    quantity: int = Field(ge=1)


class SellOut(BaseModel):
    item_instance_id: int
    item_id: str
    name: str
    quantity_sold: int
    unit_price: int
    total_earned: int
    gold_after: int
    quantity_left: int  # 归零删行后为 0


# --- 定价与过滤规则（唯一计算点）--------------------------------------------------


def _entry_or_422(npc: NpcDef, item_id: str) -> ShopEntry:
    entry = next((e for e in npc.shop_entries if e.item_id == item_id), None)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"物品 {item_id} 不在本店售卖清单",
        )
    return entry


def _city_allows(npc: NpcDef, entry: ShopEntry) -> bool:
    """§8 城市等级售卖规则（task 20 口径）：npc.city_level >= entry.min_city_level。"""
    return npc.city_level is not None and npc.city_level >= entry.min_city_level


def _unit_buy_price(entry: ShopEntry, item_def: ItemDef) -> int:
    """买入单价 = 条目价（0 = 缺省回退物品 buy_price）× SHOP_BUY_MULTIPLIER。"""
    base = entry.price if entry.price > 0 else item_def.buy_price
    return round(base * balance.SHOP_BUY_MULTIPLIER)


def _unit_sell_price(item_def: ItemDef) -> int:
    """回收单价 = 物品 buy_price × SHOP_SELL_MULTIPLIER（四舍五入取整）。"""
    return round(item_def.buy_price * balance.SHOP_SELL_MULTIPLIER)


async def _require_on_npc_map(db: DbSession, character_id: int, npc: NpcDef) -> None:
    """角色必须与 NPC 同图（list/buy 共用；maps 无 city_level，玩家侧约束即同图）。"""
    row = await db.execute(select(Character.map_id).where(Character.id == character_id))
    current_map = row.scalar_one_or_none()
    if current_map != npc.map_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"不在 {npc.name} 所在地图（当前 {current_map}，NPC 在 {npc.map_id}）",
        )


# --- GET /api/shop/list ----------------------------------------------------------


@router.get("/list")
async def shop_list(npc_id: str, db: DbSession, player_id: CurrentPlayerId) -> ShopListOut:
    """NPC 商店可售清单（todo 27）：城市等级过滤 + 库存标注。只读不加锁。"""
    character = await _character_of(db, player_id)
    npc = _npcs().get(npc_id)
    if npc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="NPC 不存在"
        )
    await _require_on_npc_map(db, character.id, npc)

    items = _items()
    entries: list[ShopEntryOut] = []
    for entry in npc.shop_entries:
        if not _city_allows(npc, entry):
            continue  # 高级药只在高级城（§8）：低级城清单直接不含
        item_def = items.get(entry.item_id)
        if item_def is None:  # 目录悬空引用（loader 校验兜底，防御性跳过）
            continue
        entries.append(
            ShopEntryOut(
                item_id=entry.item_id,
                name=item_def.name,
                kind=item_def.kind.value,
                price=_unit_buy_price(entry, item_def),
                stock=entry.stock,
                min_city_level=entry.min_city_level,
            )
        )
    return ShopListOut(
        npc_id=npc.id,
        npc_name=npc.name,
        map_id=npc.map_id,
        city_level=npc.city_level,
        entries=entries,
    )


# --- POST /api/shop/buy ----------------------------------------------------------


@router.post("/buy")
async def shop_buy(payload: BuyIn, db: DbSession, player_id: CurrentPlayerId) -> BuyOut:
    """买入（todo 27）：校验 → 扣金币 → 入背包（堆叠合并）→ commit → SSE 锁外。"""
    character = await _character_of(db, player_id)
    session = await get_or_create_player_session(db, character.id)
    items = _items()

    async with session.lock:
        char_row = await db.get(Character, character.id)
        if char_row is None:  # 建号后被删的极端竞态
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在"
            )
        await db.refresh(char_row)

        npc = _npcs().get(payload.npc_id)
        if npc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="NPC 不存在"
            )
        entry = _entry_or_422(npc, payload.item_id)
        await _require_on_npc_map(db, character.id, npc)
        if not _city_allows(npc, entry):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"城市等级不符：{item_name_of(items, payload.item_id)} 需要"
                    f" {entry.min_city_level} 级城，本店 {npc.city_level} 级"
                ),
            )
        item_def = items.get(payload.item_id)
        if item_def is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="物品不存在"
            )

        unit_price = _unit_buy_price(entry, item_def)
        total_cost = unit_price * payload.quantity
        if entry.stock is not None and payload.quantity > entry.stock:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"库存不足：本店仅剩 {entry.stock} 个",
            )
        if char_row.gold < total_cost:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"金币不足：需 {total_cost}，身上只有 {char_row.gold}",
            )

        rows = (
            (
                await db.execute(
                    select(ItemInstance).where(
                        ItemInstance.character_id == character.id
                    )
                )
            )
            .scalars()
            .all()
        )
        target: ItemInstance | None = None
        if item_def.stackable:
            target = next((r for r in rows if r.item_id == payload.item_id), None)
        if target is None:
            # 新行占一格；stackable 合并既有行不占格（豁免容量校验，todo 25 规则）
            if len(rows) >= char_row.bag_capacity:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"背包已满（{char_row.bag_capacity} 格），无法购买",
                )
            target = ItemInstance(
                character_id=character.id,
                item_id=payload.item_id,
                quantity=payload.quantity if item_def.stackable else 1,
            )
            db.add(target)
            await db.flush()  # 取实例 id 供出参/SSE
        else:
            target.quantity += payload.quantity

        char_row.gold -= total_cost
        # 出参/SSE 用原始值快照（commit 前取，与 sell 同款防脱管）
        bag_payload: dict[str, object] = {
            "reason": "buy",
            "item_instance_id": target.id,
            "item_id": target.item_id,
            "quantity": target.quantity,
        }
        await db.commit()
        await session.flush_snapshot()
        slots, instances = await _load_equipment_state(db, character.id)
        panel = _panel_for(char_row, slots, instances, items)
        gold_after = session.char_snapshot.gold

    # --- 锁外：SSE 推送（bag_update + stat，todo 15 横切约束）--------------------
    session.sse_state.publish(
        EV_BAG,
        json.dumps({**bag_payload, "gold": gold_after}, ensure_ascii=False),
    )
    _publish_stat(session, reason="buy", panel=panel)
    return BuyOut(
        npc_id=npc.id,
        item_id=payload.item_id,
        name=item_def.name,
        quantity_bought=payload.quantity,
        unit_price=unit_price,
        total_cost=total_cost,
        gold_after=gold_after,
        item_instance_id=target.id,
        quantity_in_bag=target.quantity,
    )


# --- POST /api/shop/sell ---------------------------------------------------------


@router.post("/sell")
async def shop_sell(payload: SellIn, db: DbSession, player_id: CurrentPlayerId) -> SellOut:
    """卖出（todo 27）：扣物品 + 加金币（buy_price × 回收系数）→ commit → SSE 锁外。"""
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

        inst = await db.get(ItemInstance, payload.item_instance_id)
        if inst is None or inst.character_id != character.id:
            # 不区分"不存在"与"他人的"——不泄露物品存在性（equip/use 同款口径）。
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="物品不存在"
            )
        item_def = items.get(inst.item_id)
        if item_def is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"未知物品定义: {inst.item_id}",
            )
        if inst.quantity < payload.quantity:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"数量不足：持有 {inst.quantity}，要卖 {payload.quantity}",
            )

        unit_price = _unit_sell_price(item_def)
        total_earned = unit_price * payload.quantity
        char_row.gold += total_earned
        inst.quantity -= payload.quantity
        quantity_left = max(0, inst.quantity)
        if inst.quantity <= 0:
            await db.delete(inst)
        # 归零删行后实例即将脱管：bag 事件出参用原始值快照（commit 前取）
        bag_payload: dict[str, object] = {
            "reason": "sell",
            "item_instance_id": inst.id,
            "item_id": inst.item_id,
            "quantity": quantity_left,
        }
        sold_item_id = inst.item_id
        await db.commit()
        await session.flush_snapshot()
        slots, instances = await _load_equipment_state(db, character.id)
        panel = _panel_for(char_row, slots, instances, items)
        gold_after = session.char_snapshot.gold

    # --- 锁外：SSE 推送 -----------------------------------------------------------
    session.sse_state.publish(
        EV_BAG,
        json.dumps({**bag_payload, "gold": gold_after}, ensure_ascii=False),
    )
    _publish_stat(session, reason="sell", panel=panel)
    return SellOut(
        item_instance_id=payload.item_instance_id,
        item_id=sold_item_id,
        name=item_def.name,
        quantity_sold=payload.quantity,
        unit_price=unit_price,
        total_earned=total_earned,
        gold_after=gold_after,
        quantity_left=quantity_left,
    )


# --- 辅助 -------------------------------------------------------------------------


def item_name_of(items: dict[str, ItemDef], item_id: str) -> str:
    """目录名回退（错误文案用；悬空 id 显示原始 id）。"""
    item_def = items.get(item_id)
    return item_def.name if item_def is not None else item_id


