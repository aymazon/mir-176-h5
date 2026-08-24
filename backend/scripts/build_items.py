"""build_items.py — task 19 物品全量录入构建器。

从两个独立源构建 backend/gamedata/items/*.json：
- 主源 Crystal ``Server.MirDB`` ItemInfo（scripts/mir_sources.py 解析，1344 条）
- 第二源 OpenMir2 ``sql/mir2_data.sql`` stditems 表（1000 行）

字段映射与裁决约定（docs/data-coverage.md §2 同步记录）：
- 装备类（weapon/armor/helmet/necklace/bracelet/ring/belt/boots）默认取第二源：
  其 Need 语义与数值和经典官方一致（战神盔甲需攻46/恶魔长袍需魔28/
  幽灵战衣需道27/裁决之杖 0-30/骨玉权杖魔 2-6 均为社区公认值）；
  主源 cjlaaa 存在单服自定义变体（乌木剑 Lvl4、中型盔甲全属性、
  裁决之杖 MaxDC=33、龙纹剑 Lvl40 等）。例外条目在 ROSTER 单独声明。
- 消耗品/书籍/素材类默认取主源：金疮药命名族与恢复量梯度、
  书籍习得等级（RequiredAmount）与职业旗标在主源完整。
- 门槛映射：Crystal RequiredType Level→need_level / MaxDC→need_strength /
  MaxMC→need_magic / MaxSC→need_taoism；OpenMir2 Need 0/1/2/3 同序。
- 价格：buy_price←源 Price，sell_price=Price//2（经典半价回收惯例）。
- 任务规格裁决：无极棍按社区主流 need_level=28（覆盖两源，置信度中，
  见 docs/combat-formulas.md §6）。

用法：cd backend && uv run python scripts/build_items.py [--dry-run]
"""

# pyright: reportUnusedCallResult=false, reportImplicitRelativeImport=false

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Final, TypedDict, cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.gamedata_schema.items import ItemDef, ItemKind

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mir_sources

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_ITEMS_DIR = _BACKEND_ROOT / "gamedata" / "items"

# ---------------------------------------------------------------------------
# Crystal ItemInfo → ItemDef 映射表
# ---------------------------------------------------------------------------

_CRYSTAL_KIND: Final[dict[int, ItemKind]] = {
    1: ItemKind.weapon,
    2: ItemKind.armor,
    4: ItemKind.helmet,
    5: ItemKind.necklace,
    6: ItemKind.bracelet,
    7: ItemKind.ring,
    8: ItemKind.necklace,  # Amulet（毒符挂坠；本库未选用）
    9: ItemKind.belt,
    10: ItemKind.boots,
    13: ItemKind.potion,
    16: ItemKind.material,
    17: ItemKind.scroll,
    20: ItemKind.book,
    34: ItemKind.material,  # Quest 任务素材
}

# Stat 枚举键 → stats 字典键（12/13 按 kind 分流：药品=恢复量，装备=上限加成）
_CRYSTAL_STAT_KEYS: Final[dict[int, str]] = {
    0: "ac_min",
    1: "ac_max",
    2: "mac_min",
    3: "mac_max",
    4: "dc_min",
    5: "dc_max",
    6: "mc_min",
    7: "mc_max",
    8: "sc_min",
    9: "sc_max",
    10: "accuracy",
    11: "agility",
    14: "attack_speed",
    15: "luck",
    18: "wear_weight",
    21: "holy",
    23: "poison_attack",
    30: "magic_resist",
    32: "health_recovery",
    33: "spell_recovery",
}

_EQUIPMENT_KINDS: Final[frozenset[ItemKind]] = frozenset(
    {
        ItemKind.weapon,
        ItemKind.armor,
        ItemKind.helmet,
        ItemKind.necklace,
        ItemKind.bracelet,
        ItemKind.ring,
        ItemKind.belt,
        ItemKind.boots,
    }
)

# OpenMir2 StdMode → ItemKind
_OM2_KIND: Final[dict[int, ItemKind]] = {
    0: ItemKind.potion,
    3: ItemKind.scroll,
    4: ItemKind.book,
    5: ItemKind.weapon,
    6: ItemKind.weapon,
    10: ItemKind.armor,
    11: ItemKind.armor,
    15: ItemKind.helmet,
    19: ItemKind.necklace,
    20: ItemKind.necklace,
    21: ItemKind.necklace,
    22: ItemKind.ring,
    23: ItemKind.ring,
    24: ItemKind.bracelet,
    25: ItemKind.potion,  # 药粉（灰色/黄色）
    26: ItemKind.bracelet,
    31: ItemKind.potion,  # 药包
    40: ItemKind.material,
    42: ItemKind.material,
    43: ItemKind.material,
    44: ItemKind.material,
    52: ItemKind.boots,  # 靴子（布鞋/鹿皮靴/紫绸靴…）
    54: ItemKind.belt,  # 腰带（兽皮/铁/青铜…）
}


class ItemValues(TypedDict):
    need_level: int
    need_strength: int
    need_magic: int
    need_taoism: int
    weight: int
    stackable: bool
    max_durability: int
    sell_price: int
    buy_price: int
    stats: dict[str, int]
    prefix_max: int


# 门槛语义分流：两家源编码不同，禁止混用
# Crystal RequiredType: Level=0 / MaxDC=3 / MaxMC=4 / MaxSC=5（Shared/Enums.cs）
# OpenMir2 Need:        Level=0 / DC=1    / MC=2    / SC=3（经典官方数值交叉验证）
_CRYSTAL_REQ_FIELD: Final[dict[int, str]] = {
    0: "need_level",
    3: "need_strength",
    4: "need_magic",
    5: "need_taoism",
}
_OM2_REQ_FIELD: Final[dict[int, str]] = {
    0: "need_level",
    1: "need_strength",
    2: "need_magic",
    3: "need_taoism",
}


def _apply_threshold(
    values: ItemValues,
    table: dict[int, str],
    req_type: int,
    amount: int,
) -> None:
    """门槛字段写入；未知类型保持全零（由双源抽检报告暴露）。"""
    field = table.get(req_type)
    if field is not None:
        values[field] = amount

def from_crystal(rec: mir_sources.CrystalItem) -> ItemValues:
    kind = _CRYSTAL_KIND.get(rec["type"])
    if kind is None:
        raise ValueError(f"unmapped crystal item type {rec['type']}")
    values: ItemValues = {
        "need_level": 0,
        "need_strength": 0,
        "need_magic": 0,
        "need_taoism": 0,
        "weight": rec["weight"],
        "stackable": kind not in _EQUIPMENT_KINDS,
        "max_durability": rec["durability"] if kind in _EQUIPMENT_KINDS else 0,
        "sell_price": rec["price"] // 2,
        "buy_price": rec["price"],
        "stats": {},
        "prefix_max": 1 if kind in _EQUIPMENT_KINDS else 0,
    }
    _apply_threshold(values, _CRYSTAL_REQ_FIELD, rec["required_type"], rec["required_amount"])
    restore = kind is ItemKind.potion
    for key, value in rec["stats"].items():
        if value == 0:
            continue
        if key == 12:
            values["stats"]["hp_restore" if restore else "hp_add"] = value
        elif key == 13:
            values["stats"]["mp_restore" if restore else "mp_add"] = value
        else:
            name = _CRYSTAL_STAT_KEYS.get(key)
            if name is not None:
                values["stats"][name] = value
    return values


def from_openmir2(row: dict[str, str]) -> ItemValues:
    std_mode = int(row["StdMode"])
    kind = _OM2_KIND.get(std_mode)
    if kind is None:
        raise ValueError(f"unmapped om2 stdmode {std_mode}")
    values: ItemValues = {
        "need_level": 0,
        "need_strength": 0,
        "need_magic": 0,
        "need_taoism": 0,
        "weight": int(row["Weight"]),
        "stackable": kind not in _EQUIPMENT_KINDS,
        "max_durability": int(row["DuraMax"]) if kind in _EQUIPMENT_KINDS else 0,
        "sell_price": int(row["Price"]) // 2,
        "buy_price": int(row["Price"]),
        "stats": {},
        "prefix_max": 1 if kind in _EQUIPMENT_KINDS else 0,
    }
    _apply_threshold(values, _OM2_REQ_FIELD, int(row["Need"]), int(row["NeedLevel"]))
    if kind is ItemKind.potion and std_mode == 0:
        # 药品类 Ac/Mac 编码为恢复量（区间上限语义退化为单值）
        if int(row["Ac"]):
            values["stats"]["hp_restore"] = int(row["Ac"])
        if int(row["Mac"]):
            values["stats"]["mp_restore"] = int(row["Mac"])
    else:
        for lo_col, hi_col, name in (
            ("Ac", "AcMax", "ac"),
            ("Mac", "MacMax", "mac"),
            ("Dc", "DcMax", "dc"),
            ("Mc", "McMax", "mc"),
            ("Sc", "ScMax", "sc"),
        ):
            lo, hi = int(row[lo_col]), int(row[hi_col])
            if lo:
                values["stats"][f"{name}_min"] = lo
            if hi:
                values["stats"][f"{name}_max"] = hi
    return values


# ---------------------------------------------------------------------------
# 录入名单（docs/MVP §7 装备序列 + §8 药品卷轴 + §9 素材 + 技能书）
# strategy: cr=主源同名 / om2=第二源同名 / cr_map=主源别名 / om2_map=第二源别名 /
#           derive=同族派生 / override=声明源 + 显式字段覆盖
# ---------------------------------------------------------------------------


class _RosterOptional(TypedDict, total=False):
    src_name: str
    source: str  # override 策略的声明源（"cr" | "om2"）
    base: tuple[str, str]
    overrides: dict[str, int]
    stats: dict[str, int]
    todo: str


class RosterSpec(_RosterOptional):
    """必填键放子类（total 默认 True），可选键继承自 _RosterOptional。"""

    id: str
    name: str
    kind: ItemKind
    strategy: str


class ItemEntry(ItemValues):
    """写盘前的完整记录；provenance/todo 仅用于 evidence，不落 JSON。"""

    id: str
    name: str
    kind: ItemKind
    provenance: str
    todo: str


ROSTER: Final[list[RosterSpec]] = [
    {"id": "dagger", "name": "匕首", "kind": ItemKind.weapon, "strategy": "om2",
     "todo": "task13 已有 id；主源 Lvl2/第二源 Lvl1，取后者"},
    # ===== 战士武器序列（§7.1，乌木剑→裁决之杖 共 11 + 新手木剑）=====
    {"id": "wooden_sword", "name": "木剑", "kind": ItemKind.weapon, "strategy": "om2",
     "todo": "task13 已有 id；主源 dc2-4/第二源 dc2-5，取后者（经典 2-5）"},
    {"id": "ebony_sword", "name": "乌木剑", "kind": ItemKind.weapon, "strategy": "om2",
     "todo": "主源 Lvl4 为单服上调；第二源 Lvl1 与 §7.1 锚点一致"},
    {"id": "bronze_sword", "name": "青铜剑", "kind": ItemKind.weapon, "strategy": "om2"},
    {"id": "iron_sword", "name": "铁剑", "kind": ItemKind.weapon, "strategy": "om2"},
    {"id": "bahua_sword", "name": "八荒", "kind": ItemKind.weapon, "strategy": "om2"},
    {"id": "lingfeng_sword", "name": "凌风", "kind": ItemKind.weapon, "strategy": "om2"},
    {"id": "zhanma_blade", "name": "斩马刀", "kind": ItemKind.weapon, "strategy": "om2"},
    {"id": "xiuluo_blade", "name": "修罗", "kind": ItemKind.weapon, "strategy": "om2"},
    {"id": "ningshuang_sword", "name": "凝霜", "kind": ItemKind.weapon, "strategy": "om2"},
    {"id": "lianyu_blade", "name": "炼狱", "kind": ItemKind.weapon, "strategy": "om2",
     "todo": "攻上限主源 27/第二源 25，取经典 0-25；差异进双源报告"},
    {"id": "moonwell_blade", "name": "井中月", "kind": ItemKind.weapon, "strategy": "om2",
     "todo": "主源该条为 DC:30 属性门槛变体；第二源 Lvl28 与 §7.1 锚点一致"},
    {"id": "verdict_staff", "name": "裁决之杖", "kind": ItemKind.weapon, "strategy": "om2",
     "todo": "主源 MaxDC=33 为单服上调；第二源 0-30 与经典公认一致"},
    # ===== 法师武器序列（§7.1，海魂→血饮；乌木剑共用）=====
    {"id": "seasoul_staff", "name": "海魂", "kind": ItemKind.weapon, "strategy": "om2"},
    {"id": "yanyue_blade", "name": "偃月", "kind": ItemKind.weapon, "strategy": "om2"},
    {"id": "magic_staff", "name": "魔杖", "kind": ItemKind.weapon, "strategy": "om2"},
    {"id": "bone_jade_staff", "name": "骨玉权杖", "kind": ItemKind.weapon, "strategy": "om2",
     "todo": "主源 Lvl30/mc1-7 为单服变体；第二源 Lvl35/魔2-6 与 §7.1 锚点及经典一致"},
    {"id": "xueyin_sword", "name": "血饮", "kind": ItemKind.weapon, "strategy": "om2",
     "todo": "祖玛级稀有（§7.1），商店不上架由掉落消费"},
    # ===== 道士武器序列（§7.1，降魔→龙纹剑；乌木剑共用）=====
    {"id": "xiangmo_sword", "name": "降魔", "kind": ItemKind.weapon, "strategy": "om2"},
    {"id": "silver_snake_sword", "name": "银蛇", "kind": ItemKind.weapon, "strategy": "om2"},
    {"id": "wuji_cudgel", "name": "无极棍", "kind": ItemKind.weapon, "strategy": "override",
     "src_name": "无极棍", "source": "om2",
     "overrides": {"need_level": 28, "need_taoism": 0},
     "todo": "待定项裁决：主源 Lvl30/第二源道术门槛25 两源不一，"
             "按任务规格取社区主流 28 级（置信度中，docs/combat-formulas.md §6）；"
             "第二源属性门槛 25 弃用"},
    {"id": "dragonvein_sword", "name": "龙纹剑", "kind": ItemKind.weapon, "strategy": "om2",
     "todo": "主源 Lvl40/dc8-22 为单服上调；第二源 Lvl35/攻8-20/道3-6 与 §7.1 锚点一致"},
    # ===== 神兵彩蛋（§7.1 注：只录数值，获取途径归 todo 21 掉落彩蛋）=====
    {"id": "dragonslayer_blade", "name": "屠龙", "kind": ItemKind.weapon, "strategy": "om2",
     "todo": "主源 Lvl40/攻5-40 为单服上调；第二源 Lvl34/攻5-35 接近经典，M5 校准"},
    {"id": "soul_eater_staff", "name": "嗜魂法杖", "kind": ItemKind.weapon, "strategy": "om2",
     "todo": "主源缺名，第二源补位"},
    {"id": "fate_blade", "name": "命运之刃", "kind": ItemKind.weapon, "strategy": "om2",
     "todo": "战士 35 级任务武器（§9），主源缺名，第二源补位；获取途径归 todo 20 任务"},
    # ===== 衣服七阶（§7.2；男女版数值两源完全一致，录单条通用款）=====
    {"id": "cloth_robe", "name": "布衣", "kind": ItemKind.armor, "strategy": "om2_map",
     "src_name": "布衣(男)", "todo": "task13 已有 id；男女版同值录通用款"},
    {"id": "light_armor", "name": "轻型盔甲", "kind": ItemKind.armor, "strategy": "om2_map",
     "src_name": "轻型盔甲(男)"},
    {"id": "medium_armor", "name": "中型盔甲", "kind": ItemKind.armor, "strategy": "om2_map",
     "src_name": "中型盔甲(男)",
     "todo": "主源含全职业上限加成(ac4-8/mac2-4/dc/mc/sc+2)为单服魔改；"
             "第二源 防3-5/魔御1-2 与经典一致"},
    {"id": "heavy_armor", "name": "重盔甲", "kind": ItemKind.armor, "strategy": "om2_map",
     "src_name": "重盔甲(男)"},
    {"id": "magic_robe", "name": "魔法长袍", "kind": ItemKind.armor, "strategy": "om2_map",
     "src_name": "魔法长袍(男)"},
    {"id": "soul_armor", "name": "灵魂战衣", "kind": ItemKind.armor, "strategy": "om2_map",
     "src_name": "灵魂战衣(男)"},
    {"id": "war_god_armor", "name": "战神盔甲", "kind": ItemKind.armor, "strategy": "om2_map",
     "src_name": "战神盔甲(男)",
     "todo": "经典属性门槛「需要攻击力46」：第二源 Need=1 完整保留，"
             "主源简化为 Lvl33 弃用"},
    {"id": "demon_robe", "name": "恶魔长袍", "kind": ItemKind.armor, "strategy": "om2_map",
     "src_name": "恶魔长袍(男)",
     "todo": "经典属性门槛「需要魔法力28」，同战神盔甲裁决"},
    {"id": "ghost_armor", "name": "幽灵战衣", "kind": ItemKind.armor, "strategy": "om2_map",
     "src_name": "幽灵战衣(男)",
     "todo": "经典属性门槛「需要道术27」，同战神盔甲裁决"},
    {"id": "sky_demon_armor", "name": "天魔神甲", "kind": ItemKind.armor, "strategy": "om2",
     "todo": "40 级新衣服（1.76 重装，§7.2）；主源缺名，第二源补位；BOSS 掉落归 todo 21"},
    {"id": "archmage_cloak", "name": "法神披风", "kind": ItemKind.armor, "strategy": "om2",
     "todo": "40 级新衣服；主源缺名，第二源补位"},
    {"id": "heavenly_taoist_robe", "name": "天尊道袍", "kind": ItemKind.armor, "strategy": "om2",
     "todo": "40 级新衣服；主源缺名，第二源补位"},
    # ===== 头盔（§7.4 四阶 + 骷髅头盔补充）=====
    {"id": "bronze_helmet", "name": "青铜头盔", "kind": ItemKind.helmet, "strategy": "om2"},
    {"id": "magic_helmet", "name": "魔法头盔", "kind": ItemKind.helmet, "strategy": "om2"},
    {"id": "taoist_helmet", "name": "道士头盔", "kind": ItemKind.helmet, "strategy": "om2"},
    {"id": "skeleton_helmet", "name": "骷髅头盔", "kind": ItemKind.helmet, "strategy": "om2",
     "todo": "经典属性门槛「需要攻击力30」"},
    {"id": "black_iron_helmet", "name": "黑铁头盔", "kind": ItemKind.helmet, "strategy": "om2",
     "todo": "战士高防（§7.4）；经典属性门槛「需要攻击力46」"},
    # ===== 腰带/靴子（§7.4 1.76 新增位）=====
    {"id": "cloth_shoes", "name": "布鞋", "kind": ItemKind.boots, "strategy": "om2"},
    {"id": "hide_belt", "name": "兽皮腰带", "kind": ItemKind.belt, "strategy": "om2"},
    {"id": "deerskin_boots", "name": "鹿皮靴", "kind": ItemKind.boots, "strategy": "om2"},
    {"id": "iron_belt", "name": "铁腰带", "kind": ItemKind.belt, "strategy": "om2"},
    {"id": "purple_silk_boots", "name": "紫绸靴", "kind": ItemKind.boots, "strategy": "om2"},
    {"id": "bronze_belt", "name": "青铜腰带", "kind": ItemKind.belt, "strategy": "om2"},
    # ===== 低级首饰（§7.3 商店 + 低级怪掉落）=====
    {"id": "gold_necklace", "name": "金项链", "kind": ItemKind.necklace, "strategy": "om2"},
    {"id": "traditional_necklace", "name": "传统项链", "kind": ItemKind.necklace, "strategy": "om2"},
    {"id": "black_crystal_necklace", "name": "黑色水晶项链", "kind": ItemKind.necklace,
     "strategy": "om2"},
    {"id": "small_bracelet", "name": "小手镯", "kind": ItemKind.bracelet, "strategy": "om2"},
    {"id": "big_bracelet", "name": "大手镯", "kind": ItemKind.bracelet, "strategy": "om2"},
    {"id": "silver_bracelet", "name": "银手镯", "kind": ItemKind.bracelet, "strategy": "om2"},
    {"id": "horn_ring", "name": "牛角戒指", "kind": ItemKind.ring, "strategy": "om2"},
    {"id": "pig_iron_ring", "name": "生铁戒指", "kind": ItemKind.ring, "strategy": "om2"},
    {"id": "glass_ring", "name": "玻璃戒指", "kind": ItemKind.ring, "strategy": "om2"},
    {"id": "hexagonal_ring", "name": "六角戒指", "kind": ItemKind.ring, "strategy": "om2"},
    {"id": "blue_crystal_ring", "name": "蓝色水晶戒指", "kind": ItemKind.ring, "strategy": "om2"},
    {"id": "amber_necklace", "name": "琥珀项链", "kind": ItemKind.necklace, "strategy": "om2"},
    # ===== 沃玛套 9（§7.3，约 25-27 级档）=====
    {"id": "dragon_ring", "name": "龙之戒指", "kind": ItemKind.ring, "strategy": "om2",
     "todo": "经典属性门槛「需要攻击力37」"},
    {"id": "ghost_necklace", "name": "幽灵项链", "kind": ItemKind.necklace, "strategy": "om2"},
    {"id": "ghost_gloves", "name": "幽灵手套", "kind": ItemKind.bracelet, "strategy": "om2"},
    {"id": "ruby_ring", "name": "红宝石戒指", "kind": ItemKind.ring, "strategy": "om2",
     "todo": "经典属性门槛「需要魔法力17」"},
    {"id": "life_necklace", "name": "生命项链", "kind": ItemKind.necklace, "strategy": "om2",
     "todo": "经典属性门槛「需要魔法力25」"},
    {"id": "sibeier_bracelet", "name": "思贝儿手镯", "kind": ItemKind.bracelet, "strategy": "om2",
     "todo": "主源缺名，第二源补位"},
    {"id": "platinum_ring", "name": "铂金戒指", "kind": ItemKind.ring, "strategy": "om2",
     "todo": "经典属性门槛「需要道术17」"},
    {"id": "tianzhu_necklace", "name": "天珠项链", "kind": ItemKind.necklace, "strategy": "om2",
     "todo": "经典属性门槛「需要道术22」"},
    {"id": "mind_bracelet", "name": "心灵手镯", "kind": ItemKind.bracelet, "strategy": "om2",
     "todo": "经典属性门槛「需要道术24」"},
    # ===== 祖玛套 9（§7.3，约 35-38 级档）=====
    {"id": "power_ring", "name": "力量戒指", "kind": ItemKind.ring, "strategy": "om2",
     "todo": "经典属性门槛「需要攻击力46」"},
    {"id": "green_necklace", "name": "绿色项链", "kind": ItemKind.necklace, "strategy": "om2",
     "todo": "经典属性门槛「需要攻击力37」"},
    {"id": "knight_bracelet", "name": "骑士手镯", "kind": ItemKind.bracelet, "strategy": "om2",
     "todo": "经典属性门槛「需要攻击力39」"},
    {"id": "purple_conch_ring", "name": "紫碧螺", "kind": ItemKind.ring, "strategy": "om2",
     "todo": "经典属性门槛「需要魔法力24」"},
    {"id": "demon_bell", "name": "恶魔铃铛", "kind": ItemKind.necklace, "strategy": "om2",
     "todo": "经典属性门槛「需要魔法力27」"},
    {"id": "dragonhand_bracelet", "name": "龙之手镯", "kind": ItemKind.bracelet, "strategy": "om2",
     "todo": "经典属性门槛「需要魔法力28」"},
    {"id": "titan_ring", "name": "泰坦戒指", "kind": ItemKind.ring, "strategy": "override",
     "src_name": "泰坦戒指", "source": "om2",
     "overrides": {"buy_price": 20000, "sell_price": 10000},
     "todo": "经典属性门槛「需要道术24」；第二源 Price=15 为源数据笔误，"
             "价格取主源 20000"},
    {"id": "soul_necklace", "name": "灵魂项链", "kind": ItemKind.necklace, "strategy": "om2",
     "todo": "经典属性门槛「需要道术23」"},
    {"id": "three_eye_bracelet", "name": "三眼手镯", "kind": ItemKind.bracelet, "strategy": "om2",
     "todo": "经典属性门槛「需要道术22」"},
    # ===== 特殊首饰（§7.3 幸运项链 + 彩蛋戒指）=====
    {"id": "lantern_necklace", "name": "灯笼项链", "kind": ItemKind.necklace, "strategy": "om2",
     "todo": "幸运项链族（幸运+魔法躲避在主源 Stats Luck/MagicResist 键，本库未启用）"},
    {"id": "white_tiger_tooth_necklace", "name": "白色虎齿项链", "kind": ItemKind.necklace,
     "strategy": "cr",
     "todo": "例外走主源：主源道术门槛11 与经典一致；第二源误标攻击门槛(Need=1)"},
    {"id": "paralysis_ring", "name": "麻痹戒指", "kind": ItemKind.ring, "strategy": "om2",
     "todo": "彩蛋戒指（§7.3）：只录数值，极低概率掉落归 todo 21"},
    {"id": "protection_ring", "name": "护身戒指", "kind": ItemKind.ring, "strategy": "om2",
     "todo": "彩蛋戒指，同麻痹戒指"},
    {"id": "resurrection_ring", "name": "复活戒指", "kind": ItemKind.ring, "strategy": "om2",
     "todo": "彩蛋戒指，同麻痹戒指"},
    # ===== 药品（§8；主源金疮药族恢复量梯度完整）=====
    {"id": "potion_hp_small", "name": "金创药（小量）", "kind": ItemKind.potion,
     "strategy": "cr_map", "src_name": "金疮药(小量)", "todo": "task13 已有 id，主源回填"},
    {"id": "potion_hp_medium", "name": "金创药（中量）", "kind": ItemKind.potion,
     "strategy": "cr_map", "src_name": "金疮药(中量)"},
    {"id": "potion_hp_large", "name": "金创药（大量）", "kind": ItemKind.potion,
     "strategy": "cr_map", "src_name": "金疮药(大量)"},
    {"id": "potion_hp_super", "name": "金创药（特量）", "kind": ItemKind.potion,
     "strategy": "cr_map", "src_name": "金疮药(特大)"},
    {"id": "potion_mp_small", "name": "魔法药（小量）", "kind": ItemKind.potion,
     "strategy": "cr_map", "src_name": "魔法药(小量)",
     "todo": "task13 已有 id，主源回填"},
    {"id": "potion_mp_medium", "name": "魔法药（中量）", "kind": ItemKind.potion,
     "strategy": "cr_map", "src_name": "魔法药(中量)"},
    {"id": "potion_mp_large", "name": "魔法药（大量）", "kind": ItemKind.potion,
     "strategy": "cr_map", "src_name": "魔法药(大量)"},
    {"id": "potion_mp_super", "name": "魔法药（特量）", "kind": ItemKind.potion,
     "strategy": "cr_map", "src_name": "魔法药(特大)"},
    {"id": "sun_water", "name": "太阳水", "kind": ItemKind.potion, "strategy": "cr",
     "todo": "task13 已有 id，主源回填（瞬回 HP/MP，§8 保命用）"},
    {"id": "strong_sun_water", "name": "强效太阳水", "kind": ItemKind.potion, "strategy": "cr"},
    {"id": "everfrost", "name": "万年雪霜", "kind": ItemKind.potion, "strategy": "cr"},
    {"id": "gray_powder", "name": "灰色药粉", "kind": ItemKind.potion, "strategy": "om2_map",
     "src_name": "灰色药粉(少量)",
     "todo": "施毒辅助材料（§8）；道士毒符已按 MVP 直接施放，本道具仅存档；"
             "主源缺名，第二源补位"},
    {"id": "yellow_powder", "name": "黄色药粉", "kind": ItemKind.potion, "strategy": "om2_map",
     "src_name": "黄色药粉(少量)", "todo": "同灰色药粉"},
    {"id": "red_potion", "name": "红色药水", "kind": ItemKind.potion, "strategy": "derive",
     "base": ("cr", "金疮药(大量)"),
     "stats": {"hp_restore": 150},
     "overrides": {"buy_price": 300, "sell_price": 150, "weight": 2},
     "todo": "两源均无同名物品；按金疮药族 HP 专精变体派生："
             "hp_restore=150（介于大量110与超级210），价格 300 内插。M5 校准清单"},
    {"id": "super_potion_hp", "name": "超级金创药", "kind": ItemKind.potion,
     "strategy": "cr_map", "src_name": "超级金疮药",
     "todo": "第二源同名行为药包包头(StdMode=31)无恢复量数值，故取主源"},
    {"id": "super_potion_mp", "name": "超级魔法药", "kind": ItemKind.potion,
     "strategy": "cr_map", "src_name": "超级魔法药", "todo": "同超级金创药"},
    # ===== 卷轴（§8 便利道具）=====
    {"id": "random_scroll", "name": "随机传送卷", "kind": ItemKind.scroll, "strategy": "cr"},
    {"id": "return_scroll", "name": "回城卷", "kind": ItemKind.scroll, "strategy": "cr",
     "todo": "新增道具（§1.2/§8）：杂货店售卖，使用效果归 todo 27 传送系统"},
    {"id": "dungeon_escape_scroll", "name": "地牢逃脱卷", "kind": ItemKind.scroll,
     "strategy": "cr"},
    {"id": "blessing_oil", "name": "祝福油", "kind": ItemKind.scroll, "strategy": "cr",
     "todo": "武器幸运+1（§8/§5.2）；使用效果归 todo 23 装备系统"},
    # ===== 任务素材（§9 收集类）=====
    {"id": "maggot_egg", "name": "蛆卵", "kind": ItemKind.material, "strategy": "cr"},
    {"id": "spider_fang", "name": "蜘蛛牙", "kind": ItemKind.material, "strategy": "cr",
     "todo": "第二源为『毒蜘蛛牙齿』别名，取主源原名"},
    {"id": "maneating_flower_fruit", "name": "食人花果", "kind": ItemKind.material,
     "strategy": "cr"},
    {"id": "wooma_horn", "name": "沃玛号角", "kind": ItemKind.material, "strategy": "cr",
     "todo": "沃玛教主掉落任务信物（§6），创建行会道具本作不用，仅存档"},
    # ===== 技能书（任务规格 13 本 + MVP §4 技能书补充 2 本）=====
    {"id": "book_basic_sword", "name": "基本剑术（书）", "kind": ItemKind.book,
     "strategy": "cr_map", "src_name": "基本剑术", "todo": "task13 已有 id，主源回填"},
    {"id": "book_fireball", "name": "火球术（书）", "kind": ItemKind.book,
     "strategy": "cr_map", "src_name": "火球术", "todo": "task13 已有 id，主源回填"},
    {"id": "book_healing", "name": "治愈术（书）", "kind": ItemKind.book,
     "strategy": "cr_map", "src_name": "治愈术", "todo": "task13 已有 id，主源回填"},
    {"id": "book_slaying_sword", "name": "攻杀剑术（书）", "kind": ItemKind.book, "strategy": "cr_map", "src_name": "攻杀剑术"},
    {"id": "book_assassination", "name": "刺杀剑术（书）", "kind": ItemKind.book, "strategy": "cr_map", "src_name": "刺杀剑术"},
    {"id": "book_half_moon", "name": "半月弯刀（书）", "kind": ItemKind.book, "strategy": "cr_map", "src_name": "半月弯刀"},
    {"id": "book_savage_charge", "name": "野蛮冲撞（书）", "kind": ItemKind.book, "strategy": "cr_map", "src_name": "野蛮冲撞"},
    {"id": "book_fire_sword", "name": "烈火剑法（书）", "kind": ItemKind.book, "strategy": "cr_map", "src_name": "烈火剑法"},
    {"id": "book_lightning", "name": "雷电术（书）", "kind": ItemKind.book, "strategy": "cr_map", "src_name": "雷电术"},
    {"id": "book_hell_lightning", "name": "地狱雷光（书）", "kind": ItemKind.book, "strategy": "cr_map", "src_name": "地狱雷光", "todo": "MVP §4 法师 30 级技能配套书（任务清单外补充）"},
    {"id": "book_magic_shield", "name": "魔法盾（书）", "kind": ItemKind.book, "strategy": "cr_map", "src_name": "魔法盾"},
    {"id": "book_holy_word", "name": "圣言术（书）", "kind": ItemKind.book, "strategy": "cr_map", "src_name": "圣言术",
     "todo": "MVP §4 法师 32 级技能配套书（任务清单外补充）"},
    {"id": "book_ice_roar", "name": "冰咆哮（书）", "kind": ItemKind.book, "strategy": "cr_map", "src_name": "冰咆哮"},
    {"id": "book_meteor_shower", "name": "流星火雨（书）", "kind": ItemKind.book, "strategy": "cr_map", "src_name": "流星火雨",
     "todo": "1.8+ 技能（§4 排除清单）：书道具按任务规格存档，技能本体不进 MVP"},
    {"id": "book_demon_binding", "name": "困魔咒（书）", "kind": ItemKind.book, "strategy": "cr_map", "src_name": "困魔咒"},
    {"id": "book_summon_skeleton", "name": "召唤骷髅（书）", "kind": ItemKind.book, "strategy": "cr_map", "src_name": "召唤骷髅"},
    {"id": "book_summon_beast", "name": "召唤神兽（书）", "kind": ItemKind.book, "strategy": "cr_map", "src_name": "召唤神兽"},
    {"id": "book_clone", "name": "分身术（书）", "kind": ItemKind.book, "strategy": "cr_map", "src_name": "分身术",
     "todo": "1.8+ 技能：书道具按任务规格存档，技能本体不进 MVP"},
]


def _spec_overrides(spec: RosterSpec) -> dict[str, int]:
    raw: dict[str, object] = cast("dict[str, object]", cast("object", spec))
    value = raw.get("overrides", {})
    assert isinstance(value, dict)
    return cast("dict[str, int]", value)


def _apply_overrides(values: ItemValues, spec: RosterSpec) -> None:
    """显式逐键覆盖（TypedDict 不允许动态键赋值；未知键 fail-fast）。"""
    for key, value in _spec_overrides(spec).items():
        if key == "need_level":
            values["need_level"] = value
        elif key == "need_taoism":
            values["need_taoism"] = value
        elif key == "buy_price":
            values["buy_price"] = value
        elif key == "sell_price":
            values["sell_price"] = value
        elif key == "weight":
            values["weight"] = value
        else:
            raise ValueError(f"unsupported override key: {key}")


def _spec_stats(spec: RosterSpec) -> dict[str, int]:
    raw: dict[str, object] = cast("dict[str, object]", cast("object", spec))
    value = raw.get("stats", {})
    assert isinstance(value, dict)
    return cast("dict[str, int]", value)


def build(
    crystal: list[mir_sources.CrystalItem],
    openmir2: list[dict[str, str]],
) -> tuple[list[ItemEntry], list[str]]:
    cr_by_name = {r["name"]: r for r in crystal}
    om_by_name = {r["Name"]: r for r in openmir2}
    entries: list[ItemEntry] = []
    notes: list[str] = []
    for spec in ROSTER:
        iid = spec["id"]
        name = spec["name"]
        kind = spec["kind"]
        strategy = spec["strategy"]
        values: ItemValues | None = None
        provenance = ""
        if strategy == "cr":
            rec = cr_by_name.get(name)
            if rec is None:
                notes.append(f"[MISS] {name}: 主源缺名")
                continue
            values = from_crystal(rec)
            provenance = f"cr:{rec['index']}"
        elif strategy == "om2":
            row = om_by_name.get(name)
            if row is None:
                notes.append(f"[MISS] {name}: 第二源缺名")
                continue
            values = from_openmir2(row)
            provenance = f"om2:{row['Id']}"
        elif strategy == "cr_map":
            src_name = spec.get("src_name", "")
            rec = cr_by_name.get(src_name)
            if rec is None:
                notes.append(f"[MISS] {name}: 主源别名 {src_name} 缺")
                continue
            values = from_crystal(rec)
            provenance = f"cr:{rec['index']}({src_name})"
        elif strategy == "om2_map":
            src_name = spec.get("src_name", "")
            row = om_by_name.get(src_name)
            if row is None:
                notes.append(f"[MISS] {name}: 第二源别名 {src_name} 缺")
                continue
            values = from_openmir2(row)
            provenance = f"om2:{row['Id']}({src_name})"
        elif strategy == "override":
            base_src = spec.get("source", "")
            src_name = spec.get("src_name", "")
            if base_src == "cr":
                rec = cr_by_name.get(src_name or name)
                if rec is None:
                    notes.append(f"[MISS] {name}: override 主源锚 {src_name} 缺")
                    continue
                values = from_crystal(rec)
                provenance = f"override(cr.{rec['index']})"
            else:
                row = om_by_name.get(src_name or name)
                if row is None:
                    notes.append(f"[MISS] {name}: override 第二源锚 {src_name} 缺")
                    continue
                values = from_openmir2(row)
                provenance = f"override(om2.{row['Id']})"
            _apply_overrides(values, spec)
        elif strategy == "derive":
            base_src, base_name = spec.get("base", ("", ""))
            if base_src != "cr":
                notes.append(f"[MISS] {name}: derive 仅支持主源锚点")
                continue
            rec = cr_by_name.get(base_name)
            if rec is None:
                notes.append(f"[MISS] {name}: 派生锚点 {base_name} 缺")
                continue
            values = from_crystal(rec)
            values["stats"] = dict(_spec_stats(spec))
            _apply_overrides(values, spec)
            provenance = f"derive(cr.{base_name})"
        else:  # pragma: no cover
            raise ValueError(strategy)

        assert values is not None
        entry: ItemEntry = {
            "id": iid,
            "name": name,
            "kind": kind,
            "need_level": values["need_level"],
            "need_strength": values["need_strength"],
            "need_magic": values["need_magic"],
            "need_taoism": values["need_taoism"],
            "weight": values["weight"],
            "stackable": values["stackable"],
            "max_durability": values["max_durability"],
            "sell_price": values["sell_price"],
            "buy_price": values["buy_price"],
            "stats": values["stats"],
            "prefix_max": values["prefix_max"],
            "provenance": provenance,
            "todo": spec.get("todo", ""),
        }
        # 写盘前 schema 闸：任何字段类型/约束违规立即失败
        ItemDef.model_validate(payload_of(entry))
        entries.append(entry)
    return entries, notes


def payload_of(entry: ItemEntry) -> dict[str, object]:
    """剥离内部字段后的可写盘 payload（键序对齐 ItemDef）。"""
    return {
        "id": entry["id"],
        "name": entry["name"],
        "kind": entry["kind"].value,
        "need_level": entry["need_level"],
        "need_strength": entry["need_strength"],
        "need_magic": entry["need_magic"],
        "need_taoism": entry["need_taoism"],
        "weight": entry["weight"],
        "stackable": entry["stackable"],
        "max_durability": entry["max_durability"],
        "sell_price": entry["sell_price"],
        "buy_price": entry["buy_price"],
        "stats": entry["stats"],
        "prefix_max": entry["prefix_max"],
    }


def write_entries(entries: list[ItemEntry], dry_run: bool) -> int:
    written = 0
    for entry in entries:
        path = _ITEMS_DIR / f"{entry['id']}.json"
        if dry_run:
            print(f"[dry] {path.name}")
            continue
        path.write_text(
            json.dumps(payload_of(entry), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written += 1
    return written


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    crystal, cr_path = mir_sources.load_crystal_item_records()
    openmir2, om_path = mir_sources.load_openmir2_stditem_rows()
    print(f"[source] crystal={cr_path} records={len(crystal)}")
    print(f"[source] openmir2={om_path} rows={len(openmir2)}")
    if not crystal and not openmir2:
        print(
            "[error] 两源均缺失：请将 Server.MirDB 与 mir2_data.sql 放入 "
            "/tmp/mir176-sources/ 或 backend/scripts/cache/",
            file=sys.stderr,
        )
        return 1
    entries, notes = build(crystal, openmir2)
    for note in notes:
        print(note)
    print(f"[build] roster={len(entries)}")
    written = write_entries(entries, dry_run)
    if not dry_run:
        total = len(list(_ITEMS_DIR.glob("*.json")))
        print(f"[write] {written} files -> {_ITEMS_DIR} (total {total})")
    # evidence 摘要：策略计数 + TODO 清单
    strategies: dict[str, int] = {}
    for entry in entries:
        tag = entry["provenance"].split(":")[0].split("(")[0]
        strategies[tag] = strategies.get(tag, 0) + 1
    print(f"[strategies] {strategies}")
    todos = [(e["id"], e["todo"]) for e in entries if e["todo"]]
    print(f"[todos] {len(todos)} 项")
    for iid, todo in todos:
        print(f"  - {iid}: {todo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
