"""build_drops.py — task 21 掉落表全量构建器。

为 gamedata/monsters/ 全部 60 只怪生成 gamedata/drops/{monster_id}_drops.json。
现有 12 个最小集文件（task 13）默认跳过（--force 才覆盖），增量添加而非替换。

权重定位纪律（计划 MUST NOT：不照搬私服单源）：
- 两源（Crystal Server.MirDB / OpenMir2 mir2_data.sql）均无结构化掉落权重表可解析，
  权重按 docs/MVP/README.md §6 的 BOSS 掉落定位自定：
  金币 entry 恒存在且权重占优（验收"每只怪至少有金币掉落行"）；
  药品中权、装备低权、技能书极低权；BOSS 点名物品给显著权重；
  屠龙/嗜魂法杖彩蛋 weight=1（总权重 ≥1000 ⇒ 出现率 ≤0.001）。
- 物品池按 items/ 目录的 need_level 分层过滤（引用必须来自已有 items/），
  书籍只掉 need_level ≤ 怪物档位上限的书；40 级新衣仅祖玛档极低权重。

用法：cd backend && uv run python scripts/build_drops.py [--dry-run] [--force]
"""

# pyright: reportUnusedCallResult=false, reportImplicitRelativeImport=false

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.gamedata_schema.drops import DropEntry

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_DROPS_DIR = _BACKEND_ROOT / "gamedata" / "drops"
_ITEMS_DIR = _BACKEND_ROOT / "gamedata" / "items"

# 行类型：(item_id|None, weight, min_qty, max_qty, gold_min, gold_max)
Row = tuple[str | None, int, int, int, int, int]


def _load_need_levels() -> dict[str, int]:
    out: dict[str, int] = {}
    for path in sorted(_ITEMS_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        out[raw["id"]] = int(raw.get("need_level", 0))
    return out


_NEED: Final[dict[str, int]] = _load_need_levels()


def gold(lo: int, hi: int, weight: int) -> Row:
    return (None, weight, 0, 0, lo, hi)


def items(specs: list[tuple[str, int]], qty: tuple[int, int] = (1, 1)) -> list[Row]:
    """[(item_id, weight), ...] → 物品行列表（统一数量区间）。"""
    return [(iid, w, qty[0], qty[1], 0, 0) for iid, w in specs]


def books_upto(cap: int, weights: dict[str, int]) -> list[Row]:
    """书籍池按 items/ 的 need_level ≤ cap 过滤（引用合法性由目录保证）。"""
    out: list[Row] = []
    for iid, w in weights.items():
        need = _NEED.get(iid)
        if need is None:
            raise SystemExit(f"[error] drops 引用了不存在的物品 {iid}")
        if need <= cap:
            out.append((iid, w, 1, 1, 0, 0))
    return out


# --- 分层公共池（装备/饰品按 items/ need_level 分档） ----------------------------

POTIONS_T1: Final[list[Row]] = items(
    [("potion_hp_small", 12), ("potion_mp_small", 8), ("sun_water", 3)], (1, 2)
)
GEAR_T1: Final[list[Row]] = items([
    ("wooden_sword", 4), ("dagger", 3), ("cloth_robe", 3), ("cloth_cap", 2),
    ("cloth_shoes", 2), ("small_bracelet", 2), ("traditional_necklace", 2),
    ("glass_ring", 2), ("hexagonal_ring", 2), ("gold_necklace", 2),
    ("big_bracelet", 2), ("horn_ring", 2), ("pig_iron_ring", 2),
    ("bronze_sword", 1), ("ebony_sword", 1),
])
BOOKS_T1: Final[dict[str, int]] = {
    "book_basic_sword": 1, "book_fireball": 1, "book_healing": 1,
}

POTIONS_T2: Final[list[Row]] = items([
    ("potion_hp_small", 8), ("potion_mp_small", 6), ("potion_hp_medium", 8),
    ("potion_mp_medium", 6), ("sun_water", 4), ("red_potion", 3),
    ("gray_powder", 2), ("yellow_powder", 2),
], (1, 2))
GEAR_T2: Final[list[Row]] = items([
    ("iron_sword", 3), ("light_armor", 3), ("medium_armor", 2),
    ("bronze_helmet", 2), ("magic_helmet", 1), ("silver_bracelet", 2),
    ("black_crystal_necklace", 2), ("blue_crystal_ring", 2),
    ("amber_necklace", 1), ("lantern_necklace", 1), ("bahua_sword", 1),
    ("seasoul_staff", 1),
])
BOOKS_T2: Final[dict[str, int]] = {
    "book_basic_sword": 1, "book_fireball": 1, "book_healing": 1,
    "book_slaying_sword": 1, "book_lightning": 1,
}

POTIONS_T3: Final[list[Row]] = items([
    ("potion_hp_medium", 10), ("potion_mp_medium", 8), ("potion_hp_large", 6),
    ("potion_mp_large", 5), ("sun_water", 5), ("strong_sun_water", 2),
    ("random_scroll", 2), ("return_scroll", 2), ("gray_powder", 2),
    ("yellow_powder", 2),
], (1, 2))
GEAR_T3: Final[list[Row]] = items([
    ("heavy_armor", 2), ("magic_robe", 2), ("soul_armor", 2), ("iron_belt", 2),
    ("hide_belt", 2), ("deerskin_boots", 2), ("xiangmo_sword", 1),
    ("yanyue_blade", 1), ("zhanma_blade", 1), ("xiuluo_blade", 1),
    ("taoist_helmet", 1), ("ghost_necklace", 1), ("ghost_gloves", 1),
    ("sibeier_bracelet", 1), ("ningshuang_sword", 1), ("magic_staff", 1),
    ("silver_snake_sword", 1), ("lianyu_blade", 1),
])
BOOKS_T3: Final[dict[str, int]] = {
    "book_slaying_sword": 1, "book_lightning": 1, "book_summon_skeleton": 1,
    "book_assassination": 1, "book_half_moon": 1, "book_demon_binding": 1,
}

POTIONS_T4: Final[list[Row]] = items([
    ("potion_hp_large", 10), ("potion_mp_large", 8), ("potion_hp_super", 4),
    ("potion_mp_super", 3), ("strong_sun_water", 4), ("random_scroll", 3),
    ("return_scroll", 3),
], (1, 2))
GEAR_T4: Final[list[Row]] = GEAR_T3 + items([
    ("moonwell_blade", 1), ("wuji_cudgel", 1),
])
BOOKS_T4: Final[dict[str, int]] = {
    **BOOKS_T3,
    "book_savage_charge": 1, "book_hell_lightning": 1,
    "book_magic_shield": 1, "book_holy_word": 1,
}

# 沃玛档饰品/防具（need_level=0 的经典祖玛~沃玛期首饰 + 高阶甲）
WOOMA_JEWELRY: Final[list[Row]] = items([
    ("demon_bell", 2), ("knight_bracelet", 2), ("three_eye_bracelet", 2),
    ("titan_ring", 2), ("tianzhu_necklace", 2), ("dragon_ring", 1),
    ("dragonhand_bracelet", 1), ("life_necklace", 1), ("soul_necklace", 1),
    ("purple_conch_ring", 1), ("power_ring", 1),
    ("white_tiger_tooth_necklace", 1), ("protection_ring", 1),
    ("resurrection_ring", 1),
])
WOOMA_ARMOR: Final[list[Row]] = items([("war_god_armor", 1), ("ghost_armor", 1)])
BOOKS_T5: Final[dict[str, int]] = {
    **BOOKS_T4,
    "book_fire_sword": 1, "book_ice_roar": 1, "book_summon_beast": 1,
}

# 彩蛋（weight=1，表总权重 ≥1000 ⇒ 单次出现率 ≤0.001）
EGG_DRAGONSLAYER: Final[Row] = ("dragonslayer_blade", 1, 1, 1, 0, 0)
EGG_SOULEATER: Final[Row] = ("soul_eater_staff", 1, 1, 1, 0, 0)


def tier_table(
    gold_range: tuple[int, int, int],
    potions: list[Row],
    gear: list[Row],
    book_weights: dict[str, int],
    extra: list[Row] | None = None,
) -> list[Row]:
    rows: list[Row] = [gold(*gold_range), *potions, *gear]
    rows += books_upto(99, book_weights)
    if extra:
        rows += extra
    return rows


# --- 全量名单：monster_id → (档位, 特殊附加行) -----------------------------------

TIERS: Final[dict[str, str]] = {
    # 新手区 T1
    "chicken": "t1", "deer": "t1", "scarecrow": "t1", "rake_cat": "t1",
    "hook_cat": "t1", "bat": "t1",
    # 比奇省 T2
    "toad": "t2", "toad_fighter": "t2", "orc": "t2", "orc_warrior": "t2",
    "venom_snake": "t2", "man_eating_flower": "t2",
    # 比奇省 T2（骷髅战士属骷髅洞中坚，用 T3 中阶池）
    "skeleton_warrior": "t3",
    # 矿区/蛇谷/盟重 T3
    "cave_maggot": "t3", "zombie": "t3", "zombie_fighter": "t3",
    "jumping_bee": "t3", "red_snake": "t3", "tiger_snake": "t3",
    "giant_snake": "t3", "two_head_snake": "t3", "venom_snake_fighter": "t3",
    "two_head_snake_demon": "t4",
    "scorpion": "t3", "giant_scorpion": "t3", "desert_giant_worm": "t3",
    "horn_fly": "t3",
    # 蜈蚣洞 T4
    "centipede": "t4", "giant_centipede": "t4", "black_centipede": "t4",
    # 石墓常规 T5（沃玛档低权渗透）
    "wild_boar_red": "t5", "wild_boar_black": "t5", "scorpion_snake": "t5",
    "wedge_moth": "t5", "evil_corpse_king": "t5", "lightning_zombie": "t5",
    # 沃玛森林/寺庙常规 T5
    "forest_yeti": "t5", "poison_spider": "t5", "wooma_soldier": "t5",
    "ancient_wooma_warrior": "t5", "wooma_warrior": "t5",
    "wooma_tactician": "t5", "flaming_wooma": "t5", "wooma_guardian": "t5",
    # 祖玛常规 T5
    "zuma_statue": "t5", "zuma_guardian": "t5", "zuma_archer": "t5",
}

# 签名素材/肉（经典掉落记忆点）
SIGNATURE: Final[dict[str, list[Row]]] = {
    "chicken": items([("meat", 8)]),
    "deer": items([("meat", 10)]),
    "man_eating_flower": items([("maneating_flower_fruit", 10)]),
    "cave_maggot": items([("maggot_egg", 8)]),
    "poison_spider": items([("spider_fang", 8)]),
}

GOLD_BY_TIER: Final[dict[str, tuple[int, int, int]]] = {
    "t1": (3, 25, 60),
    "t2": (10, 50, 55),
    "t3": (20, 90, 50),
    "t4": (40, 140, 50),
    "t5": (50, 200, 45),
}
POTIONS_BY_TIER: Final[dict[str, list[Row]]] = {
    "t1": POTIONS_T1, "t2": POTIONS_T2, "t3": POTIONS_T3,
    "t4": POTIONS_T4, "t5": POTIONS_T4,
}
GEAR_BY_TIER: Final[dict[str, list[Row]]] = {
    "t1": GEAR_T1, "t2": GEAR_T2, "t3": GEAR_T3, "t4": GEAR_T4,
    # T5 = 蜈蚣洞装备池 + 沃玛档低权渗透
    "t5": GEAR_T4 + WOOMA_JEWELRY + WOOMA_ARMOR,
}
BOOKS_BY_TIER: Final[dict[str, dict[str, int]]] = {
    "t1": BOOKS_T1, "t2": BOOKS_T2, "t3": BOOKS_T3, "t4": BOOKS_T4,
    "t5": BOOKS_T5,
}


# --- BOSS 表（文档 §6 点名定位，逐表手写） ----------------------------------------

BOSS_TABLES: Final[dict[str, list[Row]]] = {
    # 白野猪（猪 7）：沃玛装备 + 35 技能书；屠龙/嗜魂彩蛋
    "wild_boar_white": [
        gold(800, 2000, 3000),
        *items([
            ("sun_water", 350), ("strong_sun_water", 250),
            ("potion_hp_super", 300), ("potion_mp_super", 200),
            ("return_scroll", 100),
        ], (1, 2)),
        *items([
            ("verdict_staff", 130), ("bone_jade_staff", 110),
            ("dragonvein_sword", 110), ("xueyin_sword", 25),
            ("war_god_armor", 90), ("ghost_armor", 70),
            ("demon_bell", 70), ("knight_bracelet", 60),
            ("three_eye_bracelet", 55), ("titan_ring", 55),
            ("tianzhu_necklace", 55), ("dragon_ring", 45),
            ("dragonhand_bracelet", 45), ("life_necklace", 45),
            ("soul_necklace", 45), ("purple_conch_ring", 35),
            ("power_ring", 35), ("white_tiger_tooth_necklace", 35),
        ]),
        *books_upto(35, {
            "book_fire_sword": 65, "book_ice_roar": 65,
            "book_summon_beast": 65, "book_magic_shield": 45,
            "book_holy_word": 35, "book_savage_charge": 35,
            "book_hell_lightning": 35,
        }),
        EGG_DRAGONSLAYER,
        EGG_SOULEATER,
    ],
    # 沃玛教主：沃玛号角（点名）+ 沃玛装备 + 35 技能书
    "wooma_taurus": [
        gold(2000, 5000, 2500),
        *items([
            ("strong_sun_water", 300), ("potion_hp_super", 300),
            ("potion_mp_super", 250), ("sun_water", 200),
            ("blessing_oil", 60),
        ], (1, 2)),
        ("wooma_horn", 500, 1, 1, 0, 0),
        *items([
            ("verdict_staff", 150), ("bone_jade_staff", 130),
            ("dragonvein_sword", 130), ("xueyin_sword", 40),
            ("war_god_armor", 100), ("ghost_armor", 80),
            ("sky_demon_armor", 20),
            ("demon_bell", 90), ("knight_bracelet", 80),
            ("three_eye_bracelet", 70), ("titan_ring", 70),
            ("tianzhu_necklace", 70), ("dragon_ring", 60),
            ("dragonhand_bracelet", 60), ("life_necklace", 60),
            ("soul_necklace", 60), ("purple_conch_ring", 50),
            ("power_ring", 50), ("white_tiger_tooth_necklace", 50),
            ("protection_ring", 30), ("resurrection_ring", 20),
        ]),
        *books_upto(35, {
            "book_fire_sword": 80, "book_ice_roar": 80,
            "book_summon_beast": 80, "book_magic_shield": 50,
            "book_holy_word": 40, "book_savage_charge": 40,
            "book_hell_lightning": 40, "book_demon_binding": 30,
            "book_half_moon": 30,
        }),
        EGG_DRAGONSLAYER,
        EGG_SOULEATER,
    ],
    # 祖玛教主：祖玛装备 + 35 技能书；嗜魂正掉 + 屠龙彩蛋
    "zuma_taurus": [
        gold(3000, 8000, 3000),
        *items([
            ("strong_sun_water", 350), ("potion_hp_super", 350),
            ("potion_mp_super", 300), ("sun_water", 250),
            ("blessing_oil", 80),
        ], (1, 2)),
        *items([
            ("bone_jade_staff", 160), ("dragonvein_sword", 160),
            ("soul_eater_staff", 120), ("xueyin_sword", 60),
            ("sky_demon_armor", 40), ("archmage_cloak", 40),
            ("heavenly_taoist_robe", 40),
            ("titan_ring", 120), ("three_eye_bracelet", 110),
            ("knight_bracelet", 110), ("dragon_ring", 100),
            ("dragonhand_bracelet", 100), ("tianzhu_necklace", 100),
            ("life_necklace", 90), ("soul_necklace", 90),
            ("demon_bell", 90), ("purple_conch_ring", 80),
            ("power_ring", 80), ("white_tiger_tooth_necklace", 80),
            ("protection_ring", 50), ("resurrection_ring", 30),
        ]),
        *books_upto(35, {
            "book_fire_sword": 100, "book_ice_roar": 100,
            "book_summon_beast": 100, "book_magic_shield": 60,
            "book_holy_word": 50, "book_hell_lightning": 50,
            "book_savage_charge": 50,
        }),
        EGG_DRAGONSLAYER,
    ],
    # 尸王（尸王殿）：技能书专精（文档点名"掉技能书"）
    "corpse_king": [
        gold(500, 1500, 800),
        *items([
            ("potion_hp_large", 150), ("potion_mp_large", 120),
            ("strong_sun_water", 100),
        ], (1, 2)),
        *items([
            ("hide_belt", 20), ("iron_belt", 20), ("deerskin_boots", 20),
            ("xiangmo_sword", 15), ("yanyue_blade", 15), ("zhanma_blade", 15),
        ]),
        *books_upto(35, {
            "book_fire_sword": 70, "book_ice_roar": 70,
            "book_summon_beast": 70, "book_magic_shield": 60,
            "book_holy_word": 50, "book_hell_lightning": 50,
            "book_savage_charge": 50, "book_demon_binding": 50,
            "book_half_moon": 50, "book_assassination": 50,
            "book_summon_skeleton": 40, "book_slaying_sword": 40,
            "book_lightning": 40, "book_fireball": 30,
            "book_basic_sword": 30, "book_healing": 30,
        }),
    ],
    # 骷髅精灵（骷髅洞三层 BOSS）
    "skeleton_elf": [
        gold(200, 600, 300),
        *items([
            ("potion_hp_medium", 60), ("potion_mp_medium", 50),
            ("potion_hp_large", 40),
        ], (1, 2)),
        *items([
            ("heavy_armor", 15), ("magic_robe", 15), ("soul_armor", 15),
            ("iron_belt", 15), ("deerskin_boots", 15), ("magic_helmet", 10),
            ("xiangmo_sword", 12), ("yanyue_blade", 12), ("zhanma_blade", 12),
            ("silver_bracelet", 10), ("black_crystal_necklace", 10),
            ("blue_crystal_ring", 10), ("amber_necklace", 8),
            ("lantern_necklace", 8),
        ]),
        *books_upto(22, {
            "book_assassination": 12, "book_summon_skeleton": 10,
            "book_slaying_sword": 10, "book_lightning": 10,
            "book_half_moon": 8,
        }),
    ],
    # 邪恶钳虫（蜈蚣洞 BOSS）
    "evil_clamp_worm": [
        gold(400, 1200, 500),
        *items([
            ("potion_hp_large", 150), ("potion_mp_large", 130),
            ("strong_sun_water", 100),
        ], (1, 2)),
        *items([
            ("lianyu_blade", 40), ("moonwell_blade", 40),
            ("ningshuang_sword", 30), ("magic_staff", 30),
            ("silver_snake_sword", 30), ("wuji_cudgel", 25),
            ("ghost_gloves", 25), ("ghost_necklace", 25),
            ("taoist_helmet", 25), ("sibeier_bracelet", 25),
            ("soul_armor", 20), ("magic_robe", 20), ("heavy_armor", 20),
        ]),
        *books_upto(31, {
            "book_half_moon": 25, "book_demon_binding": 25,
            "book_savage_charge": 20, "book_hell_lightning": 20,
            "book_magic_shield": 20, "book_holy_word": 15,
        }),
    ],
    # 触龙神（死亡棺材）：沃玛档渗透 + 双彩蛋
    "touch_dragon_god": [
        gold(600, 1800, 600),
        *items([
            ("potion_hp_super", 200), ("potion_mp_super", 180),
            ("strong_sun_water", 150),
        ], (1, 2)),
        *items([
            ("verdict_staff", 30), ("bone_jade_staff", 25),
            ("dragonvein_sword", 25), ("war_god_armor", 25),
            ("ghost_armor", 25), ("demon_bell", 25),
            ("knight_bracelet", 20), ("three_eye_bracelet", 20),
            ("titan_ring", 20), ("tianzhu_necklace", 20),
            ("dragon_ring", 15), ("dragonhand_bracelet", 15),
            ("life_necklace", 15), ("soul_necklace", 15),
            ("purple_conch_ring", 12), ("power_ring", 12),
            ("white_tiger_tooth_necklace", 12), ("xueyin_sword", 10),
        ]),
        *books_upto(35, {
            "book_fire_sword": 30, "book_ice_roar": 30,
            "book_summon_beast": 30, "book_magic_shield": 20,
            "book_holy_word": 15, "book_hell_lightning": 15,
            "book_savage_charge": 15,
        }),
        EGG_DRAGONSLAYER,
        EGG_SOULEATER,
    ],
    # 尸霸（石墓尸王）：书 + 中阶装
    "corpse_overlord": [
        gold(600, 1600, 550),
        *items([
            ("potion_hp_super", 180), ("potion_mp_super", 160),
            ("strong_sun_water", 130),
        ], (1, 2)),
        *items([
            ("heavy_armor", 25), ("magic_robe", 25), ("soul_armor", 25),
            ("iron_belt", 25), ("deerskin_boots", 25),
            ("xiangmo_sword", 20), ("yanyue_blade", 20), ("zhanma_blade", 20),
            ("xiuluo_blade", 20), ("taoist_helmet", 20),
            ("ghost_necklace", 15), ("ghost_gloves", 15),
            ("sibeier_bracelet", 15),
        ]),
        *books_upto(35, {
            "book_fire_sword": 40, "book_ice_roar": 40,
            "book_summon_beast": 40, "book_magic_shield": 35,
            "book_holy_word": 30, "book_hell_lightning": 30,
            "book_savage_charge": 30, "book_demon_binding": 30,
            "book_half_moon": 30, "book_assassination": 30,
            "book_summon_skeleton": 25, "book_slaying_sword": 25,
            "book_lightning": 25,
        }),
    ],
    # 极品祖玛卫士（1400 大锤）：祖玛首饰专精
    "zuma_guardian_elite": [
        gold(1000, 3000, 900),
        *items([
            ("potion_hp_super", 250), ("potion_mp_super", 220),
            ("strong_sun_water", 180),
        ], (1, 2)),
        *items([
            ("titan_ring", 60), ("three_eye_bracelet", 55),
            ("knight_bracelet", 55), ("dragon_ring", 50),
            ("dragonhand_bracelet", 50), ("tianzhu_necklace", 50),
            ("life_necklace", 45), ("soul_necklace", 45),
            ("demon_bell", 45), ("purple_conch_ring", 40),
            ("power_ring", 40), ("white_tiger_tooth_necklace", 40),
            ("protection_ring", 25), ("resurrection_ring", 15),
            ("bone_jade_staff", 35), ("dragonvein_sword", 35),
            ("soul_eater_staff", 25), ("xueyin_sword", 20),
            ("war_god_armor", 30), ("ghost_armor", 25),
            ("sky_demon_armor", 10), ("archmage_cloak", 10),
            ("heavenly_taoist_robe", 10),
        ]),
        *books_upto(35, {
            "book_fire_sword": 40, "book_ice_roar": 40,
            "book_summon_beast": 40, "book_magic_shield": 20,
            "book_holy_word": 20, "book_hell_lightning": 20,
            "book_savage_charge": 20,
        }),
        EGG_DRAGONSLAYER,
    ],
}


def build_spec() -> dict[str, list[Row]]:
    spec: dict[str, list[Row]] = {}
    for mid, tier in TIERS.items():
        rows = tier_table(
            GOLD_BY_TIER[tier],
            POTIONS_BY_TIER[tier],
            GEAR_BY_TIER[tier],
            BOOKS_BY_TIER[tier],
            SIGNATURE.get(mid),
        )
        spec[mid] = rows
    spec.update(BOSS_TABLES)
    return spec


def payload_of(rows: list[Row]) -> dict[str, object]:
    return {
        "entries": [
            {
                "item_id": r[0],
                "weight": r[1],
                "min_qty": r[2],
                "max_qty": r[3],
                "gold_min": r[4],
                "gold_max": r[5],
            }
            for r in rows
        ]
    }


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    spec = build_spec()
    written = skipped = 0
    for mid in sorted(spec):
        path = _DROPS_DIR / f"{mid}_drops.json"
        if path.is_file() and not force:
            skipped += 1
            continue
        if dry_run:
            print(f"[dry] {path.name}")
            written += 1
            continue
        body = {"monster_id": mid, **payload_of(spec[mid])}  # type: ignore[dict-item]
        model = DropEntry.model_validate(body)
        path.write_text(
            json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        written += 1
    print(f"[build] spec={len(spec)} written={written} skipped(existing)={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
