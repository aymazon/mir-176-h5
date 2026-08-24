"""check_skill_learn_levels.py — task 20 断言：32 技能习得等级与 README §4 表逐项一致。

解析 docs/MVP/README.md §4（职业与技能）三张表的「技能 | 习得等级」列，
与 gamedata/skills/*.json 的 name/learn_level 双向比对：
- README 表中的每个技能必须在 gamedata 中存在且等级一致；
- gamedata 中不得出现 README §4 之外的技能（防录 1.8+ 技能混入）。

QA failure 场景：把魔法盾 learn_level 改 30 → 报出 文件 + 字段 + 两边数值。

用法：cd backend && uv run python scripts/check_skill_learn_levels.py
退出码：0 = 全部一致；1 = 存在差异。
"""

# pyright: reportUnusedCallResult=false

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_README = _REPO_ROOT / "docs" / "MVP" / "README.md"

_ROW_RE = re.compile(r"^\|([^|]+)\|\s*(\d+)\s*\|")


def readme_learn_levels() -> dict[str, int]:
    """README §4 三张表 → {技能名: 习得等级}。"""
    text = _README.read_text(encoding="utf-8")
    section = text.split("## 4.", 1)[1].split("## 5.", 1)[0]
    table: dict[str, int] = {}
    for line in section.splitlines():
        match = _ROW_RE.match(line.strip())
        if not match:
            continue
        name = match.group(1).strip()
        if name in ("技能", "---", "") or name.startswith("-"):
            continue
        table[name] = int(match.group(2))
    return table


def check(backend_root: Path, expected: dict[str, int] | None = None) -> list[str]:
    """gamedata/skills/*.json 与期望表双向比对，返回问题清单。"""
    table = readme_learn_levels() if expected is None else expected
    skills_dir = backend_root / "gamedata" / "skills"
    problems: list[str] = []
    seen_names: set[str] = set()
    for path in sorted(skills_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        name = str(raw.get("name", ""))
        level = int(raw.get("learn_level", -1))
        rel = f"skills/{path.name}"
        seen_names.add(name)
        want = table.get(name)
        if want is None:
            problems.append(f"{rel}: name '{name}' 不在 README §4 表中（疑似 1.8+ 技能混入）")
        elif level != want:
            problems.append(
                f"{rel}: learn_level={level} != README §4 期望 {want}（name={name}）"
            )
    for name, want in table.items():
        if name not in seen_names:
            problems.append(f"skills/: README §4 技能 '{name}'(L{want}) 未录入")
    return problems


def main() -> int:
    backend_root = Path(__file__).resolve().parent.parent
    table = readme_learn_levels()
    problems = check(backend_root, table)
    if problems:
        print(f"== 习得等级断言失败（README §4 共 {len(table)} 条）==")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"[ok] {len(table)} 个技能习得等级与 README §4 表逐项一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
