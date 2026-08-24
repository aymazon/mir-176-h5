"""spotcheck_spawns.py — task 21 验收抽检脚本（刷怪表）。

检查项（对应计划 todo 21 acceptance）：
1. spawns/ 刷怪点 ≥30，覆盖全部 10+ 区域（每张地图至少 1 个刷怪点）。
2. respawn_seconds 与 monsters/ 的 respawn_seconds 一致（单一来源回读）。
3. locations 点位不重复、坐标非负；density ≤ count（设计人口语义）。
4. BOSS 落位：maps/*.json boss_spawns 里每只 BOSS 在该图有刷怪点。
5. 覆盖文档：重写 docs/data-coverage.md §9（其余区块保留）。

用法：cd backend && uv run python scripts/spotcheck_spawns.py
退出码：0 = 全部通过；1 = 任一检查失败。
"""

# pyright: reportUnusedCallResult=false, reportImplicitRelativeImport=false

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.gamedata_loader import DEFAULT_GAMEDATA_ROOT, load_all
from app.gamedata_schema import MapDef, MonsterDef

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_SPAWNS_DIR = _BACKEND_ROOT / "gamedata" / "spawns"
_DOCS_COVERAGE = _BACKEND_ROOT.parent / "docs" / "data-coverage.md"

_MIN_SPAWNS = 30
_SECTION_START = "## 9. 刷怪表全量录入（todo 21，2026-08-25）"
_NEXT_MARKERS: tuple[str, ...] = ("## 10. 全量地图数据手录",)


def load_raw_spawns() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(_SPAWNS_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        out[f"{raw['map_id']}:{raw['monster_id']}"] = raw
    return out


def check() -> tuple[list[str], dict[str, Any]]:
    problems: list[str] = []
    data = load_all(DEFAULT_GAMEDATA_ROOT)
    spawns = load_raw_spawns()

    if len(spawns) < _MIN_SPAWNS:
        problems.append(f"spawns/ 共 {len(spawns)} 点 < {_MIN_SPAWNS}")

    # 每张地图至少一个刷怪点（10+ 区域全覆盖的强口径）
    by_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for key, raw in spawns.items():
        by_map[raw["map_id"]].append(raw)
    empty_maps = sorted(set(data["maps"]) - set(by_map))
    if empty_maps:
        problems.append(f"无刷怪点的地图: {empty_maps}")

    # respawn 与 monsters 单一来源一致
    for key, raw in spawns.items():
        monster = data["monsters"].get(raw["monster_id"])
        assert isinstance(monster, MonsterDef)
        if raw["respawn_seconds"] != monster.respawn_seconds:
            problems.append(
                f"{key}: respawn_seconds={raw['respawn_seconds']} "
                f"!= monsters/{monster.id}.json {monster.respawn_seconds}"
            )
        if raw["density"] > raw["count"]:
            problems.append(f"{key}: density {raw['density']} > count {raw['count']}")
        seen: set[tuple[int, int]] = set()
        for loc in raw["locations"]:
            pair = (loc["x"], loc["y"])
            if loc["x"] < 0 or loc["y"] < 0:
                problems.append(f"{key}: 负坐标 {pair}")
            if pair in seen:
                problems.append(f"{key}: 重复点位 {pair}")
            seen.add(pair)

    # BOSS 落位：boss_spawns ⊆ 本图刷怪点
    for map_id, m in data["maps"].items():
        assert isinstance(m, MapDef)
        for boss_id in m.boss_spawns:
            if f"{map_id}:{boss_id}" not in spawns:
                problems.append(f"maps/{map_id}.json boss_spawns 缺刷怪点: {boss_id}")

    boss_ids = {
        m.id for m in data["monsters"].values() if isinstance(m, MonsterDef) and m.boss
    }
    return problems, {
        "spawns": spawns, "by_map": dict(by_map), "boss_ids": boss_ids,
    }


def render_coverage(ctx: dict[str, Any]) -> None:
    spawns: dict[str, dict[str, Any]] = ctx["spawns"]
    by_map: dict[str, list[dict[str, Any]]] = ctx["by_map"]

    lines: list[str] = []
    lines.append("")
    lines.append(_SECTION_START)
    lines.append("")
    lines.append(
        f"- 共 {len(spawns)} 个刷怪点（≥{_MIN_SPAWNS} 达标），覆盖全部 "
        f"{len(by_map)} 张地图（每图 ≥1 点）；键 `<map_id>:<monster_id>`。"
    )
    lines.append(
        "- respawn_seconds 从 gamedata/monsters/ 回读（单一来源）："
        "BOSS=3600s、精英=1800s、普通 90–150s；density=同屏存活上限"
        "（zone.fill_initial 消费），count≈2×density 为在册人口，点位轮转 round-robin。"
    )
    lines.append(
        "- **密度/点位的出处**：Server.MirDB 的 RespawnInfo 含嵌套 Region 列表，"
        "版本分支多、本任务内无法可靠定布局，故密度/点位按 §6 怪物构成列自定；"
        "重生间隔沿用 task 18 怪物表（BOSS 3600s 社区一致）。"
    )
    lines.append(
        "- **等级带错位说明**：怪物 Crystal 等级系统性高于 README §6 玩家动线带"
        "（私服源膨胀，如尸王 L50 对矿区带 12–20），按 §6 怪物构成列主题归属放置；"
        "todo 22 等级带 lint 需按「玩家带 vs 怪物源等级」分轨处理，错位清单列 M5 观察。"
    )
    lines.append("")
    lines.append("| 地图 | 刷怪点数 | 怪物（density） |")
    lines.append("| --- | --- | --- |")
    for map_id in sorted(by_map):
        entries = sorted(by_map[map_id], key=lambda r: r["monster_id"])
        detail = "、".join(
            f"{r['monster_id']}({r['density']})"
            + ("★" if r["monster_id"] in ctx["boss_ids"] else "")
            for r in entries
        )
        lines.append(f"| {map_id} | {len(entries)} | {detail} |")
    lines.append("")
    lines.append("注：★=BOSS 点（density=1、respawn=3600s）。")
    lines.append("")

    existing = _DOCS_COVERAGE.read_text(encoding="utf-8") if _DOCS_COVERAGE.is_file() else ""
    start = existing.find(_SECTION_START)
    if start < 0:
        doc = existing.rstrip("\n") + "\n" + "\n".join(lines)
    else:
        end = len(existing)
        for marker in _NEXT_MARKERS:
            pos = existing.find(marker, start)
            if pos >= 0:
                end = min(end, pos)
        doc = existing[:start] + "\n".join(lines) + "\n" + existing[end:]
    _DOCS_COVERAGE.write_text(doc, encoding="utf-8")
    print(f"[write] {_DOCS_COVERAGE.relative_to(_BACKEND_ROOT.parent)} §9（其余区块保留）")


def main() -> int:
    problems, ctx = check()
    if problems:
        print("== 刷怪表抽检失败 ==")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        f"[ok] 刷怪点 {len(ctx['spawns'])} 个（≥{_MIN_SPAWNS}）、"
        f"{len(ctx['by_map'])} 图全覆盖、respawn 单一来源一致、BOSS 落位完整"
    )
    render_coverage(ctx)
    return 0


if __name__ == "__main__":
    sys.exit(main())
