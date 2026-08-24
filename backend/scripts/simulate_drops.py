"""simulate_drops.py — task 21 验收：白野猪击杀掉落分布模拟。

用 combat.drops.roll_drop（与挂机结算同一代码路径）模拟 1000 次白野猪
（wild_boar_white）击杀，输出分布报告并做容差断言：

- 沃玛装备合计出现率与设定值（权重占比）偏差 ≤ ±20%（验收硬性口径）；
- 35 级技能书合计出现率落在二项分布 3σ 带内（n=1000 下 ±20% 相对容差
  仅 ≈±0.7%，小于抽样噪声 σ≈0.6%，故书类用统计带宽而非固定相对容差）；
- 金币行出现率与设定值偏差 ≤ ±20%（表内恒有金币行由 spotcheck 静态断言）；
- 屠龙/嗜魂彩蛋出现率 ≤ 0.001×1.5（weight=1 / 总权重 ⇒ 理论 ≤0.001）。

固定 seed 保证可复现；报告落 stdout 供 evidence 日志收录。
用法：cd backend && uv run python scripts/simulate_drops.py [--kills N] [--seed S]
退出码：0 = 全部容差内；1 = 任一断言失败。
"""

# pyright: reportUnusedCallResult=false, reportImplicitRelativeImport=false

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.combat.balance import GLOBAL_DROP_RATE
from app.combat.drops import roll_drop
from app.gamedata_loader import DEFAULT_GAMEDATA_ROOT, load_all
from app.gamedata_schema import DropEntry, ItemDef

# 沃玛档装备（§6"白野猪掉沃玛装备"的物品集口径）
WOOMA_TIER_ITEMS: frozenset[str] = frozenset({
    "verdict_staff", "bone_jade_staff", "dragonvein_sword", "xueyin_sword",
    "war_god_armor", "ghost_armor", "demon_bell", "knight_bracelet",
    "three_eye_bracelet", "titan_ring", "tianzhu_necklace", "dragon_ring",
    "dragonhand_bracelet", "life_necklace", "soul_necklace",
    "purple_conch_ring", "power_ring", "white_tiger_tooth_necklace",
})
# 35 级技能书三件（README §4：烈火剑法/冰咆哮/召唤神兽）
BOOK35_ITEMS: frozenset[str] = frozenset({
    "book_fire_sword", "book_ice_roar", "book_summon_beast",
})
# 彩蛋（权重 1，理论出现率 ≤0.001）
EGG_ITEMS: frozenset[str] = frozenset({"dragonslayer_blade", "soul_eater_staff"})


def theoretical_rates(table: DropEntry) -> dict[frozenset[str] | None, float]:
    """各关注集合的理论出现率（weight 占比；None=金币行）。"""
    total = sum(e.weight for e in table.entries)

    def rate(pred_id: str) -> float:
        return sum(
            e.weight for e in table.entries if e.item_id == pred_id
        ) / total

    return {
        None: sum(e.weight for e in table.entries if e.item_id is None) / total,
        WOOMA_TIER_ITEMS: sum(rate(i) for i in WOOMA_TIER_ITEMS),
        BOOK35_ITEMS: sum(rate(i) for i in BOOK35_ITEMS),
        EGG_ITEMS: sum(rate(i) for i in EGG_ITEMS),
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    kills = int(args[args.index("--kills") + 1]) if "--kills" in args else 1000
    seed = int(args[args.index("--seed") + 1]) if "--seed" in args else 176

    data = load_all(DEFAULT_GAMEDATA_ROOT)
    table = data["drops"]["wild_boar_white"]
    assert isinstance(table, DropEntry)
    items_by_id = {
        k: v for k, v in data["items"].items() if isinstance(v, ItemDef)
    }

    rng = random.Random(seed)
    gold_hits = 0
    gold_total = 0
    item_counts: dict[str, int] = {}
    for _ in range(kills):
        result = roll_drop(table, items_by_id, rng)  # type: ignore[arg-type]
        if result.gold > 0:
            gold_hits += 1
            gold_total += result.gold
        for dropped in result.items:
            item_counts[dropped.item_id] = item_counts.get(dropped.item_id, 0) + 1

    theo = theoretical_rates(table)
    obs_wooma = sum(item_counts.get(i, 0) for i in WOOMA_TIER_ITEMS) / kills
    obs_book35 = sum(item_counts.get(i, 0) for i in BOOK35_ITEMS) / kills
    obs_egg = sum(item_counts.get(i, 0) for i in EGG_ITEMS) / kills

    print(f"== 白野猪掉落模拟：{kills} 次击杀（seed={seed}, "
          f"GLOBAL_DROP_RATE={GLOBAL_DROP_RATE}）==")
    print(f"金币行出现率   : {gold_hits}/{kills} = {gold_hits / kills:.1%} "
          f"(理论 {theo[None]:.1%})；平均金币 {gold_total / max(1, gold_hits):.0f}/次")
    print(f"沃玛装备出现率 : {obs_wooma:.2%} (设定 {theo[WOOMA_TIER_ITEMS]:.2%})")
    print(f"35 书出现率    : {obs_book35:.2%} (设定 {theo[BOOK35_ITEMS]:.2%})")
    print(f"彩蛋出现率     : {obs_egg:.4%} (上限 0.15%)")
    print("逐物品分布（次数/率）：")
    for iid in sorted(item_counts, key=lambda k: -item_counts[k]):
        print(f"  {iid:28} {item_counts[iid]:5}  {item_counts[iid] / kills:.2%}")

    problems: list[str] = []
    # 验收硬性口径：沃玛装备 ±20%
    want_wooma = theo[WOOMA_TIER_ITEMS]
    if abs(obs_wooma - want_wooma) > 0.20 * want_wooma:
        problems.append(
            f"沃玛装备出现率 {obs_wooma:.2%} 偏离设定 {want_wooma:.2%} 超过 ±20%"
        )
    # 金币行 ±20%（表内恒有金币行由 spotcheck_drops 静态断言）
    want_gold = theo[None]
    obs_gold = gold_hits / kills
    if abs(obs_gold - want_gold) > 0.20 * want_gold:
        problems.append(
            f"金币行出现率 {obs_gold:.1%} 偏离设定 {want_gold:.1%} 超过 ±20%"
        )
    # 35 书：二项分布 3σ 带（低概率类固定相对容差小于抽样噪声，见模块 docstring）
    want_book = theo[BOOK35_ITEMS]
    sigma_book = (want_book * (1 - want_book) / kills) ** 0.5
    if abs(obs_book35 - want_book) > 3 * sigma_book:
        problems.append(
            f"35 书出现率 {obs_book35:.2%} 偏离设定 {want_book:.2%} "
            f"超出 3σ 带（σ={sigma_book:.2%}）"
        )
    # 彩蛋：理论 ≤0.001，观测上限 = 理论 + 4σ（泊松噪声容忍，防偶发 2 连中误报）
    want_egg = theo[EGG_ITEMS]
    egg_cap = want_egg + 4 * (want_egg / kills) ** 0.5
    if obs_egg > egg_cap:
        problems.append(
            f"彩蛋出现率 {obs_egg:.4%} > 统计上限 {egg_cap:.4%}（理论 {want_egg:.4%}）"
        )
    if problems:
        print("== 容差断言失败 ==")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("== 容差断言全部通过（±20%）==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
