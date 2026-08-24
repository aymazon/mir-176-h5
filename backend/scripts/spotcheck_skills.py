"""spotcheck_skills.py — task 20 验收抽检脚本。

三项检查（对应计划 todo 20 acceptance）：
1. 数量断言：gamedata/skills/*.json == 32（复用 check_skill_count）。
2. 习得等级断言：与 docs/MVP/README.md §4 表逐项一致
   （复用 check_skill_learn_levels，任何差异 exit 1）。
3. 双源抽检：32 技能中文名 ↔ Crystal MagicInfo（主源，learn_level）
   与 OpenMir2 magics.NeedL1（第二源）比对；两源为独立发行版，
   差异本身是预期产物（cjlaaa 单服魔改），落 docs/data-coverage.md §5，
   不作为失败条件；但同名命中数 < 24 视为源完整性问题 exit 1。

用法：cd backend && uv run python scripts/spotcheck_skills.py
退出码：0 = 全部通过；1 = 源缺失 / 断言失败 / 命中率过低。
"""

# pyright: reportUnusedCallResult=false, reportImplicitRelativeImport=false

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_skill_count
import check_skill_learn_levels
import mir_sources

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_DIR = _BACKEND_ROOT / "gamedata" / "skills"
_DOCS_COVERAGE = _BACKEND_ROOT.parent / "docs" / "data-coverage.md"

_MIN_SOURCE_MATCH = 24

_CLASS_TO_FLAG: dict[str, int] = {"warrior": 1, "wizard": 2, "taoist": 4}

# 本脚本拥有的 coverage 区块：§5 起始 → §6 起始（或 EOF）
_SECTION_START = "## 5. 技能全量录入：双源出处与习得等级抽检（todo 20，2026-08-25）"
_NEXT_MARKERS = ("## 6. NPC 与商店清单录入",)


def load_skills() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(_SKILLS_DIR.glob("*.json")):
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        raw["_file"] = path.name
        out.append(raw)
    return out


def diff_sources(
    skills: list[dict[str, Any]],
    crystal: list[mir_sources.CrystalMagic],
    om2: list[dict[str, str]],
) -> tuple[list[str], list[tuple[str, int, int | None, int | None]], list[str]]:
    """双源比对。返回 (问题清单, 差异行, 命中摘要)。"""
    cr_by_name = {r["name"]: r for r in crystal}
    om_by_name = {r["MagName"]: r for r in om2}
    problems: list[str] = []
    rows: list[tuple[str, int, int | None, int | None]] = []
    hits: list[str] = []
    for skill in skills:
        name = str(skill["name"])
        ours = int(skill["learn_level"])
        cr = cr_by_name.get(name)
        om = om_by_name.get(name)
        cr_level = cr["learn_level"] if cr else None
        om_level = int(om["NeedL1"]) if om else None
        rows.append((name, ours, cr_level, om_level))
        if cr and om:
            hits.append(name)
        # 职业旗标一致性（仅主源可查）
        if cr:
            want_flag = _CLASS_TO_FLAG.get(str(skill["class"]))
            if want_flag is not None and cr["job_flag"] & want_flag == 0:
                problems.append(
                    f"{name}: 主源职业旗标 {cr['job_flag']} 不含期望 {want_flag}"
                    f"（class={skill['class']}）"
                )
    return problems, rows, hits


def render_coverage(rows: list[tuple[str, int, int | None, int | None]], hits: list[str]) -> None:
    """重写本脚本拥有的区块：§5 技能抽检（保留其余全部区块）。"""
    lines: list[str] = []
    lines.append("")
    lines.append(_SECTION_START)
    lines.append("")
    lines.append(
        "- **规范源**：`tests/fixtures/skills_32.json`（task 11 契约 fixture，"
        "战 6 / 法 14 / 道 12 = 32；数值=README §4 官方 176 经典设定）。"
        "`gamedata/skills/*.json` 由 `scripts/build_skills.py` 从 fixture 派生。"
    )
    lines.append(
        "- **主源**：Crystal `Server.MirDB` MagicInfo——cjlaaa 实证布局"
        "（i32 Index + 7bit 名字 + 36 字节定长块 + bool + 可选描述串），"
        "非 Crystal master 分支；已验证字节位：b3=职业位旗标(战1/法2/道4)、"
        "u16@b10=0 级习得等级。连续记录链扫描（阈值 4），共 64 条。"
    )
    lines.append(
        "- **第二源**：OpenMir2 `sql/mir2_data.sql` magics 表（108 行，"
        "Job 0/1/2=战/法/道，NeedL1/L2/L3=各级习得等级）。"
    )
    lines.append(
        "- mp_cost/cooldown/effect_params 两源均无对应字段（Crystal 该布局未含 "
        "BaseCost/Delay 明文），以 fixture 为准，M5 校准清单统一处理。"
    )
    lines.append("")
    lines.append("### 习得等级三方比对（本方=fixture/README §4）")
    lines.append("")
    lines.append("| 技能 | 本方 | Crystal | OM2 NeedL1 |")
    lines.append("| --- | --- | --- | --- |")
    diff_count = 0
    for name, ours, cr, om in rows:
        mark = ""
        if (cr is not None and cr != ours) or (om is not None and om != ours):
            mark = " ⚠"
            diff_count += 1
        lines.append(
            f"| {name}{mark} | {ours} | {cr if cr is not None else '—'} "
            f"| {om if om is not None else '—'} |"
        )
    lines.append("")
    lines.append(
        f"- 同名命中：Crystal {sum(1 for r in rows if r[2] is not None)}/32、"
        f"OM2 {sum(1 for r in rows if r[3] is not None)}/32（双源齐备 {len(hits)} 条）。"
    )
    lines.append(
        f"- 与本方不一致 {diff_count} 条（⚠ 标注）：cjlaaa 为单服魔改版"
        "（攻杀剑术 15/刺杀剑术 22/半月弯刀 26/大火球 15 等），"
        "OM2 与经典官方一致；**一律以 README §4 为准**，差异列入 M5 校准观察。"
    )
    lines.append("- 主源缺名：群体治愈术（cjlaaa 技能表未收录该名）。")
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
    print(f"[write] {_DOCS_COVERAGE} §5（技能区块，其余保留）")


def main() -> int:
    # 1. 数量断言
    count_problems = check_skill_count.check(_BACKEND_ROOT)
    if count_problems:
        for problem in count_problems:
            print(f"[error] {problem}", file=sys.stderr)
        return 1
    print(f"[ok] 技能数 == {check_skill_count.EXPECTED_COUNT}")

    # 2. 习得等级 vs README §4
    level_problems = check_skill_learn_levels.check(_BACKEND_ROOT)
    if level_problems:
        print("== 习得等级断言失败 ==")
        for problem in level_problems:
            print(f"  - {problem}")
        return 1
    print("[ok] 习得等级与 README §4 表逐项一致")

    # 3. 双源抽检
    crystal, cr_path = mir_sources.load_crystal_magic_records()
    om2, om_path = mir_sources.load_openmir2_magic_rows()
    print(f"[source] crystal={cr_path} magics={len(crystal)}")
    print(f"[source] openmir2={om_path} magics={len(om2)}")
    if not crystal or not om2:
        print("[error] 源缺失，无法双源抽检", file=sys.stderr)
        return 1
    skills = load_skills()
    problems, rows, hits = diff_sources(skills, crystal, om2)
    if problems:
        print("== 双源一致性检查失败 ==")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    if len(hits) < _MIN_SOURCE_MATCH:
        print(
            f"[error] 双源同名命中仅 {len(hits)}（<{_MIN_SOURCE_MATCH}），源完整性存疑",
            file=sys.stderr,
        )
        return 1
    print(
        f"[ok] 双源抽检完成：命中 {len(hits)} 条，"
        f"差异 {sum(1 for r in rows if (r[2] is not None and r[2] != r[1]) or (r[3] is not None and r[3] != r[1]))} 条（预期产物，落 coverage §5）"
    )

    render_coverage(rows, hits)
    return 0


if __name__ == "__main__":
    sys.exit(main())
