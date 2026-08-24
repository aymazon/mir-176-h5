"""掉落 roll 纯函数（todo 12）。

规则基线（tests/combat/test_drops.py 被测契约、docs/MVP/README.md §5.7/§8）::

    D1 每次击杀按 weight 加权选**一条** entry：金币 entry 掉
       gold∈[gold_min, gold_max]（闭区间），物品 entry 掉 qty∈[min_qty, max_qty]；
       金币与物品互斥（单条 entry 选择语义）。
    D2 数量 roll 用注入 rng 的 randint，固定 seed 必可复现。
    D3 GLOBAL_DROP_RATE 是总闸门：≤0 全空；<1 时先掷闸门，未过则空；
       默认 1.0 必出（活动翻倍语义见 balance 注释）。
    D4 装备词缀档位 normal/excellent/rare/epic 按 DROP_TIER_RATES 分布
       （roll_tier；浮点残差兜底归普通档）。
    D5 词缀数值 +1~DROP_TIER_BONUS_MAX[tier]，再被物品 prefix_max **钳制不超**
       （cap = min(档位上限, prefix_max)）。
    D6 prefix_max=0 的物品（药品/书/肉）不进词缀流程，永不带 extra_json。
    D7 词缀只落在物品 stats 里已有的 *_max 战斗键上，
       extra_json 形如 {"dc_max": 1}（单键值对）。

边界纪律：
- 纯函数：随机性全部经注入的 ``random.Random``，不碰全局随机源、不读不写
  DB（掉落结果入库是 sim 层的事）；装备自动拾取属 todo 25，不在本模块；
- 数值系数唯一来源是 ``app.combat.balance``——本文件禁止出现小数字面量
  （tests/test_balance.py 有 grep 防回归断言），GLOBAL_DROP_RATE 必须**每次
  调用时**经模块属性读取（测试会 monkeypatch 该属性做总闸开关）。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.combat import balance
from app.gamedata_schema import DropEntry, ItemDef

__all__ = [
    "DropResult",
    "DroppedItem",
    "roll_drop",
    "roll_tier",
]


@dataclass(frozen=True)
class DroppedItem:
    """单条掉落实例（extra_json 仅装备词缀档非 None，D6/D7）。"""

    item_id: str
    qty: int
    tier: str = "normal"
    extra_json: dict[str, int] | None = None


@dataclass(frozen=True)
class DropResult:
    """一次击杀的掉落结果：monster_id + gold 数额 + items 列表。"""

    monster_id: str
    gold: int = 0
    items: tuple[DroppedItem, ...] = ()


def roll_tier(rng: random.Random) -> str:
    """按 DROP_TIER_RATES 分布掷词缀档位（D4，浮点残差归普通档兜底）。"""
    u = rng.random()
    cumulative = 0
    for tier, rate in balance.DROP_TIER_RATES.items():
        cumulative += rate
        if u < cumulative:
            return tier
    return "normal"


def _roll_affix(item: ItemDef, rng: random.Random) -> tuple[str, dict[str, int] | None]:
    """掷档位并生成词缀（D5/D6/D7）；返回 (tier, extra_json)。"""
    if item.prefix_max <= 0:
        return "normal", None  # D6：无词缀位物品不进档位流程
    tier = roll_tier(rng)
    cap = min(balance.DROP_TIER_BONUS_MAX[tier], item.prefix_max)
    if cap <= 0:
        return tier, None  # 普通档（或被 prefix_max 钳到零）→ 无词缀
    max_keys = sorted(key for key in item.stats if key.endswith("_max"))
    if not max_keys:
        return tier, None  # 无可附魔的 *_max 战斗键 → 词缀无处安放
    key = rng.choice(max_keys)  # 多个 *_max 键时随机取一（D7 封闭集合内）
    bonus = rng.randint(1, cap)
    return tier, {key: bonus}


def roll_drop(
    table: DropEntry,
    items_by_id: dict[str, ItemDef],
    rng: random.Random,
) -> DropResult:
    """击杀结算：加权选一条 entry → 金币区间 / 物品数量 + 装备词缀（D1-D7）。"""
    monster_id = table.monster_id
    gate = balance.GLOBAL_DROP_RATE  # 每次调用读取，支持运行期总闸调整（D3）
    if gate <= 0:
        return DropResult(monster_id=monster_id)
    if gate < 1 and rng.random() >= gate:
        return DropResult(monster_id=monster_id)

    total_weight = sum(entry.weight for entry in table.entries)
    point = rng.random() * total_weight
    chosen = table.entries[-1]
    cumulative = 0
    for entry in table.entries:
        cumulative += entry.weight
        if point < cumulative:
            chosen = entry
            break

    if chosen.item_id is None:  # 金币 entry（D1 闭区间）
        gold = rng.randint(chosen.gold_min, chosen.gold_max)
        return DropResult(monster_id=monster_id, gold=gold)

    # 目录缺失该物品（如调用方只注入部分目录）→ 照常掉出，仅跳过词缀流程：
    # 词缀需要 ItemDef 的 prefix_max/stats 才能掷档与钳制（D5-D7），缺定义即无词缀。
    item = items_by_id.get(chosen.item_id)
    qty = rng.randint(chosen.min_qty, chosen.max_qty)
    if item is None:
        dropped = DroppedItem(item_id=chosen.item_id, qty=qty)
    else:
        tier, extra_json = _roll_affix(item, rng)
        dropped = DroppedItem(
            item_id=chosen.item_id,
            qty=qty,
            tier=tier,
            extra_json=extra_json,
        )
    return DropResult(monster_id=monster_id, items=(dropped,))
