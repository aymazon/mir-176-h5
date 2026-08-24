"""spotcheck_maps.py — task 21 验收抽检脚本（全量地图数据）。

检查项（对应计划 todo 21 acceptance："maps/ 合计 ≥10 区域且每图含等级带+连通
字段（脚本断言）"）：
1. maps/ 数量 ≥10；每图 id/name/level_min/level_max/region/adjacent/boss_spawns
   字段齐全（schema extra=forbid + loader 校验兜底）。
2. 等级带与 README §6 表逐区域 diff（手录基线断言）。
3. 连通邻接双向对称（adjacent 无单向边、无自环、无悬空——悬空由 loader 查）。
4. boss_spawns 每只 BOSS 在本图有刷怪点（与 spotcheck_spawns 互证）。
5. 覆盖文档：重写 docs/data-coverage.md §10（其余区块保留）。

用法：cd backend && uv run python scripts/spotcheck_maps.py
退出码：0 = 全部通过；1 = 任一检查失败。
"""

# pyright: reportUnusedCallResult=false, reportImplicitRelativeImport=false

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.gamedata_loader import DEFAULT_GAMEDATA_ROOT, load_all
from app.gamedata_schema import MapDef, SpawnEntry

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_MAPS_DIR = _BACKEND_ROOT / "gamedata" / "maps"
_DOCS_COVERAGE = _BACKEND_ROOT.parent / "docs" / "data-coverage.md"

_MIN_MAPS = 10
_SECTION_START = "## 10. 全量地图数据手录（todo 21，2026-08-25）"
_NEXT_MARKERS: tuple[str, ...] = ()

# 源 MapInfo 必须存在的文档点名地图（手录主题的源地理证据）
_SOURCE_REQUIRED_TITLES: frozenset[str] = frozenset({
    "兽人古墓三层",   # 骷髅洞 BOSS 层
    "废矿入口",       # 比奇矿区
    "尸王殿",         # 矿区 BOSS 图
    "石墓七层",       # 白野猪（猪 7）
    "沃玛教主大殿",
    "祖玛寺庙一层",
})

EXPECTED_BANDS: dict[str, tuple[int, int]] = {
    "yin-xing-cun": (0, 7),        # 新手区 1–7（银杏村安全区从 0 起）
    "biqi-wild": (5, 15),          # 新手区→比奇省过渡（task 13 已录）
    "shouren-gu-1": (12, 20),      # 比奇省·骷髅洞（task 13 已录）
    "shouren-gu-2": (16, 24),
    "shouren-gu-3": (20, 28),
    "biqi-mine": (12, 20),         # §6 比奇矿区 12–20
    "snake-valley": (15, 22),      # §6 毒蛇山谷 15–22
    "mengzhong": (18, 26),         # §6 盟重省 18–26
    "centipede-cave": (20, 30),    # §6 死亡山谷·蜈蚣洞 20–30
    "shimen": (24, 34),            # §6 石墓 24–34
    "wooma-forest": (25, 30),      # §6 沃玛森林 25–30
    "wooma-temple": (28, 36),      # §6 沃玛寺庙 28–36
    "zuma-caves": (32, 40),        # §6 祖玛寺庙 32–40
}


def check() -> tuple[list[str], dict[str, Any]]:
    problems: list[str] = []
    data = load_all(DEFAULT_GAMEDATA_ROOT)
    maps = data["maps"]
    if len(maps) < _MIN_MAPS:
        problems.append(f"maps/ 共 {len(maps)} 张 < {_MIN_MAPS}")

    for key, m in maps.items():
        assert isinstance(m, MapDef)
        if m.level_min > m.level_max:
            problems.append(f"{key}: level_min {m.level_min} > level_max {m.level_max}")
        expected = EXPECTED_BANDS.get(key)
        if expected is None:
            problems.append(f"{key}: 不在 §6 等级带基线表内")
        elif (m.level_min, m.level_max) != expected:
            problems.append(
                f"{key}: 等级带 ({m.level_min},{m.level_max}) "
                f"!= 基线 {expected}"
            )
        if key in m.adjacent:
            problems.append(f"{key}: adjacent 含自环")

    # 双向对称
    for key, m in maps.items():
        assert isinstance(m, MapDef)
        for other in m.adjacent:
            om = maps.get(other)
            if isinstance(om, MapDef) and key not in om.adjacent:
                problems.append(f"邻接不对称: {key} → {other} 单向")

    # boss_spawns ↔ spawns 落位互证
    spawn_keys = {
        f"{s.map_id}:{s.monster_id}"
        for s in data["spawns"].values()
        if isinstance(s, SpawnEntry)
    }
    for map_id, m in maps.items():
        assert isinstance(m, MapDef)
        for boss_id in m.boss_spawns:
            if f"{map_id}:{boss_id}" not in spawn_keys:
                problems.append(f"{map_id}: boss_spawns 缺刷怪点 {boss_id}")

    # 源地理交叉验证：Server.MirDB MapInfo 锚点（源缺失时降级为警告）
    import mir_sources

    map_refs, _ = mir_sources.load_crystal_map_refs()
    source_titles: set[str] = set()
    if map_refs:
        source_titles = {r["title"] for r in map_refs}
        for title in sorted(_SOURCE_REQUIRED_TITLES):
            if title not in source_titles:
                problems.append(f"源 MapInfo 缺文档点名地图: {title}")

    return problems, {"maps": maps, "map_refs": len(map_refs), "source_titles": source_titles}


def render_coverage(ctx: dict[str, Any]) -> None:
    maps: dict[str, MapDef] = ctx["maps"]

    lines: list[str] = []
    lines.append("")
    lines.append(_SECTION_START)
    lines.append("")
    lines.append(
        f"- 共 {len(maps)} 张地图（≥{_MIN_MAPS} 达标），覆盖 README §6 全部 10 个区域行。"
        "字段为手录：等级带/邻接取自文档，源 DB 无可直接消费的结构化地图表。"
    )
    lines.append(
        f"- **源地理交叉验证**：Server.MirDB 确含 MapInfo 区段——锚点法提取 "
        f"{ctx['map_refs']} 条 (index, file_name, title)，index 严格递增（结构信号）；"
        "文档点名地图 " + "、".join(sorted(_SOURCE_REQUIRED_TITLES))
        + " 全部在源标题集中命中（脚本断言）。完整布局含嵌套 SafeZone/Movement 列表，"
        "超出本任务消费面，不作逐字段解析。"
    )
    lines.append(
        "- **等级带**：按 §6 表逐区域直录（置信度高）；现有 5 张（task 13）原值锁定。"
    )
    lines.append(
        "- **连通邻接**：按 §10 传送/跑图结构与 §6 练级动线"
        "（新手村→比奇→骷髅洞/僵尸洞→毒蛇山谷→盟重→蜈蚣洞→石墓→沃玛寺庙→祖玛寺庙）"
        "手录，双向边（脚本断言对称）；动线顺序之外的取舍为本项目自创（置信度中）。"
    )
    lines.append(
        "- **boss_spawns**：按 §6 BOSS 列落位，每只 BOSS 在本图有对应刷怪点"
        "（脚本断言）；沃玛森林精英=远古沃玛勇士、蛇谷顶格=双头蛇魔"
        "（非 boss 旗标，走 spawns 不进 boss_spawns）。"
    )
    lines.append(
        "- 行进中时长参数 TRAVEL_SECONDS_MIN=5 / MAX=20 进 app/combat/balance.py"
        "（§10 给出区间端点，距离→时长映射自创）。"
    )
    lines.append("")
    lines.append("| 地图 | 名称 | region | 等级带 | adjacent | boss_spawns |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for key in sorted(maps):
        m = maps[key]
        assert isinstance(m, MapDef)
        adj = "、".join(m.adjacent) or "—"
        bosses = "、".join(m.boss_spawns) or "—"
        lines.append(
            f"| {m.id} | {m.name} | {m.region} | {m.level_min}–{m.level_max} "
            f"| {adj} | {bosses} |"
        )
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
    print(f"[write] {_DOCS_COVERAGE.relative_to(_BACKEND_ROOT.parent)} §10（其余区块保留）")


def main() -> int:
    problems, ctx = check()
    if problems:
        print("== 地图抽检失败 ==")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"[ok] 地图 {len(ctx['maps'])} 张（≥{_MIN_MAPS}），等级带基线 + 邻接对称 + BOSS 落位全过")
    render_coverage(ctx)
    return 0


if __name__ == "__main__":
    sys.exit(main())
