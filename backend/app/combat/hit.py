"""命中判定纯函数（todo 10）。

公式（docs/combat-formulas.md §1，thelegendofmir.com 2:1 公认式的离散均匀读法）::

    roll = rng.randint(0, defender_agility)   # 含两端，共 agility+1 个等概率取值
    hit  ⟺ roll < attacker_accuracy
    P_raw = min(accuracy, agility + 1) / (agility + 1)
    P_hit = clamp(P_raw, MIN_HIT_RATE, MAX_HIT_RATE)

等价实现：``rng.random() < P_hit`` 与上述离散判定同分布，且对任意 (准确, 敏捷)
组合只需一次 uniform roll。

纪律：
- 纯函数：不读 DB、不写库、不依赖全局随机源——``random.Random`` 实例由调用方
  注入（可复现）；
- 系数一律取自 ``app.combat.balance``（本文件禁止出现小数字面量，
  tests/test_balance.py 有 grep 防回归断言）。
"""

from random import Random

from app.combat.balance import MAX_HIT_RATE, MIN_HIT_RATE

__all__ = ["hit_check", "hit_probability"]


def hit_probability(attacker_accuracy: int, defender_agility: int) -> float:
    """命中率 = f(攻击方准确, 防御方敏捷)，截断到 [MIN_HIT_RATE, MAX_HIT_RATE]。

    永不绝对 miss / 绝对 hit（社区截断惯例）。
    """
    if attacker_accuracy < 0 or defender_agility < 0:
        raise ValueError("accuracy/agility 必须非负")
    sides = defender_agility + 1
    raw = min(attacker_accuracy, sides) / sides
    return min(max(raw, MIN_HIT_RATE), MAX_HIT_RATE)


def hit_check(rng: Random, attacker_accuracy: int, defender_agility: int) -> bool:
    """单次命中 roll：``rng.random() < P_hit`` 时命中。"""
    return rng.random() < hit_probability(attacker_accuracy, defender_agility)
