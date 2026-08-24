"""combat/stats.py 面板聚合 TDD 表驱动测试（todo 9）。

TDD 纪律：本文件先于 stats.py 实现编写（红→绿）；每组期望值均附手算推导
注释，证明期望是按公式**推出来的**而非照抄实现。

公式与成长表出处：docs/combat-formulas.md + app/combat/stats.py 模块注释
（职业成长表为整数角色数据；战斗调参系数唯一来源仍是 app/combat/balance.py）。

面板聚合规则（被测契约）：
    final[key] = max(0, base(class, level) + Σ装备.stats + Σ装备.extra_json
                         + Σ被动.stats + Σbuff.stats)
    luck/curse 额外累加装备列 lucky/curse（ItemInstance 同名列）。
"""

from typing import Any, Final

import pytest
from pydantic import ValidationError

from app.combat.balance import LUCK_MAX
from app.combat.stats import (
    BuffInput,
    ClassKey,
    EquipmentInput,
    PassiveInput,
    StatPanel,
    compute_panel,
)


def _panel(
    char_class: str,
    level: int,
    equipment: list[dict[str, Any]] | None = None,
    passives: list[dict[str, Any]] | None = None,
    buffs: list[dict[str, Any]] | None = None,
) -> StatPanel:
    """表驱动行 → compute_panel 入参的薄适配（dict → 入参模型）。"""
    return compute_panel(
        char_class=ClassKey(char_class),
        level=level,
        equipment=[EquipmentInput.model_validate(e) for e in equipment or []],
        passives=[PassiveInput.model_validate(p) for p in passives or []],
        buffs=[BuffInput.model_validate(b) for b in buffs or []],
    )


# --- 表驱动期望面板 ---------------------------------------------------------------
# 成长公式（stats.py CLASS_GROWTH）：value = base + gain * ((level - 1) // per)
# 战士关键行手算备忘：
#   dc_min = 2 + (L-1)//3   dc_max = 5 + (L-1)//2   accuracy = 5 + (L-1)//2
#   ac_min = 1 + (L-1)//4   ac_max = 3 + (L-1)//3   mac_max = (L-1)//5
#   agility = 3 + (L-1)//4  strength = 10 + (L-1)//2
CASES: Final[list[tuple[str, dict[str, Any], dict[str, int]]]] = [
    (
        "naked_warrior_l1_full_panel",
        {"char_class": "warrior", "level": 1},
        # 手算（L=1，所有 (L-1)//x = 0，全取 base）：
        # dc=2..5, mc=0..0, sc=0..0, ac=1..3, mac=0..0, acc=5, agi=3,
        # 攻速 0, 幸运/诅咒 0, 腕力 10, 魔法力 1, 道术 1。
        {
            "dc_min": 2,
            "dc_max": 5,
            "mc_min": 0,
            "mc_max": 0,
            "sc_min": 0,
            "sc_max": 0,
            "ac_min": 1,
            "ac_max": 3,
            "mac_min": 0,
            "mac_max": 0,
            "accuracy": 5,
            "agility": 3,
            "attack_speed": 0,
            "luck": 0,
            "curse": 0,
            "strength": 10,
            "magic_power": 1,
            "taoism": 1,
        },
    ),
    (
        "naked_wizard_l1",
        {"char_class": "wizard", "level": 1},
        # 手算：法师 base mc=3..6、dc=1..2、ac=0..1、mac_max=2、acc=4、魔法力=10。
        # 法师裸体物理近零但魔御高于战士（mac_max 2 > 战士 0）——职业差异锚点。
        {
            "dc_min": 1,
            "dc_max": 2,
            "mc_min": 3,
            "mc_max": 6,
            "ac_min": 0,
            "ac_max": 1,
            "mac_max": 2,
            "accuracy": 4,
            "magic_power": 10,
            "strength": 1,
        },
    ),
    (
        "naked_taoist_l1",
        {"char_class": "taoist", "level": 1},
        # 手算：道士 base sc=2..4、dc=1..3、mc_max=1、ac=0..2、mac_max=1、道术=10。
        # 道术(sc)是道士主通道，1 级即有 2..4，高于其物理 dc 1..3。
        {
            "sc_min": 2,
            "sc_max": 4,
            "dc_min": 1,
            "dc_max": 3,
            "mc_max": 1,
            "ac_min": 0,
            "ac_max": 2,
            "mac_max": 1,
            "agility": 4,
            "taoism": 10,
        },
    ),
    (
        "naked_warrior_l7_growth",
        {"char_class": "warrior", "level": 7},
        # 手算（L=7，(L-1)=6）：dc_min=2+6//3=4；dc_max=5+6//2=8；
        # ac_min=1+6//4=2；ac_max=3+6//3=5；mac_max=6//5=1；
        # accuracy=5+6//2=8；agility=3+6//4=4；strength=10+6//2=13。
        {
            "dc_min": 4,
            "dc_max": 8,
            "ac_min": 2,
            "ac_max": 5,
            "mac_max": 1,
            "accuracy": 8,
            "agility": 4,
            "strength": 13,
        },
    ),
    (
        "naked_wizard_l22_late_game",
        {"char_class": "wizard", "level": 22},
        # 手算（L=22，(L-1)=21）：mc_min=3+21//3=10；mc_max=6+21//2=16；
        # dc_min=1+21//6=4；dc_max=2+21//5=6；ac_min=0+21//6=3；ac_max=1+21//5=5；
        # mac_max=2+21//2=12；accuracy=4+21//3=11；magic_power=10+21//2=20。
        {
            "mc_min": 10,
            "mc_max": 16,
            "dc_min": 4,
            "dc_max": 6,
            "ac_min": 3,
            "ac_max": 5,
            "mac_max": 12,
            "accuracy": 11,
            "magic_power": 20,
        },
    ),
    (
        "wooden_sword_plus1_affix_warrior_l1",
        {
            "char_class": "warrior",
            "level": 1,
            # 木剑 gamedata stats dc 2..5 + 极品词缀 extra_json {"dc_max": 1}
            "equipment": [
                {
                    "slot": "weapon",
                    "stats": {"dc_min": 2, "dc_max": 5},
                    "extra_json": {"dc_max": 1},
                }
            ],
        },
        # 手算：词缀只抬上限 → 武器贡献 2..6；
        # 面板 dc_min = 2(裸) + 2 = 4；dc_max = 5(裸) + 5 + 1 = 11。
        {"dc_min": 4, "dc_max": 11},
    ),
    (
        "cloth_robe_wizard_l1",
        {
            "char_class": "wizard",
            "level": 1,
            # 布衣 gamedata stats ac 2..3
            "equipment": [{"slot": "armor", "stats": {"ac_min": 2, "ac_max": 3}}],
        },
        # 手算：法师裸体 ac=0..1，布衣 +2/+3 → ac=2..4；攻击通道不受防具影响，
        # mc 保持 3..6（交叉断言防具不串通道）。
        {"ac_min": 2, "ac_max": 4, "mc_min": 3, "mc_max": 6},
    ),
    (
        "woma_set_warrior_l22",
        {
            "char_class": "warrior",
            "level": 22,
            # 沃玛套样例（stats 包手工构造，stats.py 不依赖 gamedata 文件）
            "equipment": [
                {"slot": "weapon", "stats": {"dc_min": 3, "dc_max": 7, "accuracy": 2}},
                {"slot": "helmet", "stats": {"ac_min": 2, "ac_max": 3}},
                {"slot": "necklace", "stats": {"accuracy": 2, "luck": 2}},
                {"slot": "ring_r", "stats": {"mc_min": 1, "mc_max": 2, "dc_max": 2}},
            ],
        },
        # 手算：战 22 裸体 dc=9..15、ac=6..10、acc=15、mc=0..3（见 l22 推导式）。
        # 装备合计：dc_min+3、dc_max+7+2=9、accuracy+2+2=4、ac+2/+3、luck+2、mc+1/+2。
        # → dc=12..24、ac=8..13、acc=19、luck=2、mc=1..5。
        {
            "dc_min": 12,
            "dc_max": 24,
            "ac_min": 8,
            "ac_max": 13,
            "accuracy": 19,
            "luck": 2,
            "mc_min": 1,
            "mc_max": 5,
        },
    ),
    (
        "luck9_set_warrior_l1",
        {
            "char_class": "warrior",
            "level": 1,
            # 幸运来源两条独立通道：武器幸运列 lucky=6（祝福油上限档）+
            # 幸运项链词缀 extra_json {"luck": 3} → 合计 9。
            "equipment": [
                {"slot": "weapon", "lucky": 6},
                {"slot": "necklace", "extra_json": {"luck": 3}},
            ],
        },
        # 手算：luck = 0(裸) + 6(lucky 列) + 3(extra_json luck 键) = 9 == LUCK_MAX。
        # 面板层只负责把幸运聚到 9；“刀刀上限”硬规则在 todo 10 damage.py 生效。
        {"luck": LUCK_MAX},
    ),
    (
        "cursed_dagger_warrior_l1",
        {
            "char_class": "warrior",
            "level": 1,
            # 匕首 gamedata stats dc 1..4，诅咒列 curse=5（PK 掉诅咒场景）
            "equipment": [
                {"slot": "weapon", "stats": {"dc_min": 1, "dc_max": 4}, "curse": 5}
            ],
        },
        # 手算：dc_min = 2+1 = 3；dc_max = 5+4 = 9；curse = 0+5 = 5。
        # 诅咒只影响 damage roll 位置（todo 10），面板如实聚合数值本身。
        {"dc_min": 3, "dc_max": 9, "curse": 5, "luck": 0},
    ),
    (
        "basic_sword_passive_warrior_l1",
        {
            "char_class": "warrior",
            "level": 1,
            # 基本剑术被动（gamedata effect_params.accuracy_bonus=3 的等价入参）
            "passives": [{"skill_id": "basic_sword", "stats": {"accuracy": 3}}],
        },
        # 手算：accuracy = 5(裸体战 1) + 3(被动) = 8。被动不加攻防区间，
        # dc 保持 2..5（防被动越权改区间的回归锚点）。
        {"accuracy": 8, "dc_min": 2, "dc_max": 5},
    ),
    (
        "temp_buff_wizard_l1",
        {
            "char_class": "wizard",
            "level": 1,
            # 限时 buff 入参（计时归 todo 26；此处只做加法聚合）
            "buffs": [
                {"buff_id": "mc_elixir", "stats": {"mc_max": 5, "attack_speed": 1}}
            ],
        },
        # 手算：mc_max = 6(裸) + 5 = 11；mc_min 不受单边 buff 影响 = 3；
        # attack_speed = 0 + 1 = 1。
        {"mc_max": 11, "mc_min": 3, "attack_speed": 1},
    ),
    (
        "attack_speed_gear_stack_warrior_l1",
        {
            "char_class": "warrior",
            "level": 1,
            # 两件攻速装叠加（疾风类手镯/靴子原型）
            "equipment": [
                {"slot": "boots", "stats": {"attack_speed": 2}},
                {"slot": "bracelet_l", "stats": {"attack_speed": 1, "agility": 1}},
            ],
        },
        # 手算：attack_speed = 0 + 2 + 1 = 3；agility = 3(裸) + 1 = 4。
        # 攻速是加法聚合的标量键，消费方（todo 15）再换算出手间隔。
        {"attack_speed": 3, "agility": 4},
    ),
    (
        "warrior_l20_strength_threshold",
        {"char_class": "warrior", "level": 20},
        # 手算：strength = 10 + (20-1)//2 = 10 + 9 = 19。
        # 该派生值供穿戴门槛比对（如 need_strength=19 的重装恰好可穿）。
        {"strength": 19},
    ),
    (
        "taoist_l20_taoism_threshold",
        {"char_class": "taoist", "level": 20},
        # 手算：taoism = 10 + (20-1)//2 = 19；对照战士同级 taoism = 1 + 19//5 = 4，
        # 主属性职业分化明显（门槛系统依赖此差异）。
        {"taoism": 19, "strength": 1},
    ),
]


@pytest.mark.parametrize(
    ("case_id", "kwargs", "expected"),
    CASES,
    ids=[case_id for case_id, _kwargs, _expected in CASES],
)
def test_expected_panel(
    case_id: str, kwargs: dict[str, Any], expected: dict[str, int]
) -> None:
    """≥10 组期望面板：逐键比对（部分匹配，聚焦每组用例的差异点）。"""
    panel = _panel(**kwargs)
    actual = panel.model_dump()
    for key, want in expected.items():
        assert actual[key] == want, f"{case_id}: {key} 期望 {want}，实得 {actual[key]}"


def test_all_case_panels_never_negative() -> None:
    """全部表驱动用例的面板 18 个字段一律非负（钳制不变量的正向扫描）。"""
    for case_id, kwargs, _expected in CASES:
        dumped = _panel(**kwargs).model_dump()
        for key, value in dumped.items():
            assert value >= 0, f"{case_id}.{key} 出现负数: {value}"


# --- 失败用例（acceptance 指定 3 组）----------------------------------------------


def test_extra_json_unregistered_affix_key_rejected() -> None:
    """失败用例①a：extra_json 传入未注册词缀键 → ValidationError。

    词缀注册表是封闭集合（入参模型 extra="forbid"）：脏键必须在入口被拒，
    不能静默进面板（否则拼写错误的词缀永远不生效且无人察觉）。
    """
    with pytest.raises(ValidationError):
        EquipmentInput.model_validate(
            {"slot": "weapon", "extra_json": {"spell_power": 3}}
        )


def test_extra_json_non_int_value_rejected() -> None:
    """失败用例①b：词缀非法值（非整数增量）→ ValidationError。

    词缀语义是整数点数增量（{"dc_max": 1}），字符串/浮点一律拒绝，
    防 JSONB 里混入脏数据后聚合出小数面板。
    """
    with pytest.raises(ValidationError):
        EquipmentInput.model_validate({"slot": "weapon", "extra_json": {"dc_max": "2"}})


def test_negative_panel_clamped_to_floor() -> None:
    """失败用例②：负面板被钳制到下限，永不出现负数。

    场景：叠加强力减益 buff 后总和为负。战 1 裸体基线 dc 2..5 / acc 5 /
    str 10 / ac_max 3，-100/-50/-99 的减益远超基线 → 全部钳到 0（不是负数）。
    """
    panel = _panel(
        "warrior",
        1,
        buffs=[
            {
                "buff_id": "heavy_curse",
                "stats": {
                    "dc_min": -100,
                    "dc_max": -100,
                    "accuracy": -100,
                    "ac_max": -50,
                    "strength": -99,
                },
            }
        ],
    )
    dumped = panel.model_dump()
    assert dumped["dc_min"] == 0
    assert dumped["dc_max"] == 0
    assert dumped["accuracy"] == 0
    assert dumped["ac_max"] == 0
    assert dumped["strength"] == 0
    assert all(value >= 0 for value in dumped.values()), "面板存在负数字段"


def test_unknown_passive_id_raises() -> None:
    """失败用例③：未知被动 id → 报错（设计选择：fail-fast 而非忽略）。

    被动加成来自已学技能，id 对不上说明上游数据不一致；静默忽略会把
    数据 bug 掩盖成"被动没生效"。已知被动注册表当前仅 basic_sword
    （MVP 三技能中唯一带属性型被动的技能，见 gamedata/skills/）。
    """
    with pytest.raises(ValueError, match="unknown passive"):
        _panel(
            "warrior",
            7,
            passives=[{"skill_id": "spirit_sword", "stats": {"accuracy": 3}}],
        )
