"""build_spawns.py — task 21 全地图刷怪点构建器。

为 13 张地图（现有 5 + 新增 8）补齐刷怪点，写入 gamedata/spawns/
{map_id}_{monster_id}.json。现有 16 个最小集文件默认跳过（--force 才覆盖）。

设计依据：
- 怪物构成按 docs/MVP/README.md §6「主要怪物/BOSS」列对号入座；
- respawn_seconds 一律从 gamedata/monsters/*.json 回读（单一来源，
  BOSS=3600s / 精英=1800s / 普通 90–150s，与 task 18 录入一致）；
- density=同时存活上限（zone.fill_initial 消费），count≈2×density 为
  设计在册人口（信息字段）；locations 为点位轮转池（round-robin）。
- 等级带与怪物 Crystal 等级存在系统性错位（私服源等级膨胀，如尸王 L50
  对矿区带 12–20），按主题归属放置；错位清单落 coverage §9 列 M5 观察。

用法：cd backend && uv run python scripts/build_spawns.py [--dry-run] [--force]
"""

# pyright: reportUnusedCallResult=false, reportImplicitRelativeImport=false

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.gamedata_schema.spawns import SpawnEntry

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_SPAWNS_DIR = _BACKEND_ROOT / "gamedata" / "spawns"
_MONSTERS_DIR = _BACKEND_ROOT / "gamedata" / "monsters"

# 点位轮转偏移表：以锚点为中心的确定性散布（坐标钳制 ≥0）
_OFFSETS: Final[tuple[tuple[int, int], ...]] = (
    (0, 0), (22, -16), (-18, 24), (26, 20), (-24, -20),
    (40, -32), (32, 36), (-42, 28),
)


def locs(cx: int, cy: int, n: int) -> list[dict[str, int]]:
    out = [
        {"x": max(0, cx + ox), "y": max(0, cy + oy)}
        for ox, oy in _OFFSETS[:n]
    ]
    assert len(out) == n
    return out


class SpawnSpec:
    """一条刷怪点规格（respawn 从 monsters/ 回读，不在此硬编码）。"""

    def __init__(
        self,
        map_id: str,
        monster_id: str,
        density: int,
        count: int,
        locations: list[dict[str, int]],
    ) -> None:
        self.map_id = map_id
        self.monster_id = monster_id
        self.density = density
        self.count = count
        self.locations = locations


def boss(map_id: str, monster_id: str, x: int, y: int) -> SpawnSpec:
    return SpawnSpec(map_id, monster_id, 1, 1, [{"x": x, "y": y}])


def pack(map_id: str, monster_id: str, density: int, cx: int, cy: int) -> SpawnSpec:
    return SpawnSpec(
        map_id, monster_id, density, density * 2, locs(cx, cy, min(4, density + 1))
    )


# --- 新增刷怪点（现有 16 个之外的增量） ------------------------------------------
NEW_SPAWNS: Final[list[SpawnSpec]] = [
    # 比奇野外补充（蛤蟆系）
    pack("biqi-wild", "toad", 4, 70, 60),
    pack("biqi-wild", "toad_fighter", 3, 95, 45),
    # 兽人古墓补充
    pack("shouren-gu-2", "skeleton_warrior", 3, 120, 110),
    boss("shouren-gu-3", "skeleton_elf", 150, 150),  # 骷髅精灵（骷髅洞三层）
    # 比奇矿区（僵尸洞 B1-B2 + 尸王殿）
    pack("biqi-mine", "bat", 4, 60, 55),
    pack("biqi-mine", "cave_maggot", 5, 90, 80),
    pack("biqi-mine", "jumping_bee", 4, 130, 70),
    pack("biqi-mine", "zombie", 6, 110, 120),
    pack("biqi-mine", "zombie_fighter", 3, 150, 100),
    pack("biqi-mine", "lightning_zombie", 2, 170, 140),
    boss("biqi-mine", "corpse_king", 185, 165),  # 尸王殿
    # 毒蛇山谷（山谷全域 + 蛇洞）
    pack("snake-valley", "red_snake", 5, 80, 70),
    pack("snake-valley", "tiger_snake", 4, 120, 60),
    pack("snake-valley", "venom_snake", 4, 60, 110),
    pack("snake-valley", "venom_snake_fighter", 3, 140, 100),
    pack("snake-valley", "giant_snake", 3, 160, 130),
    pack("snake-valley", "two_head_snake", 3, 175, 90),
    pack("snake-valley", "two_head_snake_demon", 2, 190, 150),  # 蛇洞深处
    # 盟重土城周边（沙漠）
    pack("mengzhong", "scorpion", 4, 90, 80),
    pack("mengzhong", "giant_scorpion", 3, 130, 65),
    pack("mengzhong", "desert_giant_worm", 3, 155, 115),
    pack("mengzhong", "horn_fly", 4, 105, 145),
    # 蜈蚣洞（死亡山谷）
    pack("centipede-cave", "centipede", 5, 85, 75),
    pack("centipede-cave", "jumping_bee", 4, 125, 60),
    pack("centipede-cave", "giant_centipede", 3, 150, 105),
    pack("centipede-cave", "black_centipede", 3, 175, 135),
    boss("centipede-cave", "evil_clamp_worm", 160, 160),  # 一线天
    boss("centipede-cave", "touch_dragon_god", 195, 175),  # 死亡棺材
    # 石墓 1-7 层（猪洞）
    pack("shimen", "wild_boar_red", 5, 80, 70),
    pack("shimen", "wild_boar_black", 4, 120, 60),
    pack("shimen", "scorpion_snake", 4, 95, 115),
    pack("shimen", "wedge_moth", 3, 145, 90),
    pack("shimen", "horn_fly", 3, 165, 125),
    pack("shimen", "evil_corpse_king", 2, 135, 150),
    boss("shimen", "wild_boar_white", 185, 170),  # 猪 7
    boss("shimen", "corpse_overlord", 200, 140),  # 石墓尸王
    # 沃玛森林
    pack("wooma-forest", "forest_yeti", 4, 85, 75),
    pack("wooma-forest", "poison_spider", 4, 125, 65),
    pack("wooma-forest", "wooma_soldier", 4, 150, 110),
    pack("wooma-forest", "ancient_wooma_warrior", 1, 180, 150),  # 精英
    # 沃玛寺庙 1-3 层
    pack("wooma-temple", "wooma_soldier", 5, 80, 70),
    pack("wooma-temple", "wooma_warrior", 4, 120, 60),
    pack("wooma-temple", "wooma_tactician", 3, 95, 115),
    pack("wooma-temple", "flaming_wooma", 3, 145, 95),
    pack("wooma-temple", "wooma_guardian", 2, 165, 135),
    boss("wooma-temple", "wooma_taurus", 190, 165),  # 教主大厅
    # 祖玛寺庙 1-7 层 + 教主之家
    pack("zuma-caves", "zuma_statue", 4, 80, 70),
    pack("zuma-caves", "zuma_guardian", 3, 118, 62),
    pack("zuma-caves", "zuma_archer", 3, 95, 112),
    pack("zuma-caves", "wedge_moth", 3, 142, 92),
    pack("zuma-caves", "horn_fly", 3, 162, 128),
    pack("zuma-caves", "zuma_guardian_elite", 1, 178, 148),  # 1400 大锤
    boss("zuma-caves", "zuma_taurus", 195, 172),  # 教主之家
]


def load_respawn_seconds() -> dict[str, int]:
    out: dict[str, int] = {}
    for path in sorted(_MONSTERS_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        out[raw["id"]] = int(raw["respawn_seconds"])
    return out


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    respawn = load_respawn_seconds()
    written = skipped = 0
    for spec in NEW_SPAWNS:
        if spec.monster_id not in respawn:
            print(f"[error] 未知怪物 {spec.monster_id}", file=sys.stderr)
            return 1
        path = _SPAWNS_DIR / f"{spec.map_id}_{spec.monster_id}.json"
        if path.is_file() and not force:
            skipped += 1
            continue
        body = {
            "map_id": spec.map_id,
            "monster_id": spec.monster_id,
            "density": spec.density,
            "respawn_seconds": respawn[spec.monster_id],
            "count": spec.count,
            "locations": spec.locations,
        }
        if dry_run:
            print(f"[dry] {path.name}")
        else:
            model = SpawnEntry.model_validate(body)
            path.write_text(
                json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
        written += 1
    print(f"[build] specs={len(NEW_SPAWNS)} written={written} skipped(existing)={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
