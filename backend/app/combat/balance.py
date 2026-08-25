"""战斗数值系数唯一来源（single source of truth）。

纪律（docs/MVP/README.md §3.4 balance.py 职责、§13 风险#1）：
- combat/ 下任何其他模块**禁止**本地定义数值系数（tests/test_balance.py 有
  grep 防回归断言），一律 ``from app.combat.balance import XXX``；
- 本文件**不是** pydantic Settings：全部为模块级 ``Final`` 常量，不允许环境
  变量覆盖——balance 由 todo 37 校准流程统一调参，部署侧不得乱改；
- 每个系数必须带 ``# 出处:`` 与 ``# 置信度:`` 注释（测试强制检查）；
  完整的公式推导矩阵见 docs/combat-formulas.md；
- **不在此写死具体怪物/物品数值**（那些属于 gamedata/，todo 20 录入）。

出处置信度图例：high=社区公认/上游契约锚点；medium=社区近似但有分歧；
low=本项目自创近似，待 todo 37 挂机矩阵校准。
"""

from typing import Final

# 重导出：combat 子模块统一从这里拿出生城 id，避免各战斗模块直接 import
# app.core.config（收敛依赖面，防未来循环依赖）。
# 出处: app/core/config.py BIRTH_MAP_ID = "yin-xing-cun"（模块级 Final）; 置信度: high
from app.core.config import BIRTH_MAP_ID

__all__ = [
    "ATTACK_INTERVAL_TICKS",
    "BAG_CAPACITY",
    "BAG_FREE_SLOTS_RECALL",
    "BIRTH_MAP_ID",
    "BLESSING_OIL_LUCK_MAX",
    "BLESSING_OIL_SUCCESS_RATE",
    "CHARM_LEVEL_TOLERANCE",
    "CHARM_MAX_PER_CASTER",
    "CURSE_HIT_BIAS",
    "DEF_DAMAGE_REDUCTION_FACTOR",
    "DIVINE_WATER_DURATION_TICKS",
    "DOG_BASE_HP",
    "DOG_GROWTH_PER_LEVEL",
    "DROP_TIER_BONUS_MAX",
    "DROP_TIER_RATES",
    "DURABILITY_LOSS_PER_ATTACK",
    "DURABILITY_LOSS_PER_HIT",
    "GLOBAL_DROP_RATE",
    "GRACE_PERIOD_SECONDS",
    "LUCK_HIT_BIAS",
    "LUCK_MAX",
    "MAX_HIT_RATE",
    "MIN_DAMAGE",
    "MIN_HIT_RATE",
    "PORTAL_RANDOM_MAX_DIST",
    "PORTAL_RANDOM_MIN_DIST",
    "POTION_COOLDOWN_TICKS",
    "POTION_HP_PCT",
    "POTION_MP_PCT",
    "POTION_REGEN_TICKS",
    "POTION_URGENT_HP_PCT",
    "REPAIR_PRICE_PER_DURABILITY",
    "SHOP_BUY_MULTIPLIER",
    "SHOP_SELL_MULTIPLIER",
    "SKILL_MULTIPLIER_BASE",
    "SKULL_BASE_HP",
    "SKULL_GROWTH_PER_LEVEL",
    "TELEPORT_RATE_BASE",
    "TRAVEL_SECONDS_MAX",
    "TRAVEL_SECONDS_MIN",
]

# --- 命中 -------------------------------------------------------------------

# --- 命中 -------------------------------------------------------------------

# 命中率截断下限：命中率永不低于此值（永不绝对 miss）。
# 出处: thelegendofmir.com（2:1 公认式 + 社区"不绝对 miss/hit"截断惯例）; 置信度: medium
MIN_HIT_RATE: Final[float] = 0.05
# 命中率截断上限：命中率永不高于此值（永不绝对 hit）。
# 出处: thelegendofmir.com（同上）; 置信度: medium
MAX_HIT_RATE: Final[float] = 0.95

# 幸运硬阈值：幸运 ≥ 9 时伤害 roll 直接取区间上限（"幸运9套刀刀上限"）。
# 出处: 176 社区公认事实（祝福油武器幸运上限 +7 + 幸运项链等凑 9）; 置信度: high
LUCK_MAX: Final[int] = 9
# 每点幸运使伤害 roll 的归一化位置 u∈[0,1] 向上限偏移的幅度（幸运 < 9 时的线性近似）。
# 出处: 自创近似（社区只有"幸运9=上限"离散事实，中间档线性内插为本项目自创）; 置信度: low
LUCK_HIT_BIAS: Final[float] = 0.1
# 每点诅咒使伤害 roll 归一化位置向下限偏移的幅度（与幸运对称，无硬阈值规则）。
# 出处: 自创近似（诅咒机制社区记载模糊，对称建模为本项目自创）; 置信度: low
CURSE_HIT_BIAS: Final[float] = 0.1

# --- 伤害 / 减伤 --------------------------------------------------------------

# 每点防御（物理）/魔御（魔法）减少的伤害比例：reduction = stat × 本系数。
# 注意：真实 176 为线性直接扣减（damage - defense），百分比模型是本项目占位近似。
# 出处: 自创近似（docs/combat-formulas.md §3 有两种模型的对比推导）; 置信度: low
DEF_DAMAGE_REDUCTION_FACTOR: Final[float] = 0.05
# 平砍技能倍率占位；各技能倍率定义处为 gamedata/skills/*.json
# （todo 11 测试 fixture 手抄 docs/MVP/README.md §4 表，todo 20 全量录入）。
# 出处: 自创占位（1.0 = 无倍率恒等元）; 置信度: high
SKILL_MULTIPLIER_BASE: Final[float] = 1.0
# 单次伤害下限钳制：防御再高也至少造成 1 点伤害。
# 出处: docs/MVP/README.md §5.3（"钳制最低 1 点"）; 置信度: high
MIN_DAMAGE: Final[int] = 1

# --- 节奏 / 会话 ---------------------------------------------------------------

# 基础攻击间隔：每 N tick 出手一次（tick = 1Hz，即每 2 秒出手一次）。
# 职业/武器攻速、技能加速在 stats/skill 内派生（本值只是基准拍点）。
# 出处: 自创近似（176 无公开 tick 制攻速表，按挂机手感定基准）; 置信度: medium
ATTACK_INTERVAL_TICKS: Final[int] = 2
# 断线宽限期（秒）：SSE 断开后会话存活窗口。与 app.core.config 的
# settings.SSE_GRACE_PERIOD_SECONDS **重复声明是有意的**——combat 模块不依赖
# settings 实例以防耦合；两处必须同步改（tests/test_balance.py 有交叉断言）。
# 出处: 计划 W2 规格（todo 15 消费）；config.py SSE_GRACE_PERIOD_SECONDS; 置信度: high
GRACE_PERIOD_SECONDS: Final[int] = 60

# --- 自动吃药（策略本体在 todo 12 potion.py，此处只定默认阈值）---------------------

# HP 低于最大值 30% 触发自动喝金创药。
# 出处: 自创默认（挂机不死优先，todo 37 校准）; 置信度: low
POTION_HP_PCT: Final[float] = 0.30
# MP 低于最大值 20% 触发自动喝魔法药。
# 出处: 自创默认（同上）; 置信度: low
POTION_MP_PCT: Final[float] = 0.20
# HP 低于最大值 15% 视为危险：跳过普通药直接优先太阳水等瞬回药。
# 出处: docs/MVP/README.md §8 药品表（"太阳水保命用"），具体线自创，todo 37 校准; 置信度: low
POTION_URGENT_HP_PCT: Final[float] = 0.15
# 同一药品使用后的冷却 tick 数（tick=1s）：防低血线上连续多瓶抖动浪费。
# 出处: 自创近似（176 药品冷却社区记载模糊，按挂机手感定）; 置信度: low
POTION_COOLDOWN_TICKS: Final[int] = 5
# 背包剩余空格少于 N 格触发自动回城动作（potion.py 追加 recall 动作）。
# 出处: docs/MVP/README.md §8（"背包空格<N 时自动回城"），N 具体值自创; 置信度: medium
BAG_FREE_SLOTS_RECALL: Final[int] = 5

# --- 消耗品（todo 26：祝福油/神水/传送卷）-----------------------------------------

# 祝福油成功率：武器幸运 < 上限时单次使用 +1 的概率（失败不升幸运、油照耗）。
# 出处: docs/MVP/README.md §8（"祝福油 武器幸运 +1 有失败概率"未给数值），概率自创占位待 todo 37 校准; 置信度: low
BLESSING_OIL_SUCCESS_RATE: Final[float] = 0.6
# 武器幸运上限：祝福油最多把武器幸运叠到本值（LUCK_MAX=9 需幸运项链等凑齐）。
# 出处: docs/MVP/README.md §5（"武器幸运(祝福油,最多 +7)"）+ 176 社区公认事实; 置信度: high
BLESSING_OIL_LUCK_MAX: Final[int] = 7
# 神水限时 buff 时长（tick=1s，即 600s）：到期后面板加值自动回落。
# 出处: 计划 todo 26 规格（持续 600s）+ docs/MVP/README.md §8（攻击/魔力/道术神水限时加属性，未给时长）; 置信度: medium
DIVINE_WATER_DURATION_TICKS: Final[int] = 600
# 金创药类缓回药品的回复节奏基准（每 N tick 结算一跳；当前 REST/自动链均瞬回结算，
# 本值是缓回语义的节奏锚点，供 todo 37 校准与后续逐跳结算改造消费）。
# 出处: docs/MVP/README.md §8（金创药"缓回"无数值），节奏自创占位; 置信度: low
POTION_REGEN_TICKS: Final[int] = 5
# 随机传送卷同图随机位移距离档下限/上限（MVP 无坐标网格，距离档仅入日志播报）。
# 出处: docs/MVP/README.md §8（"随机传送卷 当前地图内随机坐标"），档位数值自创占位; 置信度: low
PORTAL_RANDOM_MIN_DIST: Final[int] = 10
PORTAL_RANDOM_MAX_DIST: Final[int] = 50

# --- 背包 / 掉落 ----------------------------------------------------------------

# 背包容量。与 app/models/character.py 的 bag_capacity 列默认值 40 互为交叉校验
# （两处同步改，tests/test_balance.py 读 models 源码断言一致）。
# 出处: docs/MVP/README.md 背包规格 + models 默认; 置信度: high
BAG_CAPACITY: Final[int] = 40
# 全局掉落权重调整乘子（活动翻倍/校准总闸；1.0 = 不调整）。
# 具体每怪掉落权重在 gamedata/drops/*.json（todo 20），此处只做全局乘子。
# 出处: 自创（中性默认值）; 置信度: high
GLOBAL_DROP_RATE: Final[float] = 1.0
# 掉落词缀档位概率分布：普通/优秀/稀有/史诗（合计 ≈ 1，残差归普通档兜底）。
# 消费方：combat/drops.py roll_tier（todo 12）；期望计数 = 次数 × 本表。
# 出处: 自创占位（社区只记词缀概念无档位表）; 置信度: low
DROP_TIER_RATES: Final[dict[str, float]] = {
    "normal": 0.94,
    "excellent": 0.05,
    "rare": 0.008,
    "epic": 0.002,
}
# 各档位词缀数值上限：普通档无词缀，优秀 +1、稀有 +3、史诗 +7；
# 实际词缀值再被物品 prefix_max 钳制（drops.py D5 规则，todo 12）。
# 出处: 自创占位（社区只记词缀概念无档位表）; 置信度: low
DROP_TIER_BONUS_MAX: Final[dict[str, int]] = {
    "normal": 0,
    "excellent": 1,
    "rare": 3,
    "epic": 7,
}

# --- 召唤物（todo 16 sim/pet.py）--------------------------------------------------

# 诱惑之光同时魅惑存活的怪物数量上限（同一施法者）。
# 出处: 自创占位（社区只记"法师可诱惑多只怪"概念，无公认数值）; 置信度: low
CHARM_MAX_PER_CASTER: Final[int] = 5
# 可魅惑怪物的等级宽容度：怪物等级 ≤ 主人等级 + 本值，超出则诱惑失败。
# 出处: 自创占位（社区只记概念无具体数值）; 置信度: low
CHARM_LEVEL_TOLERANCE: Final[int] = 7
# 召唤骷髅基础 HP：max_hp = 本值 + SKULL_GROWTH_PER_LEVEL × 技能等级。
# 出处: 自创占位（社区只记概念无具体数值）; 置信度: low
SKULL_BASE_HP: Final[int] = 30
# 召唤骷髅每级技能等级的 HP 成长。
# 出处: 自创占位（社区只记概念无具体数值）; 置信度: low
SKULL_GROWTH_PER_LEVEL: Final[int] = 10
# 召唤神兽基础 HP：max_hp = 本值 + DOG_GROWTH_PER_LEVEL × 技能等级。
# 出处: 自创占位（社区只记概念无具体数值）; 置信度: low
DOG_BASE_HP: Final[int] = 60
# 召唤神兽每级技能等级的 HP 成长。
# 出处: 自创占位（社区只记概念无具体数值）; 置信度: low
DOG_GROWTH_PER_LEVEL: Final[int] = 15

# --- 耐久（todo 24 磨损/失效/普修）-----------------------------------------------

# 玩家每次出手命中扣武器耐久的点数（整数化：不做小数磨损，扣完钳 0）.
# 出处: 自创占位（176 社区只记"装备会掉耐久"概念，无单次磨损数值）; 置信度: low
DURABILITY_LOSS_PER_ATTACK: Final[int] = 1
# 玩家每次被怪物命中扣防具（衣服槽）耐久的点数（与武器磨损对称建模）.
# 出处: 自创占位（同上，社区无单次磨损数值）; 置信度: low
DURABILITY_LOSS_PER_HIT: Final[int] = 1
# 商店普修单价：修复 1 点耐久的金币费用；cost = Σ(max_durability - current)
# × 本值（只修不降上限——特修不在 MVP 范围）。
# 出处: 自创占位（社区无普修价目表，按挂机金币产出量级定）; 置信度: low
REPAIR_PRICE_PER_DURABILITY: Final[int] = 100

# --- 商店（todo 27 NPC 商店买卖）-------------------------------------------------

# 商店买入价系数：实际买入单价 = 商店条目价（缺省回退物品 buy_price）× 本值
# （1.0 = 原价，无加成）。消费方：api/shop.py buy（todo 27）。
# 出处: 自创占位（176 社区无统一商店加价率记载，MVP 按原价出售）; 置信度: low
SHOP_BUY_MULTIPLIER: Final[float] = 1.0
# 商店回收价系数：卖出所得单价 = 买入单价 × 本值（四舍五入取整；回收折扣 < 1
# 防买卖套利刷金币）。消费方：api/shop.py sell（todo 27）。
# 出处: 自创占位（docs/MVP/README.md §8 只记"商店可回收"，无统一回收折扣，0.4 为自创）; 置信度: low
SHOP_SELL_MULTIPLIER: Final[float] = 0.4

# --- 跨地图行进（todo 30 传送/跑图消费）---------------------------------------

# 地图间"行进中"倒计时下限（秒）：最近邻地图的跑图时间。
# 出处: docs/MVP/README.md §10 给出 5–20s 区间；区间端点取文档值，区间内按
#       地图距离的具体分配为本项目自创; 置信度: medium
TRAVEL_SECONDS_MIN: Final[int] = 5
# 地图间"行进中"倒计时上限（秒）：跨半张世界图的跑图时间。
# 出处: docs/MVP/README.md §10 同上（5–20s 区间端点）；距离→时长的映射自创; 置信度: medium
TRAVEL_SECONDS_MAX: Final[int] = 20
# 传送费率基础系数（金币/费率单位）：实际扣费 = round(费率系数 × 本值)。
# 费率系数表在 gamedata/teleport.json（todo 30）；未录价的邻接边按系数 1.0。
# 出处: 自创（176 社区无公开传送员价目表，量级按挂机金币产出定）; 置信度: low
TELEPORT_RATE_BASE: Final[int] = 100
