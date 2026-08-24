"""check_skill_count.py — task 20 断言：gamedata/skills/*.json 数量 == 32。

用法：cd backend && uv run python scripts/check_skill_count.py
退出码：0 = 恰好 32；1 = 数量不符。
"""

# pyright: reportUnusedCallResult=false

from __future__ import annotations

import sys
from pathlib import Path

EXPECTED_COUNT = 32


def check(backend_root: Path) -> list[str]:
    skills_dir = backend_root / "gamedata" / "skills"
    files = sorted(skills_dir.glob("*.json"))
    problems: list[str] = []
    if len(files) != EXPECTED_COUNT:
        problems.append(
            f"skills/ 共 {len(files)} 个 JSON != 期望 {EXPECTED_COUNT}"
        )
    return problems


def main() -> int:
    backend_root = Path(__file__).resolve().parent.parent
    problems = check(backend_root)
    if problems:
        for problem in problems:
            print(f"[error] {problem}", file=sys.stderr)
        return 1
    print(f"[ok] skills/ 技能数 == {EXPECTED_COUNT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
