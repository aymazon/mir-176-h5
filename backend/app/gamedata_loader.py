"""Gamedata 启动加载器：扫描 backend/gamedata/ 8 类目录，校验失败拒绝启动（raise）。

加载分两个阶段：
1. 解析——逐文件 JSON 语法 + Pydantic 形状校验，构建各类 id 索引；
2. 交叉引用——一切引用走 id（D17），悬空 id 拒绝通过。

解析阶段有任何错误则跳过交叉引用阶段，一次性报告全部问题。
FastAPI lifespan（todo 15）在启动时调用 ``load_all()`` 一次即可。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .gamedata_schema import (
    DropEntry,
    ItemDef,
    MapDef,
    MonsterDef,
    NpcDef,
    QuestDef,
    QuestKind,
    SkillDef,
    SpawnEntry,
)
from .gamedata_schema.common import GameDataModel

DEFAULT_GAMEDATA_ROOT = Path(__file__).resolve().parent.parent / "gamedata"


class GameDataError(Exception):
    """gamedata 校验失败；message 为逐行问题清单（文件 + 字段路径 + 原因）。"""


# category -> (model, key 函数)；解析顺序即列表顺序：
# 被引用端（maps/monsters/items/skills/npcs）在前，引用发起端（quests/drops/spawns）在后。
_KeyFn = Callable[[Any], str]  # 各类模型键属性不同，键函数按模型定义


def _id_key(m: MapDef | MonsterDef | ItemDef | SkillDef | NpcDef | QuestDef) -> str:
    return m.id


def _drop_key(m: DropEntry) -> str:
    return m.monster_id


def _spawn_key(m: SpawnEntry) -> str:
    return f"{m.map_id}:{m.monster_id}"


_CATEGORIES: list[tuple[str, type[GameDataModel], _KeyFn]] = [
    ("maps", MapDef, _id_key),
    ("monsters", MonsterDef, _id_key),
    ("items", ItemDef, _id_key),
    ("skills", SkillDef, _id_key),
    ("npcs", NpcDef, _id_key),
    ("quests", QuestDef, _id_key),
    ("drops", DropEntry, _drop_key),
    ("spawns", SpawnEntry, _spawn_key),
]

# quests.target_id 按 kind 定向到被引用类别。
_QUEST_TARGET: dict[QuestKind, tuple[str, str]] = {
    QuestKind.kill: ("monsters", "kill"),
    QuestKind.collect: ("items", "collect"),
    QuestKind.dialogue: ("npcs", "dialogue"),
    QuestKind.profession: ("skills", "profession"),
}


def _loc_str(loc: tuple[object, ...]) -> str:
    if not loc:
        return "<root>"
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out


def _parse_category(
    root: Path,
    category: str,
    model: type[GameDataModel],
    key_fn: _KeyFn,
    errors: list[str],
) -> tuple[dict[str, GameDataModel], dict[str, str]]:
    """解析一类目录，返回 (key -> model, key -> 来源文件)。错误记入 errors。"""
    category_dir = root / category
    index: dict[str, GameDataModel] = {}
    key_origin: dict[str, str] = {}
    if not category_dir.is_dir():
        errors.append(f"{category}/: directory missing")
        return index, key_origin
    for path in sorted(category_dir.glob("*.json")):
        rel = f"{category}/{path.name}"
        try:
            raw: object = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(
                f"{rel}: invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}"
            )
            continue
        try:
            entry = model.model_validate(raw)
        except ValidationError as exc:
            for err in exc.errors():
                errors.append(f"{rel}: {_loc_str(err['loc'])}: {err['msg']}")
            continue
        key = key_fn(entry)
        if key in index:
            errors.append(f"{rel}: duplicate id '{key}' (already defined in {key_origin[key]})")
            continue
        index[key] = entry
        key_origin[key] = rel
    return index, key_origin


def _check_ref(
    errors: list[str],
    rel: str,
    field_path: str,
    value: str,
    target_category: str,
    target_index: dict[str, GameDataModel],
) -> None:
    if value not in target_index:
        errors.append(f"{rel}: {field_path} '{value}' not found in {target_category}")


def _check_cross_references(
    data: dict[str, dict[str, GameDataModel]],
    origins: dict[str, dict[str, str]],
    errors: list[str],
) -> None:
    maps, monsters, items, skills, npcs = (
        data["maps"],
        data["monsters"],
        data["items"],
        data["skills"],
        data["npcs"],
    )

    def rel(category: str, key: str) -> str:
        return origins[category][key]

    # maps: adjacent -> maps，boss_spawns -> monsters
    for key, m in maps.items():
        assert isinstance(m, MapDef)
        for i, ref in enumerate(m.adjacent):
            _check_ref(errors, rel("maps", key), f"adjacent[{i}]", ref, "maps", maps)
        for i, ref in enumerate(m.boss_spawns):
            _check_ref(errors, rel("maps", key), f"boss_spawns[{i}]", ref, "monsters", monsters)

    # npcs: map_id -> maps，shop_items -> items
    for key, n in npcs.items():
        assert isinstance(n, NpcDef)
        _check_ref(errors, rel("npcs", key), "map_id", n.map_id, "maps", maps)
        for i, ref in enumerate(n.shop_items):
            _check_ref(errors, rel("npcs", key), f"shop_items[{i}]", ref, "items", items)

    # drops: monster_id -> monsters，entries[*].item_id(非 null) -> items
    for key, d in data["drops"].items():
        assert isinstance(d, DropEntry)
        _check_ref(errors, rel("drops", key), "monster_id", d.monster_id, "monsters", monsters)
        for i, entry in enumerate(d.entries):
            if entry.item_id is not None:
                _check_ref(
                    errors,
                    rel("drops", key),
                    f"entries[{i}].item_id",
                    entry.item_id,
                    "items",
                    items,
                )

    # spawns: map_id -> maps，monster_id -> monsters
    for key, s in data["spawns"].items():
        assert isinstance(s, SpawnEntry)
        _check_ref(errors, rel("spawns", key), "map_id", s.map_id, "maps", maps)
        _check_ref(errors, rel("spawns", key), "monster_id", s.monster_id, "monsters", monsters)

    # quests: target_id 按 kind 定向，reward_items -> items
    quest_indexes = {"monsters": monsters, "items": items, "npcs": npcs, "skills": skills}
    for key, q in data["quests"].items():
        assert isinstance(q, QuestDef)
        target_category, _ = _QUEST_TARGET[q.kind]
        _check_ref(
            errors,
            rel("quests", key),
            "target_id",
            q.target_id,
            target_category,
            quest_indexes[target_category],
        )
        for i, ref in enumerate(q.reward_items):
            _check_ref(errors, rel("quests", key), f"reward_items[{i}]", ref, "items", items)


def load_all(
    root: Path | str | None = None,
    strict: bool = True,
) -> dict[str, dict[str, GameDataModel]]:
    """加载并校验全部 gamedata，返回 {category: {key: model}}。

    strict 预留（todo 22 扩展等级带等 lint）；基础交叉引用校验始终执行。
    任何错误（JSON 语法/缺字段/悬空 id/重复 id）都会 raise GameDataError，
    message 逐行列出 文件 + 字段路径 + 原因。
    """
    root_path = Path(root) if root is not None else DEFAULT_GAMEDATA_ROOT
    errors: list[str] = []
    data: dict[str, dict[str, GameDataModel]] = {}
    origins: dict[str, dict[str, str]] = {}

    for category, model, key_fn in _CATEGORIES:
        index, key_origin = _parse_category(root_path, category, model, key_fn, errors)
        data[category] = index
        origins[category] = key_origin

    if not errors:
        _check_cross_references(data, origins, errors)

    if errors:
        raise GameDataError(
            f"gamedata validation failed with {len(errors)} error(s):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
    return data
