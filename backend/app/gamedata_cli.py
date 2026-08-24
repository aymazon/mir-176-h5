"""Gamedata 校验 CLI。

用法：
    uv run python -m app.gamedata_cli validate [--strict | --no-strict] [--root PATH]

退出码：0 = 校验通过；1 = 校验失败（明细输出到 stderr）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

from .gamedata_lint import lint_newbie_equipment_thresholds
from .gamedata_loader import DEFAULT_GAMEDATA_ROOT, GameDataError, load_all
from .gamedata_schema.items import ItemDef


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gamedata_cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate_parser = sub.add_parser("validate", help="校验 gamedata 全部 JSON")
    validate_parser.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="启用严格 lint（todo 22 扩展等级带等规则；基础交叉引用始终执行），默认开启",
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
        # 语义 lint（todo 19 起步：新手装门槛；todo 22 扩展等级带等规则并接 --strict 门控）
        lint_problems = lint_newbie_equipment_thresholds(
            cast("dict[str, ItemDef]", data["items"])
        )
        if lint_problems:
            print(
                f"gamedata lint failed with {len(lint_problems)} problem(s):",
                file=sys.stderr,
            )
            for problem in lint_problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        counts = ", ".join(f"{cat}={len(entries)}" for cat, entries in data.items())
        print(f"OK: gamedata validation passed ({counts})")
        return 0
    parser.error(f"unknown command: {args.command}")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
