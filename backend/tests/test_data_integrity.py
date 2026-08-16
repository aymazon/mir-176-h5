"""Integrity suite for the W2T2 core numeric tables (DATA-ONLY).

Validates backend/game_data/{exp_curve,classes,skills,items}.json against the
canonical pydantic models in app.content.models, checks cross-reference
integrity, enforces the mandatory negative assertions (N11 / 4.5-D / 4.5-E),
and spot-checks numeric values against docs/MVP/MVP-DESIGN.md 4.1/4.2/4.5/4.7/4.8.

Run: cd backend && .venv/bin/pytest -q
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from app.content.models import (
    CLASS_CODES,
    SLOT_CODES,
    CountStep,
    GameClass,
    Item,
    MainStatStep,
    Skill,
    ClassesFile,
    ExpCurve,
    ItemsFile,
    SkillsFile,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "game_data"
CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

FORBIDDEN_SLOTS = {"belt", "boots", "cloak"}  # N11: 1.76 has no such slots


def _raw(name: str) -> dict[str, Any]:
    path = DATA_DIR / name
    assert path.is_file(), f"missing data file: {path}"
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def exp() -> ExpCurve:
    return ExpCurve.model_validate(_raw("exp_curve.json"))


@pytest.fixture(scope="module")
def classes() -> ClassesFile:
    return ClassesFile.model_validate(_raw("classes.json"))


@pytest.fixture(scope="module")
def skills() -> SkillsFile:
    return SkillsFile.model_validate(_raw("skills.json"))


@pytest.fixture(scope="module")
def items() -> ItemsFile:
    return ItemsFile.model_validate(_raw("items.json"))


@pytest.fixture(scope="module")
def exp_by_level(exp: ExpCurve) -> dict[int, int]:
    return {e.level: e.exp for e in exp.levels}


@pytest.fixture(scope="module")
def class_by_code(classes: ClassesFile) -> dict[str, GameClass]:
    return {c.code: c for c in classes.classes}


@pytest.fixture(scope="module")
def skill_by_code(skills: SkillsFile) -> dict[str, Skill]:
    return {s.code: s for s in skills.skills}


@pytest.fixture(scope="module")
def item_by_code(items: ItemsFile) -> dict[str, Item]:
    return {i.code: i for i in items.items}


def _r(lo: int, hi: int) -> dict[str, int]:
    return {"min": lo, "max": hi}


# ---------------------------------------------------------------------------
# 4.1 exp_curve
# ---------------------------------------------------------------------------
class TestExpCurve:
    def test_exactly_39_entries_levels_1_to_39(self, exp: ExpCurve) -> None:
        # doc 4.1: level 40 is max, so no entry for 40.
        assert [e.level for e in exp.levels] == list(range(1, 40))

    def test_strictly_increasing(self, exp: ExpCurve) -> None:
        values = [e.exp for e in exp.levels]
        assert all(b > a for a, b in zip(values, values[1:], strict=False))

    @pytest.mark.parametrize(
        ("level", "exp_needed"),
        [
            (1, 100),  # doc 4.1 row: 1 | 100
            (7, 1200),  # doc 4.1 row: 7 | 1,200
            (10, 6000),  # doc 4.1 row: 10 | 6,000
            (20, 140000),  # doc 4.1 row: 20 | 140,000
            (30, 2000000),  # doc 4.1 row: 30 | 2,000,000
            (39, 9000000),  # doc 4.1 row: 39 | 9,000,000
        ],
    )
    def test_doc_values(self, exp_by_level: dict[int, int], level: int, exp_needed: int) -> None:
        assert exp_by_level[level] == exp_needed


# ---------------------------------------------------------------------------
# 4.2 classes
# ---------------------------------------------------------------------------
class TestClasses:
    def test_exactly_three_classes(self, classes: ClassesFile) -> None:
        assert {c.code for c in classes.classes} == set(CLASS_CODES)

    def test_common_constants_doc_4_2(self, class_by_code: dict[str, GameClass]) -> None:
        # doc 4.2: 初始防御/魔御/准确/躲避 all 0; 普攻 800ms; 移动 300ms/格;
        # 准确每4级+1, 躲避每5级+1 (all classes).
        for code in CLASS_CODES:
            c = class_by_code[code]
            assert c.defense == 0
            assert c.magic_def == 0
            assert c.accuracy == 0
            assert c.dodge == 0
            assert c.base_attack_ms == 800
            assert c.base_move_ms == 300
            assert c.growth.accuracy_step == CountStep(every_n_levels=4, step=1)
            assert c.growth.dodge_step == CountStep(every_n_levels=5, step=1)

    def test_warrior_doc_4_2(self, class_by_code: dict[str, GameClass]) -> None:
        c = class_by_code["warrior"]
        assert c.initial_hp == 33 and c.initial_mp == 11  # doc 4.2 row 战士
        assert c.attack.model_dump() == _r(1, 1)
        assert c.magic.model_dump() == _r(0, 0) and c.taoist.model_dump() == _r(0, 0)
        assert c.growth.hp_per_level == 10 and c.growth.mp_per_level == 2
        assert c.growth.main_stat == "attack"
        assert c.growth.main_stat_step == MainStatStep(every_n_levels=2, min_step=1, max_step=1)
        # doc 4.2: 战士 HP 1+等级/8, MP 1+等级/12
        assert c.regen.hp_per_second.base == 1 and c.regen.hp_per_second.level_divisor == 8
        assert c.regen.mp_per_second.base == 1 and c.regen.mp_per_second.level_divisor == 12
        ref = c.level_40_reference  # doc 4.2: 战士 HP 423 / MP 89 / 攻 20-20
        assert ref.hp == 423 and ref.mp == 89
        assert ref.attack is not None and ref.attack.model_dump() == _r(20, 20)
        assert ref.accuracy == 9 and ref.dodge == 7

    def test_mage_doc_4_2(self, class_by_code: dict[str, GameClass]) -> None:
        c = class_by_code["mage"]
        assert c.initial_hp == 11 and c.initial_mp == 33  # doc 4.2 row 法师
        assert c.magic.model_dump() == _r(1, 1)
        assert c.attack.model_dump() == _r(0, 0) and c.taoist.model_dump() == _r(0, 0)
        assert c.growth.hp_per_level == 4 and c.growth.mp_per_level == 10
        assert c.growth.main_stat == "magic"
        assert c.growth.main_stat_step == MainStatStep(every_n_levels=2, min_step=1, max_step=1)
        # doc 4.2: 法师 HP 1+等级/15, MP 1+等级/8
        assert c.regen.hp_per_second.base == 1 and c.regen.hp_per_second.level_divisor == 15
        assert c.regen.mp_per_second.base == 1 and c.regen.mp_per_second.level_divisor == 8
        ref = c.level_40_reference  # doc 4.2: 法师 HP 167 / MP 423 / 魔 20-20
        assert ref.hp == 167 and ref.mp == 423
        assert ref.magic is not None and ref.magic.model_dump() == _r(20, 20)
        assert ref.accuracy == 9 and ref.dodge == 7

    def test_taoist_doc_4_2(self, class_by_code: dict[str, GameClass]) -> None:
        c = class_by_code["taoist"]
        assert c.initial_hp == 22 and c.initial_mp == 22  # doc 4.2 row 道士
        assert c.taoist.model_dump() == _r(1, 1)
        assert c.attack.model_dump() == _r(0, 0) and c.magic.model_dump() == _r(0, 0)
        assert c.growth.hp_per_level == 6 and c.growth.mp_per_level == 6
        assert c.growth.main_stat == "taoist"
        assert c.growth.main_stat_step == MainStatStep(every_n_levels=2, min_step=1, max_step=1)
        # doc 4.2: 道士 HP 1+等级/10, MP 1+等级/10
        assert c.regen.hp_per_second.base == 1 and c.regen.hp_per_second.level_divisor == 10
        assert c.regen.mp_per_second.base == 1 and c.regen.mp_per_second.level_divisor == 10
        ref = c.level_40_reference  # doc 4.2: 道士 HP 256 / MP 256 / 道 20-20
        assert ref.hp == 256 and ref.mp == 256
        assert ref.taoist is not None and ref.taoist.model_dump() == _r(20, 20)
        assert ref.accuracy == 9 and ref.dodge == 7


# ---------------------------------------------------------------------------
# 4.5 skills
# ---------------------------------------------------------------------------
# doc 4.5-A/B/C: the COMPLETE set of 1-40 level skills (4.5-E: nothing else).
EXPECTED_SKILL_CODES: frozenset[str] = frozenset(
    {
        # 4.5-A warrior (6)
        "basic_swordsmanship",  # 基本剑术
        "slaughter_sword",  # 攻杀剑术
        "pierce_sword",  # 刺杀剑术
        "crescent_moon",  # 半月弯刀
        "barbaric_charge",  # 野蛮冲撞
        "blazing_sword",  # 烈火剑法
        # 4.5-B mage (14)
        "fireball",  # 火球术
        "fire_ring",  # 抗拒火环
        "seduce",  # 诱惑之光
        "hellfire",  # 地狱火
        "thunder",  # 雷电术
        "blink",  # 瞬息移动
        "big_fireball",  # 大火球
        "exploding_flame",  # 爆裂火焰
        "fire_wall",  # 火墙
        "lightning_strike",  # 疾光电影
        "hell_thunder",  # 地狱雷光
        "magic_shield",  # 魔法盾
        "holy_word",  # 圣言术
        "ice_roar",  # 冰咆哮
        # 4.5-C taoist (13)
        "heal",  # 治愈术
        "mental_warfare",  # 精神力战法
        "poison",  # 施毒术
        "soul_fire_talisman",  # 灵魂火符
        "summon_skeleton",  # 召唤骷髅
        "invisibility",  # 隐身术
        "group_invisibility",  # 集体隐身术
        "ghost_shield",  # 幽灵盾
        "holy_armor",  # 神圣战甲术
        "mind_revelation",  # 心灵启示
        "binding_curse",  # 困魔咒
        "group_heal",  # 群体治愈术
        "summon_beast",  # 召唤神兽
    }
)


class TestSkills:
    def test_skill_count_is_33(self, skills: SkillsFile) -> None:
        assert len(skills.skills) == 33

    def test_per_class_counts_6_14_13(self, skills: SkillsFile) -> None:
        counts = Counter(s.game_class for s in skills.skills)
        assert counts == {"warrior": 6, "mage": 14, "taoist": 13}

    def test_codes_match_doc_tables_exactly(self, skills: SkillsFile) -> None:
        # 4.5-E: no derived skills, no skills beyond the doc tables.
        assert {s.code for s in skills.skills} == EXPECTED_SKILL_CODES

    def test_no_resurrection_skill(self, skills: SkillsFile) -> None:
        # 4.5-E: 复活术 does not exist in 1.76 and must not exist here.
        for s in skills.skills:
            assert "resurrect" not in s.code
            assert "复活" not in s.name

    def test_poison_material_free_doc_4_5_D(self, skill_by_code: dict[str, Skill]) -> None:
        p = skill_by_code["poison"]
        assert p.material_free is True
        # must not reference any talisman (符) item
        dump = json.dumps(p.model_dump(mode="json"), ensure_ascii=False)
        assert "毒符" not in dump
        assert "符" not in dump

    def test_passive_skills(self, skill_by_code: dict[str, Skill]) -> None:
        for code in ("basic_swordsmanship", "mental_warfare"):
            s = skill_by_code[code]
            assert s.type == "passive"
            assert s.mp_cost is None and s.cast_ms is None
            assert s.passive_bonus == {"accuracy": 3}  # doc 4.5: 准确 +3


# ---------------------------------------------------------------------------
# 4.7 + 4.8 items
# ---------------------------------------------------------------------------
# doc 4.7-A~F + 4.8: the complete item set (81 items).
EXPECTED_ITEM_CODES: frozenset[str] = frozenset(
    {
        # 4.7-A weapons (20)
        "wooden_sword", "bronze_sword", "short_sword", "iron_sword", "bronze_axe",
        "half_moon", "sea_soul", "demon_subduer", "yanyue", "asura",
        "frost", "magic_wand", "silver_snake", "purgatory", "wuji_pole",
        "jing_zhong_yue", "blood_drinker", "judgement_rod", "dragon_pattern_sword",
        "bone_jade_staff",
        # 4.7-B armor (12)
        "cloth_robe", "light_armor", "medium_armor", "heavy_armor", "magic_robe",
        "soul_war_robe", "war_god_armor", "ghost_war_robe", "demon_robe",
        "heavenly_demon_armor", "fa_shen_cloak", "tian_zun_robe",
        # 4.7-C helmets (6)
        "helmet", "taoist_helmet", "black_iron_helmet", "holy_war_helmet",
        "fa_shen_helmet", "tian_zun_helmet",
        # 4.7-D necklaces (12)
        "gold_necklace", "blue_jade_necklace", "ghost_necklace", "memory_necklace",
        "holy_war_necklace", "ebony_necklace", "magnifier", "life_necklace",
        "fa_shen_necklace", "bamboo_flute", "soul_necklace", "tian_zun_necklace",
        # 4.7-E bracelets (9)
        "iron_bracelet", "large_bracelet", "sturdy_gloves", "death_god_gloves",
        "holy_war_bracelet", "magic_bracelet", "fa_shen_bracelet", "taoist_bracelet",
        "tian_zun_bracelet",
        # 4.7-F rings (11)
        "ancient_bronze_ring", "iron_ring", "power_ring", "dragon_ring",
        "holy_war_ring", "purple_spiral", "ruby_ring", "fa_shen_ring",
        "tao_ring", "pearl_ring", "tian_zun_ring",
        # 4.8 potions (9)
        "small_hp_potion", "medium_hp_potion", "large_hp_potion",
        "small_mp_potion", "medium_mp_potion", "large_mp_potion",
        "sun_water", "strong_sun_water", "moon_water",
        # 4.8 scrolls (2)
        "return_scroll", "random_teleport_scroll",
    }
)


class TestItems:
    def test_total_count_81(self, items: ItemsFile) -> None:
        assert len(items.items) == 81

    def test_per_category_counts(self, items: ItemsFile) -> None:
        counts = Counter(i.category for i in items.items)
        assert counts == {
            "weapon": 20,  # 4.7-A
            "armor": 12,  # 4.7-B
            "helmet": 6,  # 4.7-C
            "necklace": 12,  # 4.7-D
            "bracelet": 9,  # 4.7-E
            "ring": 11,  # 4.7-F
            "potion": 9,  # 4.8 (6 potions + 3 waters)
            "scroll": 2,  # 4.8
        }

    def test_codes_match_doc_tables_exactly(self, items: ItemsFile) -> None:
        assert {i.code for i in items.items} == EXPECTED_ITEM_CODES

    def test_codes_are_snake_case_english(self, items: ItemsFile) -> None:
        for i in items.items:
            assert CODE_RE.match(i.code), f"bad code: {i.code}"

    def test_no_forbidden_slots_n11(self, items: ItemsFile) -> None:
        # N11: no belt/boots/cloak slots in 1.76.
        for i in items.items:
            assert i.slot not in FORBIDDEN_SLOTS

    def test_slot_classreq_and_stat_ranges(self, items: ItemsFile) -> None:
        for i in items.items:
            if i.slot is not None:
                assert i.slot in SLOT_CODES, f"{i.code}: bad slot {i.slot}"
            req = i.class_req
            assert req == "all" or (
                isinstance(req, list) and len(req) > 0 and set(req) <= set(CLASS_CODES)
            ), f"{i.code}: bad class_req {req!r}"
            for stat in (i.attack, i.magic, i.taoist, i.defense, i.magic_def):
                assert stat is None or stat.min <= stat.max, f"{i.code}: stat {stat}"

    def test_equipment_rules_doc_4_6(self, items: ItemsFile) -> None:
        for i in items.items:
            if i.category in SLOT_CODES:
                assert i.slot == i.category, f"{i.code}: slot must equal category"
                assert i.stack_max == 1
                assert i.durability == (50 if i.category in ("weapon", "armor") else 0)
                assert i.effect is None
            else:  # potion / scroll (doc 4.8: 叠加 99)
                assert i.slot is None
                assert i.durability == 0
                assert i.stack_max == 99

    def test_consumable_effects_resolve(self, items: ItemsFile) -> None:
        for i in items.items:
            if i.category == "potion":
                assert i.effect is not None and i.effect.teleport is None
                assert i.effect.hp_heal or i.effect.mp_heal
            elif i.category == "scroll":
                assert i.effect is not None
                assert i.effect.teleport in {"return", "random_teleport"}
                assert i.effect.hp_heal is None and i.effect.mp_heal is None

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            # (code, {field: value, ...}) — values from doc 4.7-A~F / 4.8
            ("wooden_sword", {  # 4.7-A: 木剑 全 1 攻2-4 卖50 (no buy)
                "slot": "weapon", "class_req": "all", "level_req": 1,
                "attack": _r(2, 4), "buy_price": None, "sell_price": 50,
            }),
            ("asura", {  # 4.7-A: 修罗 战 22 攻0-20 买4000 卖2000
                "class_req": ["warrior"], "level_req": 22,
                "attack": _r(0, 20), "buy_price": 4000, "sell_price": 2000,
            }),
            ("judgement_rod", {  # 4.7-A: 裁决之杖 战 30 攻0-30 买15000 卖7500
                "class_req": ["warrior"], "level_req": 30,
                "attack": _r(0, 30), "buy_price": 15000, "sell_price": 7500,
            }),
            ("dragon_pattern_sword", {  # 4.7-A: 龙纹剑 道 30 攻7-13 道3-5 卖7500
                "class_req": ["taoist"], "level_req": 30,
                "attack": _r(7, 13), "taoist": _r(3, 5), "sell_price": 7500,
            }),
            ("bone_jade_staff", {  # 4.7-A: 骨玉权杖 法 35 攻2-6 魔2-6 卖7500
                "class_req": ["mage"], "level_req": 35,
                "attack": _r(2, 6), "magic": _r(2, 6), "sell_price": 7500,
            }),
            ("heavy_armor", {  # 4.7-B: 重盔甲 战/道 16 防3-5 魔御1-2 卖1000
                "slot": "armor", "class_req": ["warrior", "taoist"], "level_req": 16,
                "defense": _r(3, 5), "magic_def": _r(1, 2), "sell_price": 1000,
            }),
            ("heavenly_demon_armor", {  # 4.7-B: 天魔神甲 战 40 防5-12 魔御4-7 攻1-2
                "class_req": ["warrior"], "level_req": 40,
                "defense": _r(5, 12), "magic_def": _r(4, 7),
                "attack": _r(1, 2), "sell_price": 10000,
            }),
            ("fa_shen_cloak", {  # 4.7-B: 法神披风 法 40 防4-9 魔御4-6 魔2-5
                "class_req": ["mage"], "level_req": 40,
                "defense": _r(4, 9), "magic_def": _r(4, 6),
                "magic": _r(2, 5), "sell_price": 10000,
            }),
            ("tian_zun_robe", {  # 4.7-B: 天尊道袍 道 40 防4-9 魔御4-6 道2-5
                "class_req": ["taoist"], "level_req": 40,
                "defense": _r(4, 9), "magic_def": _r(4, 6),
                "taoist": _r(2, 5), "sell_price": 10000,
            }),
            ("holy_war_helmet", {  # 4.7-C: 圣战头盔 战 35 防4-5 魔御2-3 攻0-2 卖5000
                "slot": "helmet", "class_req": ["warrior"], "level_req": 35,
                "defense": _r(4, 5), "magic_def": _r(2, 3),
                "attack": _r(0, 2), "sell_price": 5000, "durability": 0,
            }),
            ("life_necklace", {  # 4.7-D: 生命项链 法 26 魔1-1 HP+20 卖2000
                "slot": "necklace", "class_req": ["mage"], "level_req": 26,
                "magic": _r(1, 1), "hp_bonus": 20, "sell_price": 2000,
            }),
            ("tian_zun_ring", {  # 4.7-F: 天尊戒指 道 35 道2-5 卖5000
                "slot": "ring", "class_req": ["taoist"], "level_req": 35,
                "taoist": _r(2, 5), "sell_price": 5000,
            }),
            ("small_hp_potion", {  # 4.8: 金创药(小) HP+30 买200 卖100 叠加99
                "category": "potion", "hp_heal": 30, "buy_price": 200,
                "sell_price": 100, "stack_max": 99,
            }),
            ("large_mp_potion", {  # 4.8: 魔法药(大) MP+100 买2000 卖1000
                "category": "potion", "mp_heal": 100, "buy_price": 2000,
                "sell_price": 1000,
            }),
            ("sun_water", {  # 4.8: 太阳水 HP+150/MP+50 不出售 卖2500
                "hp_heal": 150, "mp_heal": 50, "buy_price": None, "sell_price": 2500,
            }),
            ("moon_water", {  # 4.8: 月光水 MP+300/HP+100 卖5000
                "mp_heal": 300, "hp_heal": 100, "buy_price": None, "sell_price": 5000,
            }),
            ("return_scroll", {  # 4.8: 回城卷 买1000 卖500 回到绑定城市
                "category": "scroll", "teleport": "return",
                "buy_price": 1000, "sell_price": 500,
            }),
            ("random_teleport_scroll", {  # 4.8: 随机传送卷 买500 卖250
                "category": "scroll", "teleport": "random_teleport",
                "buy_price": 500, "sell_price": 250,
            }),
        ],
    )
    def test_item_spot_checks(self, item_by_code: dict[str, Item], code: str, expected: dict[str, Any]) -> None:
        item = item_by_code[code]
        dumped = item.model_dump()
        for field, want in expected.items():
            if field == "hp_heal" or field == "mp_heal" or field == "teleport":
                got = dumped["effect"][field]
            else:
                got = dumped[field]
            assert got == want, f"{code}.{field}: got {got!r}, want {want!r}"


# ---------------------------------------------------------------------------
# 4.5 skill numeric spot-checks (doc table values, ~20%+ sample)
# ---------------------------------------------------------------------------
class TestSkillSpotChecks:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            # 4.5-A: 攻杀剑术 19 MP3 900ms cd0 单体 x1.3
            ("slaughter_sword", {"game_class": "warrior", "learn_level": 19, "mp_cost": 3,
                                 "cast_ms": 900, "cooldown_ms": 0, "type": "single",
                                 "damage_multiplier": 1.3}),
            # 4.5-A: 半月弯刀 28 MP5 1100ms 前方180度半径2 x1.1 最多3目标
            ("crescent_moon", {"game_class": "warrior", "learn_level": 28, "mp_cost": 5,
                               "cast_ms": 1100, "type": "fan", "damage_multiplier": 1.1,
                               "angle_deg": 180, "range_cells": 2, "max_targets": 3}),
            # 4.5-A: 野蛮冲撞 30 MP6 800ms 位移 冲2格 无伤害
            ("barbaric_charge", {"game_class": "warrior", "learn_level": 30, "mp_cost": 6,
                                 "cast_ms": 800, "type": "dash", "range_cells": 2,
                                 "damage_multiplier": 0}),
            # 4.5-A: 烈火剑法 35 MP8 1500ms cd2000 x2.0
            ("blazing_sword", {"game_class": "warrior", "learn_level": 35, "mp_cost": 8,
                               "cast_ms": 1500, "cooldown_ms": 2000,
                               "damage_multiplier": 2.0}),
            # 4.5-B: 诱惑之光 13 MP8 魅惑<=自身+2 上限1只 30分钟 20%+魔上限x0.5%
            ("seduce", {"game_class": "mage", "learn_level": 13, "mp_cost": 8,
                        "charm_level_bonus": 2, "max_pets": 1, "duration_ms": 1800000,
                        "success_base_percent": 20.0, "success_per_magic_max_percent": 0.5}),
            # 4.5-B: 雷电术 17 MP6 1200ms 单体 魔x1.5
            ("thunder", {"game_class": "mage", "learn_level": 17, "mp_cost": 6,
                         "cast_ms": 1200, "type": "single", "damage_multiplier": 1.5}),
            # 4.5-B: 火墙 24 MP12 直线3格 6s 每秒1跳 每跳魔x0.35
            ("fire_wall", {"game_class": "mage", "learn_level": 24, "mp_cost": 12,
                           "type": "line", "range_cells": 3, "duration_ms": 6000,
                           "ticks_per_second": 1, "damage_multiplier": 0.35}),
            # 4.5-B: 魔法盾 31 MP20 600ms 减伤30% 60s
            ("magic_shield", {"game_class": "mage", "learn_level": 31, "mp_cost": 20,
                              "cast_ms": 600, "type": "buff",
                              "damage_reduction_percent": 30.0, "duration_ms": 60000}),
            # 4.5-B: 冰咆哮 35 MP20 1400ms 3x3 魔x1.3
            ("ice_roar", {"game_class": "mage", "learn_level": 35, "mp_cost": 20,
                          "cast_ms": 1400, "type": "aoe_3x3", "damage_multiplier": 1.3}),
            # 4.5-C: 施毒术 14 MP8 800ms 绿毒每秒1+道x0.1 10s; 红毒防御-20% 10s
            ("poison", {"game_class": "taoist", "learn_level": 14, "mp_cost": 8,
                        "cast_ms": 800, "type": "dot",
                        "green.dps_base": 1, "green.dps_per_taoist_max": 0.1,
                        "green.duration_ms": 10000,
                        "red.defense_reduction_percent": 20, "red.duration_ms": 10000}),
            # 4.5-C: 召唤骷髅 19 MP12 1200ms cd3000 HP=120+道x6 攻8+道x0.6 防4+道x0.3
            ("summon_skeleton", {"game_class": "taoist", "learn_level": 19, "mp_cost": 12,
                                 "cast_ms": 1200, "cooldown_ms": 3000, "type": "summon",
                                 "pet.hp.base": 120, "pet.hp.per_taoist": 6.0,
                                 "pet.attack.base": 8, "pet.attack.per_taoist": 0.6,
                                 "pet.defense.base": 4, "pet.defense.per_taoist": 0.3}),
            # 4.5-C: 群体治愈术 33 MP15 F8群体 10+道x0.4 3x3
            ("group_heal", {"game_class": "taoist", "learn_level": 33, "mp_cost": 15,
                            "type": "heal", "heal_base": 10, "heal_per_taoist_max": 0.4,
                            "area": "3x3"}),
            # 4.5-C: 召唤神兽 35 MP30 1500ms cd3000 HP=300+道x8 攻20+道x1.0 防8+道x0.5
            ("summon_beast", {"game_class": "taoist", "learn_level": 35, "mp_cost": 30,
                              "cast_ms": 1500, "cooldown_ms": 3000, "type": "summon",
                              "pet.hp.base": 300, "pet.hp.per_taoist": 8.0,
                              "pet.attack.base": 20, "pet.attack.per_taoist": 1.0,
                              "pet.defense.base": 8, "pet.defense.per_taoist": 0.5}),
        ],
    )
    def test_skill_doc_values(
        self, skill_by_code: dict[str, Skill], code: str, expected: dict[str, Any]
    ) -> None:
        s = skill_by_code[code]
        for field, want in expected.items():
            if field in ("game_class", "learn_level", "mp_cost", "cast_ms",
                         "cooldown_ms", "type"):
                got = getattr(s, field)
            else:
                got = _dotted_get(s.formula_params, field)
            assert got == want, f"{code}.{field}: got {got!r}, want {want!r}"


def _dotted_get(data: dict[str, Any], dotted: str) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        cur = cur.get(part)  # type: ignore[union-attr]
    return cur


# ---------------------------------------------------------------------------
# Mandatory negative assertions (repo-wide over game_data)
# ---------------------------------------------------------------------------
class TestNegativeAssertions:
    def test_no_poison_talisman_anywhere_in_game_data(self) -> None:
        # 4.5-D: "毒符" removed server-wide; it must appear in NO data table.
        files = sorted(DATA_DIR.glob("*.json"))
        assert files, "game_data has no JSON tables"
        for path in files:
            text = path.read_text(encoding="utf-8")
            assert "毒符" not in text, f"毒符 found in {path.name}"

    def test_no_belt_boots_cloak_slot_tokens(self) -> None:
        # N11: no belt/boots/cloak slot anywhere in any table (raw scan;
        # note fa_shen_cloak is an item CODE, not a slot — so check slot values).
        raw_items = _raw("items.json")["items"]
        for it in raw_items:
            slot = it.get("slot")
            assert slot not in FORBIDDEN_SLOTS, f"{it['code']}: forbidden slot {slot}"
