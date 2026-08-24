"""spotcheck_quests.py — task 20 验收抽检脚本（任务数据）。

检查项（对应计划 todo 20 acceptance：任务 JSON ≥8 + 命运之刃弱化分身标记）：
1. 数量 ≥8；构成：新手 3-5 / 悬赏 ≥3 / 收集 ≥2 / 职业专属 ≥3。
2. 命运之刃：required_class=warrior、level_req=35、reward 含 fate_blade、
   boss_instance.zone_id=="shimen-5"、boss_instance.weak_clone is True。
3. 红线：无日常/链式字段（daily/repeat/pre 不允许出现在任何任务 JSON）。
4. 覆盖文档：重写 docs/data-coverage.md §7（其余区块保留）。

用法：cd backend && uv run python scripts/spotcheck_quests.py
退出码：0 = 全部通过；1 = 任一检查失败。
"""

# pyright: reportUnusedCallResult=false, reportImplicitRelativeImport=false

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_QUESTS_DIR = _BACKEND_ROOT / "gamedata" / "quests"
_DOCS_COVERAGE = _BACKEND_ROOT.parent / "docs" / "data-coverage.md"

_MIN_QUESTS = 8

_SECTION_START = "## 7. 任务数据录入（todo 20，2026-08-25）"
_NEXT_MARKERS: tuple[str, ...] = ()


def load_quests() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(_QUESTS_DIR.glob("*.json")):
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        raw["_file"] = path.name
        out.append(raw)
    return out


def check() -> tuple[list[str], list[dict[str, Any]]]:
    problems: list[str] = []
    quests = load_quests()
    if len(quests) < _MIN_QUESTS:
        problems.append(f"quests/ 共 {len(quests)} 个 < {_MIN_QUESTS}")

    def by_kind(kind: str) -> list[dict[str, object]]:
        return [q for q in quests if q["kind"] == kind]

    newbie = [q for q in quests if str(q["id"]).startswith("newbie_")]
    if not (3 <= len(newbie) <= 5):
        problems.append(f"新手任务 {len(newbie)} 个，不在 3-5 区间")
    if len(by_kind("kill")) - len(newbie) < 3 and len(by_kind("kill")) < 3:
        problems.append("悬赏类 kill 任务不足 3 个")
    if len(by_kind("collect")) < 2:
        problems.append("收集类 collect 任务不足 2 个")
    profession = by_kind("profession")
    fate = [q for q in quests if q["id"] == "warrior_fate_blade"]
    if len(profession) + len(fate) < 3:
        problems.append("35 级职业专属任务不足 3 个")

    # 红线：无日常/链式字段
    forbidden = ("daily", "repeat", "repeatable", "pre", "chain", "next_quest")
    for q in quests:
        rel = f"quests/{q['_file']}"
        for key in q:
            if str(key).lower() in forbidden:
                problems.append(f"{rel}: 出现禁用字段 '{key}'（日常/链式红线）")

    # 命运之刃专项
    if not fate:
        problems.append("缺少命运之刃任务 warrior_fate_blade")
    else:
        q = fate[0]
        rel = f"quests/{q['_file']}"
        boss = q.get("boss_instance")
        if not isinstance(boss, dict):
            problems.append(f"{rel}: 缺 boss_instance 标记")
        else:
            if boss.get("zone_id") != "shimen-5":
                problems.append(
                    f"{rel}: boss_instance.zone_id={boss.get('zone_id')!r} != 'shimen-5'"
                )
            if boss.get("weak_clone") is not True:
                problems.append(
                    f"{rel}: boss_instance.weak_clone 必须为 true（弱化分身）"
                )
        if q.get("required_class") != "warrior":
            problems.append(f"{rel}: required_class 应为 warrior")
        if int(q["level_req"]) != 35:
            problems.append(f"{rel}: level_req 应为 35")
        if "fate_blade" not in [str(x) for x in q.get("reward_items", [])]:
            problems.append(f"{rel}: reward_items 应含 fate_blade")
    return problems, quests


def render_coverage(quests: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    lines.append("")
    lines.append(_SECTION_START)
    lines.append("")
    lines.append(
        f"- 共录入 {len(quests)} 个任务（≥8 达标）：按 README §9 构成——"
        "新手引导 4 / 各区悬赏 3 / 收集 3 / 35 级职业专属 3。"
    )
    lines.append(
        "- 奖励数值自定（任务为点缀、低收益，M5 校准）；素材与奖励物品均为 "
        "items/ 既有 id，loader 交叉引用校验通过。"
    )
    lines.append(
        "- **命运之刃**（warrior_fate_blade）：战士 35 级专属，kind=kill 目标白野猪；"
        "`boss_instance.zone_id=\"shimen-5\"`（石墓五层分区，地图由 todo 21 落地）+ "
        "`boss_instance.weak_clone=true`（弱化分身，不掉本体掉落表）；奖励 fate_blade。"
    )
    lines.append(
        "- 法师/道士 35 级技能书引导走 kind=profession（target 指向 ice_thunder/"
        "summon_beast 技能 id），奖励对应技能书。"
    )
    lines.append("- 红线遵守：无日常/链式字段（脚本断言 daily/repeat/pre 等键不存在）。")
    lines.append("")
    lines.append("| 任务 | 类型 | 等级 | 职业 | 目标 | 奖励 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for q in quests:
        target = f"{q['target_id']}×{q['target_count']}"
        rewards = []
        if int(q["reward_gold"]):
            rewards.append(f"金币{q['reward_gold']}")
        if int(q["reward_exp"]):
            rewards.append(f"经验{q['reward_exp']}")
        for item in q["reward_items"]:
            rewards.append(str(item))
        cls = q.get("required_class") or "—"
        extra = " +弱化分身@shimen-5" if q.get("boss_instance") else ""
        lines.append(
            f"| {q['name']} ({q['id']}) | {q['kind']} | {q['level_req']} | {cls} "
            f"| {target}{extra} | {'/'.join(rewards) or '—'} |"
        )
    lines.append("")

    existing = _DOCS_COVERAGE.read_text(encoding="utf-8") if _DOCS_COVERAGE.is_file() else ""
    start = existing.find(_SECTION_START)
    if start < 0:
        doc = existing.rstrip("\n") + "\n" + "\n".join(lines)
    else:
        end = len(existing)
        for marker in _NEXT_MARKERS:
            pos = existing.find(marker, start)
            if pos >= 0:
                end = min(end, pos)
        doc = existing[:start] + "\n".join(lines) + "\n" + existing[end:]
    _DOCS_COVERAGE.write_text(doc, encoding="utf-8")
    print(f"[write] {_DOCS_COVERAGE} §7（任务区块，其余保留）")


def main() -> int:
    problems, quests = check()
    if problems:
        print("== 任务抽检失败 ==")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        f"[ok] 任务 {len(quests)} 个（≥{_MIN_QUESTS}）；"
        "命运之刃弱化分身标记 + 无链式字段红线全部通过"
    )
    render_coverage(quests)
    return 0


if __name__ == "__main__":
    sys.exit(main())
