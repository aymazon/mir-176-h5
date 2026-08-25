"""传送员 + 跑图倒计时 API 全链路测试（todo 30）。

用 httpx.AsyncClient(transport=ASGITransport(app=app)) 直连真实 PG（mir_dev），
与 test_shop 同一约定：随机用户名/角色名避撞唯一约束，fixture 收尾删 player
（FK CASCADE 连带删 character/item_instance）并 dispose engine 防连接池跨 loop。

覆盖验收：传送石墓 5 层扣费到位（rate 3.0 × TELEPORT_RATE_BASE=100 → 300 金）、
行进中发起战斗/再次传送 → 拒绝、倒计时结束 zone 切换（+ 主城自动绑定
bind_city）、回城卷回绑定城（绑定更新后回新主城）、面板费率清单、金币不足 422、
不可达 422、validate --strict 复跑绿（teleport 注册为第 10 类）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.combat import balance
from app.core.db import SessionLocal, engine
from app.main import app
from app.sim.session import session_for
from app.sim.tick_loop import (
    complete_travel,
    pop_due_travel,
    shutdown_all_sessions,
    simulate_tick,
)
from app.sim.travel import TravelState, clear_travel, start_travel
from app.sim.zone import shutdown_all
from app.sse.session_state import reset_sessions

BIND_CITY = "yin-xing-cun"  # 出生城（config.BIRTH_MAP_ID）
BIQI_WILD = "biqi-wild"  # 主城之一（bind_cities 成员）
CENTIPEDE_CAVE = "centipede-cave"  # 石墓入口母图相邻
SHIMEN = "shimen"  # 石墓（猪洞），cave_layers depth 1-7
SHIMEN_LAYER5_FEE = 300  # rate 3.0 × TELEPORT_RATE_BASE(100)


class _Harness:
    """一次用例的账号/角色供给与 DB 断言助手（test_shop 同款）。"""

    def __init__(self, client: AsyncClient) -> None:
        self.client = client
        self.usernames: list[str] = []

    async def create_character(self, *, map_id: str | None = None) -> int:
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
        if map_id is not None:
            await self.set_character(character_id, map_id=map_id)
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

    async def seed_item(self, character_id: int, item_id: str, quantity: int) -> int:
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

    async def db_scalar(self, query: str, params: dict[str, object]) -> object:
        async with SessionLocal() as db:
            return (
                await db.execute(text(query), params)
            ).scalar_one_or_none()


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
    assert session is not None, "写路径应已建立 PlayerSession"
    return [
        json.loads(event.data)
        for event in session.sse_state.buffer.after(0)
        if event.event == name
    ]


async def _session_of(character_id: int):
    session = session_for(character_id)
    assert session is not None
    return session


# --- 面板 ------------------------------------------------------------------------


async def test_panel_lists_routes_and_cave_layers(harness: _Harness) -> None:
    """面板：邻接路线费率 + 洞穴层递进清单 + 绑定城。"""
    cid = await harness.create_character()  # yin-xing-cun
    r = await harness.client.get("/api/teleport/panel")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_map_id"] == BIND_CITY
    assert body["bind_city"] == BIND_CITY
    assert body["traveling"] is False
    dests = {d["map_id"]: d for d in body["destinations"]}
    assert dests[BIQI_WILD]["fee"] == 100  # rate 1.0 × TELEPORT_RATE_BASE
    assert dests[BIQI_WILD]["kind"] == "route"

    # 站到石墓入口侧：面板给出石墓 1-7 层递进费率
    await harness.set_character(cid, map_id=CENTIPEDE_CAVE)
    r = await harness.client.get("/api/teleport/panel")
    assert r.status_code == 200, r.text
    body = r.json()
    caves = [d for d in body["destinations"] if d["kind"] == "cave"]
    shimen_layers = sorted(
        (d for d in caves if d["map_id"] == SHIMEN), key=lambda d: d["depth"]
    )
    assert [d["depth"] for d in shimen_layers] == [1, 2, 3, 4, 5, 6, 7]
    fees = [d["fee"] for d in shimen_layers]
    assert fees == sorted(fees), "洞穴层费率随深度递增"
    assert shimen_layers[4]["fee"] == SHIMEN_LAYER5_FEE


# --- warp 扣费 -------------------------------------------------------------------


async def test_warp_shimen_layer5_charges_exact_fee(harness: _Harness) -> None:
    """传送石墓 5 层扣费到位：rate 3.0 × 100 = 300 金，同事务落库。"""
    cid = await harness.create_character(map_id=CENTIPEDE_CAVE)
    await harness.set_character(cid, gold=1000)

    r = await harness.client.post(
        "/api/teleport/warp", json={"target_map_id": SHIMEN, "layer": 5}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fee"] == SHIMEN_LAYER5_FEE
    assert body["gold_after"] == 1000 - SHIMEN_LAYER5_FEE
    assert body["target_map_id"] == SHIMEN
    assert body["layer"] == 5
    assert 5 <= body["travel_seconds"] <= 20  # TRAVEL_SECONDS_MIN..MAX 区间

    gold = await harness.db_scalar(
        "SELECT gold FROM character WHERE id = :c", {"c": cid}
    )
    assert gold == 1000 - SHIMEN_LAYER5_FEE
    traveling_until = await harness.db_scalar(
        "SELECT traveling_until FROM character WHERE id = :c", {"c": cid}
    )
    assert traveling_until is not None, "行进中截止时刻应镜像落库"

    # SSE：travel_start 锁外推送 + stat(warp)
    starts = _events(cid, "travel_start")
    assert starts and starts[-1]["fee"] == SHIMEN_LAYER5_FEE
    stats = [e for e in _events(cid, "stat") if e.get("reason") == "warp"]
    assert stats and stats[-1]["gold"] == 1000 - SHIMEN_LAYER5_FEE


async def test_warp_insufficient_gold_422(harness: _Harness) -> None:
    """金币不足 → 422 不扣款不进入行进（failure 场景）。"""
    cid = await harness.create_character(map_id=CENTIPEDE_CAVE)
    await harness.set_character(cid, gold=50)
    r = await harness.client.post(
        "/api/teleport/warp", json={"target_map_id": SHIMEN, "layer": 5}
    )
    assert r.status_code == 422, r.text
    assert "金币不足" in r.json()["detail"]
    assert await harness.db_scalar(
        "SELECT gold FROM character WHERE id = :c", {"c": cid}
    ) == 50
    assert await harness.db_scalar(
        "SELECT traveling_until FROM character WHERE id = :c", {"c": cid}
    ) is None


async def test_warp_unreachable_and_bad_target(harness: _Harness) -> None:
    """不可达目的地 422；不存在目的地 404；原地传送 422。"""
    await harness.create_character(map_id=BIND_CITY)
    r = await harness.client.post(
        "/api/teleport/warp", json={"target_map_id": "mengzhong"}
    )
    assert r.status_code == 422, r.text  # 银杏村与盟重无直达路线
    r = await harness.client.post(
        "/api/teleport/warp", json={"target_map_id": "no-such-map"}
    )
    assert r.status_code == 404, r.text
    r = await harness.client.post(
        "/api/teleport/warp", json={"target_map_id": BIND_CITY}
    )
    assert r.status_code == 422, r.text


# --- 行进中拒绝 + 倒计时结束切图 ---------------------------------------------------


async def test_travel_blocks_combat_and_second_warp(harness: _Harness) -> None:
    """行进中：发起战斗（REST 施放技能）→ 409；再次传送 → 409。"""
    cid = await harness.create_character(map_id=BIND_CITY)
    await harness.set_character(cid, gold=10000)
    r = await harness.client.post(
        "/api/teleport/warp", json={"target_map_id": BIQI_WILD}
    )
    assert r.status_code == 200, r.text

    # 行进中发起战斗 → 拒绝（REST 闸门）
    r = await harness.client.post(
        "/api/skill/use", json={"skill_id": "basic_sword"}
    )
    assert r.status_code == 409, r.text
    assert "行进中" in r.json()["detail"]

    # 行进中再次传送 → 拒绝
    r = await harness.client.post(
        "/api/teleport/warp", json={"target_map_id": BIQI_WILD}
    )
    assert r.status_code == 409, r.text

    # tick_loop skip：行进中 simulate_tick 只产 stat 心跳——无 regen/吃药/战斗结算
    session = await _session_of(cid)
    await harness.set_character(cid, hp=10)
    await session.flush_snapshot()
    hp_before = session.char_snapshot.hp
    events = simulate_tick(session, 1)
    assert [name for name, _ in events] == ["stat"], "行进中只发 stat 心跳"
    simulate_tick(session, 2)
    assert session.char_snapshot.hp == hp_before, "行进中无自然回复（战斗结算全跳过）"

    # 面板反映行进态
    r = await harness.client.get("/api/teleport/panel")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["traveling"] is True
    assert body["travel_target_map_id"] == BIQI_WILD
    assert body["travel_remaining_seconds"] > 0


async def test_travel_arrival_switches_zone_and_binds_city(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """倒计时结束 zone 切换：map_id 落库 + traveling_until 清空 + zone_change SSE；
    目标为主城时 bind_city 自动更新（最近访问主城绑定）。"""
    monkeypatch.setattr(balance, "TRAVEL_SECONDS_MIN", 0)
    monkeypatch.setattr(balance, "TRAVEL_SECONDS_MAX", 0)
    cid = await harness.create_character(map_id=BIND_CITY)
    await harness.set_character(cid, gold=10000)

    r = await harness.client.post(
        "/api/teleport/warp", json={"target_map_id": BIQI_WILD}
    )
    assert r.status_code == 200, r.text

    session = await _session_of(cid)
    arrival = pop_due_travel(cid)
    assert arrival is not None, "0 秒倒计时应立即到期"
    target = await complete_travel(session, arrival)
    assert target == BIQI_WILD
    assert session.zone is not None and session.zone.map_id == BIQI_WILD
    assert session.char_snapshot.map_id == BIQI_WILD
    assert session.char_snapshot.bind_city == BIQI_WILD, "访问主城自动绑定"

    map_id = await harness.db_scalar(
        "SELECT map_id FROM character WHERE id = :c", {"c": cid}
    )
    assert map_id == BIQI_WILD
    bind_city = await harness.db_scalar(
        "SELECT bind_city FROM character WHERE id = :c", {"c": cid}
    )
    assert bind_city == BIQI_WILD
    traveling_until = await harness.db_scalar(
        "SELECT traveling_until FROM character WHERE id = :c", {"c": cid}
    )
    assert traveling_until is None, "到点清空行进标记"

    zone_events = _events(cid, "zone_change")
    assert zone_events and zone_events[-1] == {
        "map_id": BIQI_WILD,
        "reason": "travel",
    }


async def test_return_scroll_goes_to_updated_bind_city(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回城卷回绑定城：先传送访问比奇（绑定更新），再从野外回城卷 → 回比奇而非出生城。"""
    monkeypatch.setattr(balance, "TRAVEL_SECONDS_MIN", 0)
    monkeypatch.setattr(balance, "TRAVEL_SECONDS_MAX", 0)
    cid = await harness.create_character(map_id=BIND_CITY)
    await harness.set_character(cid, gold=10000)

    # ① 传送到比奇（主城）→ 到点绑定更新
    r = await harness.client.post(
        "/api/teleport/warp", json={"target_map_id": BIQI_WILD}
    )
    assert r.status_code == 200, r.text
    session = await _session_of(cid)
    arrival = pop_due_travel(cid)
    assert arrival is not None
    await complete_travel(session, arrival)
    assert session.char_snapshot.bind_city == BIQI_WILD

    # ② 跑到兽人古墓一层（直接改图模拟跑图）
    await harness.set_character(cid, map_id="shouren-gu-1")
    await session.flush_snapshot()
    scroll_id = await harness.seed_item(cid, "return_scroll", quantity=1)
    r = await harness.client.post(
        "/api/inventory/use", json={"item_instance_id": scroll_id}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "returned"
    assert body["quantity_left"] == 0, "回城卷应扣卷"
    assert body["map_id"] == BIQI_WILD, "回绑定城（已更新为比奇）而非出生城"
    assert session.zone is not None and session.zone.map_id == BIQI_WILD


async def test_travel_registry_cleanup_on_shutdown(harness: _Harness) -> None:
    """会话注销清理行进注册表（防复活旧会话残留幽灵行进）。"""
    from app.sim.travel import has_travel

    cid = await harness.create_character(map_id=BIND_CITY)
    start_travel(
        cid, TravelState(target_map_id=BIQI_WILD, until=time.time() + 999, fee=100)
    )
    assert has_travel(cid)
    clear_travel(cid)
    assert not has_travel(cid)


# --- validate --strict 复跑 -------------------------------------------------------


def test_validate_strict_green_with_teleport_category() -> None:
    """teleport.json 已注册进 gamedata 体系：validate --strict 复跑绿且计数含 teleport=1。"""
    proc = subprocess.run(
        [sys.executable, "-m", "app.gamedata_cli", "validate", "--strict"],
        cwd=".",
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "teleport=1" in proc.stdout
