# 战斗公式出处与置信度矩阵（docs/combat-formulas.md）

> **还原策略**（docs/MVP/README.md §3.4）：176 精确公式官方从未公开。本文以私服社区
> 公认近似公式起步，逐条记录出处与置信度（高/中/低）；目标是"手感对"，**不声称逐点
> 还原官方**。所有系数的唯一来源是 `backend/app/combat/balance.py`（grep 防回归见
> `backend/tests/test_balance.py`）；校准入口 = todo 37 挂机矩阵。
>
> **置信度图例**：高 = 社区公认事实或上游契约锚点；中 = 社区近似但存在分歧；
> 低 = 本项目自创近似，待校准。

---

## 0. 系数总表

| 系数 | 值 | 出处 | 置信度 |
| --- | --- | --- | --- |
| `MIN_HIT_RATE` | 0.05 | thelegendofmir.com 截断惯例 | 中 |
| `MAX_HIT_RATE` | 0.95 | 同上 | 中 |
| `LUCK_MAX` | 9 | 社区公认"幸运9套刀刀上限" | 高 |
| `LUCK_HIT_BIAS` | 0.1 | 自创线性内插 | 低 |
| `CURSE_HIT_BIAS` | 0.1 | 自创对称建模 | 低 |
| `DEF_DAMAGE_REDUCTION_FACTOR` | 0.05 | 自创百分比近似（真 176 为线性扣减） | 低 |
| `SKILL_MULTIPLIER_BASE` | 1.0 | 自创恒等占位（技能倍率在 gamedata/skills） | 高 |
| `MIN_DAMAGE` | 1 | docs/MVP/README.md §5.3 | 高 |
| `ATTACK_INTERVAL_TICKS` | 2 | 自创基准拍点 | 中 |
| `GRACE_PERIOD_SECONDS` | 60 | 计划 W2 规格（与 config.SSE_GRACE_PERIOD_SECONDS 双写同步） | 高 |
| `POTION_HP_PCT` | 0.30 | 自创默认（策略本体 todo 12） | 低 |
| `POTION_MP_PCT` | 0.20 | 自创默认 | 低 |
| `BAG_CAPACITY` | 40 | models 默认交叉校验 | 高 |
| `GLOBAL_DROP_RATE` | 1.0 | 自创中性默认 | 高 |
| `BIRTH_MAP_ID` | "yin-xing-cun" | app/core/config.py 重导出 | 高 |

---

## 1. 命中公式

**公式**（离散均匀读法的 2:1 比率式）：

```
roll = randint(0, defender_agility)        # 含两端，共 agility+1 个等概率取值
命中  ⟺  roll < attacker_accuracy
P_raw  = min(accuracy, agility + 1) / (agility + 1)
P_hit  = clamp(P_raw, MIN_HIT_RATE, MAX_HIT_RATE)   # 截断 5%–95%
```

- **出处**: <https://thelegendofmir.com>（准确 vs 敏捷的比率对抗为社区公认式，
  私服模拟器普遍实现为上述离散均匀判定）——比率形式**置信度：高**；
  截断上下限 5%/95% 的具体数值是社区惯例（保证永不绝对 miss/hit）——**置信度：中**。
- **锚点**: docs/MVP/README.md §5.1（命中率 = f(准确, 敏捷)，上下限截断）。

**手算推导样例 1**（1 级战士 vs 鸡，accuracy=10, agility=2）：

```
P_raw = min(10, 2+1) / (2+1) = 3/3 = 100%   → 触上限截断 → P_hit = 95%
```

**手算推导样例 2**（accuracy=5 vs agility=8，不触截断）：

```
P_raw = min(5, 8+1) / (8+1) = 5/9 ≈ 55.6%   → 5%/95% 区间内，P_hit = 55.6%
```

**TDD 表驱动期望**（todo 9 hit.py 固定 seed 用例）：

| accuracy | agility | P_raw | P_hit（截断后） |
| --- | --- | --- | --- |
| 10 | 2 | 3/3 = 100% | 0.95 |
| 5 | 8 | 5/9 ≈ 55.56% | 5/9 |
| 0 | 2 | 0/3 = 0% | 0.05 |
| 100 | 0 | 1/1 = 100% | 0.95 |

---

## 2. 伤害公式

**结算管线**（顺序固定，todo 10 damage.py 按此实现）：

```
① base  = randint(attack_min, attack_max)；u = (base − min) / (max − min) ∈ [0,1]
② 幸运/诅咒修正：u' = clamp(u + luck×LUCK_HIT_BIAS − curse×CURSE_HIT_BIAS, 0, 1)
   特例：luck ≥ LUCK_MAX(9) → u' = 1（刀刀上限硬规则）
   adjusted = attack_min + round_half_up(u' × (max − min))
③ 技能倍率：dmg = adjusted × skill_multiplier（平砍 = SKILL_MULTIPLIER_BASE = 1.0；
   各技能倍率定义处 = gamedata/skills/*.json，todo 11 fixture / todo 20 录入）
④ 减防御/魔御：dmg −= defender_stat × DEF_DAMAGE_REDUCTION_FACTOR（见 §3）
⑤ 取整 + 钳制：dmg = max(round_half_up(dmg), MIN_DAMAGE)   # 至少 1 点
```

- **出处**: 攻击区间 roll + 幸运趋上限/诅咒趋下限 + 钳制 ≥1 为社区共识骨架
  （docs/MVP/README.md §5.2–5.3）——骨架**置信度：高**；
  幸运中间档的线性偏移与百分比减防为**本项目自创近似——置信度：低**
  （真实 176 的减防是线性直接扣减 `damage − defense`，两种模型对比见 §3）。
- **不引入暴击**：176 无暴击概念，幸运即方差修正（计划 todo 10 红线）。

**手算推导样例**（1 级战 vs 鸡：attack [4,8]，luck=0，curse=0，平砍，defense=2，
本次 roll=6）：

```
① u = (6−4)/(8−4) = 0.5
② u' = 0.5 → adjusted = 4 + round(0.5×4) = 4 + 2 = 6
③ 6 × 1.0 = 6
④ 6 − 2×0.05 = 6 − 0.1 = 5.9
⑤ round_half_up(5.9) = 6 → ≥1 成立 → 本次伤害 = 6
期望（取整前）：E[base] = (4+8)/2 = 6 → E[dmg] ≈ 6 − 0.1 = 5.9
```

**防御碾压手算**（roll=4，defense=80）：

```
④ 4 − 80×0.05 = 4 − 4 = 0   ⑤ round(0)=0 → 钳制 → 伤害 = 1
```

**TDD 表驱动期望**：

| attack 区间 | roll | luck/curse | skill | defense | 期望伤害 |
| --- | --- | --- | --- | --- | --- |
| [4,8] | 6 | 0/0 | 1.0 | 2 | 6 |
| [4,8] | 4 | 0/0 | 1.0 | 80 | 1（钳制） |
| [4,8] | 任意 | luck≥9 | 1.0 | 0 | 8（刀刀上限） |

---

## 3. 物理 / 魔法 / 道术减伤

**三通道映射**（同一减伤系数 `DEF_DAMAGE_REDUCTION_FACTOR`）：

| 通道 | 攻击方属性 | 防御方属性 | 减免量 |
| --- | --- | --- | --- |
| 物理 | 攻击区间 | DEF（防御） | DEF × 0.05 |
| 魔法 | 魔法区间 | MAC（魔御） | MAC × 0.05 |
| 道术 | 道术区间 | DEF（按物理防御计） | DEF × 0.05 |

- **出处**: 三通道划分 = docs/MVP/README.md §3.4 damage.py 行（物理走攻/防、魔法走
  魔/魔御、道术走道）——**置信度：高**；道术防御方按 DEF 计为社区近似（道术是否
  无视魔御各源说法不一）——**置信度：低**；
  百分比减免模型 `stat × DEF_DAMAGE_REDUCTION_FACTOR` 为**本项目自创**——**置信度：
  低**。真实 176 为线性直接扣减（`damage − defense`），防御价值远高于本模型；
  此处选百分比是为让 todo 37 校准有单一旋钮，若校准失配可整体切回线性模型
  （届时只需改 damage.py 一处 + 本文档）。

**手算推导样例**（法师魔法 [10,20]，roll=12，目标骷髅 MAC=15）：

```
减免量 = 15 × 0.05 = 0.75
12 − 0.75 = 11.25 → round_half_up → 11 → ≥1 成立 → 本次魔法伤害 = 11
```

**TDD 表驱动期望**：

| 通道 | roll | 防御方 stat | 减免量 | 最终伤害 |
| --- | --- | --- | --- | --- |
| 物理 | 6 | DEF=2 | 0.10 | 6 |
| 魔法 | 12 | MAC=15 | 0.75 | 11 |
| 道术 | 9 | DEF=40 | 2.00 | 7 |

---

## 4. 幸运与诅咒（方差修正）

**机制**：幸运/诅咒不改变攻击区间，只改变 roll 在区间内的**位置分布**——
幸运把位置推向区间上限（方差增大→堆顶），诅咒推向下限；幸运 ≥ `LUCK_MAX`(9)
时触发硬规则"刀刀上限"（方差归零）。

- **出处**: "幸运 9 套 = 刀刀上限"、"祝福油武器幸运上限 +7"、"诅咒武器趋下限"
  为社区公认事实（<https://thelegendofmir.com>、docs/MVP/README.md §5.2）——
  **置信度：高**；幸运 1–8 档的线性偏移（每点 ±`LUCK_HIT_BIAS`=±10% 位置）与
  诅咒的对称建模为**本项目自创**——**置信度：低**。

**手算推导样例**（attack [4,8]，即 range=4；u ~ U[0,1] 连续化期望）：

```
无修正：E[u'] = 0.5            → E[adjusted] = 4 + 4×0.5  = 6.00

luck=3：u' = clamp(u+0.3, 0, 1)，u > 0.7 段被截到 1
  E[u'] = ∫₀^0.7(u+0.3)du + ∫₀.₇^1 1 du
        = (0.245 + 0.21) + 0.30 = 0.755
  → E[adjusted] = 4 + 4×0.755 = 7.02   （高于无幸运的 6.00）

curse=3：u' = clamp(u−0.3, 0, 1)，u < 0.3 段被截到 0
  E[u'] = ∫₀.₃^1(u−0.3)du = [u²/2 − 0.3u]₀.₃^1 = 0.20 − (−0.045) = 0.245
  → E[adjusted] = 4 + 4×0.245 = 4.98   （低于无修正的 6.00）

对称性 sanity check：0.755 + 0.245 = 1.000 ✓（幸运/诅咒关于 0.5 镜像）

luck=9：硬规则 u' = 1 → adjusted 恒 = 8，方差 = 0（刀刀上限）
```

**TDD 表驱动期望**（todo 10 "幸运 9 套刀刀上限 / 诅咒刀刀下限"用例）：

| luck | curse | attack [4,8] 任 roll | adjusted |
| --- | --- | --- | --- |
| 9 | 0 | 任意 | 8 |
| 0 | 0 | 4 / 6 / 8 | 4 / 6 / 8（不变） |
| 0 | 99 | 任意 | 4（贴下限，无硬规则） |

---

## 5. 经验曲线（占位——待 todo 17 回填）

- **出处**: docs/MVP/README.md §5.4（176 经典经验曲线，指数增长，1→40 级总量按
  官方表录入 `gamedata/levels.json`）；第二独立源比对（OpenMir2 Exps.ini 等）
  由 **todo 17 双源比对**产出差异报告后回填本节——**当前全部数值为占位示例，
  置信度：待定（暂标低）**。

**占位手算推导样例**（⚠️ 数值全部非最终，仅演示推导格式）：

```
假设（占位）：1→2 级需 100 exp；鸡 exp=5/只；鸡 HP=25
1 级战期望伤害 5.9（§2 推导）→ 击杀一只需 ceil(25/5.9) = 5 次出手
命中 95%（§1）→ 期望出手次数 ≈ 5 / 0.95 ≈ 5.3 次
攻速每 2 tick 出手（tick=1Hz）→ 单只耗时 ≈ 10.6s
升到 2 级需 100/5 = 20 只 → ≈ 212s ≈ 3.5min
```

**回填清单（todo 17）**：levels.json 全表双源 diff 报告链接、每级累计经验、
新手区（BIRTH_MAP_ID = yin-xing-cun 周边怪物）1–7 级 ≤30min 的外推验证
（对接 todo 37 校准矩阵）。

---

## 附 A：两处同步改清单（改一处必改另一处，测试强制）

| balance.py | 另一处 | 交叉断言 |
| --- | --- | --- |
| `BAG_CAPACITY = 40` | `app/models/character.py` bag_capacity 列 default/server_default | test_balance 读 models 源码 |
| `GRACE_PERIOD_SECONDS = 60` | `app/core/config.py` `SSE_GRACE_PERIOD_SECONDS` | test_balance 比对 settings 实例 |

重复声明是有意的：combat 模块不 import settings 实例，收敛依赖面防循环。

## 附 B：系数纪律

1. combat/ 其他模块出现 `0.x` 字面量 = 测试失败（唯一来源 balance.py）。
2. balance.py 不是 Settings：无环境变量覆盖，调参只经 todo 37 流程改代码。
3. 不在 balance.py 写死具体怪物/物品数值（属 gamedata/，todo 20 录入）。
4. 技能倍率定义处：gamedata/skills/\*.json（todo 11 测试 fixture 手抄
   docs/MVP/README.md §4 表；todo 20 全量录入）；balance.py 只留恒等占位。
