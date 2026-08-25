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
SCENARIOS: Final[tuple[str, ...]] = ("smoke", "idle_farm", "taoist_dog")
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
        help="冒烟场景：smoke=基础冒烟（本任务）；idle_farm=挂机（todo 15）；taoist_dog=技能（todo 28）",
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
        help="挂机时长秒数（默认 60，仅 idle_farm 用）",
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.scenario == "smoke":
        return run_smoke(args.base_url, args.count)
    if args.scenario == "idle_farm":
        return run_idle_farm(args.base_url, args.seconds)
    if args.scenario == "taoist_dog":
        return run_taoist_dog(args.base_url, args.seconds)
    return _fail(
        f"未知场景 {args.scenario!r}（可选 {', '.join(SCENARIOS)}）", EXIT_SCENARIO_TODO
    )


if __name__ == "__main__":
    sys.exit(main())
