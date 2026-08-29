"""数值手感校准矩阵驱动（todo 37）：15 组 (职业 × 地图) 挂机 + 聚合报告。

矩阵：3 职业 × 5 地图（比奇野外/骷髅洞一层/僵尸洞/蜈蚣洞/石墓），每格
``bot_client.py --scenario farm_metric`` 静挂 N 秒（默认 300s）。并行模型：
**跨图并行、同图串行**——zone 注册表按 map_id 隔离，同图多号会共享
alive_monsters[0] 互相抢怪，故同图 3 职业串行、5 张图各一路并行（5 worker，
单 pass 墙钟 ≈ 3×N + 开销）。

产出（.omo/evidence/，gitignored）：
- ``task-37-matrix-<class>-<zone>-<label>.log``：单格原始 bot 输出（含 METRIC 行）
- ``task-37-matrix-<class>-<zone>.log``：最近一次运行的同名单格日志（契约文件名）
- ``task-37-runs.jsonl``：每次运行的结构化汇总（追加写；报告由它整体重建，幂等）
- ``task-37-mir-176-h5-mvp.md``：汇总报告（BEFORE/AFTER 对照 = 多次 --label 累积）

断言（设计目标：docs/MVP/README.md §6 练级动线 + 计划 todo 37 验收）：
- A_lv7：比奇野外（1–7 级带）各职业**预估** 1→7 级 ≤ 1800s
  （est = wall_seconds × 累计经验需求 2500 / 实获经验；MVP 无在线升级机制，
  level 恒为种子值，故用 levels.json 累计表外推，口径见报告头）；
- A_death：新手区（比奇野外 + 骷髅洞一层）survive 事件 = 0
  （MVP 无死亡/复活系统，tick_loop 把 hp 钳 1 并发 survive 日志 = 死亡计数）；
- A_dps：同图三职业 exp/h 落在组均值 ±30% 内（同阶手感代理指标；
  全 0 图跳过 = 等级带正确拒绝越级挂机，属预期）。

退出码：0 全断言通过；1 有断言失败（报告照写——失败样本即调参依据）；
2 采集错误（某格 bot 非 0 退出 / METRIC 缺失）。

用法（cwd=backend）：
    uv run python scripts/bot_matrix.py --label baseline
    uv run python scripts/bot_matrix.py --label tune-1 --only warrior:biqi-wild
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, TypedDict, cast

# 与 bot_client.py 同约定：sys.path 定位 + chdir backend（.env / gamedata 相对 cwd）。
_BACKEND_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
_REPO_ROOT: Final[Path] = _BACKEND_ROOT.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))
os.chdir(_BACKEND_ROOT)

EVIDENCE_DIR: Final[Path] = _REPO_ROOT / ".omo" / "evidence"
RUNS_JSONL: Final[Path] = EVIDENCE_DIR / "task-37-runs.jsonl"
REPORT_MD: Final[Path] = EVIDENCE_DIR / "task-37-mir-176-h5-mvp.md"

ZONES: Final[tuple[tuple[str, str], ...]] = (
    ("biqi-wild", "比奇野外"),
    ("shouren-gu-1", "骷髅洞一层"),
    ("biqi-mine", "僵尸洞"),
    ("centipede-cave", "蜈蚣洞"),
    ("shimen", "石墓"),
)
CLASSES: Final[tuple[str, ...]] = ("warrior", "wizard", "taoist")
CLASS_NAMES: Final[dict[str, str]] = {
    "warrior": "战士",
    "wizard": "法师",
    "taoist": "道士",
}
NEWBIE_ZONES: Final[frozenset[str]] = frozenset({"biqi-wild", "shouren-gu-1"})
LV7_ZONE: Final[str] = "biqi-wild"
TARGET_LV7_SECONDS: Final[int] = 1800  # 30 min
DPS_PARITY_BAND: Final[float] = 0.3
METRIC_RE: Final[re.Pattern[str]] = re.compile(r"^METRIC (\{.*\})\s*$")

EXIT_OK: Final[int] = 0
EXIT_ASSERT: Final[int] = 1
EXIT_COLLECT: Final[int] = 2


def _num(value: object) -> float:
    """宽松数值归一（bool 拒绝）：JSON 反序列化的 object 值域收窄。"""
    if isinstance(value, bool):
        return 0.0
    return float(value) if isinstance(value, (int, float)) else 0.0


class AssertionRecord(TypedDict):
    check: str
    subject: str
    passed: bool
    detail: str


class CellRecord(TypedDict):
    class_: str
    zone_id: str
    zone_name: str
    log_file: str
    returncode: int
    elapsed_s: float
    metric: dict[str, object] | None
    error: str


class RunRecord(TypedDict):
    label: str
    started_at: str
    seconds: int
    balance_snapshot: dict[str, object]
    cells: list[CellRecord]
    assertions: list[AssertionRecord]


@dataclass
class CellResult:
    """单格采集结果（metric = bot 的 METRIC JSON 原文；error 非空 = 采集失败）。"""

    class_: str
    zone_id: str
    zone_name: str
    log_file: str
    returncode: int
    elapsed_s: float
    metric: dict[str, object] | None = None
    error: str = ""

    def to_record(self) -> CellRecord:
        return {
            "class_": self.class_,
            "zone_id": self.zone_id,
            "zone_name": self.zone_name,
            "log_file": self.log_file,
            "returncode": self.returncode,
            "elapsed_s": self.elapsed_s,
            "metric": self.metric,
            "error": self.error,
        }


@dataclass
class AssertionOutcome:
    """单条断言结果（report 与退出码共用）。"""

    check: str
    subject: str
    passed: bool
    detail: str

    def to_record(self) -> AssertionRecord:
        return {
            "check": self.check,
            "subject": self.subject,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass
class RunResult:
    label: str
    started_at: str
    seconds: int
    balance_snapshot: dict[str, object]
    cells: list[CellResult] = field(default_factory=list)
    assertions: list[AssertionOutcome] = field(default_factory=list)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bot_matrix",
        description="todo 37 数值手感校准矩阵：3 职业 × 5 地图挂机采集 + 断言 + 报告",
    )
    parser.add_argument(
        "--label",
        required=True,
        help="本次运行标签（baseline / tune-1 / ...；报告分节与日志文件名共用）",
    )
    parser.add_argument("--seconds", type=int, default=300, help="每格挂机秒数（默认 300）")
    parser.add_argument("--potions", type=int, default=40, help="每格种子金创药(小)数量")
    parser.add_argument("--sun-waters", type=int, default=5, help="每格种子太阳水数量")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="CLASS:ZONE",
        help="只跑指定格（可重复；调试用，默认全矩阵 15 格）",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("MIR_BASE_URL", "http://127.0.0.1:8000"),
        help="后端地址（默认 MIR_BASE_URL 或 http://127.0.0.1:8000）",
    )
    return parser


def _exp_need_1_to(target_level: int) -> int:
    """levels.json 累计：从 1 级 0 经验升到 target_level 所需总经验。"""
    data = json.loads(
        (_BACKEND_ROOT / "gamedata" / "levels.json").read_text(encoding="utf-8")
    )
    total = 0
    for entry in sorted(data["levels"], key=lambda e: int(e["level"])):
        level = int(entry["level"])
        if level >= target_level:
            break
        exp_to_next = int(entry["exp_to_next"])
        if exp_to_next <= 0:
            break
        total += exp_to_next
    return total


def _balance_snapshot() -> dict[str, object]:
    """记录本次运行的 balance.py 关键系数（BEFORE/AFTER 对照表头）。"""
    from app.combat import balance

    return {
        "ATTACK_INTERVAL_TICKS": balance.ATTACK_INTERVAL_TICKS,
        "MIN_HIT_RATE": balance.MIN_HIT_RATE,
        "MAX_HIT_RATE": balance.MAX_HIT_RATE,
        "DEF_DAMAGE_REDUCTION_FACTOR": balance.DEF_DAMAGE_REDUCTION_FACTOR,
        "EXP_GAIN_RATE": balance.EXP_GAIN_RATE,
        "GLOBAL_DROP_RATE": balance.GLOBAL_DROP_RATE,
        "POTION_HP_PCT": balance.POTION_HP_PCT,
        "POTION_URGENT_HP_PCT": balance.POTION_URGENT_HP_PCT,
        "POTION_COOLDOWN_TICKS": balance.POTION_COOLDOWN_TICKS,
    }


def run_cell(
    class_: str,
    zone_id: str,
    zone_name: str,
    *,
    label: str,
    seconds: int,
    potions: int,
    sun_waters: int,
    base_url: str,
) -> CellResult:
    """跑单格：子进程 bot_client farm_metric → 落日志 → 解析 METRIC 行。"""
    log_path = EVIDENCE_DIR / f"task-37-matrix-{class_}-{zone_id}-{label}.log"
    canonical_path = EVIDENCE_DIR / f"task-37-matrix-{class_}-{zone_id}.log"
    cmd = [
        sys.executable,
        "scripts/bot_client.py",
        "--scenario",
        "farm_metric",
        "--class",
        class_,
        "--map",
        zone_id,
        "--seconds",
        str(seconds),
        "--potions",
        str(potions),
        "--sun-waters",
        str(sun_waters),
        "--base-url",
        base_url,
    ]
    started = time.monotonic()
    print(f"[matrix] start {class_}@{zone_id}", flush=True)
    try:
        proc = subprocess.run(
            cmd,
            cwd=_BACKEND_ROOT,
            capture_output=True,
            text=True,
            timeout=seconds * 2 + 180,
            check=False,
        )
        output = proc.stdout + "\n--- stderr ---\n" + proc.stderr
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        output = f"[matrix] cell timeout after {seconds * 2 + 180}s"
        returncode = -1
    elapsed = round(time.monotonic() - started, 1)
    log_path.write_text(output, encoding="utf-8")
    canonical_path.write_text(output, encoding="utf-8")

    metric: dict[str, object] | None = None
    for line in output.splitlines():
        matched = METRIC_RE.match(line.strip())
        if matched:
            metric = cast("dict[str, object]", json.loads(matched.group(1)))
    error = ""
    if returncode != 0:
        error = f"bot exit={returncode}"
    elif metric is None:
        error = "METRIC line missing"
    print(
        f"[matrix] done {class_}@{zone_id} in {elapsed}s rc={returncode} "
        f"err={error or '-'}",
        flush=True,
    )
    return CellResult(
        class_=class_,
        zone_id=zone_id,
        zone_name=zone_name,
        log_file=log_path.name,
        returncode=returncode,
        elapsed_s=elapsed,
        metric=metric,
        error=error,
    )


def _cell_exp_per_hour(cell: CellResult) -> float:
    if cell.metric is None:
        return 0.0
    return _num(cell.metric.get("exp_per_hour", 0))


def evaluate(run: RunResult, exp_need_lv7: int) -> None:
    """A_lv7 / A_death / A_dps 三组断言，结果挂到 run.assertions。"""
    for cell in run.cells:
        subject = f"{cell.class_}@{cell.zone_id}"
        if cell.error or cell.metric is None:
            run.assertions.append(
                AssertionOutcome("collect", subject, False, cell.error or "no metric")
            )
            continue
        metric = cell.metric
        exp_gained = _num(metric.get("exp_gained", 0))
        wall = _num(metric.get("wall_seconds", 0))
        survive = _num(metric.get("survive_events", 0))
        if cell.zone_id == LV7_ZONE:
            if exp_gained > 0 and wall > 0:
                est = wall * exp_need_lv7 / exp_gained
                run.assertions.append(
                    AssertionOutcome(
                        "A_lv7",
                        subject,
                        est <= TARGET_LV7_SECONDS,
                        f"预估 1→7 = {est:.0f}s（目标 ≤{TARGET_LV7_SECONDS}s，"
                        f"实获 {exp_gained:.0f} exp / {wall:.0f}s）",
                    )
                )
            else:
                run.assertions.append(
                    AssertionOutcome("A_lv7", subject, False, "300s 零经验，无法外推")
                )
        if cell.zone_id in NEWBIE_ZONES:
            run.assertions.append(
                AssertionOutcome(
                    "A_death",
                    subject,
                    survive == 0,
                    f"survive(死亡)事件 = {survive:.0f}",
                )
            )
    # A_dps：同图三职业 exp/h 对组均值 ±30%（全 0 图跳过 = 越级被拒，预期形态）。
    for zone_id, zone_name in ZONES:
        zone_cells = [
            c for c in run.cells if c.zone_id == zone_id and c.metric is not None
        ]
        if len(zone_cells) < len(CLASSES):
            continue
        rates = [_cell_exp_per_hour(c) for c in zone_cells]
        mean = sum(rates) / len(rates)
        if mean <= 0:
            run.assertions.append(
                AssertionOutcome(
                    "A_dps",
                    f"*@{zone_id}",
                    True,
                    f"{zone_name} 三职业 300s 全部零经验（等级带拒绝越级，跳过 ±30% 判定）",
                )
            )
            continue
        for cell, rate in zip(zone_cells, rates, strict=True):
            lo = mean * (1 - DPS_PARITY_BAND)
            hi = mean * (1 + DPS_PARITY_BAND)
            run.assertions.append(
                AssertionOutcome(
                    "A_dps",
                    f"{cell.class_}@{zone_id}",
                    lo <= rate <= hi,
                    f"exp/h={rate:.0f} vs 组均值 {mean:.0f}（带 ±30%: {lo:.0f}–{hi:.0f}）",
                )
            )


def _comparison_section(
    first: RunRecord, last: RunRecord, exp_need_lv7: int
) -> list[str]:
    """BEFORE/AFTER 对照表：首末两次 run 按 (class, zone) 对齐 exp/死亡/预估时长。"""
    first_cells = {(c["class_"], c["zone_id"]): c for c in first.get("cells", [])}
    last_cells = {(c["class_"], c["zone_id"]): c for c in last.get("cells", [])}

    def _est(cell: CellRecord | None) -> str:
        if cell is None or cell["metric"] is None:
            return "—"
        m = cell["metric"]
        exp_gained = _num(m.get("exp_gained", 0))
        wall = _num(m.get("wall_seconds", 0))
        if exp_gained <= 0 or wall <= 0:
            return "—"
        return f"{wall * exp_need_lv7 / exp_gained / 60:.1f}min"

    def _exp(cell: CellRecord | None) -> str:
        if cell is None or cell["metric"] is None:
            return "—"
        return f"{_num(cell['metric'].get('exp_gained', 0)):.0f}"

    def _death(cell: CellRecord | None) -> str:
        if cell is None or cell["metric"] is None:
            return "—"
        return f"{_num(cell['metric'].get('survive_events', 0)):.0f}"

    lines = [
        f"## BEFORE/AFTER 对照（{first['label']} → {last['label']}）",
        "",
        "| 职业 | 地图 | exp BEFORE | exp AFTER | 死亡 BEFORE | 死亡 AFTER | 预估1→7 BEFORE | 预估1→7 AFTER |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for zone_id, zone_name in ZONES:
        for class_ in CLASSES:
            before = first_cells.get((class_, zone_id))
            after = last_cells.get((class_, zone_id))
            lines.append(
                f"| {CLASS_NAMES[class_]} | {zone_name} | {_exp(before)} | {_exp(after)} "
                f"| {_death(before)} | {_death(after)} | {_est(before)} | {_est(after)} |"
            )
    lines.append("")
    return lines


# --- 报告重建（jsonl → md，幂等）----------------------------------------------


def _fmt_minutes(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    return f"{seconds / 60:.1f}min"


def _cell_row(cell: CellRecord, exp_need_lv7: int) -> str:
    if cell["metric"] is None:
        return (
            f"| {CLASS_NAMES[cell['class_']]} | {cell['zone_name']} | 采集失败 "
            f"({cell['error'] or 'unknown'}) | — | — | — | — | — | — | — | — |"
        )
    m = cell["metric"]
    exp_gained = _num(m.get("exp_gained", 0))
    wall = _num(m.get("wall_seconds", 0))
    est: float | None = (
        wall * exp_need_lv7 / exp_gained if exp_gained > 0 and wall > 0 else None
    )
    potions = m.get("potions_used", {})
    potions_text = (
        "/".join(f"{k}×{v}" for k, v in sorted(potions.items()))
        if isinstance(potions, dict) and potions
        else "0"
    )
    ttl = m.get("time_to_level", {})
    lv2 = ttl.get("2") if isinstance(ttl, dict) else None
    lv2_text = f"{_num(lv2):.0f}s" if isinstance(lv2, (int, float)) else "未达到"
    return (
        f"| {CLASS_NAMES[cell['class_']]} | {cell['zone_name']} | {exp_gained:.0f} "
        f"| {_num(m.get('exp_per_hour', 0)):.0f} | {_num(m.get('kills', 0)):.0f} "
        f"| {_num(m.get('drops', 0)):.0f} | {_num(m.get('gold_gained', 0)):.0f} "
        f"| {potions_text} | {_num(m.get('survive_events', 0)):.0f} "
        f"| {_num(m.get('hp_min', 0)):.0f}/{_num(m.get('max_hp', 0)):.0f} "
        f"| {lv2_text} | {_fmt_minutes(est)} |"
    )


def rebuild_report(exp_need_lv7: int) -> None:
    """从 runs.jsonl 重建汇总 md（多 label 按时间序并排 = BEFORE/AFTER 对照）。"""
    runs: list[RunRecord] = []
    if RUNS_JSONL.exists():
        for line in RUNS_JSONL.read_text(encoding="utf-8").splitlines():
            if line.strip():
                runs.append(cast("RunRecord", json.loads(line)))
    line_cell = (
        "- 每格 = 新注册账号 + SQL 直种 Lv1 裸装号（hp=24/mp=15，职业 --class）"
        "+ 金创药(小)×40 + 太阳水×5，静挂 300s 收 SSE。"
    )
    line_virtual = (
        "- **虚拟升级**：MVP 无在线升级机制（tick_loop 只加 exp 不动 level），"
        "升级时长 = 按 levels.json 累计经验门槛对 SSE exp 事件外推；"
        f"预估 1→7 = wall_seconds × {exp_need_lv7}（1→7 累计需求）/ 实获经验。"
    )
    line_death = (
        "- **死亡**：MVP 无死亡/复活系统，hp 钳 1 保命并发 kind=survive 日志——"
        "survive 事件数即死亡计数。"
    )
    line_dps = (
        "- **DPS 同阶代理**：同图三职业 exp/h 对组均值 ±30%（真实伤害不经 SSE 暴露，"
        "exp 产出是击杀吞吐的直接函数）。"
    )
    line_par = (
        "- 矩阵并行模型：跨图 5 路并行、同图 3 职业串行（同图共享 alive_monsters[0]，"
        "并行会互相抢怪）。"
    )
    line_target = (
        f"设计目标（docs/MVP/README.md §6 + todo 37）：1–7 级 ≤ "
        f"{TARGET_LV7_SECONDS // 60}min、新手区（比奇野外/骷髅洞）零死亡、"
        "各职业 DPS 同阶 ±30%。"
    )
    parts = [
        "# Task 37 数值手感校准报告（bot 挂机矩阵）",
        "",
        "## 口径说明",
        "",
        line_cell,
        line_virtual,
        line_death,
        line_dps,
        line_par,
        "- 单格原始日志：task-37-matrix-<class>-<zone>-<label>.log。",
        "",
        line_target,
        "",
    ]
    for raw in runs:
        balance = raw.get("balance_snapshot", {})
        parts.append(f"## Run: {raw['label']} — {raw['started_at']}")
        parts.append("")
        balance_line = (
            "balance.py 快照：`"
            + " ".join(f"{k}={v}" for k, v in sorted(balance.items()))
            + "`"
        )
        parts.append(balance_line)
        parts.append("")
        header = (
            "| 职业 | 地图 | exp | exp/h | 击杀 | 掉落 | 金币 | 药耗 | 死亡 |"
            " hp_min | 到Lv2 | 预估1→7 |"
        )
        parts.append(header)
        parts.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for cell in raw.get("cells", []):
            parts.append(_cell_row(cell, exp_need_lv7))
        parts.append("")
        parts.append("### 断言")
        parts.append("")
        assertions = raw.get("assertions", [])
        if not assertions:
            parts.append("（无）")
        for a in assertions:
            mark = "PASS" if a["passed"] else "FAIL"
            parts.append(f"- [{mark}] {a['check']} {a['subject']}: {a['detail']}")
        failed = [a for a in assertions if not a["passed"]]
        parts.append("")
        summary_line = f"断言合计：{len(assertions) - len(failed)} 过 / {len(failed)} 败"
        parts.append(summary_line)
        parts.append("")
    if len(runs) >= 2:
        parts.extend(_comparison_section(runs[0], runs[-1], exp_need_lv7))
    REPORT_MD.write_text("\n".join(parts), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    exp_need_lv7 = _exp_need_1_to(7)
    print(f"[matrix] levels.json 1→7 累计经验需求 = {exp_need_lv7}", flush=True)

    only: set[tuple[str, str]] = set()
    for spec in args.only:
        class_part, _, zone_part = spec.partition(":")
        only.add((class_part, zone_part))

    run = RunResult(
        label=str(args.label),
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        seconds=int(args.seconds),
        balance_snapshot=_balance_snapshot(),
    )

    def _run_zone(zone_id: str, zone_name: str) -> list[CellResult]:
        cells: list[CellResult] = []
        for class_ in CLASSES:  # 同图串行
            if only and (class_, zone_id) not in only:
                continue
            cells.append(
                run_cell(
                    class_,
                    zone_id,
                    zone_name,
                    label=str(args.label),
                    seconds=int(args.seconds),
                    potions=int(args.potions),
                    sun_waters=int(args.sun_waters),
                    base_url=str(args.base_url),
                )
            )
        return cells

    with ThreadPoolExecutor(max_workers=len(ZONES)) as pool:  # 跨图并行
        for cells in pool.map(lambda z: _run_zone(*z), ZONES):
            run.cells.extend(cells)
    run.cells.sort(key=lambda c: (c.zone_id, c.class_))

    evaluate(run, exp_need_lv7)

    record: RunRecord = {
        "label": run.label,
        "started_at": run.started_at,
        "seconds": run.seconds,
        "balance_snapshot": run.balance_snapshot,
        "cells": [c.to_record() for c in run.cells],
        "assertions": [a.to_record() for a in run.assertions],
    }
    with RUNS_JSONL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    rebuild_report(exp_need_lv7)
    print(f"[matrix] report -> {REPORT_MD}", flush=True)

    failed = [a for a in run.assertions if not a.passed]
    for a in failed:
        print(f"[matrix] FAIL {a.check} {a.subject}: {a.detail}", flush=True)
    if any(c.error for c in run.cells):
        return EXIT_COLLECT
    return EXIT_ASSERT if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
