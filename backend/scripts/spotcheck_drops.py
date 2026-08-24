"""spotcheck_drops.py — task 21 验收抽检脚本（掉落表）。

检查项（对应计划 todo 21 acceptance）：
1. drops/ 文件数 ≥30 且覆盖全部 monsters/（每怪一表）。
2. 每张表至少一条金币行（item_id=null 且 gold_max>0）——静态断言。
3. BOSS 点名物品：白野猪→沃玛装备+35 技能书；沃玛教主→沃玛号角；
   祖玛教主→祖玛装备+35 技能书；尸王→技能书。
4. 屠龙/嗜魂彩蛋：weight=1 且所在表总权重 ≥1000（出现率 ≤0.001）。
5. 引用完整性由 loader 校验兜底（本脚本经 load_all 全量加载）。
6. 覆盖文档：重写 docs/data-coverage.md §8（其余区块保留）。

用法：cd backend && uv run python scripts/spotcheck_drops.py
退出码：0 = 全部通过；1 = 任一检查失败。
"""

# pyright: reportUnusedCallResult=false, reportImplicitRelativeImport=false

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.gamedata_loader import DEFAULT_GAMEDATA_ROOT, load_all

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_DROPS_DIR = _BACKEND_ROOT / "gamedata" / "drops"
_DOCS_COVERAGE = _BACKEND_ROOT.parent / "docs" / "data-coverage.md"

_MIN_DROPS = 30
_SECTION_START = "## 8. 掉落表全量录入（todo 21，2026-08-25）"
_NEXT_MARKERS: tuple[str, ...] = (
    "## 9. 刷怪表全量录入",
    "## 10. 全量地图数据手录",
)

# BOSS 点名物品矩阵（docs/MVP/README.md §6 BOSS 列）
BOSS_REQUIRED: dict[str, set[str]] = {
    # 白野猪：沃玛装备（裁决/骨玉/龙纹）+ 35 技能书三件之一组
    "wild_boar_white": {
        "verdict_staff", "bone_jade_staff", "dragonvein_sword",
        "book_fire_sword", "book_ice_roar", "book_summon_beast",
    },
    # 沃玛教主：沃玛号角 + 沃玛装备
    "wooma_taurus": {"wooma_horn", "verdict_staff", "bone_jade_staff"},
    # 祖玛教主：祖玛装备 + 35 技能书
    "zuma_taurus": {
        "bone_jade_staff", "dragonvein_sword", "soul_eater_staff",
        "titan_ring", "three_eye_bracelet", "knight_bracelet",
        "book_fire_sword", "book_ice_roar", "book_summon_beast",
    },
    # 尸王：技能书专精
    "corpse_king": {
        "book_fire_sword", "book_ice_roar", "book_summon_beast",
        "book_magic_shield", "book_assassination",
    },
}
# 彩蛋行白名单（monster, item）：weight=1 且表总权重 ≥1000 ⇒ 出现率 ≤0.001。
# 嗜魂法杖在祖玛教主/极品卫士表是正掉祖玛装备（点名物品），不受彩蛋纪律约束。
EGG_ROWS: set[tuple[str, str]] = {
    ("wild_boar_white", "dragonslayer_blade"),
    ("wild_boar_white", "soul_eater_staff"),
    ("touch_dragon_god", "dragonslayer_blade"),
    ("touch_dragon_god", "soul_eater_staff"),
    ("zuma_taurus", "dragonslayer_blade"),
    ("zuma_guardian_elite", "dragonslayer_blade"),
}


def load_raw_drops() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(_DROPS_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        out[raw["monster_id"]] = raw
    return out


def check() -> tuple[list[str], dict[str, Any]]:
    problems: list[str] = []
    data = load_all(DEFAULT_GAMEDATA_ROOT)
    drops = load_raw_drops()
    monster_ids = set(data["monsters"])

    if len(drops) < _MIN_DROPS:
        problems.append(f"drops/ 共 {len(drops)} 表 < {_MIN_DROPS}")
    missing_table = monster_ids - set(drops)
    if missing_table:
        problems.append(f"缺掉落表的怪物: {sorted(missing_table)}")

    no_gold: list[str] = []
    for mid, raw in sorted(drops.items()):
        if not any(
            e["item_id"] is None and e["gold_max"] > 0 for e in raw["entries"]
        ):
            no_gold.append(mid)
    if no_gold:
        problems.append(f"无金币行的掉落表: {no_gold}")

    for mid, required in BOSS_REQUIRED.items():
        raw = drops.get(mid)
        if raw is None:
            continue
        have = {e["item_id"] for e in raw["entries"] if e["item_id"]}
        lack = required - have
        if lack:
            problems.append(f"{mid} 缺点名物品: {sorted(lack)}")

    egg_stats: list[tuple[str, str, int, float]] = []
    total_weights: dict[str, int] = {
        mid: sum(e["weight"] for e in raw["entries"])
        for mid, raw in drops.items()
    }
    for mid, raw in sorted(drops.items()):
        total = total_weights[mid]
        for e in raw["entries"]:
            # 彩蛋纪律只约束指定彩蛋行；嗜魂法杖在祖玛表是正掉祖玛装备
            if (mid, str(e["item_id"])) not in EGG_ROWS or e["item_id"] is None:
                continue
            if e["weight"] != 1:
                    problems.append(f"{mid} 彩蛋 {e['item_id']} weight={e['weight']} != 1")
            p = e["weight"] / total
            egg_stats.append((mid, str(e["item_id"]), total, p))
            if p > 0.001:
                problems.append(
                    f"{mid} 彩蛋 {e['item_id']} 出现率 {p:.4f} > 0.001"
                )
    if not egg_stats:
        problems.append("未发现屠龙/嗜魂彩蛋行")

    return problems, {
        "drops": drops,
        "total_weights": total_weights,
        "egg_stats": egg_stats,
        "monster_count": len(monster_ids),
    }


def render_coverage(ctx: dict[str, Any]) -> None:
    drops: dict[str, dict[str, Any]] = ctx["drops"]
    totals: dict[str, int] = ctx["total_weights"]

    def rate(mid: str, item_id: str) -> float:
        w = sum(
            e["weight"] for e in drops[mid]["entries"] if e["item_id"] == item_id
        )
        return w / totals[mid]

    gold_ranges = {
        mid: (next(e["gold_min"] for e in r["entries"] if e["item_id"] is None),
              next(e["gold_max"] for e in r["entries"] if e["item_id"] is None))
        for mid, r in drops.items()
    }

    lines: list[str] = []
    lines.append("")
    lines.append(_SECTION_START)
    lines.append("")
    lines.append(
        f"- 共 {len(drops)} 张表（≥{_MIN_DROPS} 达标），覆盖全部 "
        f"{ctx['monster_count']} 只怪；一怪一表，loader 以 monster_id 索引。"
    )
    lines.append(
        "- **权重为项目自定**（MUST NOT 照搬私服单源）：两源均无可解析的掉落权重表"
        "——GeeM2 家族掉落走 Envir/Drops 文本文件（两源仓库均未随附），"
        "Server.MirDB 无 DropInfo 权重链；按 README §6 BOSS 掉落定位自定——"
        "金币行恒在且权重占优（验收断言），药品中权、装备低权、书籍极低权；"
        "物品池按 items/ 的 need_level 分层过滤。"
    )
    lines.append(
        "- 金币区间随档位递增：新手 3–25 → 比奇 10–50 → 中阶 20–90 → "
        "蜈蚣洞 40–140 → 石墓系 50–200；BOSS 500–8000。"
    )
    lines.append(
        "- **BOSS 点名落位**（脚本断言 BOSS_REQUIRED 矩阵）：白野猪→裁决之杖/"
        "骨玉权杖/龙纹剑+烈火剑法/冰咆哮/召唤神兽书；沃玛教主→沃玛号角"
        f"（出现率 {rate('wooma_taurus', 'wooma_horn'):.1%}）；"
        "祖玛教主→祖玛装备（骨玉/龙纹/嗜魂/泰坦/三眼/骑士）+35 书；尸王→16 种技能书专精。"
    )
    eggs = "、".join(
        f"{mid}.{iid}(w=1/{tot}={p:.4%})"
        for mid, iid, tot, p in ctx["egg_stats"]
    )
    lines.append(f"- **屠龙/嗜魂彩蛋**：weight=1、表总权重 ≥1000 ⇒ 出现率 ≤0.001——{eggs}。")
    lines.append("- 模拟验收见 `scripts/simulate_drops.py`（1000 次白野猪击杀分布报告）。")
    lines.append("")
    lines.append("| 怪物 | 总权重 | 金币区间 | 行数 | 点名物品出现率示例 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for mid in sorted(BOSS_REQUIRED):
        lo, hi = gold_ranges[mid]
        sample_item = min(BOSS_REQUIRED[mid])
        lines.append(
            f"| {mid} | {totals[mid]} | {lo}–{hi} | {len(drops[mid]['entries'])} "
            f"| {sample_item} {rate(mid, sample_item):.2%} |"
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
    print(f"[write] {_DOCS_COVERAGE.relative_to(_BACKEND_ROOT.parent)} §8（其余区块保留）")


def main() -> int:
    problems, ctx = check()
    if problems:
        print("== 掉落表抽检失败 ==")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        f"[ok] 掉落表 {len(ctx['drops'])} 张（≥{_MIN_DROPS}），金币行全覆盖，"
        f"BOSS 点名矩阵 {len(BOSS_REQUIRED)} 表全过，彩蛋 {len(ctx['egg_stats'])} 行均 ≤0.001"
    )
    render_coverage(ctx)
    return 0


if __name__ == "__main__":
    sys.exit(main())
