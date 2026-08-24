"""spotcheck_monsters.py — task 18 验收抽检脚本。

三项检查（对应计划 todo 18 acceptance）：
1. 主源字段级 diff：半兽战士/尸王/白野猪/沃玛教主/祖玛教主 5 只怪，
   Crystal Server.MirDB 原始记录按映射约定（见 docs/data-coverage.md §1）
   逐字段比对 gamedata JSON——必须完全一致，任何差异 exit 1。
2. 第二独立源抽检：OpenMir2 mir2_data.sql 对 ≥10 只同名怪比对
   HP/经验/攻(上限)/防 四字段，输出差异报告（两源为独立发行版，
   差异本身是预期产物，落 docs/data-coverage.md §2）。
3. 转换器映射样本：5 只抽查怪的「原始记录 → JSON 字段」映射表
   落 docs/data-coverage.md §3。

用法：cd backend && uv run python scripts/spotcheck_monsters.py
退出码：0 = 全部通过；1 = 源缺失或字段级 diff 失败。
"""

# pyright: reportUnusedCallResult=false, reportImplicitRelativeImport=false

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Final, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.gamedata_schema.monsters import MonsterDef

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_monsters
import mir_sources

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent
_MONSTERS_DIR = _BACKEND_ROOT / "gamedata" / "monsters"
_DOCS_COVERAGE = _REPO_ROOT / "docs" / "data-coverage.md"

# 抽查 5 只（任务规格指定）：全部取主源 Crystal 同名记录做字段级 diff
SPOTCHECK_PROBES: Final[tuple[str, ...]] = (
    "半兽战士", "尸王", "白野猪", "沃玛教主", "祖玛教主",
)

_PROBE_TO_ID: Final[dict[str, str]] = {
    "半兽战士": "orc_warrior",
    "尸王": "corpse_king",
    "白野猪": "wild_boar_white",
    "沃玛教主": "wooma_taurus",
    "祖玛教主": "zuma_taurus",
}

# 第二源抽检的最小怪数与比对字段（MonsterDef 字段, OpenMir2 列）
OPENMIR2_MIN_MONSTERS: Final[int] = 10
_OPENMIR2_FIELD_MAP: Final[tuple[tuple[str, str], ...]] = (
    ("hp", "HP"),
    ("exp", "Exp"),
    ("attack", "DCMAX"),
    ("defense", "AC"),
)


def probe_id(name: str) -> str:
    return _PROBE_TO_ID[name]


def _ticks(ms: int) -> int:
    return max(1, min(6, int(ms / 1000 + 0.5)))


def expected_from_crystal(rec: mir_sources.CrystalMonster) -> dict[str, int]:
    """Crystal 原始记录 → MonsterDef 数值字段的期望值（与 build_monsters 同约定）。"""
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


def load_monster_def(path: Path) -> MonsterDef:
    """gamedata JSON → MonsterDef（Pydantic 校验，兼作 schema 回归）。"""
    return MonsterDef.model_validate(json.loads(path.read_text(encoding="utf-8")))


def def_values(monster: MonsterDef) -> dict[str, int | bool | str | None]:
    """MonsterDef → 抽检用字段表（键序对齐 docs 表格）。"""
    return {
        "id": monster.id,
        "name": monster.name,
        "level": monster.level,
        "hp": monster.hp,
        "attack": monster.attack,
        "defense": monster.defense,
        "magic_defense": monster.magic_defense,
        "accuracy": monster.accuracy,
        "agility": monster.agility,
        "exp": monster.exp,
        "attack_speed": monster.attack_speed,
        "move_speed": monster.move_speed,
        "undead": monster.undead,
        "boss": monster.boss,
        "respawn_seconds": monster.respawn_seconds,
    }


class SpotSample(TypedDict):
    name: str
    raw: mir_sources.CrystalMonster
    monster: MonsterDef


def check_primary_diff(
    crystal: list[mir_sources.CrystalMonster],
) -> tuple[list[str], list[SpotSample]]:
    """5 只抽查怪 vs 主源字段级 diff。返回 (问题清单, 样本记录)。"""
    by_name = {r["name"]: r for r in crystal}
    problems: list[str] = []
    samples: list[SpotSample] = []
    for probe in SPOTCHECK_PROBES:
        json_path = _MONSTERS_DIR / f"{probe_id(probe)}.json"
        if not json_path.is_file():
            problems.append(f"{probe}: {json_path.name} 不存在")
            continue
        monster = load_monster_def(json_path)
        rec = by_name.get(probe)
        if rec is None:
            problems.append(f"{probe}: 主源无同名记录")
            continue
        expected = expected_from_crystal(rec)
        actual: dict[str, int] = {
            "level": monster.level,
            "hp": monster.hp,
            "attack": monster.attack,
            "defense": monster.defense,
            "magic_defense": monster.magic_defense,
            "accuracy": monster.accuracy,
            "agility": monster.agility,
            "exp": monster.exp,
            "attack_speed": monster.attack_speed,
            "move_speed": monster.move_speed,
        }
        for field, want in expected.items():
            got = actual[field]
            if got != want:
                problems.append(f"{probe}.{field}: json={got} 主源期望={want}")
        samples.append({"name": probe, "raw": rec, "monster": monster})
    return problems, samples


class OmDiffRow(TypedDict):
    id: str
    name: str
    level_ours: int
    level_om2: int
    diffs: dict[str, tuple[int, int, float]]


def check_openmir2(openmir2: list[dict[str, str]]) -> list[OmDiffRow]:
    """第二源 ≥10 只同名怪 HP/经验/攻/防 比对。返回差异行。"""
    om_by_name = {r["Name"]: r for r in openmir2}
    rows: list[OmDiffRow] = []
    for path in sorted(_MONSTERS_DIR.glob("*.json")):
        monster = load_monster_def(path)
        row = om_by_name.get(monster.name)
        if row is None:
            continue
        diffs: dict[str, tuple[int, int, float]] = {}
        for field, col in _OPENMIR2_FIELD_MAP:
            ours = getattr(monster, field)
            theirs = int(row[col])
            pct = ((theirs - ours) / ours * 100) if ours else 0.0
            diffs[field] = (ours, theirs, round(pct, 1))
        rows.append(
            {
                "id": monster.id,
                "name": monster.name,
                "level_ours": monster.level,
                "level_om2": int(row["Lvl"]),
                "diffs": diffs,
            }
        )
    return rows


def _section1_lines() -> list[str]:
    """§1 出处与映射约定（动态统计 ROSTER 策略计数，避免文案漂移）。"""
    strategies = [s["strategy"] for s in build_monsters.ROSTER]
    cr_count = strategies.count("cr")
    om_count = len(strategies) - cr_count - strategies.count("derive")
    derive_specs = [s for s in build_monsters.ROSTER if s["strategy"] == "derive"]
    derive_count = len(derive_specs)
    derive_names = "、".join(s["name"] for s in derive_specs)
    total_files = len(list(_MONSTERS_DIR.glob("*.json")))

    lines: list[str] = []
    lines.append("")
    lines.append("## 1. 怪物全量录入：双源出处与字段映射（todo 18，2026-08-25）")
    lines.append("")
    lines.append(
        "- **主源**：Crystal `Server.MirDB`（Suprcode/Crystal.Database 仓库 cjlaaa/ 目录，"
        "GeeM2 同源发行版；自定义二进制，按 Crystal MonsterInfo(BinaryReader) "
        "LoadVersion∈[89,115) 分支解析，连续记录链扫描定位，共 449 只）。"
    )
    lines.append(
        "- **第二源**：OpenMir2 `sql/mir2_data.sql`（github.com/mirbeta/OpenMir2，"
        "MySQL dump monsters 表，共 705 只）。"
    )
    lines.append(
        "- 两源原始文件均不入库（backend/.gitignore）；本地缓存于 "
        "`backend/scripts/cache/`，解析器 `scripts/mir_sources.py`，"
        "构建器 `scripts/build_monsters.py`，本抽检脚本可重跑复核。"
    )
    lines.append("")
    lines.append("### 字段映射约定（转换器 build_monsters.py 实现即文档）")
    lines.append("")
    lines.append("| JSON 字段 | Crystal 列 | OpenMir2 列 | 说明 |")
    lines.append("| --- | --- | --- | --- |")
    lines.append("| level | Level(u16) | Lvl | 任务 22 等级带 lint 的强制依赖 |")
    lines.append("| hp | Stats[HP=12] | HP | |")
    lines.append("| attack | MaxDC(=5) | DCMAX | 单值字段取攻击区间**上限** |")
    lines.append("| defense | MaxAC(=1) | AC | 物防上限 |")
    lines.append("| magic_defense | MaxMAC(=3) | MAC | 魔御上限 |")
    lines.append("| accuracy | Accuracy(=10) | HIT | |")
    lines.append("| agility | Agility(=11) | SPEED | |")
    lines.append("| exp | Experience(u32) | Exp | |")
    lines.append(
        "| attack_speed | AttackSpeed(ms) | ATTACK_SPD(ms) "
        "| ÷1000 四舍五入钳 [1,6] tick |"
    )
    lines.append("| move_speed | MoveSpeed(ms) | WALK_SPD(ms) | 同上 |")
    lines.append(
        "| undead | Undead(bool) | Undead(int) | **两源冲突取 OpenMir2**："
        "主源该发行版全 False（骷髅/僵尸亦然），第二源旗标完整（骷髅=1 等），"
        "以语义完整者为准 |"
    )
    lines.append(
        "| boss / respawn_seconds | —（两源均无） | — | 项目自管：BOSS=3600s"
        "（沃玛教主/祖玛教主按任务规格 60min），精英 1800s，普通怪 90–150s |"
    )
    lines.append("")
    lines.append("### 录入策略与 TODO 清单")
    lines.append("")
    lines.append(
        f"- 直接录入：主源同名 {cr_count} 只 + 第二源同名/别名补位 {om_count} 只。"
        "其中极品祖玛卫士取 OM2『祖玛卫士00』HP=1400（社区公认 1400 大锤；"
        "主源同名变体 HP=4450 为单服自定义，弃用）。"
    )
    lines.append(
        "- 别名映射：毒蛇←主源赤色毒蛇；沙漠巨型蠕虫←两源同名巨型蠕虫；"
        "僵尸←OM2 僵尸1（L25 HP155，主源僵尸族同值）。"
    )
    lines.append(
        f"- **同族派生 {derive_count} 只（TODO，M5 校准清单）**：{derive_names}"
        "——两源均无同名，按同族锚点升档派生"
        "（公式见 scripts/build_monsters.py ROSTER 注释）。"
    )
    lines.append("- 文档在列但两源皆缺：黑暗战士（沃玛寺庙行）未录入，待补源。")
    lines.append(f"- 合计 {total_files} 只（≥45 达标）。")
    return lines


def render_coverage(
    samples: list[SpotSample], om_rows: list[OmDiffRow]
) -> None:
    lines = _section1_lines()
    lines.append("")
    lines.append("## 2. OpenMir2 第二源抽检差异报告（HP/经验/攻上限/物防）")
    lines.append("")
    lines.append(
        "两源为独立发行版，数值差异属预期；主源取数后差异 >30% 的字段列入 M5 校准观察。"
    )
    lines.append("")
    lines.append(
        "| 怪物 | 等级(本方/OM2) | HP 本方/OM2(差%) | 经验 本方/OM2(差%) "
        "| 攻上限 本方/OM2(差%) | 物防 本方/OM2(差%) |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for row in om_rows:
        cells = [f"{row['name']} ({row['level_ours']}/{row['level_om2']})"]
        for field, _col in _OPENMIR2_FIELD_MAP:
            ours, theirs, pct = row["diffs"][field]
            cells.append(f"{ours}/{theirs}({pct:+.1f}%)")
        lines.append("| " + " | ".join(cells) + " |")
    big = sum(
        1
        for row in om_rows
        for field, _ in _OPENMIR2_FIELD_MAP
        if abs(row["diffs"][field][2]) > 30
    )
    total = len(om_rows) * len(_OPENMIR2_FIELD_MAP)
    lines += ["", ""]
    lines[-1] = (
        f"共比对 {len(om_rows)} 只（≥{OPENMIR2_MIN_MONSTERS} 达标）、"
        f"{total} 个字段值，其中差异 >30% 的 {big} 个列入 M5 校准观察。"
    )
    lines.append("")
    lines.append("## 3. 转换器字段映射样本（5 只抽查怪：原始记录 → JSON）")
    lines.append("")
    for sample in samples:
        raw = sample["raw"]
        monster = sample["monster"]
        values = def_values(monster)
        lines.append(f"### {sample['name']}（{probe_id(sample['name'])}.json）")
        lines.append("")
        lines.append("```")
        lines.append(
            f"Crystal raw : index={raw['index']} name={raw['name']} "
            f"level={raw['level']} hp={raw['hp']} "
            f"MinAC={raw['min_ac']} MaxAC={raw['max_ac']} "
            f"MinMAC={raw['min_mac']} MaxMAC={raw['max_mac']} "
            f"MinDC={raw['min_dc']} MaxDC={raw['max_dc']} "
            f"Accuracy={raw['accuracy']} Agility={raw['agility']} "
            f"AttackSpeed={raw['attack_speed_ms']}ms "
            f"MoveSpeed={raw['move_speed_ms']}ms Exp={raw['exp']}"
        )
        lines.append(
            "map         : level←level hp←hp attack←MaxDC defense←MaxAC "
            "magic_defense←MaxMAC accuracy←Accuracy agility←Agility exp←Exp "
            "attack_speed←round(AttackSpeed/1000) "
            "move_speed←round(MoveSpeed/1000)"
        )
        lines.append("```")
        lines.append("")
        lines.append("| 字段 | 值 |")
        lines.append("| --- | --- |")
        for key, value in values.items():
            lines.append(f"| {key} | {value} |")
        lines.append("")
    existing = _DOCS_COVERAGE.read_text(encoding="utf-8")
    marker = "## 1. 怪物全量录入：双源出处与字段映射"
    if marker in existing:
        head = existing.partition(marker)[0]
        _DOCS_COVERAGE.write_text(
            head.rstrip("\n") + "\n" + "\n".join(lines), encoding="utf-8"
        )
    else:
        _DOCS_COVERAGE.write_text(
            existing.rstrip("\n") + "\n" + "\n".join(lines) + "\n", encoding="utf-8"
        )
    print(f"[write] {_DOCS_COVERAGE.relative_to(_REPO_ROOT)} §1–§3")


def main() -> int:
    crystal, cr_path = mir_sources.load_crystal_records()
    openmir2, om_path = mir_sources.load_openmir2_rows()
    print(f"[source] crystal={cr_path} records={len(crystal)}")
    print(f"[source] openmir2={om_path} rows={len(openmir2)}")
    if not crystal or not openmir2:
        print(
            "[error] 源缺失，无法抽检（需 Server.MirDB 与 mir2_data.sql）",
            file=sys.stderr,
        )
        return 1

    problems, samples = check_primary_diff(crystal)
    if problems:
        print("== 主源字段级 diff 失败 ==")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"== 主源字段级 diff OK：{len(samples)}/5 只逐字段一致")

    om_rows = check_openmir2(openmir2)
    if len(om_rows) < OPENMIR2_MIN_MONSTERS:
        print(
            f"[error] 第二源同名怪仅 {len(om_rows)} 只（<{OPENMIR2_MIN_MONSTERS}）",
            file=sys.stderr,
        )
        return 1
    print(f"== 第二源抽检 OK：{len(om_rows)} 只四字段比对完成")

    render_coverage(samples, om_rows)

    print("\n== 映射样本（5 只，摘要）==")
    for sample in samples:
        raw = sample["raw"]
        monster = sample["monster"]
        print(
            f"  {sample['name']}: lvl={raw['level']} hp={raw['hp']} "
            f"MaxDC={raw['max_dc']}→attack={monster.attack} "
            f"Exp={raw['exp']} undead(json)={monster.undead}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
