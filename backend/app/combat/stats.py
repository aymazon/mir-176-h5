"""面板聚合纯函数（todo 9）。

聚合规则（tests/combat/test_stats.py 被测契约，docs/combat-formulas.md）::

    final[key] = max(0, base(class, level) + Σ装备.stats + Σ装备.extra_json
                         + Σ被动.stats + Σbuff.stats)
    luck/curse 额外累加装备列 lucky/curse（ItemInstance 同名列）；
    luck 钳到 [0, LUCK_MAX]（"幸运9套"上限在面板层聚合到位，
    "刀刀上限"硬规则由 todo 10 damage.py 消费）。

成长公式：``value = base + gain * ((level - 1) // per)``（整数逐档成长）。

数据与系数的边界纪律：
- CLASS_GROWTH 是**整数角色成长表**（角色数据，同 gamedata 性质），不是战斗
  调参系数——故按 test_stats.py 模块注释约定放在本文件；已测锚点（裸体
  战/法/道 1 级、战 7/20/22 级、法 22 级、道 20 级）与测试手算推导一一对应，
  未测格为自创占位，待 todo 20 全量录入时回填校准；
- 战斗调参系数唯一来源仍是 ``app.combat.balance``——本文件禁止出现小数
  字面量（tests/test_balance.py 有 grep 防回归断言），当前仅消费 LUCK_MAX。

纪律：
- 纯函数：不读 DB、不写库、不依赖全局随机源（无 random import）；
- 词缀注册表是封闭集合：extra_json/stats 出现未注册属性键 → ValidationError
  （脏键必须在入口被拒，不能静默进面板）；
- 未知被动 id → ValueError fail-fast（上游数据不一致不得掩盖成"被动没生效"）。
"""

from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Final

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt

from app.combat.balance import LUCK_MAX

__all__ = [
    "BuffInput",
    "ClassKey",
    "EquipmentInput",
    "PassiveInput",
    "StatPanel",
    "compute_panel",
]


class ClassKey(StrEnum):
    """职业键（StrEnum：JSON 里就是字符串）。"""

    warrior = "warrior"
    wizard = "wizard"
    taoist = "taoist"


class StatPanel(BaseModel):
    """最终面板：18 个非负整数标量（消费方：hit/damage/skills/穿戴门槛）。"""

    model_config = ConfigDict(extra="forbid")

    dc_min: int = Field(ge=0)
    dc_max: int = Field(ge=0)
    mc_min: int = Field(ge=0)
    mc_max: int = Field(ge=0)
    sc_min: int = Field(ge=0)
    sc_max: int = Field(ge=0)
    ac_min: int = Field(ge=0)
    ac_max: int = Field(ge=0)
    mac_min: int = Field(ge=0)
    mac_max: int = Field(ge=0)
    accuracy: int = Field(ge=0)
    agility: int = Field(ge=0)
    attack_speed: int = Field(ge=0)
    luck: int = Field(ge=0)
    curse: int = Field(ge=0)
    strength: int = Field(ge=0)
    magic_power: int = Field(ge=0)
    taoism: int = Field(ge=0)


PANEL_KEYS: Final[frozenset[str]] = frozenset(StatPanel.model_fields)


def _reject_unknown_keys(value: dict[str, int]) -> dict[str, int]:
    """属性键白名单校验：未注册键一律拒绝（fail-fast，防拼写错误静默失效）。"""
    unknown = set(value) - PANEL_KEYS
    if unknown:
        raise ValueError(f"未注册属性键: {sorted(unknown)}，允许键即 StatPanel 字段")
    return value


# StrictInt：词缀语义是整数点数增量，字符串/浮点/布尔一律拒绝（防 JSONB 脏数据
# 聚合出小数面板）；再挂白名单校验器构成封闭集合。
StatDict = Annotated[dict[str, StrictInt], AfterValidator(_reject_unknown_keys)]


class EquipmentInput(BaseModel):
    """单件装备入参（ItemInstance 的战斗投影，不依赖 gamedata 文件）。"""

    model_config = ConfigDict(extra="forbid")

    slot: str
    stats: StatDict = Field(default_factory=dict)
    # 幸运/诅咒独立列（祝福油/PK 掉诅咒写在实例列，不混进 stats 包）
    lucky: int = Field(default=0, ge=0)
    curse: int = Field(default=0, ge=0)
    # 极品词缀包（drops.py D5/D7 产出形如 {"dc_max": 1}）
    extra_json: StatDict = Field(default_factory=dict)
    # 当前耐久：None = 无耐久概念（不参与失效过滤）；0 = 已磨坏（面板不计入，
    # "属性失效"的输入侧过滤——stats 计算本体不动，todo 24）。
    durability: int | None = Field(default=None, ge=0)


class PassiveInput(BaseModel):
    """技能被动加成入参（已学技能的常驻属性增量）。"""

    model_config = ConfigDict(extra="forbid")

    skill_id: str
    stats: StatDict = Field(default_factory=dict)


class BuffInput(BaseModel):
    """限时 buff 入参（计时归 todo 26，此处只做加法聚合）。"""

    model_config = ConfigDict(extra="forbid")

    buff_id: str
    stats: StatDict = Field(default_factory=dict)


# 已知被动注册表：MVP 三技能中唯一带属性型被动的技能（gamedata/skills/，
# tests/combat/test_stats.py 失败用例③锚点）；扩表随 todo 11 skills.py。
KNOWN_PASSIVE_IDS: Final[frozenset[str]] = frozenset({"basic_sword"})

# 成长三元组 (base, gain, per)：value = base + gain * ((level - 1) // per)。
GrowthSpec = tuple[int, int, int]

# 职业成长表（整数角色数据；出处见模块 docstring——已测锚点 = test_stats.py
# 手算推导，未测格自创占位待 todo 20 校准）。
CLASS_GROWTH: Final[dict[ClassKey, dict[str, GrowthSpec]]] = {
    ClassKey.warrior: {
        "dc_min": (2, 1, 3),
        "dc_max": (5, 1, 2),
        "mc_min": (0, 0, 1),
        "mc_max": (0, 1, 7),
        "sc_min": (0, 0, 1),
        "sc_max": (0, 0, 1),
        "ac_min": (1, 1, 4),
        "ac_max": (3, 1, 3),
        "mac_min": (0, 0, 1),
        "mac_max": (0, 1, 5),
        "accuracy": (5, 1, 2),
        "agility": (3, 1, 4),
        "attack_speed": (0, 0, 1),
        "luck": (0, 0, 1),
        "curse": (0, 0, 1),
        "strength": (10, 1, 2),
        "magic_power": (1, 1, 5),
        "taoism": (1, 1, 5),
    },
    ClassKey.wizard: {
        "dc_min": (1, 1, 6),
        "dc_max": (2, 1, 5),
        "mc_min": (3, 1, 3),
        "mc_max": (6, 1, 2),
        "sc_min": (0, 0, 1),
        "sc_max": (0, 0, 1),
        "ac_min": (0, 1, 6),
        "ac_max": (1, 1, 5),
        "mac_min": (0, 0, 1),
        "mac_max": (2, 1, 2),
        "accuracy": (4, 1, 3),
        "agility": (3, 1, 4),
        "attack_speed": (0, 0, 1),
        "luck": (0, 0, 1),
        "curse": (0, 0, 1),
        "strength": (1, 0, 1),
        "magic_power": (10, 1, 2),
        "taoism": (1, 1, 5),
    },
    ClassKey.taoist: {
        "dc_min": (1, 1, 5),
        "dc_max": (3, 1, 4),
        "mc_min": (0, 0, 1),
        "mc_max": (1, 1, 4),
        "sc_min": (2, 1, 3),
        "sc_max": (4, 1, 2),
        "ac_min": (0, 1, 5),
        "ac_max": (2, 1, 4),
        "mac_min": (0, 0, 1),
        "mac_max": (1, 1, 3),
        "accuracy": (4, 1, 3),
        "agility": (4, 1, 4),
        "attack_speed": (0, 0, 1),
        "luck": (0, 0, 1),
        "curse": (0, 0, 1),
        "strength": (1, 0, 1),
        "magic_power": (1, 1, 5),
        "taoism": (10, 1, 2),
    },
}


def compute_panel(
    *,
    char_class: ClassKey,
    level: int,
    equipment: Sequence[EquipmentInput] = (),
    passives: Sequence[PassiveInput] = (),
    buffs: Sequence[BuffInput] = (),
) -> StatPanel:
    """裸装基线 + 装备（含词缀）/被动/buff 加法聚合 → 最终面板。

    纯函数：输入模型均已过 pydantic 校验（词缀白名单/StrictInt 在入口拒绝），
    本函数只做整数加法 + 非负钳制 + 幸运上限钳制。
    """
    if level < 1:
        raise ValueError(f"level 必须 ≥ 1，实得 {level}")
    unknown_passives = [
        p.skill_id for p in passives if p.skill_id not in KNOWN_PASSIVE_IDS
    ]
    if unknown_passives:
        raise ValueError(f"unknown passive skill_id: {', '.join(unknown_passives)}")

    growth = CLASS_GROWTH[char_class]
    totals: dict[str, int] = {
        key: base + gain * ((level - 1) // per)
        for key, (base, gain, per) in growth.items()
    }
    for item in equipment:
        if item.durability == 0:
            continue  # 耐久耗尽 → 属性失效（输入侧过滤；修理后恢复计入）
        for key, value in item.stats.items():
            totals[key] += value
        for key, value in item.extra_json.items():
            totals[key] += value
        totals["luck"] += item.lucky
        totals["curse"] += item.curse
    for source in (*passives, *buffs):
        for key, value in source.stats.items():
            totals[key] += value

    totals["luck"] = min(totals["luck"], LUCK_MAX)
    return StatPanel(**{key: max(0, value) for key, value in totals.items()})
