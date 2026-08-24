"""build_skills.py — task 20 技能全量录入构建器。

规范来源：backend/tests/fixtures/skills_32.json（task 11 已 commit 的契约 fixture，
战 6 / 法 14 / 道 12 = 32）。gamedata/skills/*.json 全部由该 fixture 派生，
保证「fixture 与 gamedata 不一致」的红线不破。

双源交叉验证（learn_level ↔ Crystal MagicInfo / OpenMir2 magics.NeedL1）
由 spotcheck_skills.py 负责：差异是预期产物（cjlaaa 为单服魔改版），
以 fixture（= README §4 官方 176 经典设定）为准，差异落 coverage §5。

用法：cd backend && uv run python scripts/build_skills.py [--dry-run]
"""

# pyright: reportUnusedCallResult=false, reportImplicitRelativeImport=false

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.gamedata_schema.skills import SkillDef

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE = _BACKEND_ROOT / "tests" / "fixtures" / "skills_32.json"
_SKILLS_DIR = _BACKEND_ROOT / "gamedata" / "skills"

# 录入策略（对齐 task 18/19 ROSTER 惯例）：32 条全部取自 task 11 契约 fixture。
# 无同族派生、无 override——fixture 即规范源，缺源派生场景不存在。
STRATEGY = "fixture"


def load_fixture() -> dict[str, dict[str, object]]:
    raw: object = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit(f"{_FIXTURE}: 期望顶层对象")
    return raw


def build(dry_run: bool = False) -> int:
    fixture = load_fixture()
    if len(fixture) != 32:
        print(f"[error] fixture 技能数 {len(fixture)} != 32", file=sys.stderr)
        return 1
    written = 0
    for skill_id, values in sorted(fixture.items()):
        skill = SkillDef.model_validate(values)
        if skill.id != skill_id:
            print(f"[error] fixture 键 {skill_id} != id {skill.id}", file=sys.stderr)
            return 1
        payload = json.dumps(values, ensure_ascii=False, indent=2) + "\n"
        if dry_run:
            print(f"[dry-run] skills/{skill_id}.json")
            continue
        _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        out = _SKILLS_DIR / f"{skill_id}.json"
        out.write_text(payload, encoding="utf-8")
        written += 1
    print(
        f"[build] skills/ 共写入 {written} 个（策略={STRATEGY}，"
        f"规范源=tests/fixtures/skills_32.json）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(build("--dry-run" in sys.argv))
