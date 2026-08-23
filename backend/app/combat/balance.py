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
    "BIRTH_MAP_ID",
    "CURSE_HIT_BIAS",
    "DEF_DAMAGE_REDUCTION_FACTOR",
    "GLOBAL_DROP_RATE",
    "GRACE_PERIOD_SECONDS",
    "LUCK_HIT_BIAS",
    "LUCK_MAX",
    "MAX_HIT_RATE",
    "MIN_DAMAGE",
    "MIN_HIT_RATE",
    "POTION_HP_PCT",
    "POTION_MP_PCT",
    "SKILL_MULTIPLIER_BASE",
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

# --- 背包 / 掉落 ----------------------------------------------------------------

# 背包容量。与 app/models/character.py 的 bag_capacity 列默认值 40 互为交叉校验
# （两处同步改，tests/test_balance.py 读 models 源码断言一致）。
# 出处: docs/MVP/README.md 背包规格 + models 默认; 置信度: high
BAG_CAPACITY: Final[int] = 40
# 全局掉落权重调整乘子（活动翻倍/校准总闸；1.0 = 不调整）。
# 具体每怪掉落权重在 gamedata/drops/*.json（todo 20），此处只做全局乘子。
# 出处: 自创（中性默认值）; 置信度: high
GLOBAL_DROP_RATE: Final[float] = 1.0
