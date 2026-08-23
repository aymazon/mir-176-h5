"""Dev-only 角色种子工具（todo 7）：直接写库设定 level/exp/hp/mp/物品/技能/金币。

用法（cwd 任意，脚本自行 chdir 到 backend/ 以命中 .env）：
    uv run python scripts/seed_character.py --name <character_name> --level 35 --gold 100000
    uv run python scripts/seed_character.py --name tank --hp 500 --mp 200 \
        --item gold-ingot:99 --skill fireball:2

安全门禁（硬约束）：
1. ``ENV=prod`` 直接拒绝（退出码 3），绝不碰生产库；
2. 心跳判活用 **DB 时钟**（``SELECT now() - last_active_at``，不用 Python 时钟防异机偏移）：
   年龄 <= 70s（宽限期 60s + 落库间隔 5s + 余量 5s）视为"在线或刚过宽限期"，拒绝写入（退出码 5）。

本工具**只走独立脚本**，禁止注册为 FastAPI 路由（避免生产暴露）。
物品/技能表已存在（todo 2 迁移），技能升级玩法归 todo 28、挂机归 todo 15——本工具只负责把状态摆到位。

退出码：0 成功；2 用法错误（argparse）；3 prod 拒绝；4 角色不存在；5 心跳门禁拒绝；6 DB 错误。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Final

# 先定位 backend 根并 chdir（settings 的 env_file=".env" 相对 cwd 解析），再 import app.*。
_BACKEND_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_ROOT))
os.chdir(_BACKEND_ROOT)

import sqlalchemy
from sqlalchemy import text

from app.core.config import settings
from app.core.db import SessionLocal

# 宽限期 60s + tick 落库间隔 5s + 余量 5s；与计划 todo 7 判据一致。
HEARTBEAT_GRACE_SECONDS: Final[float] = 70.0

CLASSES: Final[tuple[str, ...]] = ("warrior", "wizard", "taoist")

EXIT_OK: Final[int] = 0
EXIT_PROD: Final[int] = 3
EXIT_NOT_FOUND: Final[int] = 4
EXIT_HEARTBEAT: Final[int] = 5
EXIT_DB: Final[int] = 6


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seed_character",
        description="dev-only 角色种子工具：直接写库设定角色属性/物品/技能（ENV=prod 拒绝运行）",
    )
    parser.add_argument("--name", required=True, help="目标角色名（character.name，全局唯一）")
    parser.add_argument("--level", type=int, default=None)
    parser.add_argument("--exp", type=int, default=None)
    parser.add_argument("--hp", type=int, default=None)
    parser.add_argument("--mp", type=int, default=None)
    parser.add_argument("--gold", type=int, default=None)
    parser.add_argument("--map-id", default=None, help="所在地图 gamedata id")
    parser.add_argument("--bind-city", default=None, help="回城绑定城 gamedata id")
    parser.add_argument("--class", dest="class_", choices=CLASSES, default=None, help="职业")
    parser.add_argument(
        "--item",
        action="append",
        default=[],
        metavar="ITEM_ID[:QTY]",
        help="塞背包物品（可重复）；缺省 QTY=1",
    )
    parser.add_argument(
        "--skill",
        action="append",
        default=[],
        metavar="SKILL_ID[:LEVEL[:EXP]]",
        help="设定技能等级（可重复）；缺省 LEVEL=0 EXP=0",
    )
    return parser


def _parse_item(spec: str) -> tuple[str, int]:
    item_id, _, qty_text = spec.partition(":")
    if not item_id:
        raise ValueError(f"--item 格式错误: {spec!r}（应为 ITEM_ID[:QTY]）")
    return item_id, int(qty_text or 1)


def _parse_skill(spec: str) -> tuple[str, int, int]:
    parts = spec.split(":")
    if not parts[0]:
        raise ValueError(f"--skill 格式错误: {spec!r}（应为 SKILL_ID[:LEVEL[:EXP]]）")
    level = int(parts[1]) if len(parts) > 1 else 0
    exp = int(parts[2]) if len(parts) > 2 else 0
    return parts[0], level, exp


def _refuse(message: str, code: int) -> int:
    print(f"[seed] {message}", file=sys.stderr)
    return code


async def seed(args: argparse.Namespace) -> int:
    # 门禁 1：prod 拒绝（在任何 DB 访问之前）。
    if settings.ENV == "prod":
        return _refuse("refusing to run in prod (ENV=prod)", EXIT_PROD)

    field_args: dict[str, object] = {
        "level": args.level,
        "exp": args.exp,
        "hp": args.hp,
        "mp": args.mp,
        "gold": args.gold,
        "map_id": args.map_id,
        "bind_city": args.bind_city,
        "class": args.class_,
    }
    updates = {col: value for col, value in field_args.items() if value is not None}
    items = [_parse_item(spec) for spec in args.item]
    skills = [_parse_skill(spec) for spec in args.skill]
    if not updates and not items and not skills:
        build_parser().error("至少提供一个要修改的字段（--level/--gold/--item/--skill ...）")

    try:
        async with SessionLocal() as db:
            # 门禁 2：心跳判活必须用 DB 时钟（now()），防异机时钟偏移。
            row = (
                await db.execute(
                    text(
                        "SELECT id, level, gold, "
                        "EXTRACT(EPOCH FROM (now() - last_active_at)) AS age_seconds "
                        "FROM character WHERE name = :name"
                    ),
                    {"name": args.name},
                )
            ).first()
            if row is None:
                return _refuse(f"角色不存在: {args.name}", EXIT_NOT_FOUND)

            age = float(row.age_seconds)
            if age <= HEARTBEAT_GRACE_SECONDS:
                return _refuse(
                    f"角色在线或刚过宽限期（last_active_at 距今 {age:.1f}s <= {HEARTBEAT_GRACE_SECONDS:.0f}s），"
                    "请等待至心跳超过 70s 后再 seed",
                    EXIT_HEARTBEAT,
                )

            before = {"level": row.level, "gold": row.gold}
            params: dict[str, object] = {"character_id": row.id}
            if updates:
                sets = ", ".join(f"{col} = :{col}" for col in updates)
                params.update(updates)
                await db.execute(text(f"UPDATE character SET {sets} WHERE id = :character_id"), params)

            for item_id, qty in items:
                stacked = (
                    await db.execute(
                        text(
                            "SELECT id, quantity FROM item_instance "
                            "WHERE character_id = :character_id AND item_id = :item_id AND quantity > 1"
                        ),
                        {"character_id": row.id, "item_id": item_id},
                    )
                ).first()
                if stacked is None:
                    await db.execute(
                        text(
                            "INSERT INTO item_instance (character_id, item_id, quantity) "
                            "VALUES (:character_id, :item_id, :qty)"
                        ),
                        {"character_id": row.id, "item_id": item_id, "qty": qty},
                    )
                else:
                    await db.execute(
                        text("UPDATE item_instance SET quantity = quantity + :qty WHERE id = :row_id"),
                        {"qty": qty, "row_id": stacked.id},
                    )

            for skill_id, s_level, s_exp in skills:
                existing = (
                    await db.execute(
                        text(
                            "SELECT id FROM skill_state "
                            "WHERE character_id = :character_id AND skill_id = :skill_id"
                        ),
                        {"character_id": row.id, "skill_id": skill_id},
                    )
                ).first()
                if existing is None:
                    await db.execute(
                        text(
                            "INSERT INTO skill_state (character_id, skill_id, level, exp) "
                            "VALUES (:character_id, :skill_id, :s_level, :s_exp)"
                        ),
                        {
                            "character_id": row.id,
                            "skill_id": skill_id,
                            "s_level": s_level,
                            "s_exp": s_exp,
                        },
                    )
                else:
                    await db.execute(
                        text(
                            "UPDATE skill_state SET level = :s_level, exp = :s_exp WHERE id = :row_id"
                        ),
                        {"s_level": s_level, "s_exp": s_exp, "row_id": existing.id},
                    )

            after_row = (
                await db.execute(
                    text(
                        "SELECT level, exp, hp, mp, gold, map_id, bind_city, class AS class_name "
                        "FROM character WHERE id = :character_id"
                    ),
                    {"character_id": row.id},
                )
            ).one()
            await db.commit()
    except sqlalchemy.exc.SQLAlchemyError as exc:
        return _refuse(f"DB 错误（已回滚）：{exc}", EXIT_DB)

    print(f"[seed] OK: 角色 {args.name!r}（id={row.id}）")
    if updates:
        print(f"[seed]   level: {before['level']} -> {after_row.level}, gold: {before['gold']} -> {after_row.gold}")
        print(f"[seed]   全量: exp={after_row.exp} hp={after_row.hp} mp={after_row.mp} "
              f"map={after_row.map_id} bind={after_row.bind_city} class={after_row.class_name}")
    for item_id, qty in items:
        print(f"[seed]   item {item_id} x{qty} 已入包")
    for skill_id, s_level, s_exp in skills:
        print(f"[seed]   skill {skill_id} -> lv{s_level} exp{s_exp}")
    print(f"[seed]   心跳年龄 {age:.1f}s（> 70s，门禁通过）")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(seed(args))


if __name__ == "__main__":
    sys.exit(main())
