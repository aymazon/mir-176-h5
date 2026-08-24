"""build_monsters.py — task 18 怪物全量录入构建器。

从两个独立源构建 backend/gamedata/monsters/*.json：
- 主源：Crystal Server.MirDB（scripts/mir_sources.py 解析）
- 第二源：OpenMir2 sql/mir2_data.sql

字段映射约定（docs/data-coverage.md §1 同步记录）：
- level   ← Crystal Level / OpenMir2 Lvl
- hp      ← Crystal Stats[HP] / OpenMir2 HP
- attack  ← 攻击上限：Crystal MaxDC / OpenMir2 DCMAX（MonsterDef.attack 为单值，
            取区间上限与玩家 attack 区间上限语义对齐）
- defense ← 物防上限：Crystal MaxAC / OpenMir2 AC
- magic_defense ← 魔御上限：Crystal MaxMAC / OpenMir2 MAC
- accuracy ← Crystal Accuracy / OpenMir2 HIT
- agility ← Crystal Agility / OpenMir2 SPEED
- exp     ← Crystal Experience / OpenMir2 Exp
- attack_speed / move_speed ← 源毫秒值 ÷1000 四舍五入，钳制 [1,6] tick
- undead  ← 两源冲突时取 OpenMir2（Crystal 该发行版全 False，见 coverage §1）

主源缺名怪：第二源同名补位；两源皆缺（文档在列）按同族锚点派生并标 TODO。
用法：cd backend && uv run python scripts/build_monsters.py [--dry-run]
"""

# pyright: reportUnusedCallResult=false, reportImplicitRelativeImport=false

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Final, TypedDict, cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.gamedata_schema.monsters import MonsterDef

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mir_sources

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_MONSTERS_DIR = _BACKEND_ROOT / "gamedata" / "monsters"

# 现有 12 只（task 13）的 id → 重生秒；数值一律按主源回填（以源为准）
_EXISTING_RESPAWN: Final[dict[str, int]] = {
    "chicken": 60,
    "deer": 60,
    "scarecrow": 90,
    "hook_cat": 90,
    "rake_cat": 90,
    "man_eating_flower": 120,
    "orc": 120,
    "cave_bat": 90,
    "cave_maggot": 120,
    "skeleton": 150,
    "axe_skeleton": 150,
    "orc_champion": 3600,
}

# BOSS（MVP README §6 BOSS 列 + 任务规格）：重生统一 3600s（60min，社区一致）
_BOSSES: Final[dict[str, int]] = {
    "skeleton_elf": 3600,       # 骷髅精灵（骷髅洞 3 层）
    "corpse_king": 3600,        # 尸王（尸王殿）
    "wild_boar_white": 3600,    # 白野猪（猪 7）
    "evil_clamp_worm": 3600,    # 邪恶钳虫
    "touch_dragon_god": 3600,   # 触龙神（死亡棺材）
    "corpse_overlord": 3600,    # 尸霸（石墓）
    "wooma_taurus": 3600,       # 沃玛教主（任务规格 60min）
    "zuma_taurus": 3600,        # 祖玛教主（任务规格 60min）
}

# 精英（非 BOSS）：重生 1800s（极品祖玛社区惯例 ~60min）
_ELITES: Final[dict[str, int]] = {
    "ancient_wooma_warrior": 1800,     # 远古沃玛勇士（沃玛森林精英版槽位）
    "zuma_guardian_elite": 3600,       # 极品祖玛卫士（1400 大锤）
}


def _normal_respawn(level: int) -> int:
    """新增普通怪重生：低阶 90 / 中阶 120 / 高阶 150。"""
    if level <= 15:
        return 90
    if level <= 30:
        return 120
    return 150


def _ticks(ms: int) -> int:
    """源毫秒间隔 → tick 数（1 tick = 1s），钳制 [1, 6]。"""
    return max(1, min(6, int(ms / 1000 + 0.5)))


class MonsterValues(TypedDict):
    level: int
    hp: int
    attack: int
    defense: int
    magic_defense: int
    accuracy: int
    agility: int
    exp: int
    attack_speed: int
    move_speed: int


class MonsterEntry(MonsterValues):
    """写盘前的完整记录；provenance/todo 仅用于 evidence，不落 JSON。"""

    id: str
    name: str
    undead: bool
    boss: bool
    respawn_seconds: int
    provenance: str
    todo: str


class _RosterOptional(TypedDict, total=False):
    src_name: str
    base: tuple[str, str]
    level: int
    hp: int
    attack: int
    defense: int
    magic_defense: int
    accuracy: int
    agility: int
    exp: int
    todo: str


class RosterSpec(_RosterOptional):
    """必填键放子类（total 默认 True），可选键继承自 _RosterOptional。"""

    id: str
    name: str
    strategy: str


def _spec_int(spec: RosterSpec, field: str) -> int:
    """读取可选 int 键（derive 档必填）；运行时类型闸防 typo 静默归零。"""
    raw: dict[str, object] = cast("dict[str, object]", cast("object", spec))
    value = raw[field]
    assert isinstance(value, int) and not isinstance(value, bool)
    return value

def from_crystal(rec: mir_sources.CrystalMonster) -> MonsterValues:
    return {
        "level": rec["level"],
        "hp": rec["hp"],
        "attack": rec["max_dc"],
        "defense": rec["max_ac"],
        "magic_defense": rec["max_mac"],
        "accuracy": rec["accuracy"],
        "agility": rec["agility"],
        "exp": rec["exp"],
        "attack_speed": _ticks(rec["attack_speed_ms"]),
        "move_speed": _ticks(rec["move_speed_ms"]),
    }


def from_openmir2(row: dict[str, str]) -> MonsterValues:
    return {
        "level": int(row["Lvl"]),
        "hp": int(row["HP"]),
        "attack": int(row["DCMAX"]),
        "defense": int(row["AC"]),
        "magic_defense": int(row["MAC"]),
        "accuracy": int(row["HIT"]),
        "agility": int(row["SPEED"]),
        "exp": int(row["Exp"]),
        "attack_speed": _ticks(int(row["ATTACK_SPD"])),
        "move_speed": _ticks(int(row["WALK_SPD"])),
    }


# ---------------------------------------------------------------------------
# 录入名单（任务规格 §6 全量；strategy: cr=主源同名 / om2=第二源同名 /
# cr_map=主源别名映射 / om2_map=第二源别名映射 / derive=同族锚点派生）
# ---------------------------------------------------------------------------
ROSTER: Final[list[RosterSpec]] = [
    # 新手区 6
    {"id": "chicken", "name": "鸡", "strategy": "cr"},
    {"id": "deer", "name": "鹿", "strategy": "cr"},
    {"id": "scarecrow", "name": "稻草人", "strategy": "cr"},
    {"id": "rake_cat", "name": "钉耙猫", "strategy": "cr"},
    {"id": "hook_cat", "name": "多钩猫", "strategy": "cr"},
    {"id": "man_eating_flower", "name": "食人花", "strategy": "cr"},
    # 比奇 7
    {"id": "toad", "name": "蛤蟆", "strategy": "cr"},
    {
        "id": "toad_fighter",
        "name": "蛤蟆斗士",
        "strategy": "derive",
        "base": ("cr", "蛤蟆"),
        "level": 15,
        "hp": 32,
        "attack": 7,
        "defense": 0,
        "magic_defense": 0,
        "accuracy": 8,
        "agility": 13,
        "exp": 25,
        "todo": "两源均无同名怪；按同族蛤蟆(Crystal L12 HP20 DC0-5 EXP15)升档派生",
    },
    {
        "id": "venom_snake",
        "name": "毒蛇",
        "strategy": "cr_map",
        "src_name": "赤色毒蛇",
        "todo": "两源均无『毒蛇』原名；取主源同族『赤色毒蛇』数值（L17 HP60 DC9-14）",
    },
    {
        "id": "venom_snake_fighter",
        "name": "毒蛇斗士",
        "strategy": "derive",
        "base": ("cr", "赤色毒蛇"),
        "level": 20,
        "hp": 96,
        "attack": 17,
        "defense": 1,
        "magic_defense": 5,
        "accuracy": 13,
        "agility": 15,
        "exp": 145,
        "todo": "两源均无同名怪；按毒蛇(赤色毒蛇)升档派生",
    },
    {"id": "orc", "name": "半兽人", "strategy": "cr"},
    {"id": "orc_warrior", "name": "半兽战士", "strategy": "cr"},
    {"id": "orc_champion", "name": "半兽勇士", "strategy": "cr"},
    # 矿区 5 + 掷斧骷髅（task 13 已有）
    {"id": "cave_maggot", "name": "洞蛆", "strategy": "cr"},
    {
        "id": "bat",
        "name": "蝙蝠",
        "strategy": "om2",
        "todo": "主源仅有山洞蝙蝠/小蝙蝠变体；第二源 OpenMir2 同名『蝙蝠』直取",
    },
    {"id": "skeleton", "name": "骷髅", "strategy": "cr"},
    {"id": "skeleton_warrior", "name": "骷髅战士", "strategy": "cr"},
    {"id": "skeleton_elf", "name": "骷髅精灵", "strategy": "cr"},
    {"id": "axe_skeleton", "name": "掷斧骷髅", "strategy": "cr"},
    # 蛇谷 5
    {"id": "red_snake", "name": "红蛇", "strategy": "cr"},
    {"id": "tiger_snake", "name": "虎蛇", "strategy": "cr"},
    {
        "id": "giant_snake",
        "name": "巨蛇",
        "strategy": "derive",
        "base": ("cr", "虎蛇"),
        "level": 22,
        "hp": 110,
        "attack": 16,
        "defense": 1,
        "magic_defense": 4,
        "accuracy": 14,
        "agility": 15,
        "exp": 175,
        "todo": "两源均无同名怪；按虎蛇(Crystal L18 HP60 DC10-11)升档派生",
    },
    {
        "id": "two_head_snake",
        "name": "双头蛇",
        "strategy": "derive",
        "base": ("cr", "虎蛇"),
        "level": 26,
        "hp": 180,
        "attack": 21,
        "defense": 2,
        "magic_defense": 5,
        "accuracy": 16,
        "agility": 15,
        "exp": 280,
        "todo": "两源均无同名怪；按蛇谷族谱(红蛇→虎蛇→邪恶毒蛇)内插派生",
    },
    {
        "id": "two_head_snake_demon",
        "name": "双头蛇魔",
        "strategy": "derive",
        "base": ("cr", "虎蛇"),
        "level": 30,
        "hp": 300,
        "attack": 28,
        "defense": 3,
        "magic_defense": 6,
        "accuracy": 18,
        "agility": 16,
        "exp": 450,
        "todo": "两源均无同名怪；按蛇谷族谱顶格派生（对标主源邪恶毒蛇）",
    },
    # 盟重 4
    {"id": "scorpion", "name": "蝎子", "strategy": "cr"},
    {
        "id": "giant_scorpion",
        "name": "巨型蝎子",
        "strategy": "derive",
        "base": ("cr", "蝎子"),
        "level": 24,
        "hp": 100,
        "attack": 20,
        "defense": 4,
        "magic_defense": 1,
        "accuracy": 13,
        "agility": 15,
        "exp": 170,
        "todo": "两源均无同名怪；按蝎子(Crystal L18 HP45 DC7-16)升档派生",
    },
    {"id": "horn_fly", "name": "角蝇", "strategy": "cr"},
    {
        "id": "desert_giant_worm",
        "name": "沙漠巨型蠕虫",
        "strategy": "cr_map",
        "src_name": "巨型蠕虫",
        "todo": "两源均无『沙漠巨型蠕虫』全名；取两源同名『巨型蠕虫』（主源优先）",
    },
    # 蜈蚣洞 5
    {"id": "centipede", "name": "蜈蚣", "strategy": "cr"},
    {
        "id": "giant_centipede",
        "name": "巨型蜈蚣",
        "strategy": "derive",
        "base": ("cr", "蜈蚣"),
        "level": 30,
        "hp": 380,
        "attack": 23,
        "defense": 2,
        "magic_defense": 7,
        "accuracy": 15,
        "agility": 15,
        "exp": 500,
        "todo": "两源均无同名怪（OpenMir2 有蜈蚣王）；按蜈蚣(Crystal L26 HP210)升档派生",
    },
    {
        "id": "black_centipede",
        "name": "黑色蜈蚣",
        "strategy": "derive",
        "base": ("cr", "蜈蚣"),
        "level": 33,
        "hp": 450,
        "attack": 27,
        "defense": 3,
        "magic_defense": 9,
        "accuracy": 16,
        "agility": 15,
        "exp": 620,
        "todo": "两源均无同名怪；按蜈蚣升档+高魔御派生",
    },
    {"id": "touch_dragon_god", "name": "触龙神", "strategy": "cr"},
    {"id": "evil_clamp_worm", "name": "邪恶钳虫", "strategy": "cr"},
    # 石墓 6
    {
        "id": "zombie",
        "name": "僵尸",
        "strategy": "om2_map",
        "src_name": "僵尸1",
        "todo": "主源无 plain『僵尸』（有萨满/爬行等变体同值 L25 HP155）；"
                "取第二源『僵尸1』（L25 HP155 DC12-16 undead=1）",
    },
    {
        "id": "zombie_fighter",
        "name": "僵尸斗士",
        "strategy": "derive",
        "base": ("om2", "僵尸1"),
        "level": 28,
        "hp": 220,
        "attack": 20,
        "defense": 2,
        "magic_defense": 2,
        "accuracy": 14,
        "agility": 13,
        "exp": 240,
        "todo": "两源均无同名怪；按僵尸(OM2 僵尸1)升档派生",
    },
    {"id": "corpse_king", "name": "尸王", "strategy": "cr"},
    {"id": "evil_corpse_king", "name": "恶灵尸王", "strategy": "cr"},
    {
        "id": "corpse_overlord",
        "name": "尸霸",
        "strategy": "derive",
        "base": ("cr", "尸王"),
        "level": 55,
        "hp": 1000,
        "attack": 48,
        "defense": 5,
        "magic_defense": 5,
        "accuracy": 18,
        "agility": 15,
        "exp": 2400,
        "todo": "两源均无同名怪；按尸王(Crystal L50 HP500 DC18-38)BOSS 档派生",
    },
    {
        "id": "lightning_zombie",
        "name": "闪电僵尸",
        "strategy": "derive",
        "base": ("om2", "僵尸1"),
        "level": 34,
        "hp": 320,
        "attack": 26,
        "defense": 3,
        "magic_defense": 4,
        "accuracy": 16,
        "agility": 13,
        "exp": 380,
        "todo": "两源均无同名怪；按僵尸族高阶派生",
    },
    # 石墓补充（MVP §6 在列，双源同名）
    {"id": "wild_boar_red", "name": "红野猪", "strategy": "cr"},
    {"id": "wild_boar_black", "name": "黑野猪", "strategy": "cr"},
    {"id": "wild_boar_white", "name": "白野猪", "strategy": "cr"},
    {"id": "scorpion_snake", "name": "蝎蛇", "strategy": "cr"},
    # 沃玛森林 3 + 精英
    {"id": "wooma_soldier", "name": "沃玛战士", "strategy": "cr"},
    {"id": "wooma_warrior", "name": "沃玛勇士", "strategy": "cr"},
    {
        "id": "wooma_tactician",
        "name": "沃玛战将",
        "strategy": "om2",
        "todo": "主源该名下为『沃玛军士』变体；第二源 OpenMir2 同名『沃玛战将』直取",
    },
    {"id": "forest_yeti", "name": "森林雪人", "strategy": "cr"},
    {"id": "poison_spider", "name": "毒蜘蛛", "strategy": "cr"},
    {"id": "ancient_wooma_warrior", "name": "远古沃玛勇士", "strategy": "cr"},
    # 沃玛寺庙 2 + 教主
    {"id": "wooma_guardian", "name": "沃玛卫士", "strategy": "cr"},
    {"id": "flaming_wooma", "name": "火焰沃玛", "strategy": "cr"},
    {"id": "wooma_taurus", "name": "沃玛教主", "strategy": "cr"},
    # 祖玛 3 + 极品 + 教主
    {"id": "zuma_statue", "name": "祖玛雕像", "strategy": "cr"},
    {"id": "zuma_guardian", "name": "祖玛卫士", "strategy": "cr"},
    {"id": "zuma_archer", "name": "祖玛弓箭手", "strategy": "cr"},
    {
        "id": "zuma_guardian_elite",
        "name": "极品祖玛卫士",
        "strategy": "om2_map",
        "src_name": "祖玛卫士00",
        "todo": "极品祖玛(1400 大锤)：第二源『祖玛卫士00』HP=1400 与社区公认一致；"
                "主源同名变体 HP=4450 为该服自定义，弃用",
    },
    {"id": "zuma_taurus", "name": "祖玛教主", "strategy": "cr"},
    # 文档在列补充（双源同名）
    {"id": "wedge_moth", "name": "楔蛾", "strategy": "cr"},
    {"id": "jumping_bee", "name": "跳跳蜂", "strategy": "cr"},
]


def build(
    crystal: list[mir_sources.CrystalMonster],
    openmir2: list[dict[str, str]],
) -> tuple[list[MonsterEntry], list[str]]:
    cr_by_name = {r["name"]: r for r in crystal}
    om_by_name = {r["Name"]: r for r in openmir2}
    entries: list[MonsterEntry] = []
    notes: list[str] = []
    for spec in ROSTER:
        mid = spec["id"]
        name = spec["name"]
        strategy = spec["strategy"]
        values: MonsterValues | None = None
        undead = False
        provenance = ""
        if strategy == "cr":
            rec = cr_by_name.get(name)
            if rec is None:
                notes.append(f"[MISS] {name}: 主源缺名")
                continue
            values = from_crystal(rec)
            om_row = om_by_name.get(name)
            if om_row is not None:
                undead = om_row["Undead"] == "1"
                provenance = f"cr:{rec['index']}" + (" +om2.undead" if undead else "")
            else:
                provenance = f"cr:{rec['index']}"
        elif strategy == "om2":
            row = om_by_name.get(name)
            if row is None:
                notes.append(f"[MISS] {name}: 第二源缺名")
                continue
            values = from_openmir2(row)
            undead = row["Undead"] == "1"
            provenance = f"om2:{row['Idx']}"
        elif strategy == "cr_map":
            src_name = spec.get("src_name", "")
            rec = cr_by_name.get(src_name)
            if rec is None:
                notes.append(f"[MISS] {name}: 主源别名 {src_name} 缺")
                continue
            values = from_crystal(rec)
            om_row = om_by_name.get(src_name)
            if om_row is not None and om_row["Undead"] == "1":
                undead = True
            provenance = f"cr:{rec['index']}({src_name})"
        elif strategy == "om2_map":
            src_name = spec.get("src_name", "")
            row = om_by_name.get(src_name)
            if row is None:
                notes.append(f"[MISS] {name}: 第二源别名 {src_name} 缺")
                continue
            values = from_openmir2(row)
            undead = row["Undead"] == "1"
            provenance = f"om2:{row['Idx']}({src_name})"
        elif strategy == "derive":
            base_src, base_name = spec.get("base", ("", ""))
            if base_src == "cr":
                rec = cr_by_name.get(base_name)
                if rec is None:
                    notes.append(f"[MISS] {name}: 派生锚点 {base_name} 缺")
                    continue
                values = from_crystal(rec)
                provenance = f"derive(cr.{base_name})"
            else:
                row = om_by_name.get(base_name)
                if row is None:
                    notes.append(f"[MISS] {name}: 派生锚点 {base_name} 缺")
                    continue
                values = from_openmir2(row)
                undead = row["Undead"] == "1"
                provenance = f"derive(om2.{base_name})"

            values["level"] = _spec_int(spec, "level")
            values["hp"] = _spec_int(spec, "hp")
            values["attack"] = _spec_int(spec, "attack")
            values["defense"] = _spec_int(spec, "defense")
            values["magic_defense"] = _spec_int(spec, "magic_defense")
            values["accuracy"] = _spec_int(spec, "accuracy")
            values["agility"] = _spec_int(spec, "agility")
            values["exp"] = _spec_int(spec, "exp")
        else:  # pragma: no cover
            raise ValueError(strategy)

        assert values is not None
        level = values["level"]
        if mid in _BOSSES:
            boss, respawn = True, _BOSSES[mid]
        elif mid in _ELITES:
            boss, respawn = False, _ELITES[mid]
        elif mid in _EXISTING_RESPAWN:
            boss = mid == "orc_champion"
            respawn = _EXISTING_RESPAWN[mid]
        else:
            boss, respawn = False, _normal_respawn(level)

        entry: MonsterEntry = {
            "id": mid,
            "name": name,
            "level": values["level"],
            "hp": values["hp"],
            "attack": values["attack"],
            "defense": values["defense"],
            "magic_defense": values["magic_defense"],
            "accuracy": values["accuracy"],
            "agility": values["agility"],
            "exp": values["exp"],
            "attack_speed": values["attack_speed"],
            "move_speed": values["move_speed"],
            "undead": undead,
            "boss": boss,
            "respawn_seconds": respawn,
            "provenance": provenance,
            "todo": spec.get("todo", ""),
        }
        entries.append(entry)
    return entries, notes


def payload_of(entry: MonsterEntry) -> dict[str, object]:
    """剥离内部字段后的可写盘 payload（键序对齐 MonsterDef）。"""
    return {
        "id": entry["id"],
        "name": entry["name"],
        "level": entry["level"],
        "hp": entry["hp"],
        "attack": entry["attack"],
        "defense": entry["defense"],
        "magic_defense": entry["magic_defense"],
        "accuracy": entry["accuracy"],
        "agility": entry["agility"],
        "exp": entry["exp"],
        "attack_speed": entry["attack_speed"],
        "move_speed": entry["move_speed"],
        "undead": entry["undead"],
        "boss": entry["boss"],
        "respawn_seconds": entry["respawn_seconds"],
    }


def write_entries(entries: list[MonsterEntry], dry_run: bool) -> int:
    written = 0
    for entry in entries:
        path = _MONSTERS_DIR / f"{entry['id']}.json"
        if dry_run:
            print(f"[dry] {path.name}")
            continue
        path.write_text(
            json.dumps(payload_of(entry), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written += 1
    return written


class MappingSample(TypedDict):
    raw_crystal: mir_sources.CrystalMonster | None
    monster_def: dict[str, object]
    provenance: str


def mapping_samples(
    entries: list[MonsterEntry],
    crystal: list[mir_sources.CrystalMonster],
) -> dict[str, MappingSample]:
    """5 只抽查怪的「原始记录 → JSON 字段」映射样本（过 MonsterDef 校验）。"""
    samples: dict[str, MappingSample] = {}
    for probe in ("半兽战士", "尸王", "白野猪", "沃玛教主", "祖玛教主"):
        for entry in entries:
            if entry["name"] != probe:
                continue
            raw = next((r for r in crystal if r["name"] == probe), None)
            monster_def = MonsterDef.model_validate(payload_of(entry))
            samples[probe] = {
                "raw_crystal": raw,
                "monster_def": monster_def.model_dump(mode="json"),
                "provenance": entry["provenance"],
            }
            break
    return samples


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    crystal, cr_path = mir_sources.load_crystal_records()
    openmir2, om_path = mir_sources.load_openmir2_rows()
    print(f"[source] crystal={cr_path} records={len(crystal)}")
    print(f"[source] openmir2={om_path} rows={len(openmir2)}")
    if not crystal and not openmir2:
        print(
            "[error] 两源均缺失：请将 Server.MirDB 与 mir2_data.sql 放入 "
            "/tmp/mir176-sources/ 或 backend/scripts/cache/",
            file=sys.stderr,
        )
        return 1
    entries, notes = build(crystal, openmir2)
    for note in notes:
        print(note)
    print(f"[build] roster={len(entries)}")
    written = write_entries(entries, dry_run)
    if not dry_run:
        print(f"[write] {written} files -> {_MONSTERS_DIR}")
    samples = mapping_samples(entries, crystal)
    sample_path = Path("/tmp/monster_mapping_samples.json")
    sample_path.write_text(
        json.dumps(samples, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"[samples] {sample_path} ({len(samples)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
