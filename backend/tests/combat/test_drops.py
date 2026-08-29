"""todo 12 TDD：掉落 roll 纯函数 roll_drop（先写测试，后实现）。

推导注释纪律：期望值由「加权表结构 + balance 档位分布 + prefix_max 钳制」推出，
不复述实现代码。随机性全部经注入的 random.Random(seed)，固定 seed 必可复现。

规则基线（docs/MVP/README.md §5.7/§8 + 计划 todo 12 规格）：
  D1 每次击杀按 weight 加权选**一条** entry；金币 entry 掉 gold∈[gold_min, gold_max]；
  D2 物品 entry 掉 qty∈[min_qty, max_qty]；
  D3 GLOBAL_DROP_RATE 是总闸门：rate≤0 全空、默认 1.0 必出（活动翻倍语义见 balance）;
  D4 装备词缀档位 普通/优秀/稀有/史诗 按 DROP_TIER_RATES 分布（普通怪约 5% 出优秀）；
  D5 词缀数值 +1~DROP_TIER_BONUS_MAX[tier]，再被物品 prefix_max **钳制不超**；
  D6 prefix_max=0 的物品（药品/书/肉）永不带词缀；
  D7 词缀只落在 stats 里已有的 *_max 战斗键上（extra_json 形如 {"dc_max": 1}）。
"""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from app.combat import balance
from app.combat.drops import DroppedItem, roll_drop, roll_tier
from app.gamedata_schema import DropEntry, ItemDef

GAMEDATA_ROOT = Path(__file__).resolve().parents[2] / "gamedata"

# 真实 gamedata 物品（task 13 数据集）：dagger/cloth_cap prefix_max=1，
# meat/potion_hp_small prefix_max=0 —— 词缀规则直接对真实数据负责。
ITEM_IDS = ("dagger", "cloth_cap", "meat", "potion_hp_small")
ITEMS: dict[str, ItemDef] = {
    item_id: ItemDef.model_validate(
        json.loads((GAMEDATA_ROOT / "items" / f"{item_id}.json").read_text(encoding="utf-8"))
    )
    for item_id in (*ITEM_IDS, "sun_water")
}


def _table(entries: list[dict[str, Any]], monster_id: str = "chicken") -> DropEntry:
    return DropEntry.model_validate({"monster_id": monster_id, "entries": entries})


CHICKEN_TABLE = _table(
    [
        {"item_id": None, "weight": 50, "gold_min": 5, "gold_max": 20},
        {"item_id": "meat", "weight": 30, "min_qty": 1, "max_qty": 1},
        {"item_id": "wooden_sword", "weight": 1, "min_qty": 1, "max_qty": 1},
    ]
)

SUN_WATER_TABLE = _table(
    [{"item_id": "sun_water", "weight": 10, "min_qty": 1, "max_qty": 2}],
    monster_id="orc_champion",
)


def _roll_1000(table: DropEntry, seed: int) -> list[Any]:
    rng = random.Random(seed)
    return [roll_drop(table, ITEMS, rng) for _ in range(1000)]


# --- 金币区间 + 固定 seed 可复现（验收 #2）--------------------------------------


def test_gold_within_range_and_seed_reproducible() -> None:
    runs = [_roll_1000(CHICKEN_TABLE, seed=42) for _ in range(2)]
    # 可复现：同 seed 两次 1000 连滚逐结果全等
    assert runs[0] == runs[1]

    golds = [r.gold for r in runs[0]]
    non_zero = [g for g in golds if g > 0]
    assert non_zero, "权重 50/81 的金币 entry 千滚内必出现"
    # D1 区间闭区间 [min, max]
    assert all(5 <= g <= 20 for g in non_zero)
    # 金币与物品互斥：单条 entry 选择语义
    for r in runs[0]:
        assert (r.gold > 0) != bool(r.items)


def test_result_shape_echoes_monster_id() -> None:
    rng = random.Random(1)
    result = roll_drop(SUN_WATER_TABLE, ITEMS, rng)
    assert result.monster_id == "orc_champion"
    assert isinstance(result.items, tuple)
    for dropped in result.items:
        assert isinstance(dropped, DroppedItem)
        assert dropped.extra_json is None or isinstance(dropped.extra_json, dict)


# --- 加权统计分布（千滚统计 + 复现）---------------------------------------------


def test_weighted_distribution_matches_weights_1000_rolls() -> None:
    results = _roll_1000(CHICKEN_TABLE, seed=42)
    kinds = Counter(
        "gold" if r.gold > 0 else r.items[0].item_id for r in results if r.items or r.gold > 0
    )
    total = sum(kinds.values())
    assert total == 1000
    # 权重比 50:30:1 → 金币约 61.7%、肉约 37.1%（±5pp 容差），木剑千滚内必现身
    assert 0.55 <= kinds["gold"] / total <= 0.69
    assert 0.31 <= kinds["meat"] / total <= 0.44
    assert kinds["wooden_sword"] >= 1


def test_qty_within_declared_bounds() -> None:
    # D2: 太阳水 min=1 max=2 → 千滚 qty 只能是 1 或 2，且两值都应出现过
    results = _roll_1000(SUN_WATER_TABLE, seed=7)
    qtys = {dropped.qty for r in results for dropped in r.items}
    assert qtys == {1, 2}


def test_global_drop_rate_gate_blocks_all_at_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # D3: 总闸归零 → 全空且不报错；恢复默认 1.0 → 权重恒正必非空
    def _is_empty(r: Any) -> bool:
        return r.gold == 0 and not r.items

    monkeypatch.setattr(balance, "GLOBAL_DROP_RATE", 0.0)
    rng = random.Random(3)
    assert all(_is_empty(roll_drop(CHICKEN_TABLE, ITEMS, rng)) for _ in range(50))
    monkeypatch.setattr(balance, "GLOBAL_DROP_RATE", 1.0)
    assert all(not _is_empty(roll_drop(CHICKEN_TABLE, ITEMS, rng)) for _ in range(50))


# --- 档位分布（验收 #3：极品率 + 无超档）----------------------------------------


def test_roll_tier_distribution_fixed_seed() -> None:
    # D4: 期望计数 = 1000 × DROP_TIER_RATES（excellent≈50、rare≈8、epic≈2）
    expected = {tier: 1000 * p for tier, p in balance.DROP_TIER_RATES.items()}
    rng = random.Random(11)
    counts = Counter(roll_tier(rng) for _ in range(1000))
    assert counts["excellent"] == pytest.approx(expected["excellent"], abs=25)
    assert counts["rare"] == pytest.approx(expected["rare"], abs=10)
    assert counts["epic"] == pytest.approx(expected["epic"], abs=5)
    assert counts["normal"] == pytest.approx(expected["normal"], abs=35)
    # 复现性：重放同 seed 计数完全一致
    rng2 = random.Random(11)
    assert Counter(roll_tier(rng2) for _ in range(1000)) == counts


@pytest.mark.parametrize("item_id", ["dagger", "cloth_cap"])
def test_affix_never_exceeds_item_prefix_max(item_id: str) -> None:
    # D5 fail-case 实证：prefix_max=1 的装备千滚词缀值只能是 1，绝不出现 +2/+3
    equip_table = _table([{"item_id": item_id, "weight": 1, "min_qty": 1, "max_qty": 1}])
    affixed = [
        dropped.extra_json
        for r in _roll_1000(equip_table, seed=5)
        for dropped in r.items
        if dropped.extra_json
    ]
    assert affixed, "千滚内必出词缀（非普通档合计约 6%）"
    max_keys = {k for k in ITEMS[item_id].stats if k.endswith("_max")}
    for extra in affixed:
        ((key, bonus),) = extra.items()
        assert key in max_keys  # D7 词缀只落在物品已有的 *_max 战斗键上
        assert 1 <= bonus <= ITEMS[item_id].prefix_max


def test_prefix_max_zero_items_never_get_affix() -> None:
    # D6: meat / potion_hp_small prefix_max=0 → 即使掷中史诗档也无词缀
    for item_id in ("meat", "potion_hp_small"):
        table = _table([{"item_id": item_id, "weight": 1, "min_qty": 1, "max_qty": 1}])
        extras = [
            dropped.extra_json for r in _roll_1000(table, seed=9) for dropped in r.items
        ]
        assert all(e is None for e in extras), item_id


def test_high_prefix_cap_item_shows_full_tier_spread() -> None:
    # 合成 prefix_max=3 的武器：D4+D5 叠加 → 词缀率≈6%，且 +3（仅史诗可达）千滚内出现
    high_cap = ItemDef.model_validate(
        {
            "id": "test_blade",
            "name": "测试之刃",
            "kind": "weapon",
            "stackable": False,
            "max_durability": 10,
            "sell_price": 1,
            "buy_price": 2,
            "stats": {"dc_min": 1, "dc_max": 5},
            "prefix_max": 3,
        }
    )
    items = {**ITEMS, "test_blade": high_cap}
    table = _table([{"item_id": "test_blade", "weight": 1, "min_qty": 1, "max_qty": 1}])
    bonuses = [
        dropped.extra_json["dc_max"]
        for r in _roll_1000_with_items(table, items, seed=13)
        for dropped in r.items
        if dropped.extra_json
    ]
    affix_rate = len(bonuses) / 1000
    assert 0.03 <= affix_rate <= 0.09  # 非普通档合计 ≈ 6%
    assert max(bonuses) == 3  # 史诗档未被钳制时可达上限
    assert all(1 <= b <= 3 for b in bonuses)


def _roll_1000_with_items(
    table: DropEntry, items: dict[str, ItemDef], seed: int
) -> list[Any]:
    rng = random.Random(seed)
    return [roll_drop(table, items, rng) for _ in range(1000)]
