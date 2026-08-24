"""mir_sources.py — Mir2 服务端源数据解析（task 18）。

两个独立数值源：
1. 主源 Crystal ``Server.MirDB``（Suprcode/Crystal.Database 仓库 cjlaaa/ 目录，
   与 levels.json 主源 GeeM2 Exps.ini 同一发行版家族）——自定义二进制格式，
   按 Crystal ``MonsterInfo(BinaryReader)``（LoadVersion∈[89,115) 分支）逐字段解析；
   记录区定位用"连续记录链扫描"（≥3 条合法记录即认，索引连续性做旁证）。
2. 第二源 OpenMir2 ``sql/mir2_data.sql``（github.com/mirbeta/OpenMir2）——
   标准 MySQL dump 的 ``monsters`` 表 INSERT 行。

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
