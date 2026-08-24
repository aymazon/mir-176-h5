"""mir_sources.py — Mir2 服务端源数据解析（task 18/19）。

两个独立数值源：
1. 主源 Crystal ``Server.MirDB``（Suprcode/Crystal.Database 仓库 cjlaaa/ 目录，
   与 levels.json 主源 GeeM2 Exps.ini 同一发行版家族）——自定义二进制，
   怪物按 Crystal ``MonsterInfo(BinaryReader)``、物品按 ``ItemInfo(BinaryReader)``
   （Shared/Data/ItemData.cs，LoadVersion>84 分支）逐字段解析；
   记录区定位用“连续记录链扫描”（≥3 条合法记录即认，索引连续性做旁证）。
2. 第二源 OpenMir2 ``sql/mir2_data.sql``（github.com/mirbeta/OpenMir2）——
   标准 MySQL dump 的 ``monsters`` 表（task 18）与 ``stditems`` 表（task 19）INSERT 行。

两源均不入库（backend/.gitignore：**/*.db 等）；本地查找顺序：
/tmp/mir176-sources → backend/scripts/cache。缺失时返回空并给出获取指引。
"""

# pyright: reportUnusedCallResult=false, reportImplicitRelativeImport=false

from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import TypedDict

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CACHE_DIR = Path(__file__).resolve().parent / "cache"
TMP_SOURCE_DIR = Path("/tmp/mir176-sources")

CRYSTAL_FILENAME = "Server.MirDB"
OPENMIR2_FILENAME = "mir2_data.sql"

# Crystal Stat 枚举键（Shared/Data/Stat.cs）
_STAT_MIN_AC, _STAT_MAX_AC = 0, 1
_STAT_MIN_MAC, _STAT_MAX_MAC = 2, 3
_STAT_MIN_DC, _STAT_MAX_DC = 4, 5
_STAT_ACCURACY, _STAT_AGILITY, _STAT_HP = 10, 11, 12
# ItemInfo Stats 块额外键（Shared/Data/Stat.cs）：MinMC=6/MaxMC=7/MinSC=8/MaxSC=9/
# MP=13/AttackSpeed=14/Luck=15/WearWeight=18/Holy=21/PoisonAttack=23/MagicResist=30
# /HealthRecovery=32/SpellRecovery=33
_STAT_MIN_MC, _STAT_MAX_MC = 6, 7
_STAT_MIN_SC, _STAT_MAX_SC = 8, 9
_STAT_MP, _STAT_ATTACK_SPEED, _STAT_LUCK = 13, 14, 15
_STAT_WEAR_WEIGHT, _STAT_HOLY = 18, 21
_STAT_POISON_ATTACK, _STAT_MAGIC_RESIST = 23, 30
_STAT_HEALTH_RECOVERY, _STAT_SPELL_RECOVERY = 32, 33


class CrystalMonster(TypedDict):
    """Crystal MonsterInfo 解析结果（字段名对齐 C# 属性）。"""

    index: int
    name: str
    image: int
    level: int
    hp: int
    min_ac: int
    max_ac: int
    min_mac: int
    max_mac: int
    min_dc: int
    max_dc: int
    accuracy: int
    agility: int
    light: int
    attack_speed_ms: int
    move_speed_ms: int
    exp: int
    undead: bool


class _Reader:
    """BinaryReader 语义的最小实现（little-endian + 7-bit 编码字符串长度）。"""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    def i32(self) -> int:
        value = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return value

    def u32(self) -> int:
        value = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def u16(self) -> int:
        value = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return value

    def i16(self) -> int:
        value = struct.unpack_from("<h", self.data, self.pos)[0]
        self.pos += 2
        return value

    def u8(self) -> int:
        value = self.data[self.pos]
        self.pos += 1
        return value

    def boolean(self) -> bool:
        return self.u8() != 0

    def string(self) -> str:
        length = 0
        shift = 0
        while True:
            byte = self.data[self.pos]
            self.pos += 1
            length |= (byte & 0x7F) << shift
            if not (byte & 0x80):
                break
            shift += 7
        raw = self.data[self.pos : self.pos + length]
        if len(raw) < length:
            raise ValueError("string out of range")
        self.pos += length
        return raw.decode("utf-8")

    def stats(self) -> dict[int, int]:
        count = self.i32()
        if not (0 <= count <= 100):
            raise ValueError("stats count out of range")
        out: dict[int, int] = {}
        for _ in range(count):
            key = self.u8()
            out[key] = self.i32()
        return out


def _parse_crystal_monster(reader: _Reader) -> CrystalMonster:
    """Crystal MonsterInfo(BinaryReader)，LoadVersion ∈ [89, 115) 分支。"""
    index = reader.i32()
    name = reader.string()
    if not name or len(name) > 40:
        raise ValueError("bad name")
    image = reader.u16()
    reader.u8()  # AI
    reader.u8()  # Effect
    level = reader.u16()
    reader.u8()  # ViewRange
    reader.u8()  # CoolEye
    stats = reader.stats()
    light = reader.u8()
    attack_speed_ms = reader.u16()
    move_speed_ms = reader.u16()
    exp = reader.u32()
    reader.boolean()  # CanPush
    reader.boolean()  # CanTame
    reader.boolean()  # AutoRev
    undead = reader.boolean()
    drop_path = reader.string()
    if len(drop_path) > 200:
        raise ValueError("bad drop path")
    if not (1 <= level <= 500):
        raise ValueError("level out of range")
    hp = stats.get(_STAT_HP, 0)
    if not (0 < hp <= 1_000_000):
        raise ValueError("hp out of range")
    if exp > 100_000_000:
        raise ValueError("exp out of range")
    return {
        "index": index,
        "name": name,
        "image": image,
        "level": level,
        "hp": hp,
        "min_ac": stats.get(_STAT_MIN_AC, 0),
        "max_ac": stats.get(_STAT_MAX_AC, 0),
        "min_mac": stats.get(_STAT_MIN_MAC, 0),
        "max_mac": stats.get(_STAT_MAX_MAC, 0),
        "min_dc": stats.get(_STAT_MIN_DC, 0),
        "max_dc": stats.get(_STAT_MAX_DC, 0),
        "accuracy": stats.get(_STAT_ACCURACY, 0),
        "agility": stats.get(_STAT_AGILITY, 0),
        "light": light,
        "attack_speed_ms": attack_speed_ms,
        "move_speed_ms": move_speed_ms,
        "exp": exp,
        "undead": undead,
    }


def parse_crystal_mirdb(path: Path) -> list[CrystalMonster]:
    """扫描 Server.MirDB，收集全部可解析的 MonsterInfo 连续记录链。

    同名怪取首次出现（编号后缀变体如 ``骷髅0`` 是独立名，不受影响）。
    """
    data = path.read_bytes()
    found: list[CrystalMonster] = []
    seen_names: set[str] = set()
    size = len(data)
    i = 0
    while i < size - 20:
        reader = _Reader(data, i)
        chain: list[CrystalMonster] = []
        while True:
            save = reader.pos
            try:
                record = _parse_crystal_monster(reader)
            except (ValueError, UnicodeDecodeError, struct.error, IndexError):
                reader.pos = save
                break
            chain.append(record)
        if len(chain) >= 3:
            for record in chain:
                if record["name"] not in seen_names:
                    seen_names.add(record["name"])
                    found.append(record)
            i = reader.pos
        else:
            i += 1
    return found


_OPENMIR2_COLS = (
    "Idx", "Name", "Race", "RaceImg", "Appr", "Lvl", "Undead", "CoolEye",
    "Exp", "HP", "MP", "AC", "MAC", "DC", "DCMAX", "MC", "SC", "SPEED",
    "HIT", "WALK_SPD", "WalkStep", "WalkWait", "ATTACK_SPD", "UnFireRate",
    "UnParalysis", "UnPosion", "UnDragonRate", "ViewRange", "MaxDamage",
    "DecDamageRate", "Tc", "HeartPower", "HeartAC", "DropMode", "UnCutting",
    "DecDamage",
)

_INSERT_RE = re.compile(r"INSERT INTO `monsters` VALUES \((.*?)\);", re.DOTALL)


def _split_sql_values(segment: str) -> list[str]:
    out: list[str] = []
    cur = ""
    in_quote = False
    i = 0
    while i < len(segment):
        ch = segment[i]
        if in_quote:
            if ch == "'" and i + 1 < len(segment) and segment[i + 1] == "'":
                cur += "'"
                i += 2
                continue
            if ch == "'":
                in_quote = False
                i += 1
                continue
            cur += ch
            i += 1
        else:
            if ch == "'":
                in_quote = True
                i += 1
            elif ch == ",":
                out.append(cur.strip())
                cur = ""
                i += 1
            else:
                cur += ch
                i += 1
    out.append(cur.strip())
    return out


def parse_openmir2_sql(path: Path) -> list[dict[str, str]]:
    """OpenMir2 mir2_data.sql 的 monsters 表全行（列值保持字符串原样）。"""
    sql = path.read_text(encoding="utf-8")
    rows: list[dict[str, str]] = []
    for match in _INSERT_RE.finditer(sql):
        values = _split_sql_values(match.group(1))
        if len(values) != len(_OPENMIR2_COLS):
            continue
        rows.append(dict(zip(_OPENMIR2_COLS, values)))
    return rows


def resolve_source_paths() -> tuple[Path | None, Path | None]:
    """按 /tmp/mir176-sources → scripts/cache 顺序解析两源路径。"""
    crystal: Path | None = None
    openmir2: Path | None = None
    for base in (TMP_SOURCE_DIR, _CACHE_DIR):
        candidate = base / CRYSTAL_FILENAME
        if crystal is None and candidate.is_file():
            crystal = candidate
        candidate = base / OPENMIR2_FILENAME
        if openmir2 is None and candidate.is_file():
            openmir2 = candidate
    return crystal, openmir2


class _CrystalCachePayload(TypedDict):
    source_file: str
    records: list[CrystalMonster]


def load_crystal_records() -> tuple[list[CrystalMonster], Path | None]:
    path, _ = resolve_source_paths()
    if path is None:
        return [], None
    cache = _CACHE_DIR / "crystal_monsters.json"
    if cache.is_file():
        try:
            raw: object = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                payload: _CrystalCachePayload = {
                    "source_file": str(raw.get("source_file", "")),
                    "records": raw.get("records", []),
                }
                if payload["source_file"] == str(path):
                    return payload["records"], path
        except (json.JSONDecodeError, OSError):
            pass
    records = parse_crystal_mirdb(path)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload_out: _CrystalCachePayload = {
        "source_file": str(path),
        "records": records,
    }
    cache.write_text(json.dumps(payload_out, ensure_ascii=False), encoding="utf-8")
    return records, path


def load_openmir2_rows() -> tuple[list[dict[str, str]], Path | None]:
    _, path = resolve_source_paths()
    if path is None:
        return [], None
    return parse_openmir2_sql(path), path


# ---------------------------------------------------------------------------
# Crystal ItemInfo（task 19）：Shared/Data/ItemData.cs，LoadVersion>84 分支
# ---------------------------------------------------------------------------


class CrystalItem(TypedDict):
    """Crystal ItemInfo 解析结果（字段名对齐 C# 属性，Stats 已展开为 int 键字典）。"""

    index: int
    name: str
    type: int  # ItemType 枚举原值
    required_type: int  # RequirementType：0=Level/3=MaxDC/4=MaxMC/5=MaxSC
    required_class: int  # 位旗标：Warrior=1/Wizard=2/Taoist=4/None=31(全职业)
    required_amount: int  # 门槛值（语义由 required_type 决定）
    shape: int
    weight: int
    image: int
    durability: int
    stack_size: int
    price: int
    stats: dict[int, int]  # Stat 枚举键 → 值（仅非零项）


_VALID_REQUIRED_CLASS = {0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 28, 31}
_VALID_REQUIRED_GENDER = {0, 1, 2, 3}
_ITEM_TYPE_MAX = 42  # ItemType.SealedHero
_REQUIRED_TYPE_MAX = 11  # RequiredType.MinSC


def _parse_crystal_item(reader: _Reader) -> CrystalItem:
    """Crystal ItemInfo(BinaryReader)，LoadVersion>84 分支（cjlaaa 为 v105）。"""
    index = reader.i32()
    name = reader.string()
    if not name or len(name) > 40 or any(ord(c) < 32 for c in name):
        raise ValueError("bad name")
    item_type = reader.u8()
    if item_type > _ITEM_TYPE_MAX:
        raise ValueError("item type out of range")
    reader.u8()  # Grade
    required_type = reader.u8()
    if required_type > _REQUIRED_TYPE_MAX:
        raise ValueError("required type out of range")
    required_class = reader.u8()
    if required_class not in _VALID_REQUIRED_CLASS:
        raise ValueError("required class out of range")
    required_gender = reader.u8()
    if required_gender not in _VALID_REQUIRED_GENDER:
        raise ValueError("required gender out of range")
    reader.u8()  # Set
    shape = reader.i16()
    weight = reader.u8()
    reader.u8()  # Light
    required_amount = reader.u8()
    image = reader.u16()
    durability = reader.u16()
    stack_size = reader.u16()  # v>84: u16（≤84 为 u32 包一层 ushort，本库不涉及）
    price = reader.u32()
    if price > 100_000_000:
        raise ValueError("price out of range")
    reader.boolean()  # StartItem
    reader.u8()  # Effect
    reader.u8()  # bools(NeedIdentify/ClassBased/LevelBased/CanMine…位包)
    reader.i16()  # Bind
    reader.i16()  # Unique(SpecialItemMode)
    reader.u8()  # RandomStatsId
    reader.boolean()  # CanFastRun
    reader.boolean()  # CanAwakening
    reader.u8()  # Slots（v>83）
    stats = reader.stats()  # v>84：int32 count + count×(byte key, int32 value)
    has_tooltip = reader.boolean()
    if has_tooltip:
        tooltip = reader.string()
        if len(tooltip) > 500:
            raise ValueError("tooltip out of range")
    return {
        "index": index,
        "name": name,
        "type": item_type,
        "required_type": required_type,
        "required_class": required_class,
        "required_amount": required_amount,
        "shape": shape,
        "weight": weight,
        "image": image,
        "durability": durability,
        "stack_size": stack_size,
        "price": price,
        "stats": stats,
    }


def parse_crystal_mirdb_items(path: Path) -> list[CrystalItem]:
    """扫描 Server.MirDB，收集全部可解析的 ItemInfo 连续记录链。

    链长阈值取 10（物品表是文件中最大的连续区块之一；实测主链 1300+ 条，
    前置 MapInfo 段的杂讯链 ≤3 条，阈值拉高即可零误报）。同名取首次出现。
    """
    data = path.read_bytes()
    found: list[CrystalItem] = []
    seen_names: set[str] = set()
    size = len(data)
    i = 0
    while i < size - 30:
        reader = _Reader(data, i)
        chain = 0
        while True:
            save = reader.pos
            try:
                _parse_crystal_item(reader)
            except (ValueError, UnicodeDecodeError, struct.error, IndexError):
                reader.pos = save
                break
            chain += 1
        if chain >= 10:
            # 第二遍：逐条解析并收集（首遍只测链长，避免半条尾巴计入）
            walk = _Reader(data, i)
            for _ in range(chain):
                record = _parse_crystal_item(walk)
                if record["name"] not in seen_names:
                    seen_names.add(record["name"])
                    found.append(record)
            i = walk.pos
        else:
            i += 1
    return found


class _CrystalItemCachePayload(TypedDict):
    source_file: str
    records: list[CrystalItem]


def load_crystal_item_records() -> tuple[list[CrystalItem], Path | None]:
    """Crystal ItemInfo 全量记录（带 scripts/cache/crystal_items.json 缓存）。"""
    path, _ = resolve_source_paths()
    if path is None:
        return [], None
    cache = _CACHE_DIR / "crystal_items.json"
    if cache.is_file():
        try:
            raw: object = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                payload: _CrystalItemCachePayload = {
                    "source_file": str(raw.get("source_file", "")),
                    "records": raw.get("records", []),
                }
                if payload["source_file"] == str(path):
                    records = payload["records"]
                    # JSON 对象键恒为字符串：还原 Stat 枚举 int 键
                    for record in records:
                        record["stats"] = {
                            int(key): value for key, value in record["stats"].items()
                        }
                    return records, path
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            pass
    records = parse_crystal_mirdb_items(path)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload_out: _CrystalItemCachePayload = {
        "source_file": str(path),
        "records": records,
    }
    cache.write_text(json.dumps(payload_out, ensure_ascii=False), encoding="utf-8")
    return records, path


# ---------------------------------------------------------------------------
# OpenMir2 stditems 表（task 19 第二源）
# ---------------------------------------------------------------------------

_OPENMIR2_STDITEMS_COLS = (
    "Id", "Name", "StdMode", "Shape", "Weight", "AniCount", "Source", "Reserved",
    "ImgIndex", "DuraMax", "Ac", "AcMax", "Mac", "MacMax", "Dc", "DcMax",
    "Mc", "McMax", "Sc", "ScMax", "Need", "NeedLevel", "Price", "Stock",
    "Atkspd", "AgilIty", "Accurate", "Mgavoid", "Strong", "Undead",
    "HpAdd", "MpAdd", "ExpAdd", "Efftype1", "Effrate1", "Effvalue1",
    "Efftype2", "Effrate2", "Effvalue2", "SlowDown", "Tox", "ToxAvoid",
    "UniqueItem", "OverlapItem", "LIGHT", "ItemType", "ItemSet", "Reference",
)

_INSERT_STDITEMS_RE = re.compile(
    r"INSERT INTO `stditems` VALUES \((.*?)\);", re.DOTALL
)


def parse_openmir2_stditems(path: Path) -> list[dict[str, str]]:
    """OpenMir2 mir2_data.sql 的 stditems 表全行（列值保持字符串原样）。

    Need 列语义（经经典官方数值交叉验证：战神盔甲需攻46/恶魔长袍需魔28/
    幽灵战衣需道27）：0=等级、1=攻击力(DC)、2=魔法力(MC)、3=道术(SC)。
    """
    sql = path.read_text(encoding="utf-8")
    rows: list[dict[str, str]] = []
    for match in _INSERT_STDITEMS_RE.finditer(sql):
        values = _split_sql_values(match.group(1))
        if len(values) != len(_OPENMIR2_STDITEMS_COLS):
            continue
        rows.append(dict(zip(_OPENMIR2_STDITEMS_COLS, values)))
    return rows


def load_openmir2_stditem_rows() -> tuple[list[dict[str, str]], Path | None]:
    _, path = resolve_source_paths()
    if path is None:
        return [], None
    return parse_openmir2_stditems(path), path

# ---------------------------------------------------------------------------
# Crystal MagicInfo（task 20）：Server/MirDatabase/MagicInfo.cs BinaryReader 分支
# ---------------------------------------------------------------------------


class CrystalMagic(TypedDict):
    """Crystal MagicInfo 解析结果（cjlaaa 实证布局，逆向自字节样本）。

    该发行版的 MagicInfo 与 Crystal master 版（Name+Spell+BaseCost…）不同：
    记录 = i32 Index + 7bit 名字 + 36 字节定长块 + bool HasDesc + 可选描述串。
    定长块内已验证的字节位（10 条已知技能交叉验证，见 docs/data-coverage.md §5）：
    - b3：职业位旗标 Warrior=1 / Wizard=2 / Taoist=4（可组合）
    - u16@b6：Spell 效果 id
    - u16@b10：技能 0 级习得等级（与 OpenMir2 NeedL1 同语义）
    其余字节未解码（各技能恒定或无对应 schema 字段），不参与映射。
    """

    index: int
    name: str
    job_flag: int  # 位旗标：Warrior=1/Wizard=2/Taoist=4
    spell_id: int
    learn_level: int


_MAGIC_JOB_FLAGS = {1, 2, 3, 4, 5, 6, 7}


def _parse_crystal_magic(reader: _Reader) -> CrystalMagic:
    """cjlaaa Server.MirDB MagicInfo 记录（实证布局，非 Crystal master 分支）。"""
    index = reader.i32()
    name = reader.string()
    if not name or len(name) > 40 or any(ord(c) < 32 for c in name):
        raise ValueError("bad name")
    block = reader.data[reader.pos : reader.pos + 36]
    if len(block) != 36:
        raise ValueError("magic block out of range")
    reader.pos += 36
    # 定长块结构校验：b1/b2 恒 0、b4=3(max_level)、b5=0、b3 职业旗标、习得等级合理
    if block[1] != 0 or block[2] != 0 or block[4] != 3 or block[5] != 0:
        raise ValueError("magic block shape mismatch")
    if block[3] not in _MAGIC_JOB_FLAGS:
        raise ValueError("magic job flag out of range")
    spell_id = struct.unpack_from("<H", block, 6)[0]
    learn_level = struct.unpack_from("<H", block, 10)[0]
    if spell_id > 500 or not (1 <= learn_level <= 100):
        raise ValueError("magic field out of range")
    has_desc = reader.boolean()
    if has_desc:
        desc = reader.string()
        if len(desc) > 300:
            raise ValueError("magic desc out of range")
    return {
        "index": index,
        "name": name,
        "job_flag": block[3],
        "spell_id": spell_id,
        "learn_level": learn_level,
    }


def parse_crystal_mirdb_magics(path: Path) -> list[CrystalMagic]:
    """扫描 Server.MirDB，收集全部可解析的 MagicInfo 连续记录链。

    链长阈值取 4（技能表百余条且定长块约束强；物品段的同名书名不满足
    本布局，不会成链）。同名取首次出现。
    """
    data = path.read_bytes()
    found: list[CrystalMagic] = []
    seen_names: set[str] = set()
    size = len(data)
    i = 0
    while i < size - 48:
        reader = _Reader(data, i)
        chain = 0
        while True:
            save = reader.pos
            try:
                _parse_crystal_magic(reader)
            except (ValueError, UnicodeDecodeError, struct.error, IndexError):
                reader.pos = save
                break
            chain += 1
        if chain >= 4:
            walk = _Reader(data, i)
            for _ in range(chain):
                record = _parse_crystal_magic(walk)
                if record["name"] not in seen_names:
                    seen_names.add(record["name"])
                    found.append(record)
            i = walk.pos
        else:
            i += 1
    return found


class _CrystalMagicCachePayload(TypedDict):
    source_file: str
    records: list[CrystalMagic]


def load_crystal_magic_records() -> tuple[list[CrystalMagic], Path | None]:
    """Crystal MagicInfo 全量记录（带 scripts/cache/crystal_magics.json 缓存）。"""
    path, _ = resolve_source_paths()
    if path is None:
        return [], None
    cache = _CACHE_DIR / "crystal_magics.json"
    if cache.is_file():
        try:
            raw: object = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                payload: _CrystalMagicCachePayload = {
                    "source_file": str(raw.get("source_file", "")),
                    "records": raw.get("records", []),
                }
                if payload["source_file"] == str(path):
                    return payload["records"], path
        except (json.JSONDecodeError, OSError):
            pass
    records = parse_crystal_mirdb_magics(path)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload_out: _CrystalMagicCachePayload = {
        "source_file": str(path),
        "records": records,
    }
    cache.write_text(json.dumps(payload_out, ensure_ascii=False), encoding="utf-8")
    return records, path


# OpenMir2 magics 表（task 20 第二源）
# ---------------------------------------------------------------------------


_OPENMIR2_MAGICS_COLS = (
    "Idx", "MagID", "MagName", "EffectType", "Effect", "Spell", "Power",
    "MaxPower", "DefSpell", "DefPower", "DefMaxPower", "Job", "NeedL1",
    "L1Train", "NeedL2", "L2Train", "NeedL3", "L3Train", "Delay", "Descr",
)

_INSERT_MAGICS_RE = re.compile(
    r"INSERT INTO `magics` VALUES \((.*?)\);", re.DOTALL
)


def parse_openmir2_magics(path: Path) -> list[dict[str, str]]:
    """OpenMir2 mir2_data.sql 的 magics 表全行（列值保持字符串原样）。

    列语义（经经典设定交叉验证）：Job 0=战士/1=法师/2=道士；
    NeedL1/L2/L3 = 技能 0/1/2 级习得等级；Delay 单位为源自定义（非毫秒，
    与 Crystal DelayBase 不同量纲，仅作参考不比对）。
    """
    sql = path.read_text(encoding="utf-8")
    rows: list[dict[str, str]] = []
    for match in _INSERT_MAGICS_RE.finditer(sql):
        values = _split_sql_values(match.group(1))
        if len(values) != len(_OPENMIR2_MAGICS_COLS):
            continue
        rows.append(dict(zip(_OPENMIR2_MAGICS_COLS, values)))
    return rows


def load_openmir2_magic_rows() -> tuple[list[dict[str, str]], Path | None]:
    _, path = resolve_source_paths()
    if path is None:
        return [], None
    return parse_openmir2_magics(path), path
