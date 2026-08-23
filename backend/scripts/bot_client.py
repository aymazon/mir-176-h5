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
HTTP_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)

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
        help="需收到的 message 事件条数（默认 10）",
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
            return _fail(f"Cannot connect to {base_url} —— 后端未启动？({exc})", EXIT_CONNECT)
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
        resp = client.post("/api/character", json={"name": char_name, "class": "warrior"})
        if resp.status_code != 201:
            return _fail(f"建号失败 {resp.status_code}: {resp.text}", EXIT_HTTP)
        print(f"[bot] character created: {resp.json()['name']} (id={resp.json()['id']})")

        # 4. 开 SSE 收事件（httpx-sse 忽略 `: ping` 注释行）
        ids: list[int] = []
        messages = 0
        got_gap = False
        deadline = time.monotonic() + count * 5 + 30
        try:
            with connect_sse(client, "GET", "/api/stream") as event_source:
                for sse in event_source.iter_sse():
                    if time.monotonic() > deadline:
                        return _fail(f"超时：{messages}/{count} 条 message 内未收满", EXIT_ASSERT)
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
        print(f"[bot] OK: {messages} 条 message、共 {len(ids)} 个带 id 事件，id 单调递增 {ids[0]}..{ids[-1]}"
              + ("（含 gap 重置后新 epoch）" if got_gap else ""))
        return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.scenario != "smoke":
        print(
            f"[bot] 场景 {args.scenario!r} 尚未实现：idle_farm 归 todo 15（session+tick_loop），"
            "taoist_dog 归 todo 28（技能升级）。当前仅支持 --scenario smoke。",
            file=sys.stderr,
        )
        return EXIT_SCENARIO_TODO
    return run_smoke(args.base_url, args.count)


if __name__ == "__main__":
    sys.exit(main())
