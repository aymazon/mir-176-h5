"""build_npcs.py — task 20 NPC 与商店清单录入构建器。

数据来源与策略（docs/data-coverage.md §6 同步记录）：
- 结构/服务/坐标：项目自管（README §8 商店、§10 传送员；静态区域图展示点，
  坐标为自创占位，M5 校准）。
- 商品价格：取 items/*.json 的 buy_price（task 19 双源录入产物）。
- §8 城市等级售卖规则：城市等级 银杏村=1 / 比奇=2 / 盟重=3（自定，M5 校准）；
  药品按档位 min_city_level 入账——高级药只在高级城。
- 地图约束：maps/ 现有 5 张（todo 21 落地其余 8 区域），故盟重土城/
  沃玛城 NPC 待地图落地后补录（本脚本 ROSTER 已备条目标注 pending_map）。

用法：cd backend && uv run python scripts/build_npcs.py [--dry-run]
"""

# pyright: reportUnusedCallResult=false, reportImplicitRelativeImport=false

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.gamedata_schema.npcs import NpcDef

sys.path.insert(0, str(Path(__file__).resolve().parent))

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_ITEMS_DIR = _BACKEND_ROOT / "gamedata" / "items"
_NPCS_DIR = _BACKEND_ROOT / "gamedata" / "npcs"

# §8 药品/便利道具档位 → 最低城市等级（自定规则，M5 校准）
POTION_CITY_TIER: dict[str, int] = {
    "potion_hp_small": 1,
    "potion_mp_small": 1,
    "potion_hp_medium": 2,
    "potion_mp_medium": 2,
    "potion_hp_large": 3,
    "potion_mp_large": 3,
    "sun_water": 3,
    "super_potion_hp": 4,
    "super_potion_mp": 4,
    "strong_sun_water": 4,
}


class NpcValues(TypedDict):
    id: str
    name: str
    map_id: str
    kind: str
    city_level: int | None
    coords: tuple[int, int] | None
    services: list[str]
    shop_items: list[str]
    shop_entries: list[dict[str, Any]]
    dialogue: list[str]


def _buy_prices() -> dict[str, int]:
    out: dict[str, int] = {}
    for path in _ITEMS_DIR.glob("*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        out[raw["id"]] = int(raw.get("buy_price", 0))
    return out


def _entries(
    item_ids: list[str],
    prices: dict[str, int],
    tier_table: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    tiers = tier_table if tier_table is not None else POTION_CITY_TIER
    return [
        {
            "item_id": item_id,
            "price": prices.get(item_id, 0),
            "min_city_level": tiers.get(item_id, 1),
        }
        for item_id in item_ids
    ]


def roster() -> list[NpcValues]:
    """NPC 清单。pending_map 条目仅作规划注记，不落盘。"""
    prices = _buy_prices()
    small_potions = ["potion_hp_small", "potion_mp_small"]
    npcs: list[NpcValues] = [
        # ---- 银杏村（新手主城，city_level=1）----
        {
            "id": "yinxing_chief", "name": "村长", "map_id": "yin-xing-cun",
            "kind": "quest", "city_level": 1, "coords": (40, 45),
            "services": ["dialog"], "shop_items": [], "shop_entries": [],
            "dialogue": ["孩子，外面的世界很危险，先去村口练练手吧。"],
        },
        {
            "id": "yinxing_butcher", "name": "屠夫", "map_id": "yin-xing-cun",
            "kind": "quest", "city_level": 1, "coords": (52, 38),
            "services": ["dialog"], "shop_items": [], "shop_entries": [],
            "dialogue": ["新鲜的肉我全收，去给我弄点来。"],
        },
        {
            "id": "yinxing_blacksmith", "name": "银杏铁匠", "map_id": "yin-xing-cun",
            "kind": "vendor", "city_level": 1, "coords": (35, 50),
            "services": ["dialog", "shop", "sell", "buy", "repair"],
            "shop_items": [
                "wooden_sword", "dagger", "bronze_sword", "cloth_robe",
                "small_bracelet", "glass_ring",
            ],
            "shop_entries": [], "dialogue": ["打铁要趁热，买剑要趁早。"],
        },
        {
            "id": "healer_npc", "name": "村庄医师", "map_id": "yin-xing-cun",
            "kind": "vendor", "city_level": 1, "coords": (46, 52),
            "services": ["dialog", "shop", "sell", "buy"],
            "shop_items": list(small_potions),
            "shop_entries": [], "dialogue": ["欢迎来到银杏村。", "需要点什么吗？"],
        },
        {
            "id": "yinxing_teleporter", "name": "传送员", "map_id": "yin-xing-cun",
            "kind": "teleport", "city_level": 1, "coords": (48, 48),
            "services": ["dialog", "teleport"], "shop_items": [],
            "shop_entries": [],
            "dialogue": ["想去哪一层？只要金币够，我送你到洞窟深处。"],
        },
        {
            "id": "yinxing_grocer", "name": "杂货店老板", "map_id": "yin-xing-cun",
            "kind": "vendor", "city_level": 1, "coords": (44, 42),
            "services": ["dialog", "shop", "sell", "buy"],
            "shop_items": [
                "return_scroll", "random_scroll", "dungeon_escape_scroll",
                "gray_powder", "yellow_powder",
            ],
            "shop_entries": [], "dialogue": ["卷轴药粉，应有尽有。"],
        },
        # ---- 比奇（biqi-wild 承担比奇城职能，city_level=2）----
        {
            "id": "biqi_blacksmith", "name": "比奇武器店老板", "map_id": "biqi-wild",
            "kind": "vendor", "city_level": 2, "coords": (120, 130),
            "services": ["dialog", "shop", "sell", "buy", "repair"],
            "shop_items": [
                "iron_sword", "lingfeng_sword", "zhanma_blade", "xiuluo_blade",
                "ningshuang_sword", "lianyu_blade", "moonwell_blade",
                "light_armor", "medium_armor", "bronze_helmet", "magic_helmet",
            ],
            "shop_entries": [], "dialogue": ["比奇最好的兵器，都在这儿了。"],
        },
        {
            "id": "biqi_apothecary", "name": "比奇药店老板", "map_id": "biqi-wild",
            "kind": "vendor", "city_level": 2, "coords": (125, 138),
            "services": ["dialog", "shop", "sell", "buy"],
            "shop_items": [
                *small_potions, "potion_hp_medium", "potion_mp_medium",
            ],
            "shop_entries": [], "dialogue": ["金创药魔法药，行军打仗少不了。"],
        },
        {
            "id": "biqi_bookstore", "name": "比奇书店老板", "map_id": "biqi-wild",
            "kind": "vendor", "city_level": 2, "coords": (130, 128),
            "services": ["dialog", "shop", "sell", "buy"],
            "shop_items": [
                "book_basic_sword", "book_fireball", "book_healing",
                "book_slaying_sword", "book_assassination", "book_lightning",
                "book_half_moon", "book_savage_charge", "book_summon_skeleton",
                "book_demon_binding", "book_fire_sword", "book_hell_lightning",
                "book_magic_shield", "book_holy_word", "book_ice_roar",
                "book_summon_beast",
            ],
            "shop_entries": [], "dialogue": ["秘籍虽好，还得自己练。"],
        },
        {
            "id": "biqi_veteran", "name": "比奇老兵", "map_id": "biqi-wild",
            "kind": "quest", "city_level": 2, "coords": (122, 140),
            "services": ["dialog"], "shop_items": [], "shop_entries": [],
            "dialogue": ["骷髅洞里的骷髅越来越猖狂了，替我清剿一番？"],
        },
        # ---- 兽人古墓（骷髅洞区）----
        {
            "id": "shouren_guide", "name": "古墓向导", "map_id": "shouren-gu-1",
            "kind": "teleport", "city_level": None, "coords": (30, 30),
            "services": ["dialog", "teleport"], "shop_items": [],
            "shop_entries": [],
            "dialogue": ["古墓共三层，越深越凶险，也越值钱。"],
        },
    ]
    # 补 shop_entries（价格 + §8 档位）
    for npc in npcs:
        if npc["shop_items"]:
            npc["shop_entries"] = _entries(npc["shop_items"], prices)
    return npcs


PENDING_MAP_NPCS = (
    # 盟重土城（city_level=3）待 todo 21 maps 落地后补录：土城守卫(悬赏钳虫)/
# 盟重药店(大药+太阳水档)/盟重传送员；沃玛城入口：沃玛猎人(讨伐半兽勇士)/沃玛补给商
    "mengzhong_guard", "mengzhong_apothecary", "mengzhong_teleporter",
    "wooma_hunter", "wooma_supplier",
)


def build(dry_run: bool = False) -> int:
    npcs = roster()
    written = 0
    for values in npcs:
        npc = NpcDef.model_validate(values)
        payload = json.dumps(values, ensure_ascii=False, indent=2) + "\n"
        if dry_run:
            print(f"[dry-run] npcs/{npc.id}.json")
            continue
        _NPCS_DIR.mkdir(parents=True, exist_ok=True)
        (_NPCS_DIR / f"{npc.id}.json").write_text(payload, encoding="utf-8")
        written += 1
    print(
        f"[build] npcs/ 共写入 {written} 个"
        f"（待地图补录 {len(PENDING_MAP_NPCS)} 个：todo 21）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(build("--dry-run" in sys.argv))
