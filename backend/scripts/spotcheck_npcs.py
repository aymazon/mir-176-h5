"""spotcheck_npcs.py — task 20 验收抽检脚本（NPC 与商店清单）。

检查项：
1. 数量：npcs/*.json ≥ 5，且 vendor 均有非空商店。
2. 一致性：shop_entries 与 shop_items 同 id 集；entry.price == 物品 buy_price。
3. §8 城市等级售卖规则：任何 entry.min_city_level ≤ 所在 NPC city_level；
   药品/太阳水档位与 build_npcs.POTION_CITY_TIER 一致（高级药只在高级城）。
4. 覆盖文档：重写 docs/data-coverage.md §6（其余区块保留）。

用法：cd backend && uv run python scripts/spotcheck_npcs.py
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

import build_npcs

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_NPCS_DIR = _BACKEND_ROOT / "gamedata" / "npcs"
_ITEMS_DIR = _BACKEND_ROOT / "gamedata" / "items"
_DOCS_COVERAGE = _BACKEND_ROOT.parent / "docs" / "data-coverage.md"

_MIN_NPCS = 5

_SECTION_START = "## 6. NPC 与商店清单录入（todo 20，2026-08-25）"
_NEXT_MARKERS = ("## 7. 任务数据录入",)


def _buy_prices() -> dict[str, int]:
    out: dict[str, int] = {}
    for path in _ITEMS_DIR.glob("*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        out[raw["id"]] = int(raw.get("buy_price", 0))
    return out


def check() -> tuple[list[str], list[dict[str, Any]]]:
    problems: list[str] = []
    npcs: list[dict[str, Any]] = []
    prices = _buy_prices()
    paths = sorted(_NPCS_DIR.glob("*.json"))
    if len(paths) < _MIN_NPCS:
        problems.append(f"npcs/ 共 {len(paths)} 个 < {_MIN_NPCS}")
    for path in paths:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        npcs.append(raw)
        rel = f"npcs/{path.name}"
        npc_id = str(raw["id"])
        shop_items = [str(x) for x in raw.get("shop_items", [])]
        entries = [dict(e) for e in raw.get("shop_entries", [])]
        city_level = raw.get("city_level")
        if raw["kind"] == "vendor":
            if not shop_items:
                problems.append(f"{rel}: vendor 商店为空")
            if not raw.get("coords"):
                problems.append(f"{rel}: 缺 coords 展示点")
        entry_ids = [str(e["item_id"]) for e in entries]
        if sorted(entry_ids) != sorted(shop_items):
            problems.append(f"{rel}: shop_entries 与 shop_items 不同集")
        for entry in entries:
            item_id = str(entry["item_id"])
            if entry["price"] != prices.get(item_id):
                problems.append(
                    f"{rel}: {item_id} price={entry['price']} "
                    f"!= buy_price={prices.get(item_id)}"
                )
            tier = build_npcs.POTION_CITY_TIER.get(item_id)
            if tier is not None and int(entry["min_city_level"]) != tier:
                problems.append(
                    f"{rel}: {item_id} min_city_level={entry['min_city_level']} "
                    f"!= 档位表 {tier}（§8 高级药只在高级城）"
                )
            if city_level is not None and int(entry["min_city_level"]) > int(city_level):
                problems.append(
                    f"{rel}: {item_id} min_city_level 超过本城等级 {city_level}"
                )
        # id 与文件名一致
        if f"{npc_id}.json" != path.name:
            problems.append(f"{rel}: id '{npc_id}' 与文件名不一致")
    return problems, npcs


def render_coverage(npcs: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    lines.append("")
    lines.append(_SECTION_START)
    lines.append("")
    vendors = [n for n in npcs if n["kind"] == "vendor"]
    lines.append(
        f"- 共录入 {len(npcs)} 个 NPC（vendor {len(vendors)} / quest / teleport），"
        "结构自管（README §8 商店、§10 传送员）；坐标为静态区域图展示点（自创占位，M5 校准）。"
    )
    lines.append(
        "- **商品价格**：取 items/*.json 的 `buy_price`（task 19 双源录入产物）；"
        "shop_entries.price 与之逐条比对通过。"
    )
    lines.append(
        "- **§8 城市等级售卖规则**（城市等级自定，M5 校准）：银杏村=1、比奇=2、盟重=3、"
        "沙巴克=4（MVP 排除沙巴克，不录）。药品档位 min_city_level：小药=1 / 中药=2 / "
        "大药+太阳水=3 / 超级药+强效太阳水=4——高级药只在高级城，脚本逐条断言通过。"
    )
    lines.append(
        "- **地图约束**：maps/ 现有 5 张（todo 21 落地其余区域），故盟重土城/"
        "沃玛城 NPC 待地图落地后补录（规划清单：土城守卫/盟重药店/盟重传送员/"
        "沃玛猎人/沃玛补给商）。"
    )
    lines.append("- 技能书仅售 32 技能对应书目；分身术（书）/流星火雨（书）不入店（1.8+/非 MVP）。")
    lines.append("")
    lines.append("| NPC | 图 | 类型 | 城市等级 | 商品数 | 服务 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for n in npcs:
        lines.append(
            f"| {n['name']} ({n['id']}) | {n['map_id']} | {n['kind']} "
            f"| {n.get('city_level') or '—'} | {len(n.get('shop_items', []))} "
            f"| {'/'.join(n['services']) or '—'} |"
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
    print(f"[write] {_DOCS_COVERAGE} §6（NPC 区块，其余保留）")


def main() -> int:
    problems, npcs = check()
    if problems:
        print("== NPC 抽检失败 ==")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"[ok] NPC {len(npcs)} 个（≥{_MIN_NPCS}）；商店一致性 + §8 城市等级规则全部通过")
    render_coverage(npcs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
