"""商店买卖 API 全链路测试（todo 27）。

用 httpx.AsyncClient(transport=ASGITransport(app=app)) 直连真实 PG（mir_dev），
与 test_equip 同一约定：随机用户名/角色名避撞唯一约束，fixture 收尾删 player
（FK CASCADE 连带删 character/item_instance）并 dispose engine 防连接池跨 loop。

覆盖验收：买卖闭环金币守恒（买 X 花 X×单价 → 卖 X 收 X×单价×0.4）、堆叠合并
（多次买入同一行累加）、城市等级过滤（低级城清单不含高级药 / 强效太阳水 buy 422）、
金币不足 422、库存不足 422、背包已满 422、卖出数量不足 422、NPC/物品 404、
未登录 401、卖价 = 买价 × SHOP_SELL_MULTIPLIER 系数断言、SSE bag_update/stat。
"""

import json
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api import shop as shop_api
from app.core.db import SessionLocal, engine
from app.gamedata_schema.items import ItemKind
from app.gamedata_schema.npcs import NpcDef, NpcKind, ShopEntry
from app.main import app
from app.sim.session import session_for
from app.sim.tick_loop import shutdown_all_sessions
from app.sim.zone import shutdown_all
from app.sse.session_state import reset_sessions

# 真实数据锚点（gamedata/npcs/biqi_apothecary.json + gamedata/items/potion_hp_medium.json）
BIQI_APOTHECARY = "biqi_apothecary"
HEALER_NPC = "healer_npc"  # yin-xing-cun, city_level=1
MEDIUM_HP = "potion_hp_medium"  # 条目价 110 = buy_price；sell_price 字段不参与回收计价
STRONG_SUN_WATER = "strong_sun_water"  # buy_price=2000，无真实 NPC 售卖（测试注入）


class _Harness:
    """一次用例的账号/角色供给与 DB 断言助手（test_equip 同款裁剪）。"""

    def __init__(self, client: AsyncClient) -> None:
        self.client = client
        self.usernames: list[str] = []

    async def create_character(self) -> int:
        creds = {"username": f"u_{uuid4().hex[:12]}", "password": "p@ssw0rd"}
        r = await self.client.post("/api/auth/register", json=creds)
        assert r.status_code == 201, r.text
        r = await self.client.post("/api/auth/login", json=creds)
        assert r.status_code == 200, r.text
        r = await self.client.post(
            "/api/character", json={"name": f"c_{uuid4().hex[:10]}", "class": "warrior"}
        )
        assert r.status_code == 201, r.text
        character_id = int(r.json()["id"])
        self.usernames.append(creds["username"])
        return character_id

    async def set_character(self, character_id: int, **fields: object) -> None:
        sets = ", ".join(f"{col} = :{col}" for col in fields)
        async with SessionLocal() as db:
            await db.execute(
                text(f"UPDATE character SET {sets} WHERE id = :cid"),
                {**fields, "cid": character_id},
            )
            await db.commit()

    async def seed_stack(self, character_id: int, item_id: str, quantity: int) -> int:
        """塞一堆可堆叠物品进背包（quantity>1 触发 partial unique 索引语义）。"""
        async with SessionLocal() as db:
            row = await db.execute(
                text(
                    "INSERT INTO item_instance (character_id, item_id, quantity) "
                    "VALUES (:cid, :iid, :qty) RETURNING id"
                ),
                {"cid": character_id, "iid": item_id, "qty": quantity},
            )
            row_id = int(row.scalar_one())
            await db.commit()
        return row_id

    async def item_rows(self, character_id: int, item_id: str) -> list[tuple[int, int]]:
        """该角色的 (instance_id, quantity) 行列表。"""
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT id, quantity FROM item_instance "
                        "WHERE character_id = :cid AND item_id = :iid ORDER BY id"
                    ),
                    {"cid": character_id, "iid": item_id},
                )
            ).all()
        return [(int(r[0]), int(r[1])) for r in rows]

    async def gold_of(self, character_id: int) -> int:
        async with SessionLocal() as db:
            return (
                await db.execute(
                    text("SELECT gold FROM character WHERE id = :cid"), {"cid": character_id}
                )
            ).scalar_one()


@pytest.fixture
async def harness() -> AsyncIterator[_Harness]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        h = _Harness(c)
        yield h
    async with SessionLocal() as db:
        for username in h.usernames:
            await db.execute(
                text("DELETE FROM player WHERE username = :u"), {"u": username}
            )
        await db.commit()
    await shutdown_all_sessions()
    await shutdown_all()
    reset_sessions()
    await engine.dispose()


def _events(character_id: int, name: str) -> list[dict[str, object]]:
    """该角色 SSE buffer 里指定事件的全部 JSON payload。"""
    session = session_for(character_id)
    assert session is not None, "商店写路径应已建立 PlayerSession"
    return [
        json.loads(event.data)
        for event in session.sse_state.buffer.after(0)
        if event.event == name
    ]


def _fake_vendor(
    npc_id: str,
    *,
    city_level: int | None,
    item_id: str,
    min_city_level: int,
    price: int = 2000,
    stock: int | None = None,
) -> NpcDef:
    """注入用假商人（真 pydantic 模型；schema extra=forbid 下无需 SimpleNamespace）。"""
    return NpcDef(
        id=npc_id,
        name=f"假商人{npc_id}",
        map_id="yin-xing-cun",
        kind=NpcKind.vendor,
        city_level=city_level,
        services=["shop"],
        shop_items=[item_id],
        shop_entries=[
            ShopEntry(
                item_id=item_id, price=price, stock=stock, min_city_level=min_city_level
            )
        ],
    )


def _patch_npcs(monkeypatch: pytest.MonkeyPatch, *npcs: NpcDef) -> None:
    merged = dict(shop_api._npcs())
    for npc in npcs:
        merged[npc.id] = npc
    monkeypatch.setattr(shop_api, "_npcs", lambda: merged)


async def test_shop_list_city_level_filter(harness: _Harness) -> None:
    """低级城（healer_npc city_level=1）清单不含中量药（min_city_level=2）；高级城含。"""
    cid = await harness.create_character()  # 出生图 yin-xing-cun

    r = await harness.client.get("/api/shop/list", params={"npc_id": HEALER_NPC})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["npc_id"] == HEALER_NPC
    assert body["city_level"] == 1
    listed = {e["item_id"] for e in body["entries"]}
    assert {"potion_hp_small", "potion_mp_small"} <= listed
    assert MEDIUM_HP not in listed  # 高级药只在高级城：min_city_level=2 > 1

    # 高级城：比奇药店（city_level=2）清单含中量药——需先跑到 biqi-wild
    await harness.set_character(cid, map_id="biqi-wild")
    r = await harness.client.get(
        "/api/shop/list", params={"npc_id": BIQI_APOTHECARY}
    )
    assert r.status_code == 200, r.text
    entries = {e["item_id"]: e for e in r.json()["entries"]}
    assert MEDIUM_HP in entries
    assert entries[MEDIUM_HP]["price"] == 110  # 条目价 × SHOP_BUY_MULTIPLIER(1.0)
    assert entries[MEDIUM_HP]["stock"] is None  # 不限量

    # NPC 不存在 → 404；不在 NPC 所在地图 → 422
    r = await harness.client.get("/api/shop/list", params={"npc_id": "no_such_npc"})
    assert r.status_code == 404, r.text
    await harness.set_character(cid, map_id="yin-xing-cun")
    r = await harness.client.get(
        "/api/shop/list", params={"npc_id": BIQI_APOTHECARY}
    )
    assert r.status_code == 422, r.text


async def test_buy_sell_closed_loop_gold_conservation(harness: _Harness) -> None:
    """闭环守恒：买 10+5 中药花 1650 → 卖 15 收 15×44=660；堆叠合并同一行。"""
    cid = await harness.create_character()
    await harness.set_character(cid, map_id="biqi-wild", gold=5000)

    # 买 10 × 110 = 1100
    r = await harness.client.post(
        "/api/shop/buy",
        json={"npc_id": BIQI_APOTHECARY, "item_id": MEDIUM_HP, "quantity": 10},
    )
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["unit_price"] == 110
    assert first["total_cost"] == 1100
    assert first["gold_after"] == 3900
    assert first["quantity_in_bag"] == 10
    instance_id = first["item_instance_id"]
    assert await harness.item_rows(cid, MEDIUM_HP) == [(instance_id, 10)]

    # 再买 5：堆叠合并同一行（同 item_id 单行），不占新格
    r = await harness.client.post(
        "/api/shop/buy",
        json={"npc_id": BIQI_APOTHECARY, "item_id": MEDIUM_HP, "quantity": 5},
    )
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["item_instance_id"] == instance_id
    assert second["quantity_in_bag"] == 15
    assert second["gold_after"] == 3350
    assert len(await harness.item_rows(cid, MEDIUM_HP)) == 1

    # 卖 15：回收单价 = 110 × 0.4 = 44（卖价 = 买价 × 系数断言）
    sell_unit = round(110 * 0.4)
    assert sell_unit == 44
    r = await harness.client.post(
        "/api/shop/sell", json={"item_instance_id": instance_id, "quantity": 15}
    )
    assert r.status_code == 200, r.text
    sold = r.json()
    assert sold["unit_price"] == sell_unit
    assert sold["total_earned"] == 15 * sell_unit
    # 金币守恒：5000 - 1100 - 550 + 660 = 4010
    assert sold["gold_after"] == 4010
    assert sold["quantity_left"] == 0
    assert await harness.gold_of(cid) == 4010
    assert await harness.item_rows(cid, MEDIUM_HP) == []  # 归零删行

    # SSE：bag_update 与 stat 各 3 次（buy/buy/sell），锁外推送
    bag_reasons = [e["reason"] for e in _events(cid, "bag_update")]
    assert bag_reasons == ["buy", "buy", "sell"]
    stat_reasons = [e["reason"] for e in _events(cid, "stat")]
    assert stat_reasons == ["buy", "buy", "sell"]
    assert _events(cid, "stat")[-1]["gold"] == 4010


async def test_strong_sun_water_blocked_in_low_city(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """强效太阳水（min_city_level=2）：低级城（city_level=1）清单不含 + buy 422；
    同店把门槛降到 1 即可买入（证明闸门是城市等级过滤而非注入本身）。"""
    cid = await harness.create_character()  # yin-xing-cun
    low = _fake_vendor(
        "fake_low_city_vendor",
        city_level=1,
        item_id=STRONG_SUN_WATER,
        min_city_level=2,
    )
    open_gate = _fake_vendor(
        "fake_open_vendor",
        city_level=1,
        item_id=STRONG_SUN_WATER,
        min_city_level=1,
    )
    _patch_npcs(monkeypatch, low, open_gate)
    await harness.set_character(cid, gold=10000)

    # 清单过滤：低级城不含强效太阳水
    r = await harness.client.get(
        "/api/shop/list", params={"npc_id": "fake_low_city_vendor"}
    )
    assert r.status_code == 200, r.text
    assert STRONG_SUN_WATER not in {e["item_id"] for e in r.json()["entries"]}

    # 买入被城市等级闸门拒绝
    r = await harness.client.post(
        "/api/shop/buy",
        json={
            "npc_id": "fake_low_city_vendor",
            "item_id": STRONG_SUN_WATER,
            "quantity": 1,
        },
    )
    assert r.status_code == 422, r.text
    assert "城市等级不符" in r.json()["detail"]
    assert await harness.gold_of(cid) == 10000  # 未扣款
    assert await harness.item_rows(cid, STRONG_SUN_WATER) == []

    # 阳性对照：门槛 1 的同城商人可正常买入（2000 金）
    r = await harness.client.post(
        "/api/shop/buy",
        json={
            "npc_id": "fake_open_vendor",
            "item_id": STRONG_SUN_WATER,
            "quantity": 1,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["total_cost"] == 2000
    assert r.json()["gold_after"] == 8000


async def test_buy_insufficient_gold_422(harness: _Harness) -> None:
    """金币不足 → 422，不扣款不入包（单事务原子性）。"""
    cid = await harness.create_character()
    await harness.set_character(cid, map_id="biqi-wild", gold=50)
    r = await harness.client.post(
        "/api/shop/buy",
        json={"npc_id": BIQI_APOTHECARY, "item_id": MEDIUM_HP, "quantity": 10},
    )
    assert r.status_code == 422, r.text
    assert "金币不足" in r.json()["detail"]
    assert await harness.gold_of(cid) == 50
    assert await harness.item_rows(cid, MEDIUM_HP) == []


async def test_buy_stock_insufficient_422(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """条目 stock=5 一次买 6 → 422 库存不足（静态目录属性，无限购刷新）。"""
    cid = await harness.create_character()
    stocked = _fake_vendor(
        "fake_stocked_vendor",
        city_level=1,
        item_id="potion_hp_small",
        min_city_level=1,
        price=40,
        stock=5,
    )
    _patch_npcs(monkeypatch, stocked)
    await harness.set_character(cid, gold=10000)
    r = await harness.client.post(
        "/api/shop/buy",
        json={"npc_id": "fake_stocked_vendor", "item_id": "potion_hp_small", "quantity": 6},
    )
    assert r.status_code == 422, r.text
    assert "库存不足" in r.json()["detail"]

    # 恰好买光库存 → 200
    r = await harness.client.post(
        "/api/shop/buy",
        json={"npc_id": "fake_stocked_vendor", "item_id": "potion_hp_small", "quantity": 5},
    )
    assert r.status_code == 200, r.text


async def test_buy_bag_full_422(harness: _Harness) -> None:
    """不可堆叠物品入包需新格：容量 1 已占 → 422 背包已满。"""
    cid = await harness.create_character()
    await harness.set_character(cid, bag_capacity=1, gold=10000)
    await harness.seed_stack(cid, "potion_hp_small", 3)  # 占满唯一一格
    r = await harness.client.post(
        "/api/shop/buy",
        json={"npc_id": "yinxing_blacksmith", "item_id": "wooden_sword", "quantity": 1},
    )
    assert r.status_code == 422, r.text
    assert "背包已满" in r.json()["detail"]
    assert await harness.gold_of(cid) == 10000


async def test_sell_errors_422_404(harness: _Harness) -> None:
    """卖出：数量不足 422；他人实例/不存在实例 404（不泄露存在性）。"""
    other_cid = await harness.create_character()
    other_iid = await harness.seed_stack(other_cid, "potion_mp_small", 2)
    cid = await harness.create_character()  # 此后请求以本号身份发出
    iid = await harness.seed_stack(cid, "potion_hp_small", 3)

    # 数量不足：持有 3 卖 5
    r = await harness.client.post(
        "/api/shop/sell", json={"item_instance_id": iid, "quantity": 5}
    )
    assert r.status_code == 422, r.text
    assert "数量不足" in r.json()["detail"]

    # 他人的实例 → 404；不存在的实例 → 404
    r = await harness.client.post(
        "/api/shop/sell", json={"item_instance_id": other_iid, "quantity": 1}
    )
    assert r.status_code == 404, r.text
    r = await harness.client.post(
        "/api/shop/sell", json={"item_instance_id": 999_999_999, "quantity": 1}
    )
    assert r.status_code == 404, r.text

    # 部分卖出：3 卖 1 → 余 2（行保留）
    r = await harness.client.post(
        "/api/shop/sell", json={"item_instance_id": iid, "quantity": 1}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unit_price"] == round(40 * 0.4)  # potion_hp_small buy_price=40
    assert body["quantity_left"] == 2
    assert await harness.item_rows(cid, "potion_hp_small") == [(iid, 2)]


async def test_buy_npc_and_item_errors(harness: _Harness) -> None:
    """买入：NPC 不存在 404；不在售卖清单 422；不在 NPC 地图 422；非法 quantity 422。"""
    await harness.create_character()  # yin-xing-cun
    r = await harness.client.post(
        "/api/shop/buy",
        json={"npc_id": "no_such_npc", "item_id": MEDIUM_HP, "quantity": 1},
    )
    assert r.status_code == 404, r.text

    # healer_npc 只卖小量药：买中量药 → 不在售卖清单
    r = await harness.client.post(
        "/api/shop/buy",
        json={"npc_id": HEALER_NPC, "item_id": MEDIUM_HP, "quantity": 1},
    )
    assert r.status_code == 422, r.text
    assert "不在本店售卖清单" in r.json()["detail"]

    # 在比奇药店买但人不在比奇 → 422
    r = await harness.client.post(
        "/api/shop/buy",
        json={"npc_id": BIQI_APOTHECARY, "item_id": MEDIUM_HP, "quantity": 1},
    )
    assert r.status_code == 422, r.text
    assert "所在地图" in r.json()["detail"]

    # quantity < 1 → pydantic 422
    r = await harness.client.post(
        "/api/shop/buy",
        json={"npc_id": HEALER_NPC, "item_id": "potion_hp_small", "quantity": 0},
    )
    assert r.status_code == 422, r.text


async def test_no_cookie_401(harness: _Harness) -> None:
    harness.client.cookies.clear()
    r = await harness.client.get("/api/shop/list", params={"npc_id": HEALER_NPC})
    assert r.status_code == 401, r.text
    r = await harness.client.post(
        "/api/shop/buy",
        json={"npc_id": HEALER_NPC, "item_id": "potion_hp_small", "quantity": 1},
    )
    assert r.status_code == 401, r.text
    r = await harness.client.post(
        "/api/shop/sell", json={"item_instance_id": 1, "quantity": 1}
    )
    assert r.status_code == 401, r.text


def test_item_kind_import_guards_real_data() -> None:
    """防漂移哨兵：锚点物品仍是 potion 类（若 gamedata 改 kind 本文件断言先炸）。"""
    items = shop_api._items()
    assert items[MEDIUM_HP].kind is ItemKind.potion
    assert items[STRONG_SUN_WATER].kind is ItemKind.potion
