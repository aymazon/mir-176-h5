"""build_maps.py — task 21 全量地图数据构建器（手录，源 DB 不含地图数据）。

新增 8 区域地图（现有 5 张不动），并对既有 2 张做**增量**字段追加：
- biqi-wild.adjacent += [biqi-mine, snake-valley]
- shouren-gu-3.boss_spawns += [skeleton_elf]（骷髅精灵=骷髅洞三层 BOSS）

出处纪律（docs/MVP/README.md §6 表 :241-252 + §10 :318-324）：
- 等级带（level_min/level_max）：按 §6 表逐区域手录；
- 连通邻接（adjacent）：按 §10 传送/跑图结构与 §6 练级动线
  （新手村→比奇→骷髅洞/僵尸洞→毒蛇山谷→盟重→蜈蚣洞→石墓→沃玛寺庙→祖玛寺庙）
  手录，双向边；具体邻接取舍为本项目自创（文档只给动线顺序）；
- boss_spawns：按 §6 BOSS 列落位。
- 置信度：等级带=high（文档表格直录）；邻接=medium（动线推导+自创补全）。

用法：cd backend && uv run python scripts/build_maps.py [--dry-run]
"""

# pyright: reportUnusedCallResult=false, reportImplicitRelativeImport=false

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.gamedata_schema.maps import MapDef

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_MAPS_DIR = _BACKEND_ROOT / "gamedata" / "maps"

# 新增 8 区域（§6 表剩余行；region 为 slug）
NEW_MAPS: Final[list[dict[str, Any]]] = [
    {
        "id": "biqi-mine",
        "name": "比奇矿区（僵尸洞）",
        "level_min": 12,
        "level_max": 20,
        "region": "biqi_mine",
        "adjacent": ["biqi-wild"],
        "boss_spawns": ["corpse_king"],
    },
    {
        "id": "snake-valley",
        "name": "毒蛇山谷",
        "level_min": 15,
        "level_max": 22,
        "region": "snake_valley",
        "adjacent": ["biqi-wild", "mengzhong"],
        "boss_spawns": [],
    },
    {
        "id": "mengzhong",
        "name": "盟重土城周边",
        "level_min": 18,
        "level_max": 26,
        "region": "mengzhong",
        "adjacent": ["snake-valley", "centipede-cave", "wooma-forest", "zuma-caves"],
        "boss_spawns": [],
    },
    {
        "id": "centipede-cave",
        "name": "蜈蚣洞（死亡山谷）",
        "level_min": 20,
        "level_max": 30,
        "region": "death_valley",
        "adjacent": ["mengzhong", "shimen"],
        "boss_spawns": ["evil_clamp_worm", "touch_dragon_god"],
    },
    {
        "id": "shimen",
        "name": "石墓（猪洞）",
        "level_min": 24,
        "level_max": 34,
        "region": "shimen",
        "adjacent": ["centipede-cave"],
        "boss_spawns": ["wild_boar_white", "corpse_overlord"],
    },
    {
        "id": "wooma-forest",
        "name": "沃玛森林",
        "level_min": 25,
        "level_max": 30,
        "region": "wooma_forest",
        "adjacent": ["mengzhong", "wooma-temple"],
        "boss_spawns": [],
    },
    {
        "id": "wooma-temple",
        "name": "沃玛寺庙",
        "level_min": 28,
        "level_max": 36,
        "region": "wooma_temple",
        "adjacent": ["wooma-forest", "zuma-caves"],
        "boss_spawns": ["wooma_taurus"],
    },
    {
        "id": "zuma-caves",
        "name": "祖玛寺庙",
        "level_min": 32,
        "level_max": 40,
        "region": "zuma_temple",
        "adjacent": ["mengzhong", "wooma-temple"],
        "boss_spawns": ["zuma_taurus"],
    },
]

# 既有地图的增量追加（不覆盖其他字段）
PATCHES: Final[dict[str, dict[str, list[str]]]] = {
    "biqi-wild": {"adjacent_add": ["biqi-mine", "snake-valley"]},
    "shouren-gu-3": {"boss_spawns_add": ["skeleton_elf"]},
}


def write_map(raw: dict[str, Any], dry_run: bool) -> None:
    path = _MAPS_DIR / f"{raw['id']}.json"
    if dry_run:
        print(f"[dry] {path.name}")
        return
    model = MapDef.model_validate(raw)
    path.write_text(
        json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    for raw in NEW_MAPS:
        path = _MAPS_DIR / f"{raw['id']}.json"
        if path.is_file():
            print(f"[skip] {path.name} 已存在")
            continue
        write_map(raw, dry_run)
    patched = 0
    for map_id, patch in PATCHES.items():
        path = _MAPS_DIR / f"{map_id}.json"
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        for add in patch.get("adjacent_add", []):
            if add not in raw["adjacent"]:
                raw["adjacent"].append(add)
                changed = True
        for add in patch.get("boss_spawns_add", []):
            if add not in raw["boss_spawns"]:
                raw["boss_spawns"].append(add)
                changed = True
        if changed:
            write_map(raw, dry_run)
            patched += 1
    print(f"[build] new={len(NEW_MAPS)} patched={patched}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
