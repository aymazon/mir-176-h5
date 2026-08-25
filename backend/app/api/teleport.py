"""传送员 API（todo 30）：面板 / 传送（跨图跑动倒计时）。

GET ``/api/teleport/panel``：当前图 + 可达目的地费率清单（邻接地表路线 +
洞穴层列表）+ 绑定主城 + 行进中状态。只读不加锁。

POST ``/api/teleport/warp`` body ``{target_map_id, layer?}``：
    1. 锁内校验：非行进中（409）、目标可达（邻接路线或洞穴层，422）、
       金币足够（422，failure 场景）；
    2. 扣金币 + 写 character.traveling_until = now + TRAVEL_SECONDS_MIN..MAX
       随机（同事务单 commit，REST 写路径规则 todo 15：commit 后锁内
       flush_snapshot，内存跟随 DB）；
    3. 目标为主城时到点自动 bind_city=target（最近访问主城绑定，
       由 tick_loop.complete_travel 落地）；
    4. 锁外推 SSE travel_start + stat。
倒计时期间 tick_loop 跳过战斗/技能/吃药结算；到点 complete_travel 切
zone_id 并推 zone_change(reason=travel)。行进目标只存进程内存
（sim/travel.py 注册表，PlayerSession 冻结不可挂字段），进程重启即取消
行进——traveling_until 列仅作镜像审计，不据此恢复倒计时。

费率口径：扣费 = round(费率系数 × balance.TELEPORT_RATE_BASE)；系数表在
gamedata/teleport.json（routes 地表路线 / cave_layers 洞穴层随深度递增），
未录价的邻接边按系数 1.0。不做自由坐标传送、不做跑图路径渲染（计划红线）。
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from functools import lru_cache
from typing import Final, cast

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentPlayerId, DbSession
from app.api.inventory import (
    _character_of,
    _items,
    _load_equipment_state,
    _panel_for,
    _publish_stat,
)
from app.combat import balance
from app.gamedata_loader import load_all
from app.gamedata_schema.common import GameDataModel
from app.gamedata_schema.maps import MapDef
from app.gamedata_schema.teleport import CaveLayer, TeleportTable
from app.models.character import Character
from app.sim.session import get_or_create_player_session
from app.sim.travel import (
    TravelState,
    is_traveling,
    remaining_seconds,
    start_travel,
    travel_of,
    travel_seconds,
)

router = APIRouter(prefix="/api/teleport", tags=["teleport"])

# SSE 事件名（todo 32 前端接入；与 tick_loop EV_* 同通道）
EV_TRAVEL_START: Final = "travel_start"
EV_LOG: Final = "log"

# 未录入 routes 的邻接边按基础费率系数通行（与 TELEPORT_RATE_BASE 同源默认）
_DEFAULT_RATE: Final[float] = 1.0


@lru_cache(maxsize=1)
def _teleport_table() -> TeleportTable:
    """进程内缓存已校验传送表（inventory._items 同款；测试可 monkeypatch）。"""
    entry = cast("dict[str, GameDataModel]", load_all()["teleport"])["table"]
    assert isinstance(entry, TeleportTable)
    return entry


@lru_cache(maxsize=1)
def _maps() -> dict[str, MapDef]:
    return cast("dict[str, MapDef]", load_all()["maps"])


def _map_name(map_id: str) -> str:
    map_def = _maps().get(map_id)
    return map_def.name if map_def is not None else map_id


def _fee(rate: float) -> int:
    """费率系数 → 实际扣费金币（唯一计算点）。"""
    return round(rate * balance.TELEPORT_RATE_BASE)


def _cave_rows(table: TeleportTable, map_id: str) -> list[CaveLayer]:
    """某洞穴图的分层费率档（按深度升序）；非洞穴图返回空。"""
    return sorted(
        (c for c in table.cave_layers if c.map_id == map_id), key=lambda c: c.depth
    )


def _route_rate(table: TeleportTable, current: str, target: str) -> float | None:
    """地表路线价：routes 双向各录一条，命中即返回系数。"""
    for r in table.routes:
        if r.from_map_id == current and r.to_map_id == target:
            return r.rate
    return None


def resolve_warp(
    table: TeleportTable,
    current: str,
    target: str,
    layer: int | None,
) -> tuple[int, int | None]:
    """校验可达性并解析费用，返回 (fee, depth)；不可达 raise ValueError。

    - 洞穴图（有 cave_layers）：当前图须为入口母图、已在该洞内、或与其
      入口/本体相邻；layer 缺省取第 1 层，费率按该深度档。
    - 地表图：须与当前图相邻；费率优先查 routes，缺省按基础系数。
    """
    cave_rows = _cave_rows(table, target)
    if cave_rows:
        parent = cave_rows[0].parent_map_id
        adjacent = set(getattr(_maps().get(current), "adjacent", []) or [])
        if (
            current != parent
            and current != target
            and parent not in adjacent
            and target not in adjacent
        ):
            raise ValueError(f"不在 {target} 的洞穴入口附近，无法传送进入")
        depth = layer if layer is not None else cave_rows[0].depth
        row = next((c for c in cave_rows if c.depth == depth), None)
        if row is None:
            raise ValueError(f"{_map_name(target)} 无第 {depth} 层费率档")
        return _fee(row.rate), depth
    rate = _route_rate(table, current, target)
    if rate is None:
        adjacent = getattr(_maps().get(current), "adjacent", []) or []
        if target in adjacent:
            rate = _DEFAULT_RATE
        else:
            raise ValueError(f"{_map_name(target)} 不在当前地图的可达路线清单")
    return _fee(rate), None


# --- 出入参模型 -----------------------------------------------------------------


class PanelDestination(BaseModel):
    map_id: str
    name: str
    fee: int
    kind: str  # route=地表路线 | cave=洞穴层
    depth: int | None = None


class PanelOut(BaseModel):
    current_map_id: str
    current_map_name: str
    bind_city: str
    traveling: bool
    travel_remaining_seconds: float | None = None
    travel_target_map_id: str | None = None
    destinations: list[PanelDestination]


class WarpIn(BaseModel):
    target_map_id: str
    layer: int | None = Field(default=None, ge=1)


class WarpOut(BaseModel):
    target_map_id: str
    layer: int | None
    fee: int
    gold_after: int
    travel_seconds: int
    traveling_until: float  # time.time() 墙钟到期时刻（与 DB 列同源）


# --- GET /api/teleport/panel -----------------------------------------------------


@router.get("/panel")
async def teleport_panel(db: DbSession, player_id: CurrentPlayerId) -> PanelOut:
    """传送面板（todo 30）：可达目的地费率 + 主城绑定 + 行进中状态。只读不加锁。"""
    character = await _character_of(db, player_id)
    await db.refresh(character)
    table = _teleport_table()
    current = character.map_id
    destinations: list[PanelDestination] = []

    for adj in getattr(_maps().get(current), "adjacent", []) or []:
        rate = _route_rate(table, current, adj)
        if rate is None:
            rate = _DEFAULT_RATE
        destinations.append(
            PanelDestination(
                map_id=adj, name=_map_name(adj), fee=_fee(rate), kind="route"
            )
        )
    # 洞穴层清单：入口母图为本图、或已在洞内（跨层传送）
    for cave_id in sorted({c.map_id for c in table.cave_layers}):
        rows = _cave_rows(table, cave_id)
        parent = rows[0].parent_map_id if rows else None
        if current == parent or current == cave_id:
            for row in rows:
                destinations.append(
                    PanelDestination(
                        map_id=cave_id,
                        name=f"{_map_name(cave_id)} {row.depth} 层",
                        fee=_fee(row.rate),
                        kind="cave",
                        depth=row.depth,
                    )
                )

    state = travel_of(character.id)
    return PanelOut(
        current_map_id=current,
        current_map_name=_map_name(current),
        bind_city=character.bind_city,
        traveling=is_traveling(character.id),
        travel_remaining_seconds=(
            remaining_seconds(character.id) if state is not None else None
        ),
        travel_target_map_id=state.target_map_id if state is not None else None,
        destinations=destinations,
    )


# --- POST /api/teleport/warp -----------------------------------------------------


@router.post("/warp")
async def teleport_warp(
    payload: WarpIn, db: DbSession, player_id: CurrentPlayerId
) -> WarpOut:
    """付费传送（todo 30）：校验 → 扣费 + 行进中登记 → commit → SSE 锁外。"""
    character = await _character_of(db, player_id)
    session = await get_or_create_player_session(db, character.id)
    table = _teleport_table()

    async with session.lock:
        char_row = await db.get(Character, character.id)
        if char_row is None:  # 建号后被删的极端竞态
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在"
            )
        await db.refresh(char_row)

        if is_traveling(character.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="行进中无法再次传送（跑图倒计时进行中）",
            )
        if payload.target_map_id not in _maps():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="目的地不存在"
            )
        if payload.target_map_id == char_row.map_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="已在目的地图，无需传送",
            )
        try:
            fee, depth = resolve_warp(
                table, char_row.map_id, payload.target_map_id, payload.layer
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        if char_row.gold < fee:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"金币不足：需 {fee}，身上只有 {char_row.gold}",
            )

        seconds = travel_seconds(session.rng)
        until_epoch = time.time() + seconds
        char_row.gold -= fee
        char_row.traveling_until = datetime.fromtimestamp(until_epoch, tz=UTC)
        await db.commit()
        await session.flush_snapshot()
        gold_after = session.char_snapshot.gold
        start_travel(
            character.id,
            TravelState(
                target_map_id=payload.target_map_id,
                until=until_epoch,
                fee=fee,
                layer=depth,
            ),
        )
        log_payload = json.dumps(
            {
                "kind": "travel",
                "text": (
                    f"踏上前往 {_map_name(payload.target_map_id)}"
                    f"{f'第 {depth} 层' if depth else ''}的路途，"
                    f"约 {seconds} 秒后到达（花费 {fee} 金币）"
                ),
            },
            ensure_ascii=False,
        )
        slots, instances = await _load_equipment_state(db, character.id)
        panel = _panel_for(char_row, slots, instances, items=_items())

    # --- 锁外：SSE 推送（travel_start + stat，todo 15 横切约束）------------------
    session.sse_state.publish(EV_LOG, log_payload)
    _publish_stat(session, reason="warp", panel=panel)
    session.sse_state.publish(
        EV_TRAVEL_START,
        json.dumps(
            {
                "target_map_id": payload.target_map_id,
                "layer": depth,
                "fee": fee,
                "gold_after": gold_after,
                "seconds": seconds,
                "traveling_until": until_epoch,
            },
            ensure_ascii=False,
        ),
    )
    return WarpOut(
        target_map_id=payload.target_map_id,
        layer=depth,
        fee=fee,
        gold_after=gold_after,
        travel_seconds=seconds,
        traveling_until=until_epoch,
    )
