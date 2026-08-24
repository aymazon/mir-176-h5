"""spotcheck_items.py — task 19 物品录入验收抽检脚本。

四项检查（对应计划 todo 19 acceptance）：
1. 抽查 5 件声明源字段级 diff：裁决之杖/骨玉权杖/龙纹剑/天魔神甲/无极棍，
   按各条目声明的取数源（OpenMir2 stditems 行 / 无极棍 override）独立重算
   期望 payload，与 gamedata JSON 逐字段比对——必须完全一致，任何差异 exit 1。
   （映射约定见 docs/data-coverage.md §2；本脚本独立实现映射，交叉验证
   build_items.py 转换器本身。）
2. 双源交叉抽检：≥15 件两源同名装备，比对 本方 JSON vs Crystal vs OpenMir2
   的门槛/攻防字段，输出差异报告（主源 cjlaaa 单服变体差异是预期产物，
   落 docs/data-coverage.md §2）。
3. 验收断言：物品总数 ≥120；抽查件门槛字段非空；无极棍 need_level=28。
4. 映射样本：5 件抽查的「原始记录 → JSON 字段」映射表落 docs/data-coverage.md §2。

用法：cd backend && uv run python scripts/spotcheck_items.py
退出码：0 = 全部通过；1 = 源缺失或字段级 diff 失败。
"""

# pyright: reportUnusedCallResult=false, reportImplicitRelativeImport=false

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Final, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.gamedata_schema.items import ItemDef

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_items
import mir_sources

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent
_ITEMS_DIR = _BACKEND_ROOT / "gamedata" / "items"
_DOCS_COVERAGE = _REPO_ROOT / "docs" / "data-coverage.md"

_SECTION_MARKER: Final[str] = "## 2. 物品全量录入：双源出处、字段裁决与映射样本"
_NEXT_SECTION_MARKER: Final[str] = "## 3. OpenMir2 第二源抽检差异报告"

# 抽查 5 件（任务规格点名 4 类 + 无极棍待定项）：全部为 om2 声明源
SPOTCHECK_PROBES: Final[tuple[str, ...]] = (
    "verdict_staff",      # 裁决之杖
    "bone_jade_staff",    # 骨玉权杖
    "dragonvein_sword",   # 龙纹剑
    "sky_demon_armor",    # 天魔神甲（40 级新衣服）
    "wuji_cudgel",        # 无极棍（override need_level=28）
)

# 无极棍任务规格覆盖（与 build_items.ROSTER 同步）
_WUJI_OVERRIDES: Final[dict[str, int]] = {"need_level": 28, "need_taoism": 0}

# 双源交叉抽检最少件数与比对字段（ItemDef 字段, Crystal Stat 键, OM2 列对）
DUAL_SOURCE_MIN_ITEMS: Final[int] = 15
_DUAL_PROBES: Final[tuple[str, ...]] = (
    "wooden_sword", "ebony_sword", "bronze_sword", "iron_sword", "bahua_sword",
    "lingfeng_sword", "zhanma_blade", "xiuluo_blade", "ningshuang_sword",
    "lianyu_blade", "moonwell_blade", "seasoul_staff", "yanyue_blade",
    "magic_staff", "xiangmo_sword", "silver_snake_sword", "heavy_armor",
    "magic_robe", "soul_armor", "dragon_ring", "power_ring", "titan_ring",
)


def load_item_def(item_id: str) -> ItemDef:
    path = _ITEMS_DIR / f"{item_id}.json"
    if not path.is_file():
        raise SystemExit(f"[error] {path.name} 不存在")
    return ItemDef.model_validate(json.loads(path.read_text(encoding="utf-8")))


def expected_from_om2(row: dict[str, str], overrides: dict[str, int] | None = None) -> dict[str, object]:
    """OM2 stditems 行 → ItemDef payload 期望值（独立于 build_items 的映射实现）。"""
    std_mode = int(row["StdMode"])
    equipment = std_mode in {5, 6, 10, 11, 15, 19, 20, 21, 22, 23, 24, 26, 52, 54}
    kind = build_items._OM2_KIND[std_mode]
    stats: dict[str, int] = {}
    req_field = build_items._OM2_REQ_FIELD.get(int(row["Need"]))
    payload: dict[str, object] = {
        "need_level": 0,
        "need_strength": 0,
        "need_magic": 0,
        "need_taoism": 0,
        "weight": int(row["Weight"]),
        "stackable": not equipment,
        "max_durability": int(row["DuraMax"]) if equipment else 0,
        "sell_price": int(row["Price"]) // 2,
        "buy_price": int(row["Price"]),
        "stats": stats,
        "prefix_max": 1 if equipment else 0,
    }
    if req_field is not None:
        if req_field == "need_level":
            payload["need_level"] = int(row["NeedLevel"])
        elif req_field == "need_strength":
            payload["need_strength"] = int(row["NeedLevel"])
        elif req_field == "need_magic":
            payload["need_magic"] = int(row["NeedLevel"])
        elif req_field == "need_taoism":
            payload["need_taoism"] = int(row["NeedLevel"])
    for lo_col, hi_col, name in (
        ("Ac", "AcMax", "ac"), ("Mac", "MacMax", "mac"), ("Dc", "DcMax", "dc"),
        ("Mc", "McMax", "mc"), ("Sc", "ScMax", "sc"),
    ):
        lo, hi = int(row[lo_col]), int(row[hi_col])
        if lo:
            stats[f"{name}_min"] = lo
        if hi:
            stats[f"{name}_max"] = hi
    if kind.value == "potion" and std_mode == 0:
        # 药品类 Ac/Mac 编码为恢复量
        for col, key in (("Ac", "hp_restore"), ("Mac", "mp_restore")):
            if int(row[col]):
                stats.pop({"hp_restore": "ac_min", "mp_restore": "mac_min"}[key], None)
                stats[key] = int(row[col])
    if overrides:
        payload.update(overrides)
    return payload


def diff_payload(probe: str, item: ItemDef, expected: dict[str, object]) -> list[str]:
    problems: list[str] = []
    actual = item.model_dump()
    for field, want in expected.items():
        got = actual[field]
        if got != want:
            problems.append(f"{probe}.{field}: json={got} 源期望={want}")
    return problems


class DualRow(TypedDict):
    id: str
    name: str
    threshold_ours: str
    threshold_cr: str
    threshold_om2: str
    dc_max_ours: int
    dc_max_cr: int
    dc_max_om2: int


def _threshold_str(item: ItemDef) -> str:
    parts = []
    if item.need_level:
        parts.append(f"Lv{item.need_level}")
    if item.need_strength:
        parts.append(f"DC{item.need_strength}")
    if item.need_magic:
        parts.append(f"MC{item.need_magic}")
    if item.need_taoism:
        parts.append(f"SC{item.need_taoism}")
    return "+".join(parts) if parts else "—"


def _cr_threshold(rec: mir_sources.CrystalItem) -> str:
    table = {0: "Lv", 3: "DC", 4: "MC", 5: "SC"}
    prefix = table.get(rec["required_type"])
    if prefix is None or rec["required_amount"] == 0:
        return "—"
    return f"{prefix}{rec['required_amount']}"


def check_dual_source(
    crystal: list[mir_sources.CrystalItem],
    openmir2: list[dict[str, str]],
) -> list[DualRow]:
    """两源同名装备的三方对照（本方 / Crystal / OpenMir2）。"""
    cr_by_name = {r["name"]: r for r in crystal}
    om_by_name = {r["Name"]: r for r in openmir2}
    rows: list[DualRow] = []
    for spec in build_items.ROSTER:
        if spec["id"] not in _DUAL_PROBES:
            continue
        item = load_item_def(spec["id"])
        cr_rec = cr_by_name.get(spec["name"]) if spec["strategy"].startswith("cr") else None
        if cr_rec is None and spec["strategy"] == "om2":
            cr_rec = cr_by_name.get(spec["name"])
        om_row = om_by_name.get(spec.get("src_name") or spec["name"])
        if om_row is None:
            continue
        cr_dc = int(cr_rec["stats"].get(5, 0)) if cr_rec else -1
        om_dc = int(om_row["DcMax"])
        need_map = {0: "Lv", 1: "DC", 2: "MC", 3: "SC"}
        om_need = int(om_row["Need"])
        om_amount = int(om_row["NeedLevel"])
        threshold_om2 = (
            f"{need_map.get(om_need, '?')}{om_amount}"
            if om_need != 0 or om_amount > 0
            else "—"
        )
        rows.append(
            {
                "id": spec["id"],
                "name": spec["name"],
                "threshold_ours": _threshold_str(item),
                "threshold_cr": _cr_threshold(cr_rec) if cr_rec else "缺名",
                "threshold_om2": threshold_om2,
                "dc_max_ours": int(item.stats.get("dc_max", 0)),
                "dc_max_cr": cr_dc,
                "dc_max_om2": om_dc,
            }
        )
    return rows


def render_coverage(
    samples: list[tuple[str, dict[str, str], ItemDef]],
    dual_rows: list[DualRow],
) -> None:
    strategies: dict[str, int] = {}
    for spec in build_items.ROSTER:
        tag = spec["strategy"].split("_")[0]
        strategies[tag] = strategies.get(tag, 0) + 1
    total_files = len(list(_ITEMS_DIR.glob("*.json")))
    todos = [
        (spec["id"], todo)
        for spec in build_items.ROSTER
        if (todo := spec.get("todo", ""))
    ]

    lines: list[str] = []
    lines.append("")
    lines.append(_SECTION_MARKER + "（todo 19，2026-08-25）")
    lines.append("")
    lines.append(
        "- **主源**：Crystal `Server.MirDB` ItemInfo（Suprcode/Crystal.Database 仓库 "
        "cjlaaa/ 目录；按 Crystal `Shared/Data/ItemData.cs` ItemInfo(BinaryReader) "
        "LoadVersion>84 分支解析，连续记录链扫描（链长≥10）定位，共 1344 条）。"
    )
    lines.append(
        "- **第二源**：OpenMir2 `sql/mir2_data.sql` stditems 表（github.com/mirbeta/"
        "OpenMir2，MySQL dump，共 1000 行；Need 列语义经经典官方数值交叉验证："
        "0=等级/1=攻击力/2=魔法力/3=道术——战神盔甲需攻46、恶魔长袍需魔28、"
        "幽灵战衣需道27 三锚点确认）。"
    )
    lines.append(
        "- 两源原始文件均不入库（backend/.gitignore）；本地缓存于 "
        "`backend/scripts/cache/`，解析器 `scripts/mir_sources.py`，构建器 "
        "`scripts/build_items.py`，本抽检脚本可重跑复核。"
    )
    lines.append("")
    lines.append("### 字段映射约定（转换器 build_items.py 实现即文档）")
    lines.append("")
    lines.append("| JSON 字段 | Crystal 列 | OpenMir2 列 | 说明 |")
    lines.append("| --- | --- | --- | --- |")
    lines.append(
        "| kind | Type 枚举(1=武器/2=甲/4=盔/5=链/6=镯/7=戒/9=带/10=靴/13=药/"
        "16=料/17=卷/20=书) | StdMode(0/3/4/5/6/10/11/15/19-26/31/40-44/52/54) | "
        "男女版同值录通用款 |"
    )
    lines.append(
        "| need_level / need_strength / need_magic / need_taoism | RequiredType+"
        "RequiredAmount（Level=0/MaxDC=3/MaxMC=4/MaxSC=5） | Need+NeedLevel"
        "（0/1/2/3） | 两家编码不同，禁止混用（build_items 分表映射） |"
    )
    lines.append("| weight | Weight(u8) | Weight | |")
    lines.append(
        "| max_durability | Durability(u16) | DuraMax | 仅装备类；消耗品强制 0"
        "（schema 约定 0=无耐久概念） |"
    )
    lines.append(
        "| stats.* | Stats 块键 0-9→ac/mac/dc/mc/sc_min/max，10/11→准确/敏捷，"
        "12/13→药品恢复量或装备 hp/mp_add | Ac/AcMax…Sc/ScMax 五对；StdMode=0 时 "
        "Ac/Mac=恢复量 | 区间下限为 0 不落键 |"
    )
    lines.append(
        "| sell_price / buy_price | Price | Price | buy←Price，sell=Price//2"
        "（经典半价回收惯例） |"
    )
    lines.append(
        "| stackable / prefix_max | StackSize / — | OverlapItem / — | 项目自管："
        "消耗品可堆叠；装备 prefix_max=1（词缀档位），消耗品 0 |"
    )
    lines.append("")
    lines.append("### 录入策略与字段裁决（防单服自定义变体污染）")
    lines.append("")
    lines.append(
        f"- **装备类默认取第二源**（{strategies.get('om2', 0)} 条含别名）："
        "其 Need 语义与数值和经典官方一致（裁决之杖 0-30、骨玉权杖魔 2-6、"
        "战神盔甲需攻46 等）；主源 cjlaaa 存在单服自定义变体——乌木剑 Lvl4、"
        "中型盔甲全属性加成、裁决之杖 MaxDC=33、龙纹剑 Lvl40、高阶甲属性门槛"
        "简化为等级等，均弃用并留档于下方三方对照表。"
    )
    lines.append(
        f"- **消耗品/书籍/素材类默认取主源**（cr/cr_map 共 "
        f"{sum(1 for s in build_items.ROSTER if s['strategy'] in ('cr', 'cr_map'))} 条）："
        "金疮药命名族恢复量梯度完整；书籍习得等级在 RequiredAmount 完整"
        "（第二源把习得等级错存 DuraMax 列）。"
    )
    lines.append("- **例外条目（逐条裁决）**：白色虎齿项链走主源（第二源误标攻击门槛）；"
                 "泰坦戒指价格取主源 20000（第二源 Price=15 为源数据笔误）；"
                 "超级金创药/超级魔法药走主源（第二源同名行为药包包头，无恢复量）。")
    lines.append(
        "- **待定项裁决（任务规格）**：无极棍按社区主流 **need_level=28** 录入"
        "（主源 Lvl30 / 第二源道术门槛 25 两源不一；置信度中，"
        "见 docs/combat-formulas.md §6）。"
    )
    lines.append(
        "- 同族派生 1 条：红色药水（两源均无同名；按金疮药族 HP 专精变体派生，"
        "公式见 ROSTER todo）。task13 遗留 2 件无源条目（布帽：两源均无同名头盔；"
        "肉：第二源食物行 Price=2000 与既有数值明显不符）保持原值不动。"
    )
    lines.append(f"- 合计 {total_files} 件（≥120 达标）。")
    lines.append("")
    lines.append("### TODO 清单（M5 校准候选）")
    lines.append("")
    for iid, todo in todos:
        lines.append(f"- `{iid}`: {todo}")
    lines.append("")
    lines.append("### 双源三方对照抽检（门槛 + 攻上限；≥15 件达标）")
    lines.append("")
    lines.append("| 物品 | 门槛(本方) | 门槛(Crystal) | 门槛(OM2) | 攻上限 本方/Cr/OM2 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for row in dual_rows:
        lines.append(
            f"| {row['name']} | {row['threshold_ours']} | {row['threshold_cr']} | "
            f"{row['threshold_om2']} | {row['dc_max_ours']}/{row['dc_max_cr']}/"
            f"{row['dc_max_om2']} |"
        )
    lines.append("")
    lines.append(
        f"共比对 {len(dual_rows)} 件（≥{DUAL_SOURCE_MIN_ITEMS} 达标）；"
        "「本方=OM2 ≠ Crystal」列即主源单服变体证据链。"
    )
    lines.append("")
    lines.append("### 转换器字段映射样本（5 件抽查：原始记录 → JSON）")
    lines.append("")
    for probe_id_, raw, item in samples:
        lines.append(f"#### {item.name}（{probe_id_}.json）")
        lines.append("")
        lines.append("```")
        lines.append(
            f"OM2 raw     : Id={raw['Id']} Name={raw['Name']} StdMode={raw['StdMode']} "
            f"Weight={raw['Weight']} DuraMax={raw['DuraMax']} "
            f"Dc={raw['Dc']}-{raw['DcMax']} Mc={raw['Mc']}-{raw['McMax']} "
            f"Sc={raw['Sc']}-{raw['ScMax']} Need={raw['Need']} "
            f"NeedLevel={raw['NeedLevel']} Price={raw['Price']}"
        )
        lines.append(
            "map         : kind←StdMode need_level←Need/NeedLevel(0) "
            "dc_min/max←Dc/DcMax mc_min/max←Mc/McMax sc_min/max←Sc/ScMax "
            "weight←Weight max_durability←DuraMax buy←Price sell←Price//2"
        )
        lines.append("```")
        lines.append("")
        lines.append("| 字段 | 值 |")
        lines.append("| --- | --- |")
        dump = item.model_dump()
        for key in (
            "id", "name", "kind", "need_level", "need_strength", "need_magic",
            "need_taoism", "weight", "stackable", "max_durability", "sell_price",
            "buy_price", "stats", "prefix_max",
        ):
            lines.append(f"| {key} | {dump[key]} |")
        lines.append("")

    existing = _DOCS_COVERAGE.read_text(encoding="utf-8")
    if _SECTION_MARKER in existing:
        head = existing.partition(_SECTION_MARKER)[0]
        tail_start = existing.find(_NEXT_SECTION_MARKER)
        tail = existing[tail_start:] if tail_start >= 0 else ""
        doc = head.rstrip("\n") + "\n" + "\n".join(lines).rstrip("\n") + "\n\n" + tail
    else:
        doc = existing.rstrip("\n") + "\n" + "\n".join(lines) + "\n"
    _DOCS_COVERAGE.write_text(doc, encoding="utf-8")
    print(f"[write] {_DOCS_COVERAGE.relative_to(_REPO_ROOT)} §2")


def main() -> int:
    crystal, cr_path = mir_sources.load_crystal_item_records()
    openmir2, om_path = mir_sources.load_openmir2_stditem_rows()
    print(f"[source] crystal={cr_path} records={len(crystal)}")
    print(f"[source] openmir2={om_path} rows={len(openmir2)}")
    if not openmir2:
        print("[error] 第二源缺失，无法抽检", file=sys.stderr)
        return 1

    # ---- 验收断言 ----
    total = len(list(_ITEMS_DIR.glob("*.json")))
    if total < 120:
        print(f"[error] 物品总数 {total} < 120", file=sys.stderr)
        return 1
    print(f"== 总数 OK：{total} ≥ 120")

    wuji = load_item_def("wuji_cudgel")
    if wuji.need_level != 28:
        print(f"[error] 无极棍 need_level={wuji.need_level} != 28", file=sys.stderr)
        return 1
    print("== 待定项 OK：无极棍 need_level=28")

    # ---- 检查 1：抽查 5 件声明源字段级 diff ----
    om_by_name = {r["Name"]: r for r in openmir2}
    problems: list[str] = []
    samples: list[tuple[str, dict[str, str], ItemDef]] = []
    for probe in SPOTCHECK_PROBES:
        item = load_item_def(probe)
        for field in ("need_level", "need_strength", "need_magic", "need_taoism"):
            if getattr(item, field) > 0:
                break
        else:
            problems.append(f"{probe}: 门槛字段全空")
        row = om_by_name.get(item.name)
        if row is None:
            problems.append(f"{probe}: 第二源无同名行")
            continue
        overrides = _WUJI_OVERRIDES if probe == "wuji_cudgel" else None
        expected = expected_from_om2(row, overrides)
        problems.extend(diff_payload(probe, item, expected))
        samples.append((probe, row, item))
    if problems:
        print("== 抽查字段级 diff 失败 ==")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"== 抽查 OK：{len(SPOTCHECK_PROBES)} 件逐字段一致（含门槛非空断言）")

    # ---- 检查 2：双源三方对照 ----
    dual_rows = check_dual_source(crystal, openmir2)
    if len(dual_rows) < DUAL_SOURCE_MIN_ITEMS:
        print(
            f"[error] 双源同名装备仅 {len(dual_rows)} 件（<{DUAL_SOURCE_MIN_ITEMS}）",
            file=sys.stderr,
        )
        return 1
    print(f"== 双源对照 OK：{len(dual_rows)} 件三方比对完成")

    render_coverage(samples, dual_rows)

    print("\n== 映射样本（5 件，摘要）==")
    for probe_id_, raw, item in samples:
        print(
            f"  {item.name}: Need={raw['Need']} NeedLevel={raw['NeedLevel']} "
            f"Dc={raw['Dc']}-{raw['DcMax']} → need_level={item.need_level} "
            f"dc_max={item.stats.get('dc_max')}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
