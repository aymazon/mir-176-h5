"""coverage_report.py — todo 22 数据覆盖率报告生成器。

生成 docs/data-coverage.md 的两块自动区块（幂等重跑，其余章节不动）：
1. 顶部「覆盖率摘要」：9 类数据规模 + strict 门禁状态（插在引言后、§0 前）；
2. 文末「§11 全量覆盖率明细」：每图/每怪/每技能覆盖表 + 等级带例外登记 +
   数据缺漏清单（M5 待办）。

区块以 HTML 注释标记界定（coverage_report:begin/end:*），重跑时原位替换；
spotcheck_* 脚本的 §0–§10 切片不受影响。

用法：cd backend && uv run python scripts/coverage_report.py
退出码：0 = 报告已生成且 strict lint 全绿；1 = strict lint 有问题。
"""

# pyright: reportUnusedCallResult=false, reportImplicitRelativeImport=false

from __future__ import annotations

import sys
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.gamedata_lint import (
    LEVEL_BAND_EXCEPTIONS,
    lint_birth_map_exists,
    lint_cross_refs_integrity,
    lint_id_only_references,
    lint_monster_level_in_band,
    lint_newbie_equipment_thresholds,
    lint_weapon_level_sequence,
)
from app.gamedata_loader import DEFAULT_GAMEDATA_ROOT, load_all
from app.gamedata_schema import (
    DropEntry,
    MapDef,
    MonsterDef,
    NpcDef,
    QuestDef,
    SpawnEntry,
)
from app.gamedata_schema.items import ItemDef
from app.gamedata_schema.skills import SkillDef

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_DOCS_COVERAGE = _BACKEND_ROOT.parent / "docs" / "data-coverage.md"

_SUMMARY_BEGIN = "<!-- coverage_report:begin:summary -->"
_SUMMARY_END = "<!-- coverage_report:end:summary -->"
_DETAIL_BEGIN = "<!-- coverage_report:begin:detail -->"
_DETAIL_END = "<!-- coverage_report:end:detail -->"

_CATEGORY_ORDER = (
    "maps",
    "monsters",
    "items",
    "skills",
    "npcs",
    "quests",
    "drops",
    "spawns",
    "levels",
)


def _find_book_by_skill_name(
    items: dict[str, ItemDef], skill_name: str
) -> ItemDef | None:
    """按「<技能名>（书）」命名约定找对应书物品。"""
    for item in items.values():
        if item.kind.value == "book" and item.name.startswith(skill_name):
            return item
    return None


def _skill_learn_entry(
    skill: SkillDef,
    book: ItemDef | None,
    npcs: dict[str, NpcDef],
    quests: dict[str, QuestDef],
) -> str:
    """技能学习入口描述：职业任务 > 商店售书 > 任务奖励书 > 书在库无入口 > 缺书。"""
    for qid, q in sorted(quests.items()):
        if q.kind.value == "profession" and q.target_id == skill.id:
            return f"职业任务 {qid}"
    if book is not None:
        for nid, n in sorted(npcs.items()):
            if any(e.item_id == book.id for e in n.shop_entries):
                return f"商店 {nid} 售 {book.id}"
        for qid, q in sorted(quests.items()):
            if book.id in q.reward_items:
                return f"任务奖励 {qid} 授 {book.id}"
        return f"书 {book.id} 在库但无售卖/奖励入口"
    return "缺书（M5 待办）"


def build_summary_block(counts: dict[str, int], lint_lines: list[str]) -> str:
    rows = "\n".join(f"| {cat} | {counts[cat]} |" for cat in _CATEGORY_ORDER)
    gate = "\n".join(f"- {line}" for line in lint_lines)
    return f"""## 覆盖率摘要（todo 22 自动生成）

> 本区块由 `cd backend && uv run python scripts/coverage_report.py` 生成（幂等可重跑），
> 手工编辑会被下次生成覆盖；双源出处与抽检结论见 §0–§10。

| 类别 | 数量 |
| --- | --- |
{rows}

**strict 校验门禁**（`uv run python -m app.gamedata_cli validate --strict`）：

{gate}
"""


def build_detail_block(
    data: Mapping[str, Mapping[str, object]],
) -> tuple[str, list[str]]:
    """生成 §11 明细区块；同时返回缺漏清单行（供调用方打印 stdout）。"""
    maps = {k: v for k, v in data["maps"].items() if isinstance(v, MapDef)}
    monsters = {k: v for k, v in data["monsters"].items() if isinstance(v, MonsterDef)}
    items = {k: v for k, v in data["items"].items() if isinstance(v, ItemDef)}
    skills = {k: v for k, v in data["skills"].items() if isinstance(v, SkillDef)}
    npcs = {k: v for k, v in data["npcs"].items() if isinstance(v, NpcDef)}
    quests = {k: v for k, v in data["quests"].items() if isinstance(v, QuestDef)}
    drops = {k: v for k, v in data["drops"].items() if isinstance(v, DropEntry)}
    spawns = {k: v for k, v in data["spawns"].items() if isinstance(v, SpawnEntry)}

    # ---- 索引 ----
    spawn_by_map: dict[str, list[SpawnEntry]] = defaultdict(list)
    spawn_maps_by_monster: dict[str, set[str]] = defaultdict(set)
    for s in spawns.values():
        spawn_by_map[s.map_id].append(s)
        spawn_maps_by_monster[s.monster_id].add(s.map_id)
    drop_by_monster = {d.monster_id: d for d in drops.values()}

    gaps: list[str] = []

    # ---- 11.1 每图覆盖 ----
    lines = ["## 11. 全量覆盖率明细（todo 22 自动生成）", ""]
    lines.append("### 11.1 每图覆盖（怪物/刷怪点/掉落表计数 + 等级带 + 邻接）")
    lines.append("")
    lines.append("| 地图 | 名称 | 等级带 | 邻接图 | 怪物种数 | 刷怪点 | 掉落表 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for mid in sorted(maps):
        m = maps[mid]
        sps = spawn_by_map.get(mid, [])
        mons = {s.monster_id for s in sps}
        dc = sum(1 for mo in mons if mo in drop_by_monster)
        adj = ", ".join(m.adjacent) if m.adjacent else "—"
        row = (
            f"| {mid} | {m.name} | {m.level_min}-{m.level_max} | {adj} "
            + f"| {len(mons)} | {len(sps)} | {dc} |"
        )
        lines.append(row)
        if not sps:
            gaps.append(f"地图 {mid} 无刷怪点")
        if dc < len(mons):
            gaps.append(f"地图 {mid} 有 {len(mons) - dc} 只怪缺掉落表")

    # ---- 11.2 每怪覆盖 ----
    lines += ["", "### 11.2 每怪覆盖（掉落表存在性 + 金币行 + 所在图）"]
    lines.append("")
    lines.append("| 怪物 | 名称 | Lv | BOSS | 所在图 | 掉落表 | 金币行 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for mid_ in sorted(monsters):
        mo = monsters[mid_]
        table = drop_by_monster.get(mid_)
        has_table = "✅" if table else "❌ 缺"
        has_gold = "—"
        if table is not None:
            has_gold = (
                "✅"
                if any(e.item_id is None and e.gold_max > 0 for e in table.entries)
                else "❌"
            )
        maps_str = (
            ", ".join(sorted(spawn_maps_by_monster.get(mid_, set()))) or "⚠️ 无刷怪点"
        )
        boss_mark = "👑" if mo.boss else ""
        row = (
            f"| {mid_} | {mo.name}{boss_mark} | {mo.level} | {'是' if mo.boss else '否'} "
            + f"| {maps_str} | {has_table} | {has_gold} |"
        )
        lines.append(row)
        if table is None:
            gaps.append(f"怪物 {mid_} 缺掉落表")
        if not spawn_maps_by_monster.get(mid_):
            note = "BOSS（计时器刷新）" if mo.boss else "普通怪"
            gaps.append(f"怪物 {mid_} 无 spawns 刷怪点（{note}）")

    # ---- 11.3 每技能学习入口 ----
    lines += ["", "### 11.3 每技能学习入口（商店售书 / 任务奖励 / 职业任务）"]
    lines.append("")
    lines.append("| 技能 | 职业 | 习得等级 | 对应书 | 学习入口 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for sid in sorted(skills):
        sk = skills[sid]
        book = _find_book_by_skill_name(items, sk.name)
        entry = _skill_learn_entry(sk, book, npcs, quests)
        book_str = book.id if book else "—"
        lines.append(
            f"| {sid}（{sk.name}） | {sk.class_.value} | {sk.learn_level} | {book_str} | {entry} |"
        )
        if entry.startswith(("缺书", "书 ")):
            gaps.append(f"技能 {sid}（{sk.name}）：{entry}")

    # ---- 11.4 等级带例外登记 ----
    lines += ["", "### 11.4 等级带例外登记（lint_monster_level_in_band 放行项）"]
    lines.append("")
    intro = (
        "规则基线：怪物 level ∈ 地图等级带 ±3；BOSS（boss=true）一律豁免"
        + "（经典 176 设计——低级图藏高级 BOSS）。以下非 BOSS 越带怪经人工复核放行，"
        + "与 app/gamedata_lint.py `LEVEL_BAND_EXCEPTIONS` 登记表一一对应；"
        + "新增越带怪不在表内即 strict 硬错。"
    )
    lines.append(intro)
    lines.append("")
    lines.append("| 刷怪点 | 等级 | 放行理由 |")
    lines.append("| --- | --- | --- |")
    for key in sorted(LEVEL_BAND_EXCEPTIONS):
        monster_id = key.partition(":")[2]
        mo = monsters.get(monster_id)
        lvl = mo.level if mo else "?"
        lines.append(f"| {key} | {lvl} | {LEVEL_BAND_EXCEPTIONS[key]} |")

    # ---- 11.5 数据缺漏清单 ----
    lines += ["", "### 11.5 数据缺漏清单（M5 待办）"]
    lines.append("")
    if gaps:
        for gap in gaps:
            lines.append(f"- [ ] {gap}")
    else:
        lines.append("- （无缺漏）")

    detail = "\n".join(lines)
    return detail, gaps


def _splice_block(
    content: str, begin: str, end: str, block: str, *, append: bool
) -> str:
    """原位替换标记区块；不存在时：append=False 插到首个「## 」节前，append=True 追加文末。"""
    full = f"{begin}\n{block}\n{end}"
    if begin in content and end in content:
        pre = content[: content.index(begin)]
        post = content[content.index(end) + len(end) :]
        return pre + full + post
    if append:
        return content.rstrip() + "\n\n" + full + "\n"
    idx = content.find("\n## ")
    if idx == -1:
        return content.rstrip() + "\n\n" + full + "\n"
    return content[:idx] + "\n" + full + content[idx:]


def main() -> int:
    data = load_all(DEFAULT_GAMEDATA_ROOT)
    counts = {cat: len(entries) for cat, entries in data.items()}
    maps = {k: v for k, v in data["maps"].items() if isinstance(v, MapDef)}
    monsters = {k: v for k, v in data["monsters"].items() if isinstance(v, MonsterDef)}
    items = {k: v for k, v in data["items"].items() if isinstance(v, ItemDef)}
    spawns = {k: v for k, v in data["spawns"].items() if isinstance(v, SpawnEntry)}

    # strict 门禁状态写入摘要（六条规则逐一标注）
    lint_results: list[tuple[str, list[str]]] = [
        ("lint_cross_refs_integrity", lint_cross_refs_integrity(data)),
        ("lint_birth_map_exists", lint_birth_map_exists(maps)),
        (
            "lint_monster_level_in_band",
            lint_monster_level_in_band(spawns, monsters, maps),
        ),
        ("lint_weapon_level_sequence", lint_weapon_level_sequence(items)),
        (
            "lint_id_only_references",
            lint_id_only_references(DEFAULT_GAMEDATA_ROOT),
        ),
        ("lint_newbie_equipment_thresholds", lint_newbie_equipment_thresholds(items)),
    ]
    lint_lines = [
        f"✅ {name} 通过" if not problems else f"❌ {name} 失败 {len(problems)} 条"
        for name, problems in lint_results
    ]
    all_problems = [p for _, problems in lint_results for p in problems]

    detail_block, gaps = build_detail_block(data)
    summary_block = build_summary_block(counts, lint_lines)

    content = _DOCS_COVERAGE.read_text(encoding="utf-8")
    content = _splice_block(
        content, _SUMMARY_BEGIN, _SUMMARY_END, summary_block, append=False
    )
    content = _splice_block(
        content, _DETAIL_BEGIN, _DETAIL_END, detail_block, append=True
    )
    _DOCS_COVERAGE.write_text(content, encoding="utf-8")

    print(f"coverage report written: {_DOCS_COVERAGE}")
    print("counts: " + ", ".join(f"{cat}={counts[cat]}" for cat in _CATEGORY_ORDER))
    print(f"gaps listed in §11.5: {len(gaps)}")
    if all_problems:
        print(f"strict lint problems: {len(all_problems)}", file=sys.stderr)
        for p in all_problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("strict lint: all 6 rules passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
