"""E2E 冒烟机器人（todo 7）：注册 → 登录 → 建号 → 开 SSE → 收 10 条模拟日志 → 断言 id 单调递增。

用法（cwd 任意，脚本自行定位 backend 根）：
    uv run python scripts/bot_client.py [--base-url URL] [--scenario smoke] [--count N]

- 后端地址：--base-url > 环境变量 MIR_BASE_URL > 默认 http://127.0.0.1:8000（不写死端口）。
- 走真实 uvicorn 起的后端（不要 ASGITransport——与无限 SSE 流有死锁前科，见 todo 5 教训）。
- 场景钩子：--scenario idle_farm 归 todo 15、taoist_dog 归 todo 28；本任务只实现 smoke。
- 连接失败 → 人话报错 + 非 0 退出码。

退出码：0 成功；1 冒烟断言失败；2 无法连接后端；3 场景未实现；4 HTTP 协议错误。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from itertools import pairwise
from pathlib import Path
from typing import Final

# 保证 `uv run python scripts/bot_client.py` 在任意 cwd 下都能 import app.*（cwd 需为 backend/ 以命中 .env）。
_BACKEND_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import httpx
from httpx_sse import connect_sse

DEFAULT_BASE_URL: Final[str] = "http://127.0.0.1:8000"
SCENARIOS: Final[tuple[str, ...]] = ("smoke", "idle_farm", "taoist_dog", "quest_kill", "farm_metric")
# SSE_MOCK_INTERVAL=1s/条：10 条约 11s；read 超时兜底防流挂起时永久阻塞。
HTTP_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(
    connect=5.0, read=30.0, write=5.0, pool=5.0
)

EXIT_OK: Final[int] = 0
EXIT_ASSERT: Final[int] = 1
EXIT_CONNECT: Final[int] = 2
EXIT_SCENARIO_TODO: Final[int] = 3
EXIT_HTTP: Final[int] = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bot_client",
        description="mir-176-h5 e2e 冒烟机器人：注册→建号→SSE 收日志→断言 id 单调递增",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("MIR_BASE_URL", DEFAULT_BASE_URL),
        help=f"后端地址（默认取 MIR_BASE_URL 环境变量，再退 {DEFAULT_BASE_URL}）",
    )
    parser.add_argument(
        "--scenario",
        choices=SCENARIOS,
        default="smoke",
        help="冒烟场景：smoke=基础冒烟（本任务）；idle_farm=挂机（todo 15）；taoist_dog=技能（todo 28）；quest_kill=击杀悬赏（todo 29）",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="需收到的 message 事件条数（默认 10，仅 smoke 用）",
    )
    parser.add_argument(
        "--seconds",
        type=int,
        default=60,
        help="挂机时长秒数（默认 60，idle_farm / farm_metric 用）",
    )
    parser.add_argument(
        "--map",
        dest="map_id",
        default="biqi-wild",
        help="挂机目标地图 gamedata id（默认 biqi-wild，仅 farm_metric 用）",
    )
    parser.add_argument(
        "--class",
        dest="class_",
        choices=("warrior", "wizard", "taoist"),
        default="warrior",
        help="角色职业（默认 warrior，仅 farm_metric 用）",
    )
    parser.add_argument(
        "--potions",
        type=int,
        default=40,
        help="种子金创药(小量)数量（默认 40；0 = 不塞药的对照组，仅 farm_metric 用）",
    )
    parser.add_argument(
        "--sun-waters",
        type=int,
        default=5,
        help="种子太阳水数量（默认 5，仅 farm_metric 用）",
    )
    return parser


def _fail(message: str, code: int) -> int:
    print(f"[bot] {message}", file=sys.stderr)
    return code


def run_smoke(base_url: str, count: int) -> int:
    """基础冒烟：注册→建号→开 SSE→收 count 条 message→断言 id 严格单调递增。"""
    username = f"bot_{uuid.uuid4().hex[:12]}"
    password = f"pw_{uuid.uuid4().hex[:16]}"
    char_name = f"bot{uuid.uuid4().hex[:10]}"

    with httpx.Client(base_url=base_url, timeout=HTTP_TIMEOUT) as client:
        # 1. 探活（连接失败在这里以人话暴露）
        try:
            resp = client.get("/api/health")
        except httpx.ConnectError as exc:
            return _fail(
                f"Cannot connect to {base_url} —— 后端未启动？({exc})", EXIT_CONNECT
            )
        if resp.status_code != 200:
            return _fail(f"/api/health 返回 {resp.status_code}: {resp.text}", EXIT_HTTP)
        print(f"[bot] health ok: {resp.json()}")

        # 2. 注册 → 登录（register 只建号不发 cookie，必须 login 换取 session_id）
        creds = {"username": username, "password": password}
        resp = client.post("/api/auth/register", json=creds)
        if resp.status_code == 201:
            print(f"[bot] registered: {resp.json()}")
        elif resp.status_code != 409:
            return _fail(f"注册失败 {resp.status_code}: {resp.text}", EXIT_HTTP)
        resp = client.post("/api/auth/login", json=creds)
        if resp.status_code != 200:
            return _fail(f"登录失败 {resp.status_code}: {resp.text}", EXIT_HTTP)
        print(f"[bot] logged in: {resp.json()}（session cookie 已入 jar）")

        # 3. 建号（cookie 已由 client 自动携带）
        resp = client.post(
            "/api/character", json={"name": char_name, "class": "warrior"}
        )
        if resp.status_code != 201:
            return _fail(f"建号失败 {resp.status_code}: {resp.text}", EXIT_HTTP)
        print(
            f"[bot] character created: {resp.json()['name']} (id={resp.json()['id']})"
        )

        # 4. 开 SSE 收事件（httpx-sse 忽略 `: ping` 注释行）
        ids: list[int] = []
        messages = 0
        got_gap = False
        deadline = time.monotonic() + count * 5 + 30
        try:
            with connect_sse(client, "GET", "/api/stream") as event_source:
                for sse in event_source.iter_sse():
                    if time.monotonic() > deadline:
                        return _fail(
                            f"超时：{messages}/{count} 条 message 内未收满", EXIT_ASSERT
                        )
                    if sse.event == "gap":
                        got_gap = True
                        print("[bot] received gap event (no id)")
                        continue
                    if sse.id:
                        ids.append(int(sse.id))
                    if sse.event == "message":
                        messages += 1
                        print(f"[bot] message id={sse.id}: {sse.data[:60]}")
                        if messages >= count:
                            break
        except httpx.RemoteProtocolError as exc:
            return _fail(f"SSE 流异常中断：{exc}", EXIT_HTTP)

        # 5. 断言：message 条数达标 + 全部带 id 事件严格单调递增
        if messages < count:
            return _fail(f"只收到 {messages} 条 message（需 {count}）", EXIT_ASSERT)
        monotonic = all(b > a for a, b in pairwise(ids))
        if not monotonic:
            return _fail(f"id 非单调递增: {ids}", EXIT_ASSERT)
        print(
            f"[bot] OK: {messages} 条 message、共 {len(ids)} 个带 id 事件，id 单调递增 {ids[0]}..{ids[-1]}"
            + ("（含 gap 重置后新 epoch）" if got_gap else "")
        )
        return EXIT_OK


# --- idle_farm（todo 15）：比奇野外挂机 + 断网重连全断言 ------------------------------


def _seed_idle_character(username: str, char_name: str) -> int:
    """直接写库把角色摆到比奇野外（Lv8 战士，心跳置 120s 前绕过判活门禁）。

    不走 API 建号：API 会把 last_active_at 刷成 now()，心跳判活（>70s 才可 seed）
    会拒绝。物品/技能一并种好；返回 character_id。
    """
    import asyncio

    os.chdir(_BACKEND_ROOT)  # .env 相对 cwd 解析（与 seed_character 同约定）
    from sqlalchemy import text

    from app.core.db import SessionLocal

    async def _run() -> int:
        async with SessionLocal() as db:
            row = await db.execute(
                text(
                    "INSERT INTO character "
                    "(player_id, name, class, level, hp, mp, gold, map_id, bind_city, last_active_at) "
                    "SELECT p.id, :n, 'warrior', 8, 80, 50, 0, 'biqi-wild', 'yin-xing-cun', "
                    "now() - make_interval(secs => 120) "
                    "FROM player p WHERE p.username = :u RETURNING id"
                ),
                {"n": char_name, "u": username},
            )
            character_id = row.scalar_one()
            for item_id, qty in (("potion_hp_small", 40), ("sun_water", 5)):
                await db.execute(
                    text(
                        "INSERT INTO item_instance (character_id, item_id, quantity) "
                        "VALUES (:cid, :iid, :qty)"
                    ),
                    {"cid": character_id, "iid": item_id, "qty": qty},
                )
            await db.execute(
                text(
                    "INSERT INTO skill_state (character_id, skill_id, level, exp) "
                    "VALUES (:cid, 'basic_sword', 0, 0)"
                ),
                {"cid": character_id},
            )
            await db.commit()
            return character_id

    return asyncio.run(_run())


def run_idle_farm(base_url: str, seconds: int) -> int:
    """挂机场景：注册→登录→种号→SSE 挂机→主动断网 10s→Last-Event-ID 重连→断言。

    硬断言（计划验收）：stat 日志流 ≥ seconds 条（1Hz 连续）；经验 > 0；至少一次
    掉落；HP 跌破 30% 上限后自动吃药；宽限期内重连日志补全（replay 链连续）且
    经验继续增长。
    """
    username = f"bot_{uuid.uuid4().hex[:12]}"
    password = f"pw_{uuid.uuid4().hex[:16]}"
    char_name = f"bot{uuid.uuid4().hex[:10]}"

    with httpx.Client(base_url=base_url, timeout=HTTP_TIMEOUT) as client:
        try:
            resp = client.get("/api/health")
        except httpx.ConnectError as exc:
            return _fail(
                f"Cannot connect to {base_url} —— 后端未启动？({exc})", EXIT_CONNECT
            )
        if resp.status_code != 200:
            return _fail(f"/api/health 返回 {resp.status_code}: {resp.text}", EXIT_HTTP)
        print(f"[bot] health ok: {resp.json()}")

        creds = {"username": username, "password": password}
        resp = client.post("/api/auth/register", json=creds)
        if resp.status_code != 201:
            return _fail(f"注册失败 {resp.status_code}: {resp.text}", EXIT_HTTP)
        resp = client.post("/api/auth/login", json=creds)
        if resp.status_code != 200:
            return _fail(f"登录失败 {resp.status_code}: {resp.text}", EXIT_HTTP)
        character_id = _seed_idle_character(username, char_name)
        print(
            f"[bot] seeded character {char_name} (id={character_id}) "
            "@ biqi-wild Lv8 warrior hp=80 potions=40+5"
        )

        stats_out: list[dict[str, object]] = []
        exp_out: list[dict[str, object]] = []
        drops_out: list[dict[str, object]] = []
        potions_out: list[dict[str, object]] = []
        ids: list[int] = []

        def _int_field(payload: dict[str, object], key: str) -> int:
            value = payload.get(key, 0)
            return (
                value if isinstance(value, int) and not isinstance(value, bool) else 0
            )

        def _consume(event: str, sid: str, data: str) -> None:
            if event == "gap":  # 不携带 id；本场景 buffer 不满不会出现
                return
            if sid:
                ids.append(int(sid))
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                return
            assert isinstance(payload, dict)
            if event == "stat":
                stats_out.append(payload)
            elif event == "exp":
                exp_out.append(payload)
            elif event == "drop":
                drops_out.append(payload)
            elif event == "log" and payload.get("kind") == "potion":
                potions_out.append(payload)

        def _iter_sse(headers: dict[str, str] | None = None):
            """手解 SSE 帧（httpx_sse 的 SSELineDecoder 在 chunk 边界会把同一事件
            重发一次——原始流无重复，见 evidence；为保断言严格性不用 iter_sse）。"""
            with client.stream("GET", "/api/stream", headers=headers) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(f"stream HTTP {resp.status_code}")
                event_name, event_id, data_buf = "", "", []
                for line in resp.iter_lines():
                    if line == "":
                        if event_name or data_buf:
                            yield event_name, event_id, "\n".join(data_buf)
                        event_name, event_id, data_buf = "", "", []
                    elif line.startswith(":"):
                        continue  # 注释行（ping）
                    else:
                        field, _, value = line.partition(":")
                        value = value.lstrip(" ")
                        if field == "event":
                            event_name = value
                        elif field == "id":
                            event_id = value
                        elif field == "data":
                            data_buf.append(value)
                if event_name or data_buf:
                    yield event_name, event_id, "\n".join(data_buf)

        # 阶段 1：挂 seconds/3 秒后主动断开（模拟断网，宽限期内）
        phase1_target = max(5, seconds // 3)
        for event, sid, data in _iter_sse():
            _consume(event, sid, data)
            if len(stats_out) >= phase1_target:
                break
        last_id_before_drop = ids[-1] if ids else 0
        exp_before = sum(_int_field(e, "amount") for e in exp_out)
        print(
            f"[bot] phase1 done: {len(stats_out)} stat / exp={exp_before} / "
            f"drops={len(drops_out)} / last_id={last_id_before_drop}；断网 10s…"
        )

        time.sleep(10)  # < 宽限期 60s：tick 必须续跑并持续产事件入 buffer

        # 阶段 2：带 Last-Event-ID 重连 → 先补全缺口再继续直播，直到 stat 总数达标
        expected = last_id_before_drop + 1
        replay_count = 0
        deadline = time.monotonic() + seconds * 2 + 30
        for event, sid, data in _iter_sse(
            headers={"Last-Event-ID": str(last_id_before_drop)}
        ):
            if time.monotonic() > deadline:
                return _fail(
                    f"超时：{len(stats_out)}/{seconds} 条 stat 内未收满",
                    EXIT_ASSERT,
                )
            if sid and int(sid) == expected:
                replay_count += 1
                expected += 1
            _consume(event, sid, data)
            if len(stats_out) >= seconds:
                break

        # ---- 硬断言区 ----
        stats = stats_out
        exp_total = sum(_int_field(e, "amount") for e in exp_out)
        exp_after = exp_total - exp_before
        hp_min = min(_int_field(p, "hp") for p in stats)
        max_hp = _int_field(stats[0], "max_hp")

        if len(stats) < seconds:
            return _fail(
                f"只收到 {len(stats)} 条 stat 事件（需 ≥{seconds}，1Hz 连续性）",
                EXIT_ASSERT,
            )
        if not all(b > a for a, b in pairwise(ids)):
            bad = next((i for i in range(1, len(ids)) if ids[i] <= ids[i - 1]), None)
            ctx = ids[max(0, (bad or 0) - 4) : (bad or 0) + 4]
            return _fail(
                f"事件 id 非严格单调递增（跨重连也应连续）：位置 {bad} 附近 {ctx}",
                EXIT_ASSERT,
            )
        if exp_total <= 0:
            return _fail("经验为 0：挂机期间未击杀任何怪物", EXIT_ASSERT)
        if len(drops_out) < 1:
            return _fail("无任何掉落事件", EXIT_ASSERT)
        # stat 事件在吃药后发布：hp 触及阈值线（≤30%）+ 有 potion 事件即证明触发链
        if hp_min > max_hp * 0.3 or not potions_out:
            return _fail(
                f"自动吃药未验证：hp_min={hp_min}/{max_hp}，potion 事件={len(potions_out)}",
                EXIT_ASSERT,
            )
        if replay_count < 8:
            return _fail(
                f"重连补全不足：仅 {replay_count} 条重放（断网 10s 应有 ≥8 条）",
                EXIT_ASSERT,
            )
        if exp_after <= 0:
            return _fail(
                f"重连后经验未继续增长：重连前 {exp_before}，重连后 {exp_total}",
                EXIT_ASSERT,
            )

        resp = client.post("/api/auth/logout")
        print(f"[bot] logout: HTTP {resp.status_code}（显式登出应取消 tick task）")
        print(
            f"（重连后 +{exp_after}），drops={len(drops_out)}，"
            f"potions={len(potions_out)}，hp_min={hp_min}/{max_hp}，"
        )
        return EXIT_OK


def _seed_taoist_character(username: str, char_name: str) -> tuple[int, int]:
    """直接写库种 35 级道士号 @ 比奇野外，返回 (character_id, 书实例 id)。

    不预插 skill_state——召唤神兽的习得本身是场景的一部分（技能书入口）。
    心跳置 120s 前绕过 seed 心跳门禁（idle_farm 同款混合路径）。
    """
    import asyncio

    os.chdir(_BACKEND_ROOT)
    from sqlalchemy import text

    from app.core.db import SessionLocal

    async def _run() -> tuple[int, int]:
        async with SessionLocal() as db:
            row = await db.execute(
                text(
                    "INSERT INTO character "
                    "(player_id, name, class, level, hp, mp, gold, map_id, bind_city, last_active_at) "
                    "SELECT p.id, :n, 'taoist', 35, 300, 200, 0, 'biqi-wild', 'yin-xing-cun', "
                    "now() - make_interval(secs => 120) "
                    "FROM player p WHERE p.username = :u RETURNING id"
                ),
                {"n": char_name, "u": username},
            )
            character_id = int(row.scalar_one())
            book = await db.execute(
                text(
                    "INSERT INTO item_instance (character_id, item_id, quantity) "
                    "VALUES (:cid, 'book_summon_beast', 1) RETURNING id"
                ),
                {"cid": character_id},
            )
            book_id = int(book.scalar_one())
            await db.execute(
                text(
                    "INSERT INTO item_instance (character_id, item_id, quantity) "
                    "VALUES (:cid, 'potion_hp_small', 30)"
                ),
                {"cid": character_id},
            )
            await db.commit()
            return character_id, book_id

    return asyncio.run(_run())


def run_taoist_dog(base_url: str, seconds: int = 60) -> int:
    """35 级道士召狗场景（todo 28）：书学神兽→召狗→狗杀怪经验归主人→

    断线宽限超时后重连，重放流里收到 pet_dispersed（狗已随会话注销解散）。
    seconds 仅用于事件收集 deadline 缩放；宽限期固定为服务端 60s。
    """
    username = f"bot_{uuid.uuid4().hex[:12]}"
    password = f"pw_{uuid.uuid4().hex[:16]}"
    char_name = f"bot{uuid.uuid4().hex[:10]}"

    with httpx.Client(base_url=base_url, timeout=HTTP_TIMEOUT) as client:
        try:
            resp = client.get("/api/health")
        except httpx.ConnectError as exc:
            return _fail(
                f"Cannot connect to {base_url} —— 后端未启动？({exc})", EXIT_CONNECT
            )
        if resp.status_code != 200:
            return _fail(f"/api/health 返回 {resp.status_code}: {resp.text}", EXIT_HTTP)
        print(f"[bot] health ok: {resp.json()}")

        creds = {"username": username, "password": password}
        resp = client.post("/api/auth/register", json=creds)
        if resp.status_code != 201:
            return _fail(f"注册失败 {resp.status_code}: {resp.text}", EXIT_HTTP)
        resp = client.post("/api/auth/login", json=creds)
        if resp.status_code != 200:
            return _fail(f"登录失败 {resp.status_code}: {resp.text}", EXIT_HTTP)
        character_id, book_id = _seed_taoist_character(username, char_name)
        print(
            f"[bot] seeded taoist {char_name} (id={character_id}) Lv35 + book_summon_beast"
        )

        # 1. 技能书习得召唤神兽（35 级技能唯一习得入口）
        resp = client.post(
            "/api/skill/learn_from_book", json={"item_instance_id": book_id}
        )
        if resp.status_code != 200:
            return _fail(
                f"learn_from_book 失败 {resp.status_code}: {resp.text}", EXIT_HTTP
            )
        learned = resp.json()
        print(f"[bot] learned from book: {learned}")
        if learned.get("skill_id") != "summon_beast":
            return _fail(
                f"书映射错误：期望 summon_beast，实得 {learned.get('skill_id')}",
                EXIT_ASSERT,
            )

        # 2. 施放召唤神兽（REST /use：扣蓝+熟练度+summon_pet）
        resp = client.post("/api/skill/use", json={"skill_id": "summon_beast"})
        if resp.status_code != 200:
            return _fail(f"skill/use 失败 {resp.status_code}: {resp.text}", EXIT_HTTP)
        used = resp.json()
        print(f"[bot] skill/use: {used}")
        if not used.get("success") or not used.get("pet_id"):
            return _fail(f"施放未成功或无 pet_id: {used}", EXIT_ASSERT)

        # 3. 连 SSE：收 pet_summon 重放 + 等狗击杀（exp by=pet / pet_kill 日志）
        pet_summon_seen = False
        pet_kill_seen = False
        ids: list[int] = []
        collect_deadline = time.monotonic() + max(30, seconds)

        def _consume(event: str, sid: str, data: str) -> None:
            nonlocal pet_summon_seen, pet_kill_seen
            if sid:
                ids.append(int(sid))
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                return
            assert isinstance(payload, dict)
            if event == "pet_summon":
                pet_summon_seen = True
            if event == "log" and payload.get("kind") == "pet_kill":
                pet_kill_seen = True

        for event, sid, data in _iter_sse_frames(client):
            if time.monotonic() > collect_deadline:
                break
            _consume(event, sid, data)
            if pet_summon_seen and pet_kill_seen:
                break
        if not pet_summon_seen:
            return _fail("未收到 pet_summon 事件", EXIT_ASSERT)
        if not pet_kill_seen:
            return _fail("宽限前狗未击杀任何怪物（无 pet_kill）", EXIT_ASSERT)
        last_id = ids[-1] if ids else 0
        print(
            f"[bot] phase1 done: pet_summon+pet_kill seen, last_id={last_id}；断线等宽限超时…"
        )

        # 4. 断线 > 宽限期（服务端 60s）：tick task 应自行 teardown 并解散狗
        time.sleep(66)

        # 5. 带 Last-Event-ID 重连：重放窗口内必含 pet_dispersed（狗不存在证明）
        dispersed_seen = False
        stat_after = 0
        reconnect_deadline = time.monotonic() + 30
        for event, sid, data in _iter_sse_frames(
            client, headers={"Last-Event-ID": str(last_id)}
        ):
            if time.monotonic() > reconnect_deadline:
                break
            if event == "pet_dispersed":
                dispersed_seen = True
                break
            if event == "stat":
                stat_after += 1
            if sid:
                ids.append(int(sid))
        if not dispersed_seen:
            return _fail(
                f"重连后未见 pet_dispersed（宽限超时未解散狗）；stat_after={stat_after}",
                EXIT_ASSERT,
            )
        if not all(b > a for a, b in pairwise(ids)):
            return _fail("事件 id 非单调递增", EXIT_ASSERT)

        resp = client.post("/api/auth/logout")
        print(f"[bot] logout: HTTP {resp.status_code}")
        print(
            "[bot] OK: 书学→召狗→狗杀怪经验归主人→宽限超时后狗已解散（pet_dispersed）"
        )
        return EXIT_OK


def _iter_sse_frames(client: httpx.Client, headers: dict[str, str] | None = None):
    """手解 SSE 帧（idle_farm 同款；httpx_sse 在 chunk 边界会重发同一事件）。"""
    with client.stream("GET", "/api/stream", headers=headers) as resp:
        if resp.status_code != 200:
            raise RuntimeError(f"stream HTTP {resp.status_code}")
        event_name, event_id, data_buf = "", "", []
        for line in resp.iter_lines():
            if line == "":
                if event_name or data_buf:
                    yield event_name, event_id, "\n".join(data_buf)
                event_name, event_id, data_buf = "", "", []
            elif line.startswith(":"):
                continue  # 注释行（ping）
            else:
                field, _, value = line.partition(":")
                value = value.lstrip(" ")
                if field == "event":
                    event_name = value
                elif field == "id":
                    event_id = value
                elif field == "data":
                    data_buf.append(value)
        if event_name or data_buf:
            yield event_name, event_id, "\n".join(data_buf)


def _seed_quest_character(username: str, char_name: str) -> int:
    """直接写库种 15 级战士 @ 骷髅洞二层（bounty_skeleton_20 需 L10+）。

    心跳置 120s 前绕过 seed 心跳门禁（idle_farm 同款混合路径）；带小量药防早夭。
    """
    import asyncio

    os.chdir(_BACKEND_ROOT)  # .env 相对 cwd 解析（与 seed_character 同约定）
    from sqlalchemy import text

    from app.core.db import SessionLocal

    async def _run() -> int:
        async with SessionLocal() as db:
            row = await db.execute(
                text(
                    "INSERT INTO character "
                    "(player_id, name, class, level, hp, mp, gold, map_id, bind_city, last_active_at) "
                    "SELECT p.id, :n, 'warrior', 15, 140, 90, 0, 'shouren-gu-2', 'yin-xing-cun', "
                    "now() - make_interval(secs => 120) "
                    "FROM player p WHERE p.username = :u RETURNING id"
                ),
                {"n": char_name, "u": username},
            )
            character_id = int(row.scalar_one())
            await db.execute(
                text(
                    "INSERT INTO item_instance (character_id, item_id, quantity) "
                    "VALUES (:cid, 'potion_hp_small', 30)"
                ),
                {"cid": character_id},
            )
            await db.commit()
            return character_id

    return asyncio.run(_run())


def run_quest_kill(base_url: str, seconds: int = 60) -> int:
    """击杀悬赏场景（todo 29）：接悬赏→真实挂机证明 tick 击杀钩子推进进度→

    REST progress 补满→交付领奖（金币/经验入账）→重复交付被拒（409）。
    seconds 仅用于事件收集 deadline 缩放；骷髅洞混生多种怪，真实击杀不保证
    全是目标怪，故用 REST progress(action=kill) 补满剩余计数（同一内存账本）。
    """
    username = f"bot_{uuid.uuid4().hex[:12]}"
    password = f"pw_{uuid.uuid4().hex[:16]}"
    char_name = f"bot{uuid.uuid4().hex[:10]}"
    quest_id = "bounty_skeleton_20"

    with httpx.Client(base_url=base_url, timeout=HTTP_TIMEOUT) as client:
        try:
            resp = client.get("/api/health")
        except httpx.ConnectError as exc:
            return _fail(
                f"Cannot connect to {base_url} —— 后端未启动？({exc})", EXIT_CONNECT
            )
        if resp.status_code != 200:
            return _fail(f"/api/health 返回 {resp.status_code}: {resp.text}", EXIT_HTTP)
        print(f"[bot] health ok: {resp.json()}")

        creds = {"username": username, "password": password}
        resp = client.post("/api/auth/register", json=creds)
        if resp.status_code != 201:
            return _fail(f"注册失败 {resp.status_code}: {resp.text}", EXIT_HTTP)
        resp = client.post("/api/auth/login", json=creds)
        if resp.status_code != 200:
            return _fail(f"登录失败 {resp.status_code}: {resp.text}", EXIT_HTTP)
        character_id = _seed_quest_character(username, char_name)
        print(f"[bot] seeded warrior {char_name} (id={character_id}) Lv15 @ shouren-gu-2")

        # 1. 接悬赏
        resp = client.post("/api/quest/accept", json={"quest_id": quest_id})
        if resp.status_code != 200:
            return _fail(f"quest/accept 失败 {resp.status_code}: {resp.text}", EXIT_HTTP)
        accepted = resp.json()
        print(
            f"[bot] accepted: {accepted['quest']['name']} "
            f"status={accepted['quest']['status']}"
        )

        # 2. 连 SSE 真实挂机：等第一个 quest_progress（tick 击杀钩子集成证明）
        tick_progress_seen = False
        # 骷髅在刷怪序里排第 8–11 位（axe×4 → bat×3 → skeleton×4），裸装战士
        # 每杀约 10–15s：留足清完前置怪的时间窗。
        deadline = time.monotonic() + max(240, seconds * 5)
        for event, _sid, data in _iter_sse_frames(client):
            if time.monotonic() > deadline:
                break
            if event != "quest_progress":
                continue
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            assert isinstance(payload, dict)
            if payload.get("quest_id") == quest_id:
                tick_progress_seen = True
                print(
                    f"[bot] tick-driven quest_progress: "
                    f"{payload['count']}/{payload['need']}"
                )
                break
        if not tick_progress_seen:
            return _fail(
                "超时：挂机期间未见 tick 驱动的 quest_progress（击杀钩子未生效）",
                EXIT_ASSERT,
            )

        # 3. REST progress 补满剩余计数（与 tick 钩子同一内存账本）。
        # 注意竞态：tick 可能与 REST 同时推进——达标后任务移入 completed 组，
        # 两组都要找；都找不到才算异常。
        entry: dict[str, object] | None = None
        status_now = ""
        while True:
            resp = client.get("/api/quest/list")
            if resp.status_code != 200:
                return _fail(f"quest/list 失败 {resp.status_code}: {resp.text}", EXIT_HTTP)
            body = resp.json()
            entry = next(
                (
                    q
                    for q in body["in_progress"] + body["completed"]
                    if q["quest_id"] == quest_id
                ),
                None,
            )
            if entry is None:
                return _fail("补满阶段任务不在进行/完成分组", EXIT_ASSERT)
            status_now = str(entry["status"])
            if status_now == "completed":
                break
            resp = client.post(
                "/api/quest/progress", json={"quest_id": quest_id, "action": "kill"}
            )
            if resp.status_code != 200:
                return _fail(
                    f"quest/progress 失败 {resp.status_code}: {resp.text}", EXIT_HTTP
                )
        assert entry is not None
        print(f"[bot] bounty ready: {entry['count']}/{entry['need']} status={status_now}")

        # 4. 交付前基线 → 交付 → 断言奖励入账
        me_before = client.get("/api/character/me")
        if me_before.status_code != 200:
            return _fail(f"character/me 失败 {me_before.status_code}", EXIT_HTTP)
        gold_before = int(me_before.json()["gold"])
        exp_before = int(me_before.json()["exp"])
        resp = client.post("/api/quest/complete", json={"quest_id": quest_id})
        if resp.status_code != 200:
            return _fail(f"quest/complete 失败 {resp.status_code}: {resp.text}", EXIT_HTTP)
        done = resp.json()
        rewards = done["rewards"]
        if int(rewards["gold"]) != 500 or int(rewards["exp"]) != 1500:
            return _fail(f"奖励数额不符：{rewards}", EXIT_ASSERT)
        me_after = client.get("/api/character/me")
        gold_after = int(me_after.json()["gold"])
        exp_after = int(me_after.json()["exp"])
        if gold_after != gold_before + 500 or exp_after <= exp_before:
            return _fail(
                f"奖励未入账：gold {gold_before}->{gold_after}，exp {exp_before}->{exp_after}",
                EXIT_ASSERT,
            )
        print(
            f"[bot] rewards credited: gold +500 ({gold_before}->{gold_after}), "
            f"exp +{exp_after - exp_before}"
        )

        # 5. 重复交付 → 409
        resp = client.post("/api/quest/complete", json={"quest_id": quest_id})
        if resp.status_code != 409:
            return _fail(
                f"重复交付未被拒：期望 409 实得 {resp.status_code}: {resp.text}",
                EXIT_ASSERT,
            )

        resp = client.post("/api/auth/logout")
        print(f"[bot] logout: HTTP {resp.status_code}")
        print("[bot] OK: 接悬赏→tick 击杀推进→REST 补满→交付入账→重复交付 409")
        return EXIT_OK


# --- farm_metric（todo 37）：数值手感校准矩阵单元 ------------------------------
#
# 与 idle_farm 的差异：本场景是**测量**而非冒烟断言——不走断网重连链路，
# 只在目标图静挂 seconds 秒收集事件流，采集结束后读库对账，汇总成一行
# METRIC JSON 供 scripts/bot_matrix.py 聚合。数值不达标是**数据**不是错误：
# 只有采集链断裂（stat 心跳严重不足）才返回非 0。


def _seed_farm_character(
    username: str,
    char_name: str,
    class_: str,
    map_id: str,
    potions: int,
    sun_waters: int,
) -> int:
    """直接写库种 1 级裸装号到目标图（register/login 走 API、角色走 DB 的混合路径）。

    hp/mp 按 session.py 占位上限公式落库（max_hp=16+8×1=24，max_mp=10+5×1=15）；
    last_active_at 置 120s 前绕过 seed 心跳门禁（idle_farm 同款）。药瓶数量参数化
    （0 = 不塞，供"不氪药"对照组）。返回 character_id。
    """
    import asyncio

    os.chdir(_BACKEND_ROOT)  # .env 相对 cwd 解析（与 seed_character 同约定）
    from sqlalchemy import text

    from app.core.db import SessionLocal, engine

    async def _run() -> int:
        # 收尾 dispose 全局 engine（task 15 跨 loop 投毒教训：池化连接绑旧 loop，
        # 同一脚本下一个 asyncio.run 检出即 RuntimeError）。
        try:
            async with SessionLocal() as db:
                row = await db.execute(
                    text(
                        "INSERT INTO character "
                        "(player_id, name, class, level, hp, mp, gold, map_id, bind_city, last_active_at) "
                        "SELECT p.id, :n, :c, 1, 24, 15, 0, :m, 'yin-xing-cun', "
                        "now() - make_interval(secs => 120) "
                        "FROM player p WHERE p.username = :u RETURNING id"
                    ),
                    {"n": char_name, "c": class_, "m": map_id, "u": username},
                )
                character_id = int(row.scalar_one())
                for item_id, qty in (
                    ("potion_hp_small", potions),
                    ("sun_water", sun_waters),
                ):
                    if qty <= 0:
                        continue
                    await db.execute(
                        text(
                            "INSERT INTO item_instance (character_id, item_id, quantity) "
                            "VALUES (:cid, :iid, :qty)"
                        ),
                        {"cid": character_id, "iid": item_id, "qty": qty},
                    )
                await db.commit()
                return character_id
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _final_char_state(character_id: int) -> dict[str, object]:
    """采集结束后读库对账：等级/经验/金币/背包余量（dispose 同 _seed_farm_character）。"""
    import asyncio

    os.chdir(_BACKEND_ROOT)
    from sqlalchemy import text

    from app.core.db import SessionLocal, engine

    async def _run() -> dict[str, object]:
        try:
            async with SessionLocal() as db:
                row = (
                    await db.execute(
                        text(
                            "SELECT level, exp, gold, hp, mp FROM character WHERE id = :cid"
                        ),
                        {"cid": character_id},
                    )
                ).one()
                items = (
                    await db.execute(
                        text(
                            "SELECT item_id, quantity FROM item_instance "
                            "WHERE character_id = :cid ORDER BY item_id"
                        ),
                        {"cid": character_id},
                    )
                ).all()
                return {
                    "level": int(row.level),
                    "exp": int(row.exp),
                    "gold": int(row.gold),
                    "hp": int(row.hp),
                    "mp": int(row.mp),
                    "bag": {str(r.item_id): int(r.quantity) for r in items},
                }
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _cumulative_exp_thresholds() -> dict[int, int]:
    """levels.json → {目标等级: 从 1 级 0 经验升到该级所需累计经验}。"""
    data = json.loads(
        (_BACKEND_ROOT / "gamedata" / "levels.json").read_text(encoding="utf-8")
    )
    entries = sorted(
        (int(e["level"]), int(e["exp_to_next"])) for e in data["levels"]
    )
    cumulative: dict[int, int] = {}
    total = 0
    for level, exp_to_next in entries:
        if exp_to_next <= 0:
            break
        total += exp_to_next
        cumulative[level + 1] = total
    return cumulative


def _as_int(value: object) -> int:
    """宽松 int 归一（bool 拒绝）：SSE payload 的 object 值域收窄。"""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def run_farm_metric(
    base_url: str,
    seconds: int,
    map_id: str,
    class_: str,
    potions: int,
    sun_waters: int,
) -> int:
    """校准矩阵单元：种 1 级号到 --map → 静挂 seconds 秒 → 汇总 METRIC JSON。

    采集：exp/drop/stat/potion/survive/kill/recall 事件全量计数，exp 事件按
    levels.json 累计门槛记录 time_to_level（虚拟升级——MVP 无在线升级机制，
    level 永不自动变，见 todo 37 前勘）。survive 事件 = MVP 钳 1 血保命次数，
    即"死亡"指标（死亡/复活系统不在 MVP）。
    """
    username = f"bot_{uuid.uuid4().hex[:12]}"
    password = f"pw_{uuid.uuid4().hex[:16]}"
    char_name = f"bot{uuid.uuid4().hex[:10]}"

    with httpx.Client(base_url=base_url, timeout=HTTP_TIMEOUT) as client:
        try:
            resp = client.get("/api/health")
        except httpx.ConnectError as exc:
            return _fail(
                f"Cannot connect to {base_url} —— 后端未启动？({exc})", EXIT_CONNECT
            )
        if resp.status_code != 200:
            return _fail(f"/api/health 返回 {resp.status_code}: {resp.text}", EXIT_HTTP)

        creds = {"username": username, "password": password}
        resp = client.post("/api/auth/register", json=creds)
        if resp.status_code != 201:
            return _fail(f"注册失败 {resp.status_code}: {resp.text}", EXIT_HTTP)
        resp = client.post("/api/auth/login", json=creds)
        if resp.status_code != 200:
            return _fail(f"登录失败 {resp.status_code}: {resp.text}", EXIT_HTTP)
        character_id = _seed_farm_character(
            username, char_name, class_, map_id, potions, sun_waters
        )
        print(
            f"[bot] seeded {class_} {char_name} (id={character_id}) Lv1 @ {map_id} "
            f"potions={potions}+{sun_waters}",
            flush=True,
        )

        thresholds = _cumulative_exp_thresholds()
        stats_out: list[dict[str, object]] = []
        exp_total = 0
        kills = 0
        drops = 0
        recalls = 0
        survive_events = 0
        potions_used: dict[str, int] = {}
        time_to_level: dict[str, float] = {}
        t0: float | None = None

        def _consume(event: str, _sid: str, data: str) -> None:
            nonlocal exp_total, kills, drops, recalls, survive_events, t0
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                return
            if not isinstance(payload, dict):
                return
            now = time.monotonic()
            if event == "stat":
                if t0 is None:
                    t0 = now
                stats_out.append(payload)
                return
            if t0 is None:
                t0 = now
            if event == "exp":
                amount = payload.get("amount", 0)
                total = payload.get("total", 0)
                exp_total += amount if isinstance(amount, int) else 0
                total_int = total if isinstance(total, int) else 0
                for level, need in thresholds.items():
                    key = str(level)
                    if total_int >= need and key not in time_to_level:
                        time_to_level[key] = round(now - t0, 1)
            elif event == "drop":
                drops += 1
            elif event == "log":
                kind = payload.get("kind")
                if kind == "potion":
                    text_body = str(payload.get("text", ""))
                    matched = re.search(r"自动喝下([a-z0-9_]+)", text_body)
                    item_id = matched.group(1) if matched else "unknown"
                    potions_used[item_id] = potions_used.get(item_id, 0) + 1
                elif kind == "survive":
                    survive_events += 1
                elif kind == "kill":
                    kills += 1
                elif kind == "recall":
                    recalls += 1

        deadline = time.monotonic() + seconds * 2 + 60
        stream_iter = _iter_sse_frames(client)
        try:
            for event, sid, data in stream_iter:
                if time.monotonic() > deadline:
                    return _fail(
                        f"超时：{len(stats_out)}/{seconds} 条 stat 内未收满",
                        EXIT_ASSERT,
                    )
                _consume(event, sid, data)
                if len(stats_out) >= seconds:
                    break
        finally:
            stream_iter.close()

        # 显式登出：取消 tick task（防宽限期 60s 内继续空烧药/占图）。
        resp = client.post("/api/auth/logout")
        print(f"[bot] logout: HTTP {resp.status_code}", flush=True)

        if len(stats_out) < seconds * 9 // 10:
            return _fail(
                f"stat 心跳断裂：只收到 {len(stats_out)} 条（期望 ≥{seconds}）",
                EXIT_ASSERT,
            )

        first_stat, last_stat = stats_out[0], stats_out[-1]
        wall_seconds = round(time.monotonic() - (t0 or time.monotonic()), 1)
        hp_values: list[int] = []
        for p in stats_out:
            hp_value = p.get("hp")
            if isinstance(hp_value, int) and not isinstance(hp_value, bool):
                hp_values.append(hp_value)
        final = _final_char_state(character_id)
        metric: dict[str, object] = {
            "scenario": "farm_metric",
            "character_id": character_id,
            "class": class_,
            "map_id": map_id,
            "seconds_requested": seconds,
            "stat_events": len(stats_out),
            "wall_seconds": wall_seconds,
            "exp_gained": exp_total,
            "exp_per_hour": round(exp_total / max(wall_seconds, 1) * 3600, 1),
            "kills": kills,
            "drops": drops,
            "gold_gained": _as_int(last_stat.get("gold"))
            - _as_int(first_stat.get("gold")),
            "potions_used": potions_used,
            "survive_events": survive_events,
            "recalls": recalls,
            "hp_min": min(hp_values) if hp_values else 0,
            "max_hp": _as_int(first_stat.get("max_hp")),
            "time_to_level": time_to_level,
            "final_db": final,
        }
        print(
            f"[bot] done: exp={exp_total} kills={kills} drops={drops} "
            f"survive={survive_events} potions={potions_used} "
            f"hp_min={metric['hp_min']}/{metric['max_hp']}",
            flush=True,
        )
        print("METRIC " + json.dumps(metric, ensure_ascii=False), flush=True)
        return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.scenario == "smoke":
        return run_smoke(args.base_url, args.count)
    if args.scenario == "idle_farm":
        return run_idle_farm(args.base_url, args.seconds)
    if args.scenario == "taoist_dog":
        return run_taoist_dog(args.base_url, args.seconds)
    if args.scenario == "quest_kill":
        return run_quest_kill(args.base_url, args.seconds)
    if args.scenario == "farm_metric":
        return run_farm_metric(
            args.base_url,
            args.seconds,
            args.map_id,
            args.class_,
            args.potions,
            args.sun_waters,
        )
    return _fail(
        f"未知场景 {args.scenario!r}（可选 {', '.join(SCENARIOS)}）", EXIT_SCENARIO_TODO
    )


if __name__ == "__main__":
    sys.exit(main())
