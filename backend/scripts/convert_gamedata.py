"""gamedata 转换器（todo 13）：开源模拟器数据 → schema 合法 JSON。

数据源优先级链（fallback chain）：
  1. 本地源文件 /tmp/mir176-sources/{Monster.DB, StdItems.DB, Magic.DB, Exps.ini}
     （Suprcode/Crystal.Database 系导出，或 GeeM2 Exps.ini；原始文件放 /tmp 绝不入库）
  2. web 抓取候选镜像（urllib 标准库，总预算 30s，请求间隔 0.5s；候选 URL 可能随
     上游仓库变动失效，失效属预期，自动落入下一级）
  3. 内置 C3a 最小可信数据集（本文件 BUILT_IN_* 常量；置信度=低，待 W3 全量录入替换）

模式（--only）：
  minimal  只写内置最小数据集。零网络、零源依赖，离线可跑（todo 13 验收路径）。
  sources  只做源转换。任一类别缺源 → 逐条报"源缺失，无法转换 X"并退出 1
           （不静默伪造——fail case 实证用）。
  auto     默认。探测本地源 → 缺则 web 抓 → 仍缺的类别逐类落内置兜底并打
           [fallback] 置信度=低 警告，保下限退出 0。

输出：backend/gamedata/<category>/<id>.json + backend/gamedata/levels.json。
写后自检（所有模式）：≥3 图 / ≥10 怪 / ≥5 物品 / ≥3 技能、每图 adjacent 非空且带
等级带（level_min/level_max）、每怪有掉落表、每图 ≥1 刷怪点、
银杏村 map id == config.BIRTH_MAP_ID（跨层引用锚点，硬编码自 app.core.config）。

源文件获取路径说明：
  - Suprcode/Crystal.Database（GitHub: suprcode/Crystal.Database 等 Crystal 系镜像）
    导出 Monster.DB / StdItems.DB / Magic.DB；
  - GeeM2 引擎取 Exps.ini；
  - 放到 /tmp/mir176-sources/ 后运行 `uv run python scripts/convert_gamedata.py`。
  - SQLite（魔数 "SQLite format 3"）/ XML 两种容器均支持；其余格式报清晰错误。

约定映射（schema 见 app/gamedata_schema/，extra=forbid 故 JSON 不携带置信度字段，
置信度标注走本脚本的 stdout 与 evidence 日志）：
  - 内部 id = 拼音/英文 slug（已知怪名走 NAME_TO_SLUG 表）；schema 的 name 字段即
    中文显示名（M5 换皮映射目标）。
  - 地图"连通邻接"= schema 的 adjacent 字段；"等级带"= level_min/level_max。
  - Exps.ini → gamedata/levels.json（loader 暂不消费该文件，为 W3 经验曲线预留）。

退出码：0 成功；1 源缺失/解析失败/自检断言失败；2 用法错误。
"""

# 本脚本为数据搬运工具：源解析层大量动态 dict/sqlite 行，按仓库迁移文件先例做文件级抑制。
# pyright: reportExplicitAny=false, reportAny=false, reportImplicitStringConcatenation=false, reportUnusedCallResult=false
from __future__ import annotations

import argparse
import configparser
import json
import sqlite3
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from hashlib import md5
from pathlib import Path
from typing import Any, Final

# 先定位 backend 根并 chdir（settings 的 env_file=".env" 相对 cwd 解析），再 import app.*。
# 与 scripts/seed_character.py 同一布局。
_BACKEND_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import BIRTH_MAP_ID  # 出生城 id 唯一来源（硬锚点）

DEFAULT_SOURCE_DIR: Final[Path] = Path("/tmp/mir176-sources")
DEFAULT_GAMEDATA_ROOT: Final[Path] = _BACKEND_ROOT / "gamedata"

WEB_BUDGET_SECONDS: Final[float] = 30.0
WEB_SLEEP_SECONDS: Final[float] = 0.5
WEB_TIMEOUT_SECONDS: Final[float] = 8.0

# web 抓取候选镜像（best-effort；404/超时属预期，静默尝试下一个）
_WEB_CANDIDATES: Final[dict[str, tuple[str, ...]]] = {
    "Monster.DB": (
        "https://raw.githubusercontent.com/suprcode/Crystal.Database/main/Monster.DB",
        "https://raw.githubusercontent.com/suprcode/mir2/master/Mir2/Monster.DB",
    ),
    "StdItems.DB": (
        "https://raw.githubusercontent.com/suprcode/Crystal.Database/main/StdItems.DB",
        "https://raw.githubusercontent.com/suprcode/mir2/master/Mir2/StdItems.DB",
    ),
    "Magic.DB": (
        "https://raw.githubusercontent.com/suprcode/Crystal.Database/main/Magic.DB",
        "https://raw.githubusercontent.com/suprcode/mir2/master/Mir2/Magic.DB",
    ),
    "Exps.ini": (
        "https://raw.githubusercontent.com/suprcode/Crystal.Database/main/Exps.ini",
    ),
}

# 各类别对应的源文件名
_SOURCE_OF_CATEGORY: Final[dict[str, str]] = {
    "monsters": "Monster.DB",
    "items": "StdItems.DB",
    "skills": "Magic.DB",
    "levels": "Exps.ini",
}


class SourceMissingError(Exception):
    """源缺失/不可解析；message 面向使用者列出缺什么、去哪拿。"""


# ---------------------------------------------------------------------------
# 内置 C3a 最小可信数据集（confidence=low；数值为 mir176 公认量级的保守近似）
# ---------------------------------------------------------------------------

# 出生城 id 必须来自 config.BIRTH_MAP_ID（禁止在数据里自由生成后再 patch）。
_BIRTH_MAP: Final[dict[str, Any]] = {
    "id": BIRTH_MAP_ID,
    "name": "银杏村",
    "level_min": 0,
    "level_max": 7,
    "region": "newbie",
    "adjacent": ["biqi-wild"],
    "boss_spawns": [],
}

BUILT_IN_MAPS: Final[list[dict[str, Any]]] = [
    _BIRTH_MAP,
    {
        "id": "biqi-wild",
        "name": "比奇野外",
        "level_min": 5,
        "level_max": 15,
        "region": "bichi",
        "adjacent": [BIRTH_MAP_ID, "shouren-gu-1"],
        "boss_spawns": [],
    },
    {
        "id": "shouren-gu-1",
        "name": "兽人古墓一层",
        "level_min": 12,
        "level_max": 20,
        "region": "orc_tomb",
        "adjacent": ["biqi-wild", "shouren-gu-2"],
        "boss_spawns": [],
    },
    {
        "id": "shouren-gu-2",
        "name": "兽人古墓二层",
        "level_min": 16,
        "level_max": 24,
        "region": "orc_tomb",
        "adjacent": ["shouren-gu-1", "shouren-gu-3"],
        "boss_spawns": [],
    },
    {
        "id": "shouren-gu-3",
        "name": "兽人古墓三层",
        "level_min": 20,
        "level_max": 28,
        "region": "orc_tomb",
        "adjacent": ["shouren-gu-2"],
        "boss_spawns": ["orc_champion"],
    },
]

# 怪物字段顺序对齐 MonsterDef：id/name/level/hp/attack/defense/magic_defense/
# accuracy/agility/exp/attack_speed/move_speed/undead/boss/respawn_seconds
BUILT_IN_MONSTERS: Final[list[dict[str, Any]]] = [
    {"id": "chicken", "name": "鸡", "level": 5, "hp": 25, "attack": 3, "defense": 0,
     "magic_defense": 0, "accuracy": 5, "agility": 5, "exp": 15,
     "attack_speed": 1, "move_speed": 1, "undead": False, "boss": False,
     "respawn_seconds": 60},
    {"id": "deer", "name": "鹿", "level": 8, "hp": 40, "attack": 4, "defense": 0,
     "magic_defense": 0, "accuracy": 5, "agility": 7, "exp": 20,
     "attack_speed": 1, "move_speed": 1, "undead": False, "boss": False,
     "respawn_seconds": 60},
    {"id": "scarecrow", "name": "稻草人", "level": 10, "hp": 50, "attack": 6, "defense": 2,
     "magic_defense": 0, "accuracy": 6, "agility": 5, "exp": 25,
     "attack_speed": 1, "move_speed": 1, "undead": False, "boss": False,
     "respawn_seconds": 90},
    {"id": "hook_cat", "name": "多钩猫", "level": 11, "hp": 55, "attack": 7, "defense": 2,
     "magic_defense": 0, "accuracy": 7, "agility": 8, "exp": 30,
     "attack_speed": 1, "move_speed": 2, "undead": False, "boss": False,
     "respawn_seconds": 90},
    {"id": "rake_cat", "name": "钉耙猫", "level": 13, "hp": 65, "attack": 9, "defense": 3,
     "magic_defense": 0, "accuracy": 8, "agility": 8, "exp": 35,
     "attack_speed": 1, "move_speed": 2, "undead": False, "boss": False,
     "respawn_seconds": 90},
    {"id": "man_eating_flower", "name": "食人花", "level": 14, "hp": 70, "attack": 10,
     "defense": 3, "magic_defense": 0, "accuracy": 9, "agility": 2, "exp": 40,
     "attack_speed": 1, "move_speed": 0, "undead": False, "boss": False,
     "respawn_seconds": 120},
    {"id": "orc", "name": "半兽人", "level": 15, "hp": 90, "attack": 13, "defense": 5,
     "magic_defense": 0, "accuracy": 9, "agility": 7, "exp": 60,
     "attack_speed": 1, "move_speed": 1, "undead": False, "boss": False,
     "respawn_seconds": 120},
    {"id": "cave_bat", "name": "山洞蝙蝠", "level": 16, "hp": 80, "attack": 12, "defense": 3,
     "magic_defense": 0, "accuracy": 10, "agility": 14, "exp": 65,
     "attack_speed": 2, "move_speed": 3, "undead": False, "boss": False,
     "respawn_seconds": 90},
    {"id": "cave_maggot", "name": "洞蛆", "level": 17, "hp": 95, "attack": 14, "defense": 6,
     "magic_defense": 2, "accuracy": 9, "agility": 3, "exp": 70,
     "attack_speed": 1, "move_speed": 1, "undead": False, "boss": False,
     "respawn_seconds": 120},
    {"id": "skeleton", "name": "骷髅", "level": 18, "hp": 110, "attack": 16, "defense": 7,
     "magic_defense": 2, "accuracy": 10, "agility": 7, "exp": 80,
     "attack_speed": 1, "move_speed": 1, "undead": True, "boss": False,
     "respawn_seconds": 150},
    {"id": "axe_skeleton", "name": "掷斧骷髅", "level": 19, "hp": 120, "attack": 18,
     "defense": 8, "magic_defense": 2, "accuracy": 12, "agility": 7, "exp": 90,
     "attack_speed": 1, "move_speed": 1, "undead": True, "boss": False,
     "respawn_seconds": 150},
    {"id": "orc_champion", "name": "半兽勇士", "level": 25, "hp": 500, "attack": 35,
     "defense": 15, "magic_defense": 5, "accuracy": 14, "agility": 8, "exp": 600,
     "attack_speed": 1, "move_speed": 1, "undead": False, "boss": True,
     "respawn_seconds": 3600},
]

BUILT_IN_ITEMS: Final[list[dict[str, Any]]] = [
    {"id": "wooden_sword", "name": "木剑", "kind": "weapon", "need_level": 1,
     "need_strength": 0, "need_magic": 0, "need_taoism": 0, "weight": 7,
     "stackable": False, "max_durability": 4, "sell_price": 50, "buy_price": 100,
     "stats": {"dc_min": 2, "dc_max": 5}, "prefix_max": 1},
    {"id": "dagger", "name": "匕首", "kind": "weapon", "need_level": 1,
     "need_strength": 0, "need_magic": 0, "need_taoism": 0, "weight": 5,
     "stackable": False, "max_durability": 5, "sell_price": 40, "buy_price": 80,
     "stats": {"dc_min": 1, "dc_max": 4}, "prefix_max": 1},
    {"id": "cloth_robe", "name": "布衣", "kind": "armor", "need_level": 1,
     "need_strength": 0, "need_magic": 0, "need_taoism": 0, "weight": 10,
     "stackable": False, "max_durability": 6, "sell_price": 30, "buy_price": 60,
     "stats": {"ac_min": 2, "ac_max": 3}, "prefix_max": 1},
    {"id": "cloth_cap", "name": "布帽", "kind": "helmet", "need_level": 1,
     "need_strength": 0, "need_magic": 0, "need_taoism": 0, "weight": 2,
     "stackable": False, "max_durability": 3, "sell_price": 15, "buy_price": 30,
     "stats": {"ac_min": 0, "ac_max": 1}, "prefix_max": 1},
    {"id": "meat", "name": "肉", "kind": "material", "need_level": 0,
     "need_strength": 0, "need_magic": 0, "need_taoism": 0, "weight": 1,
     "stackable": True, "max_durability": 0, "sell_price": 5, "buy_price": 0,
     "stats": {}, "prefix_max": 0},
    {"id": "potion_hp_small", "name": "金创药（小量）", "kind": "potion", "need_level": 0,
     "need_strength": 0, "need_magic": 0, "need_taoism": 0, "weight": 1,
     "stackable": True, "max_durability": 0, "sell_price": 10, "buy_price": 25,
     "stats": {"hp_restore": 30}, "prefix_max": 0},
    {"id": "potion_mp_small", "name": "魔法药（小量）", "kind": "potion", "need_level": 0,
     "need_strength": 0, "need_magic": 0, "need_taoism": 0, "weight": 1,
     "stackable": True, "max_durability": 0, "sell_price": 12, "buy_price": 30,
     "stats": {"mp_restore": 20}, "prefix_max": 0},
    {"id": "sun_water", "name": "太阳水", "kind": "potion", "need_level": 0,
     "need_strength": 0, "need_magic": 0, "need_taoism": 0, "weight": 1,
     "stackable": True, "max_durability": 0, "sell_price": 40, "buy_price": 90,
     "stats": {"hp_restore": 50, "mp_restore": 30}, "prefix_max": 0},
    {"id": "book_basic_sword", "name": "基本剑术（书）", "kind": "book", "need_level": 7,
     "need_strength": 0, "need_magic": 0, "need_taoism": 0, "weight": 1,
     "stackable": True, "max_durability": 0, "sell_price": 150, "buy_price": 500,
     "stats": {}, "prefix_max": 0},
    {"id": "book_fireball", "name": "火球术（书）", "kind": "book", "need_level": 7,
     "need_strength": 0, "need_magic": 0, "need_taoism": 0, "weight": 1,
     "stackable": True, "max_durability": 0, "sell_price": 150, "buy_price": 500,
     "stats": {}, "prefix_max": 0},
    {"id": "book_healing", "name": "治愈术（书）", "kind": "book", "need_level": 7,
     "need_strength": 0, "need_magic": 0, "need_taoism": 0, "weight": 1,
     "stackable": True, "max_durability": 0, "sell_price": 150, "buy_price": 500,
     "stats": {}, "prefix_max": 0},
]

BUILT_IN_SKILLS: Final[list[dict[str, Any]]] = [
    {"id": "basic_sword", "name": "基本剑术", "class": "warrior", "learn_level": 7,
     "mp_cost": 0, "cooldown_seconds": 0, "max_level": 3,
     "level_exp_required": [100, 200, 300], "target": "enemy",
     "effect_params": {"accuracy_bonus": 3, "damage_multiplier": 1.05}},
    {"id": "fireball", "name": "火球术", "class": "wizard", "learn_level": 7,
     "mp_cost": 7, "cooldown_seconds": 1, "max_level": 3,
     "level_exp_required": [120, 240, 360], "target": "enemy",
     "effect_params": {"damage_multiplier": 1.2}},
    {"id": "healing", "name": "治愈术", "class": "taoist", "learn_level": 7,
     "mp_cost": 8, "cooldown_seconds": 2, "max_level": 3,
     "level_exp_required": [130, 260, 390], "target": "ally",
     "effect_params": {"heal_amount": 25}},
]


def _gold(weight: int, gold_min: int, gold_max: int) -> dict[str, Any]:
    return {"item_id": None, "weight": weight, "min_qty": 0, "max_qty": 0,
            "gold_min": gold_min, "gold_max": gold_max}


def _item(item_id: str, weight: int, min_qty: int = 1, max_qty: int = 1) -> dict[str, Any]:
    return {"item_id": item_id, "weight": weight, "min_qty": min_qty,
            "max_qty": max_qty, "gold_min": 0, "gold_max": 0}


# 每怪至少一条金币掉落（验收硬要求）
BUILT_IN_DROPS: Final[list[dict[str, Any]]] = [
    {"monster_id": "chicken", "entries": [
        _gold(50, 5, 20), _item("meat", 30), _item("wooden_sword", 1)]},
    {"monster_id": "deer", "entries": [
        _gold(50, 8, 25), _item("meat", 40, 1, 2)]},
    {"monster_id": "scarecrow", "entries": [
        _gold(50, 10, 30), _item("potion_hp_small", 10), _item("cloth_robe", 8)]},
    {"monster_id": "hook_cat", "entries": [
        _gold(50, 12, 35), _item("potion_hp_small", 12), _item("cloth_cap", 8)]},
    {"monster_id": "rake_cat", "entries": [
        _gold(50, 15, 40), _item("potion_mp_small", 12), _item("dagger", 5)]},
    {"monster_id": "man_eating_flower", "entries": [
        _gold(50, 15, 45), _item("meat", 25, 1, 3), _item("sun_water", 5)]},
    {"monster_id": "orc", "entries": [
        _gold(50, 20, 50), _item("potion_hp_small", 15, 1, 2), _item("cloth_robe", 6)]},
    {"monster_id": "cave_bat", "entries": [
        _gold(50, 20, 55), _item("potion_mp_small", 15), _item("book_fireball", 2)]},
    {"monster_id": "cave_maggot", "entries": [
        _gold(50, 22, 60), _item("meat", 20, 1, 3), _item("potion_hp_small", 12)]},
    {"monster_id": "skeleton", "entries": [
        _gold(50, 25, 70), _item("potion_hp_small", 15, 1, 2),
        _item("book_basic_sword", 3)]},
    {"monster_id": "axe_skeleton", "entries": [
        _gold(50, 30, 80), _item("sun_water", 8), _item("book_healing", 3)]},
    {"monster_id": "orc_champion", "entries": [
        _gold(100, 200, 500), _item("sun_water", 40, 1, 2), _item("cloth_robe", 20),
        _item("book_fireball", 10), _item("book_basic_sword", 10),
        _item("book_healing", 10)]},
]


def _loc(x: int, y: int) -> dict[str, int]:
    return {"x": x, "y": y}


# 每图至少一个刷怪点（验收硬要求）
BUILT_IN_SPAWNS: Final[list[dict[str, Any]]] = [
    {"map_id": BIRTH_MAP_ID, "monster_id": "chicken", "density": 5,
     "respawn_seconds": 60, "count": 10,
     "locations": [_loc(88, 88), _loc(105, 96), _loc(120, 110)]},
    {"map_id": "biqi-wild", "monster_id": "deer", "density": 6,
     "respawn_seconds": 60, "count": 12,
     "locations": [_loc(70, 70), _loc(90, 85), _loc(110, 60)]},
    {"map_id": "biqi-wild", "monster_id": "scarecrow", "density": 5,
     "respawn_seconds": 90, "count": 10,
     "locations": [_loc(75, 100), _loc(95, 115)]},
    {"map_id": "biqi-wild", "monster_id": "hook_cat", "density": 4,
     "respawn_seconds": 90, "count": 8,
     "locations": [_loc(120, 90), _loc(135, 105)]},
    {"map_id": "biqi-wild", "monster_id": "rake_cat", "density": 4,
     "respawn_seconds": 90, "count": 8,
     "locations": [_loc(140, 70), _loc(155, 88)]},
    {"map_id": "biqi-wild", "monster_id": "man_eating_flower", "density": 3,
     "respawn_seconds": 120, "count": 6,
     "locations": [_loc(100, 130), _loc(125, 140)]},
    {"map_id": "biqi-wild", "monster_id": "orc", "density": 4,
     "respawn_seconds": 120, "count": 8,
     "locations": [_loc(160, 120), _loc(175, 135)]},
    {"map_id": "shouren-gu-1", "monster_id": "skeleton", "density": 5,
     "respawn_seconds": 150, "count": 10,
     "locations": [_loc(30, 30), _loc(55, 45), _loc(80, 60)]},
    {"map_id": "shouren-gu-1", "monster_id": "cave_bat", "density": 4,
     "respawn_seconds": 90, "count": 8,
     "locations": [_loc(45, 70), _loc(65, 85)]},
    {"map_id": "shouren-gu-1", "monster_id": "cave_maggot", "density": 4,
     "respawn_seconds": 120, "count": 8,
     "locations": [_loc(90, 35), _loc(105, 50)]},
    {"map_id": "shouren-gu-2", "monster_id": "axe_skeleton", "density": 4,
     "respawn_seconds": 150, "count": 8,
     "locations": [_loc(40, 40), _loc(70, 55)]},
    {"map_id": "shouren-gu-2", "monster_id": "skeleton", "density": 4,
     "respawn_seconds": 150, "count": 8,
     "locations": [_loc(85, 70), _loc(110, 85)]},
    {"map_id": "shouren-gu-2", "monster_id": "cave_bat", "density": 3,
     "respawn_seconds": 90, "count": 6,
     "locations": [_loc(60, 100), _loc(95, 115)]},
    {"map_id": "shouren-gu-3", "monster_id": "orc_champion", "density": 1,
     "respawn_seconds": 3600, "count": 1,
     "locations": [_loc(150, 150)]},
    {"map_id": "shouren-gu-3", "monster_id": "axe_skeleton", "density": 3,
     "respawn_seconds": 150, "count": 6,
     "locations": [_loc(60, 60), _loc(90, 80)]},
    {"map_id": "shouren-gu-3", "monster_id": "orc", "density": 3,
     "respawn_seconds": 120, "count": 6,
     "locations": [_loc(110, 100), _loc(130, 120)]},
]

# 经验曲线兜底（confidence=low；GeeM2 Exps.ini 缺失时写入 levels.json 供 W3 参考）
BUILT_IN_LEVELS: Final[dict[str, Any]] = {
    "comment": "内置兜底经验曲线（confidence=low）。GeeM2 Exps.ini 就位后由 "
               "scripts/convert_gamedata.py 覆盖。exp_to_next = 升到下一级所需经验。",
    "levels": [
        {"level": lv, "exp_to_next": exp}
        for lv, exp in enumerate(
            [100, 200, 350, 550, 800, 1200, 1700, 2400, 3300, 4500,
             6000, 8000, 10500, 13600, 17500, 22300, 28200, 35400, 44200, 54800,
             67600, 82900, 101200, 123000, 148900, 179500, 215700, 258300, 308600, 368000],
            start=1,
        )
    ],
}


# ---------------------------------------------------------------------------
# 已知中文名 → slug 映射（源转换时保 id 稳定；未知名退化为 unknown-{hash} 并告警）
# ---------------------------------------------------------------------------
NAME_TO_SLUG: Final[dict[str, str]] = {m["name"]: m["id"] for m in BUILT_IN_MONSTERS}
NAME_TO_SLUG.update({i["name"]: i["id"] for i in BUILT_IN_ITEMS})
NAME_TO_SLUG.update({
    "鸡": "chicken", "鹿": "deer", "稻草人": "scarecrow", "多钩猫": "hook_cat",
    "钉耙猫": "rake_cat", "食人花": "man_eating_flower", "半兽人": "orc",
    "骷髅": "skeleton", "掷斧骷髅": "axe_skeleton", "山洞蝙蝠": "cave_bat",
    "洞蛆": "cave_maggot", "半兽勇士": "orc_champion",
    "木剑": "wooden_sword", "匕首": "dagger", "布衣(男)": "cloth_robe",
    "布衣(女)": "cloth_robe", "布衣": "cloth_robe",
    "金创药": "potion_hp_small", "魔法药": "potion_mp_small", "太阳水": "sun_water",
    "基本剑术": "basic_sword", "火球术": "fireball", "治愈术": "healing",
})


def slug_for(name: str) -> str:
    """中文显示名 → 内部 slug；未知名给稳定 hash slug 并由调用方告警。"""
    if name in NAME_TO_SLUG:
        return NAME_TO_SLUG[name]
    return f"unknown-{md5(name.encode('utf-8')).hexdigest()[:8]}"


# ---------------------------------------------------------------------------
# 源探测 / web 抓取
# ---------------------------------------------------------------------------

def probe_sources(source_dir: Path) -> dict[str, Path | None]:
    """逐类别探测本地源文件；缺失记 None。"""
    found: dict[str, Path | None] = {}
    for category, filename in _SOURCE_OF_CATEGORY.items():
        path = source_dir / filename
        found[category] = path if path.is_file() else None
    return found


def web_fetch_missing(source_dir: Path, missing: set[str]) -> set[str]:
    """尝试把缺失源抓到 source_dir；返回仍缺失的类别集合。

    urllib 标准库实现（零新增依赖）；总预算 WEB_BUDGET_SECONDS，请求间隔 0.5s。
    """
    deadline = time.monotonic() + WEB_BUDGET_SECONDS
    still_missing: set[str] = set()
    source_dir.mkdir(parents=True, exist_ok=True)
    for category in sorted(missing):
        filename = _SOURCE_OF_CATEGORY[category]
        ok = False
        for url in _WEB_CANDIDATES.get(filename, ()):
            if time.monotonic() > deadline:
                break
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "mir176-h5-converter/0.1"})
                with urllib.request.urlopen(req, timeout=WEB_TIMEOUT_SECONDS) as resp:
                    data = resp.read()
                if not data:
                    continue
                (source_dir / filename).write_bytes(data)
                print(f"[web] {filename} <- {url} ({len(data)} bytes)")
                ok = True
                break
            except Exception as exc:  # noqa: BLE001 —— 任何抓取失败都只是换下一候选
                print(f"[web] {filename} <- {url} 失败：{exc}")
            time.sleep(WEB_SLEEP_SECONDS)
        if not ok:
            still_missing.add(category)
    return still_missing


# ---------------------------------------------------------------------------
# 源解析（SQLite / XML / INI；无法识别的格式报清晰错误，绝不静默伪造）
# ---------------------------------------------------------------------------

_SQLITE_MAGIC: Final[bytes] = b"SQLite format 3\x00"

# 列名模糊匹配规则（小写子串 → 规范字段），按声明顺序首个命中生效
_MONSTER_COL_RULES: Final[tuple[tuple[str, str], ...]] = (
    ("name", "name"), ("chr_name", "name"),
    ("level", "level"), ("lv", "level"),
    ("hp", "hp"),
    ("exp", "exp"),
    ("dc", "attack"), ("attack", "attack"),
    ("ac", "defense"), ("defen", "defense"),
    ("mac", "magic_defense"), ("magic_def", "magic_defense"),
    ("hit", "accuracy"), ("acc", "accuracy"),
    ("speed", "attack_speed"),
    ("walk", "move_speed"), ("spd", "move_speed"),
)


def _fuzzy_map_columns(columns: list[str], rules: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """列名 → 规范字段名映射；无法识别的列忽略。"""
    mapping: dict[str, str] = {}
    lowered = [(col, col.lower()) for col in columns]
    for needle, field in rules:
        if field in mapping:
            continue
        for col, low in lowered:
            if needle in low:
                mapping[field] = col
                break
    return mapping


def _require(mapping: dict[str, str], fields: set[str], what: str) -> None:
    absent = fields - mapping.keys()
    if absent:
        raise SourceMissingError(
            f"{what}：可识别列缺 {sorted(absent)}（现有列无法模糊匹配）。"
            "该源格式不受支持——请改用 Suprcode/Crystal.Database 标准导出，"
            "或运行 --only minimal 使用内置兜底数据集。"
        )


def _to_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value).strip())  # DB 值可能为 int/float/str/None
    except (TypeError, ValueError):
        return default


def _parse_monster_rows(rows: list[dict[str, Any]], mapping: dict[str, str]) -> list[dict[str, Any]]:
    monsters: list[dict[str, Any]] = []

    def col(row: dict[str, Any], field: str) -> Any:
        # 模糊映射是稀疏的：源表缺列时该字段取默认值（宁缺勿假）
        key = mapping.get(field)
        return row.get(key) if key else None

    for row in rows:
        raw_name = row.get(mapping["name"])
        name = str(raw_name or "").strip()
        if not name:
            continue
        sid = slug_for(name)
        if sid.startswith("unknown-"):
            print(f"[warn] 未知名 {name!r} → {sid}（请补 NAME_TO_SLUG 映射）")
        monsters.append({
            "id": sid, "name": name,
            "level": max(1, _to_int(col(row, "level"), 1)),
            "hp": max(1, _to_int(col(row, "hp"), 1)),
            "attack": _to_int(col(row, "attack")),
            "defense": _to_int(col(row, "defense")),
            "magic_defense": _to_int(col(row, "magic_defense")),
            "accuracy": _to_int(col(row, "accuracy")),
            "agility": _to_int(col(row, "move_speed")),
            "exp": _to_int(col(row, "exp")),
            "attack_speed": _to_int(col(row, "attack_speed"), 1),
            "move_speed": _to_int(col(row, "move_speed"), 1),
            "undead": False, "boss": False, "respawn_seconds": 90,
        })
    return monsters


def parse_monster_db(path: Path) -> list[dict[str, Any]]:
    """Monster.DB → MonsterDef 兼容 dict 列表（SQLite 或 XML 容器）。"""
    head = path.read_bytes()[: len(_SQLITE_MAGIC)]
    if head == _SQLITE_MAGIC:
        conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        try:
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
            for table in tables:
                cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
                mapping = _fuzzy_map_columns(cols, _MONSTER_COL_RULES)
                if "name" not in mapping or "hp" not in mapping:
                    continue
                _require(mapping, {"name", "hp", "exp"}, f"{path.name} 表 {table}")
                rows = [dict(zip(cols, row)) for row in conn.execute(f'SELECT * FROM "{table}"')]
                return _parse_monster_rows(rows, mapping)
        finally:
            conn.close()
        raise SourceMissingError(f"{path}：SQLite 中找不到含 name/hp 列的怪物表。")
    if head.lstrip().startswith(b"<"):
        root = ET.parse(path).getroot()
        rows: list[dict[str, Any]] = []
        for elem in root.iter():
            children = list(elem)
            if not children:
                continue
            rows.append({child.tag.lower(): child.text for child in children})
        mapping = _fuzzy_map_columns(list(rows[0].keys()) if rows else [], _MONSTER_COL_RULES)
        _require(mapping, {"name", "hp", "exp"}, f"{path.name} (XML)")
        return _parse_monster_rows(rows, mapping)
    raise SourceMissingError(
        f"{path}：既非 SQLite 也非 XML 容器，无法解析。"
        "请提供 Suprcode/Crystal.Database 标准导出，或运行 --only minimal。"
    )


_ITEM_KIND_RULES: Final[tuple[tuple[str, str], ...]] = (
    ("stdmode", "kind_raw"), ("type", "kind_raw"),
    ("need", "need_level"), ("needlevel", "need_level"),
    ("weight", "weight"), ("dur", "max_durability"),
    ("price", "sell_price"), ("stock", "buy_price"),
)


def parse_stditems_db(path: Path) -> list[dict[str, Any]]:
    """StdItems.DB → ItemDef 兼容 dict 列表（best-effort：名称/kind/价格/耐久）。"""
    head = path.read_bytes()[: len(_SQLITE_MAGIC)]
    if head != _SQLITE_MAGIC:
        raise SourceMissingError(
            f"{path}：仅支持 SQLite 容器的 StdItems.DB（XML 变体列名差异过大，宁缺勿假）。"
            "或运行 --only minimal 使用内置兜底数据集。"
        )
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            mapping = _fuzzy_map_columns(cols, (("name", "name"),) + _ITEM_KIND_RULES)
            if "name" not in mapping:
                continue
            items: list[dict[str, Any]] = []
            for row in conn.execute(f'SELECT * FROM "{table}"'):
                record = dict(zip(cols, row))
                name = str(record.get(mapping["name"]) or "").strip()
                if not name:
                    continue
                sid = slug_for(name)
                kind_raw = str(record.get(mapping.get("kind_raw", ""), "") or "").lower()
                kind = "weapon" if "sword" in kind_raw or "weapon" in kind_raw else "material"
                items.append({
                    "id": sid, "name": name, "kind": kind,
                    "need_level": _to_int(record.get(mapping.get("need_level", ""))),
                    "need_strength": 0, "need_magic": 0, "need_taoism": 0,
                    "weight": _to_int(record.get(mapping.get("weight", ""))),
                    "stackable": kind in ("potion", "book", "material", "scroll"),
                    "max_durability": _to_int(record.get(mapping.get("max_durability", ""))),
                    "sell_price": _to_int(record.get(mapping.get("sell_price", ""))),
                    "buy_price": _to_int(record.get(mapping.get("buy_price", ""))),
                    "stats": {}, "prefix_max": 0,
                })
            if items:
                return items
    finally:
        conn.close()
    raise SourceMissingError(f"{path}：SQLite 中找不到含 name 列的物品表。")


_MAGIC_COL_RULES: Final[tuple[tuple[str, str], ...]] = (
    ("name", "name"), ("job", "class_raw"), ("occupation", "class_raw"),
    ("trainlvl", "learn_level"), ("needlevel", "learn_level"), ("level", "learn_level"),
    ("defspell", "mp_cost"), ("mp", "mp_cost"), ("spell", "mp_cost"),
    ("delay", "cooldown_seconds"),
)


def parse_magic_db(path: Path) -> list[dict[str, Any]]:
    """Magic.DB → SkillDef 兼容 dict 列表（best-effort：名称/职业/耗蓝/习得级）。"""
    head = path.read_bytes()[: len(_SQLITE_MAGIC)]
    if head != _SQLITE_MAGIC:
        raise SourceMissingError(
            f"{path}：仅支持 SQLite 容器的 Magic.DB。或运行 --only minimal 使用内置兜底数据集。"
        )
    class_map = {"0": "warrior", "1": "wizard", "2": "taoist", "warrior": "warrior",
                 "wizard": "wizard", "taoist": "taoist"}
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            mapping = _fuzzy_map_columns(cols, _MAGIC_COL_RULES)
            if "name" not in mapping:
                continue
            skills: list[dict[str, Any]] = []
            for row in conn.execute(f'SELECT * FROM "{table}"'):
                record = dict(zip(cols, row))
                name = str(record.get(mapping["name"]) or "").strip()
                if not name:
                    continue
                sid = slug_for(name)
                class_raw = str(record.get(mapping.get("class_raw", ""), "") or "").strip()
                skill_class = class_map.get(class_raw, "all")
                learn_level = max(0, _to_int(record.get(mapping.get("learn_level", "")), 7))
                mp_cost = _to_int(record.get(mapping.get("mp_cost", "")))
                cooldown = _to_int(record.get(mapping.get("cooldown_seconds", "")))
                skills.append({
                    "id": sid, "name": name, "class": skill_class,
                    "learn_level": learn_level, "mp_cost": mp_cost,
                    "cooldown_seconds": cooldown, "max_level": 3,
                    "level_exp_required": [100, 200, 300],
                    "target": "enemy", "effect_params": {},
                })
            if skills:
                return skills
    finally:
        conn.close()
    raise SourceMissingError(f"{path}：SQLite 中找不到含 name 列的技能表。")


def parse_exps_ini(path: Path) -> dict[str, Any]:
    """Exps.ini（GeeM2 经验表）→ levels.json 结构。"""
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    exp_by_level: dict[int, int] = {}
    for section in parser.sections():
        for key, value in parser.items(section):
            digits = "".join(ch for ch in key if ch.isdigit())
            if not digits:
                continue
            level = int(digits)
            try:
                exp_by_level[level] = int(value.strip())
            except ValueError:
                continue
    if not exp_by_level:
        raise SourceMissingError(
            f"{path}：没有解析到任何 Level<N>=<exp> 条目，Exps.ini 格式不受支持。"
        )
    return {
        "comment": f"转换自 {path}（GeeM2 Exps.ini）。exp_to_next = 升到下一级所需经验。",
        "levels": [
            {"level": lv, "exp_to_next": exp_by_level[lv]}
            for lv in sorted(exp_by_level)
        ],
    }


# ---------------------------------------------------------------------------
# 写盘 + 自检断言
# ---------------------------------------------------------------------------

def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[write] {path.relative_to(_BACKEND_ROOT)}")


def write_dataset(root: Path, dataset: dict[str, list[dict[str, Any]]]) -> None:
    """按 loader 目录约定写 JSON：drops 以 <monster_id>_drops、spawns 以 <map>_<monster> 命名。"""
    for category in ("maps", "monsters", "items", "skills"):
        for entry in dataset[category]:
            write_json(root / category / f"{entry['id']}.json", entry)
    for entry in dataset["drops"]:
        write_json(root / "drops" / f"{entry['monster_id']}_drops.json", entry)
    for entry in dataset["spawns"]:
        write_json(
            root / "spawns" / f"{entry['map_id']}_{entry['monster_id']}.json", entry
        )


def verify_dataset(dataset: dict[str, list[dict[str, Any]]]) -> None:
    """验收断言（对应 todo 13 Acceptance 5/6）；失败抛 AssertionError 退出码 1。"""
    maps = dataset["maps"]
    monsters = dataset["monsters"]
    items = dataset["items"]
    skills = dataset["skills"]

    checks: list[tuple[str, int, int]] = [
        ("maps", len(maps), 3),
        ("monsters", len(monsters), 10),
        ("items", len(items), 5),
        ("skills", len(skills), 3),
    ]
    for label, actual, minimum in checks:
        assert actual >= minimum, f"最小数据集 {label} 数不足：{actual} < {minimum}"
        print(f"[assert] {label}={actual} >= {minimum} OK")

    birth = [m for m in maps if m["id"] == BIRTH_MAP_ID]
    assert birth, f"BIRTH_MAP_ID 锚点缺失：maps 里没有 id={BIRTH_MAP_ID} 的地图"
    print(f"[assert] BIRTH_MAP_ID 锚点 OK：maps/{BIRTH_MAP_ID}.json")

    ids = {m["id"] for m in maps}
    for m in maps:
        assert m["adjacent"], f"地图 {m['id']} 的 adjacent 为空数组（必须至少连通 1 图）"
        assert m["level_min"] <= m["level_max"], f"地图 {m['id']} 等级带非法"
        assert all(ref in ids for ref in m["adjacent"]), f"地图 {m['id']} adjacent 有悬空引用"
    print("[assert] 每图 adjacent 非空 + 等级带合法 OK")

    monster_ids = {m["id"] for m in monsters}
    drop_ids = {d["monster_id"] for d in dataset["drops"]}
    missing_drops = monster_ids - drop_ids
    assert not missing_drops, f"缺掉落表的怪物：{sorted(missing_drops)}"
    for d in dataset["drops"]:
        assert any(e["item_id"] is None for e in d["entries"]), \
            f"{d['monster_id']} 掉落表缺金币条目"
    print(f"[assert] drops={len(dataset['drops'])}（每怪含金币）OK")

    spawn_maps = {s["map_id"] for s in dataset["spawns"]}
    assert ids <= spawn_maps, f"缺刷怪点的地图：{sorted(ids - spawn_maps)}"
    print(f"[assert] spawns={len(dataset['spawns'])}（每图 ≥1 点位）OK")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def build_builtin_dataset() -> dict[str, list[dict[str, Any]]]:
    return {
        "maps": [dict(m) for m in BUILT_IN_MAPS],
        "monsters": [dict(m) for m in BUILT_IN_MONSTERS],
        "items": [dict(i) for i in BUILT_IN_ITEMS],
        "skills": [dict(s) for s in BUILT_IN_SKILLS],
        "drops": [json.loads(json.dumps(d)) for d in BUILT_IN_DROPS],
        "spawns": [json.loads(json.dumps(s)) for s in BUILT_IN_SPAWNS],
    }


def run(mode: str, source_dir: Path, root: Path, skip_web: bool) -> int:
    print(f"[run] mode={mode} source_dir={source_dir} root={root}")
    print(f"[anchor] BIRTH_MAP_ID={BIRTH_MAP_ID}（from app.core.config）")

    if mode == "minimal":
        dataset = build_builtin_dataset()
        print("[fallback] 使用内置 C3a 最小数据集（confidence=low，W3 全量录入替换）")
        write_json(root / "levels.json", BUILT_IN_LEVELS)
        write_dataset(root, dataset)
        verify_dataset(dataset)
        print("[done] minimal 数据集写入完成")
        return 0

    # sources / auto：先探测本地源
    found = probe_sources(source_dir)
    missing = {cat for cat, path in found.items() if path is None}
    present = {cat for cat, path in found.items() if path is not None}
    if present:
        print(f"[probe] 本地源就绪：{sorted(present)}")
    if missing:
        print(f"[probe] 本地源缺失：{sorted(missing)}")

    if mode == "sources":
        errors: list[str] = []
        for category in sorted(missing):
            filename = _SOURCE_OF_CATEGORY[category]
            errors.append(
                f"源缺失，无法转换 {category}：未找到 {source_dir / filename}"
                f"（web 抓取{'已跳过' if skip_web else '亦失败'}）。"
                "请将 Suprcode/Crystal.Database 导出放入该路径，"
                "参考 docs/combat-formulas.md 兜底约定，或运行 --only minimal。"
            )
        if errors:
            for err in errors:
                print(f"[error] {err}", file=sys.stderr)
            return 1

    if mode == "auto" and missing and not skip_web:
        print("[web] 尝试抓取缺失源……")
        missing = web_fetch_missing(source_dir, missing)

    dataset = build_builtin_dataset()
    failed: list[str] = []
    parsers = {
        "monsters": lambda: parse_monster_db(source_dir / "Monster.DB"),
        "items": lambda: parse_stditems_db(source_dir / "StdItems.DB"),
        "skills": lambda: parse_magic_db(source_dir / "Magic.DB"),
    }
    for category, parser in parsers.items():
        if category in missing:
            if mode == "auto":
                print(
                    f"[fallback] {category}: 源缺失，使用内置兜底数据（confidence=low）",
                    file=sys.stderr,
                )
                failed.append(category)
            continue
        try:
            converted = parser()
            if mode == "auto":
                # auto 模式按 id 合并（源优先覆盖同名，内置保底），防部分源跌破最低数据量；
                # sources 模式纯转换，不掺兜底。
                merged = {e["id"]: e for e in dataset[category]}
                for entry in converted:
                    merged[entry["id"]] = entry
                dataset[category] = list(merged.values())
            else:
                dataset[category] = converted
        except SourceMissingError as exc:
            if mode == "auto":
                print(f"[fallback] {category}: {exc}（confidence=low）", file=sys.stderr)
                failed.append(category)
                continue
            print(f"[error] 无法转换 {category}：{exc}", file=sys.stderr)
            return 1

    if "levels" in missing:
        if mode == "auto":
            print("[fallback] levels: Exps.ini 缺失，使用内置经验曲线（confidence=low）",
                  file=sys.stderr)
            write_json(root / "levels.json", BUILT_IN_LEVELS)
        else:
            print(
                f"[error] 源缺失，无法转换 levels：未找到 {source_dir / 'Exps.ini'}。"
                "参考 docs/combat-formulas.md 兜底约定，或运行 --only minimal。",
                file=sys.stderr,
            )
            return 1
    else:
        try:
            write_json(root / "levels.json", parse_exps_ini(source_dir / "Exps.ini"))
        except SourceMissingError as exc:
            print(f"[error] 无法转换 levels：{exc}", file=sys.stderr)
            return 1

    write_dataset(root, dataset)
    verify_dataset(dataset)
    if mode == "auto" and failed:
        print(
            f"[done] auto 完成，但以下类别为内置兜底（confidence=low）：{sorted(failed)}",
            file=sys.stderr,
        )
    else:
        print("[done] 源转换完成")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="convert_gamedata",
        description="开源模拟器数据 → schema 合法 gamedata JSON（详见模块 docstring）",
    )
    parser.add_argument(
        "--only",
        choices=("minimal", "sources", "auto"),
        default="auto",
        help="minimal=只写内置最小数据集（离线验收路径）；"
             "sources=只做源转换（缺源报错退出 1）；auto=探测→抓取→逐类兜底（默认）",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help=f"原始源文件目录（默认 {DEFAULT_SOURCE_DIR}，绝不入库）",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_GAMEDATA_ROOT,
        help=f"gamedata 输出根目录（默认 {DEFAULT_GAMEDATA_ROOT}）",
    )
    parser.add_argument(
        "--skip-web",
        action="store_true",
        help="跳过 web 抓取（离线环境加速 auto 模式）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args.only, args.source_dir, args.root, args.skip_web)
    except SourceMissingError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    except AssertionError as exc:
        print(f"[assert-failed] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
