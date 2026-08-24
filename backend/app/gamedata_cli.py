"""Gamedata 校验 CLI。

用法：
    uv run python -m app.gamedata_cli validate [--strict | --no-strict] [--root PATH]

退出码：0 = 校验通过；1 = 校验失败（明细输出到 stderr）。

--strict（todo 22 门禁）：在 loader 结构校验之上追加全部语义 lint
（task 19 新手装门槛 + todo 22 五条规则），任一失败 exit 1；
默认非 strict，只跑 loader 结构校验（形状/引用/重复 id）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

from .gamedata_lint import (
    lint_birth_map_exists,
    lint_cross_refs_integrity,
    lint_id_only_references,
    lint_monster_level_in_band,
    lint_newbie_equipment_thresholds,
    lint_weapon_level_sequence,
    weapon_level_sequence_report,
)
from .gamedata_loader import DEFAULT_GAMEDATA_ROOT, GameDataError, load_all
from .gamedata_schema import MapDef, MonsterDef, SpawnEntry
from .gamedata_schema.common import GameDataModel
from .gamedata_schema.items import ItemDef


def _run_strict_lints(
    data: dict[str, dict[str, GameDataModel]],
    root: Path,
) -> tuple[list[str], list[str]]:
    """strict 模式全量 lint：task 19 规则 + todo 22 五条规则。

    返回 (problems, info_lines)：problems 任一非空 → exit 1；
    info_lines 为信息性 diff 报告（打印 stdout，不影响退出码）。
    """
    maps = cast("dict[str, MapDef]", data["maps"])
    monsters = cast("dict[str, MonsterDef]", data["monsters"])
    items = cast("dict[str, ItemDef]", data["items"])
    spawns = cast("dict[str, SpawnEntry]", data["spawns"])

    problems: list[str] = []
    problems.extend(lint_cross_refs_integrity(data))
    problems.extend(lint_birth_map_exists(maps))
    problems.extend(lint_monster_level_in_band(spawns, monsters, maps))
    problems.extend(lint_weapon_level_sequence(items))
    problems.extend(lint_id_only_references(root))
    problems.extend(lint_newbie_equipment_thresholds(items))
    info_lines = weapon_level_sequence_report(items)
    return problems, info_lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gamedata_cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate_parser = sub.add_parser("validate", help="校验 gamedata 全部 JSON")
    validate_parser.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="追加全量语义 lint 门禁（task 19 + todo 22 六条规则）；默认仅结构校验",
    )
    validate_parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=f"gamedata 根目录，默认 {DEFAULT_GAMEDATA_ROOT}",
    )
    args = parser.parse_args(argv)

    if args.command == "validate":
        try:
            data = load_all(root=args.root, strict=args.strict)
        except GameDataError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        root_path = args.root if args.root is not None else DEFAULT_GAMEDATA_ROOT
        info_lines: list[str] = []
        if args.strict:
            # todo 22 门禁：全部 lint 规则，任一失败 exit 1
            lint_problems, info_lines = _run_strict_lints(data, root_path)
            if lint_problems:
                print(
                    f"gamedata strict lint failed with {len(lint_problems)} problem(s):",
                    file=sys.stderr,
                )
                for problem in lint_problems:
                    print(f"  - {problem}", file=sys.stderr)
                return 1
        counts = ", ".join(f"{cat}={len(entries)}" for cat, entries in data.items())
        mode = "strict" if args.strict else "structural"
        print(f"OK: gamedata validation passed ({mode}) ({counts})")
        for line in info_lines:
            print(line)
        return 0
    parser.error(f"unknown command: {args.command}")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
