# 数据覆盖度报告（docs/data-coverage.md）

各类 gamedata 的双源/单源录入情况与差异标注。







## 0. 经验表双源比对差异清单（todo 17，2026-08-25）

- 主源：/home/aymazon/WorkSpace/mir-176-h5/backend/scripts/cache/Exps.ini
- 对照源：built-in XUEXILEI_CLASSIC_1_62
- 判定阈值：|差异| > 5.0%；完整 40 行比对表见 `uv run python scripts/compare_exp_sources.py` 输出。
- 结论：两源仅 1–3 级完全一致；4–9 级 GeeM2 低于经典表 25–42%；  10 级起 GeeM2 反超并持续放大（40 级 ≈ 经典表的 6.4 倍）。
- 取数：**levels.json 以主源 GeeM2 为准**；差异 >5% 的等级置信度标 **中**  （主源为真实服务端配置但属私服发行版，非官方公开数值）。
- 归属：全部超阈等级列入 **M5 校准清单**（todo 37 挂机矩阵以实际升级节奏回验）。

| level | 主源 exp | 对照 exp | 差异% | 置信度 | M5 校准 |
| --- | --- | --- | --- | --- | --- |
| 4 | 400 | 500 | +25.0% | 中 | ✅ |
| 5 | 600 | 800 | +33.3% | 中 | ✅ |
| 6 | 900 | 1,200 | +33.3% | 中 | ✅ |
| 7 | 1,200 | 1,700 | +41.7% | 中 | ✅ |
| 8 | 1,700 | 2,400 | +41.2% | 中 | ✅ |
| 9 | 2,500 | 3,300 | +32.0% | 中 | ✅ |
| 10 | 6,000 | 4,500 | -25.0% | 中 | ✅ |
| 11 | 8,000 | 6,000 | -25.0% | 中 | ✅ |
| 12 | 10,000 | 8,000 | -20.0% | 中 | ✅ |
| 13 | 15,000 | 10,500 | -30.0% | 中 | ✅ |
| 14 | 30,000 | 13,600 | -54.7% | 中 | ✅ |
| 15 | 40,000 | 17,500 | -56.2% | 中 | ✅ |
| 16 | 50,000 | 22,300 | -55.4% | 中 | ✅ |
| 17 | 70,000 | 28,200 | -59.7% | 中 | ✅ |
| 18 | 100,000 | 35,400 | -64.6% | 中 | ✅ |
| 19 | 120,000 | 44,200 | -63.2% | 中 | ✅ |
| 20 | 140,000 | 54,800 | -60.9% | 中 | ✅ |
| 21 | 250,000 | 67,600 | -73.0% | 中 | ✅ |
| 22 | 300,000 | 82,900 | -72.4% | 中 | ✅ |
| 23 | 350,000 | 101,200 | -71.1% | 中 | ✅ |
| 24 | 400,000 | 123,000 | -69.2% | 中 | ✅ |
| 25 | 500,000 | 148,900 | -70.2% | 中 | ✅ |
| 26 | 700,000 | 179,500 | -74.4% | 中 | ✅ |
| 27 | 1,000,000 | 215,700 | -78.4% | 中 | ✅ |
| 28 | 1,400,000 | 258,300 | -81.5% | 中 | ✅ |
| 29 | 1,800,000 | 308,600 | -82.9% | 中 | ✅ |
| 30 | 2,000,000 | 368,000 | -81.6% | 中 | ✅ |
| 31 | 2,400,000 | 438,000 | -81.8% | 中 | ✅ |
| 32 | 2,800,000 | 520,000 | -81.4% | 中 | ✅ |
| 33 | 3,200,000 | 616,000 | -80.8% | 中 | ✅ |
| 34 | 3,600,000 | 728,000 | -79.8% | 中 | ✅ |
| 35 | 4,000,000 | 858,000 | -78.5% | 中 | ✅ |
| 36 | 4,800,000 | 1,008,000 | -79.0% | 中 | ✅ |
| 37 | 5,600,000 | 1,182,000 | -78.9% | 中 | ✅ |
| 38 | 8,200,000 | 1,382,000 | -83.1% | 中 | ✅ |
| 39 | 9,000,000 | 1,612,000 | -82.1% | 中 | ✅ |
| 40 | 12,000,000 | 1,876,000 | -84.4% | 中 | ✅ |

共 37 级超阈。低等级锚点（1–3 级两源一致 =100/200/300）置信度**高**。





## 1. 怪物全量录入：双源出处与字段映射（todo 18，2026-08-25）

- **主源**：Crystal `Server.MirDB`（Suprcode/Crystal.Database 仓库 cjlaaa/ 目录，GeeM2 同源发行版；自定义二进制，按 Crystal MonsterInfo(BinaryReader) LoadVersion∈[89,115) 分支解析，连续记录链扫描定位，共 449 只）。
- **第二源**：OpenMir2 `sql/mir2_data.sql`（github.com/mirbeta/OpenMir2，MySQL dump monsters 表，共 705 只）。
- 两源原始文件均不入库（backend/.gitignore）；本地缓存于 `backend/scripts/cache/`，解析器 `scripts/mir_sources.py`，构建器 `scripts/build_monsters.py`，本抽检脚本可重跑复核。

### 字段映射约定（转换器 build_monsters.py 实现即文档）

| JSON 字段 | Crystal 列 | OpenMir2 列 | 说明 |
| --- | --- | --- | --- |
| level | Level(u16) | Lvl | 任务 22 等级带 lint 的强制依赖 |
| hp | Stats[HP=12] | HP | |
| attack | MaxDC(=5) | DCMAX | 单值字段取攻击区间**上限** |
| defense | MaxAC(=1) | AC | 物防上限 |
| magic_defense | MaxMAC(=3) | MAC | 魔御上限 |
| accuracy | Accuracy(=10) | HIT | |
| agility | Agility(=11) | SPEED | |
| exp | Experience(u32) | Exp | |
| attack_speed | AttackSpeed(ms) | ATTACK_SPD(ms) | ÷1000 四舍五入钳 [1,6] tick |
| move_speed | MoveSpeed(ms) | WALK_SPD(ms) | 同上 |
| undead | Undead(bool) | Undead(int) | **两源冲突取 OpenMir2**：主源该发行版全 False（骷髅/僵尸亦然），第二源旗标完整（骷髅=1 等），以语义完整者为准 |
| boss / respawn_seconds | —（两源均无） | — | 项目自管：BOSS=3600s（沃玛教主/祖玛教主按任务规格 60min），精英 1800s，普通怪 90–150s |

### 录入策略与 TODO 清单

- 直接录入：主源同名 42 只 + 第二源同名/别名补位 6 只。其中极品祖玛卫士取 OM2『祖玛卫士00』HP=1400（社区公认 1400 大锤；主源同名变体 HP=4450 为单服自定义，弃用）。
- 别名映射：毒蛇←主源赤色毒蛇；沙漠巨型蠕虫←两源同名巨型蠕虫；僵尸←OM2 僵尸1（L25 HP155，主源僵尸族同值）。
- **同族派生 11 只（TODO，M5 校准清单）**：蛤蟆斗士、毒蛇斗士、巨蛇、双头蛇、双头蛇魔、巨型蝎子、巨型蜈蚣、黑色蜈蚣、僵尸斗士、尸霸、闪电僵尸——两源均无同名，按同族锚点升档派生（公式见 scripts/build_monsters.py ROSTER 注释）。
- 文档在列但两源皆缺：黑暗战士（沃玛寺庙行）未录入，待补源。
- 合计 60 只（≥45 达标）。
## 2. 物品全量录入：双源出处、字段裁决与映射样本（todo 19，2026-08-25）

- **主源**：Crystal `Server.MirDB` ItemInfo（Suprcode/Crystal.Database 仓库 cjlaaa/ 目录；按 Crystal `Shared/Data/ItemData.cs` ItemInfo(BinaryReader) LoadVersion>84 分支解析，连续记录链扫描（链长≥10）定位，共 1344 条）。
- **第二源**：OpenMir2 `sql/mir2_data.sql` stditems 表（github.com/mirbeta/OpenMir2，MySQL dump，共 1000 行；Need 列语义经经典官方数值交叉验证：0=等级/1=攻击力/2=魔法力/3=道术——战神盔甲需攻46、恶魔长袍需魔28、幽灵战衣需道27 三锚点确认）。
- 两源原始文件均不入库（backend/.gitignore）；本地缓存于 `backend/scripts/cache/`，解析器 `scripts/mir_sources.py`，构建器 `scripts/build_items.py`，本抽检脚本可重跑复核。

### 字段映射约定（转换器 build_items.py 实现即文档）

| JSON 字段 | Crystal 列 | OpenMir2 列 | 说明 |
| --- | --- | --- | --- |
| kind | Type 枚举(1=武器/2=甲/4=盔/5=链/6=镯/7=戒/9=带/10=靴/13=药/16=料/17=卷/20=书) | StdMode(0/3/4/5/6/10/11/15/19-26/31/40-44/52/54) | 男女版同值录通用款 |
| need_level / need_strength / need_magic / need_taoism | RequiredType+RequiredAmount（Level=0/MaxDC=3/MaxMC=4/MaxSC=5） | Need+NeedLevel（0/1/2/3） | 两家编码不同，禁止混用（build_items 分表映射） |
| weight | Weight(u8) | Weight | |
| max_durability | Durability(u16) | DuraMax | 仅装备类；消耗品强制 0（schema 约定 0=无耐久概念） |
| stats.* | Stats 块键 0-9→ac/mac/dc/mc/sc_min/max，10/11→准确/敏捷，12/13→药品恢复量或装备 hp/mp_add | Ac/AcMax…Sc/ScMax 五对；StdMode=0 时 Ac/Mac=恢复量 | 区间下限为 0 不落键 |
| sell_price / buy_price | Price | Price | buy←Price，sell=Price//2（经典半价回收惯例） |
| stackable / prefix_max | StackSize / — | OverlapItem / — | 项目自管：消耗品可堆叠；装备 prefix_max=1（词缀档位），消耗品 0 |

### 录入策略与字段裁决（防单服自定义变体污染）

- **装备类默认取第二源**（82 条含别名）：其 Need 语义与数值和经典官方一致（裁决之杖 0-30、骨玉权杖魔 2-6、战神盔甲需攻46 等）；主源 cjlaaa 存在单服自定义变体——乌木剑 Lvl4、中型盔甲全属性加成、裁决之杖 MaxDC=33、龙纹剑 Lvl40、高阶甲属性门槛简化为等级等，均弃用并留档于下方三方对照表。
- **消耗品/书籍/素材类默认取主源**（cr/cr_map 共 40 条）：金疮药命名族恢复量梯度完整；书籍习得等级在 RequiredAmount 完整（第二源把习得等级错存 DuraMax 列）。
- **例外条目（逐条裁决）**：白色虎齿项链走主源（第二源误标攻击门槛）；泰坦戒指价格取主源 20000（第二源 Price=15 为源数据笔误）；超级金创药/超级魔法药走主源（第二源同名行为药包包头，无恢复量）。
- **待定项裁决（任务规格）**：无极棍按社区主流 **need_level=28** 录入（主源 Lvl30 / 第二源道术门槛 25 两源不一；置信度中，见 docs/combat-formulas.md §6）。
- 同族派生 1 条：红色药水（两源均无同名；按金疮药族 HP 专精变体派生，公式见 ROSTER todo）。task13 遗留 2 件无源条目（布帽：两源均无同名头盔；肉：第二源食物行 Price=2000 与既有数值明显不符）保持原值不动。
- 合计 127 件（≥120 达标）。

### TODO 清单（M5 校准候选）

- `dagger`: task13 已有 id；主源 Lvl2/第二源 Lvl1，取后者
- `wooden_sword`: task13 已有 id；主源 dc2-4/第二源 dc2-5，取后者（经典 2-5）
- `ebony_sword`: 主源 Lvl4 为单服上调；第二源 Lvl1 与 §7.1 锚点一致
- `lianyu_blade`: 攻上限主源 27/第二源 25，取经典 0-25；差异进双源报告
- `moonwell_blade`: 主源该条为 DC:30 属性门槛变体；第二源 Lvl28 与 §7.1 锚点一致
- `verdict_staff`: 主源 MaxDC=33 为单服上调；第二源 0-30 与经典公认一致
- `bone_jade_staff`: 主源 Lvl30/mc1-7 为单服变体；第二源 Lvl35/魔2-6 与 §7.1 锚点及经典一致
- `xueyin_sword`: 祖玛级稀有（§7.1），商店不上架由掉落消费
- `wuji_cudgel`: 待定项裁决：主源 Lvl30/第二源道术门槛25 两源不一，按任务规格取社区主流 28 级（置信度中，docs/combat-formulas.md §6）；第二源属性门槛 25 弃用
- `dragonvein_sword`: 主源 Lvl40/dc8-22 为单服上调；第二源 Lvl35/攻8-20/道3-6 与 §7.1 锚点一致
- `dragonslayer_blade`: 主源 Lvl40/攻5-40 为单服上调；第二源 Lvl34/攻5-35 接近经典，M5 校准
- `soul_eater_staff`: 主源缺名，第二源补位
- `fate_blade`: 战士 35 级任务武器（§9），主源缺名，第二源补位；获取途径归 todo 20 任务
- `cloth_robe`: task13 已有 id；男女版同值录通用款
- `medium_armor`: 主源含全职业上限加成(ac4-8/mac2-4/dc/mc/sc+2)为单服魔改；第二源 防3-5/魔御1-2 与经典一致
- `war_god_armor`: 经典属性门槛「需要攻击力46」：第二源 Need=1 完整保留，主源简化为 Lvl33 弃用
- `demon_robe`: 经典属性门槛「需要魔法力28」，同战神盔甲裁决
- `ghost_armor`: 经典属性门槛「需要道术27」，同战神盔甲裁决
- `sky_demon_armor`: 40 级新衣服（1.76 重装，§7.2）；主源缺名，第二源补位；BOSS 掉落归 todo 21
- `archmage_cloak`: 40 级新衣服；主源缺名，第二源补位
- `heavenly_taoist_robe`: 40 级新衣服；主源缺名，第二源补位
- `skeleton_helmet`: 经典属性门槛「需要攻击力30」
- `black_iron_helmet`: 战士高防（§7.4）；经典属性门槛「需要攻击力46」
- `dragon_ring`: 经典属性门槛「需要攻击力37」
- `ruby_ring`: 经典属性门槛「需要魔法力17」
- `life_necklace`: 经典属性门槛「需要魔法力25」
- `sibeier_bracelet`: 主源缺名，第二源补位
- `platinum_ring`: 经典属性门槛「需要道术17」
- `tianzhu_necklace`: 经典属性门槛「需要道术22」
- `mind_bracelet`: 经典属性门槛「需要道术24」
- `power_ring`: 经典属性门槛「需要攻击力46」
- `green_necklace`: 经典属性门槛「需要攻击力37」
- `knight_bracelet`: 经典属性门槛「需要攻击力39」
- `purple_conch_ring`: 经典属性门槛「需要魔法力24」
- `demon_bell`: 经典属性门槛「需要魔法力27」
- `dragonhand_bracelet`: 经典属性门槛「需要魔法力28」
- `titan_ring`: 经典属性门槛「需要道术24」；第二源 Price=15 为源数据笔误，价格取主源 20000
- `soul_necklace`: 经典属性门槛「需要道术23」
- `three_eye_bracelet`: 经典属性门槛「需要道术22」
- `lantern_necklace`: 幸运项链族（幸运+魔法躲避在主源 Stats Luck/MagicResist 键，本库未启用）
- `white_tiger_tooth_necklace`: 例外走主源：主源道术门槛11 与经典一致；第二源误标攻击门槛(Need=1)
- `paralysis_ring`: 彩蛋戒指（§7.3）：只录数值，极低概率掉落归 todo 21
- `protection_ring`: 彩蛋戒指，同麻痹戒指
- `resurrection_ring`: 彩蛋戒指，同麻痹戒指
- `potion_hp_small`: task13 已有 id，主源回填
- `potion_mp_small`: task13 已有 id，主源回填
- `sun_water`: task13 已有 id，主源回填（瞬回 HP/MP，§8 保命用）
- `gray_powder`: 施毒辅助材料（§8）；道士毒符已按 MVP 直接施放，本道具仅存档；主源缺名，第二源补位
- `yellow_powder`: 同灰色药粉
- `red_potion`: 两源均无同名物品；按金疮药族 HP 专精变体派生：hp_restore=150（介于大量110与超级210），价格 300 内插。M5 校准清单
- `super_potion_hp`: 第二源同名行为药包包头(StdMode=31)无恢复量数值，故取主源
- `super_potion_mp`: 同超级金创药
- `return_scroll`: 新增道具（§1.2/§8）：杂货店售卖，使用效果归 todo 27 传送系统
- `blessing_oil`: 武器幸运+1（§8/§5.2）；使用效果归 todo 23 装备系统
- `spider_fang`: 第二源为『毒蜘蛛牙齿』别名，取主源原名
- `wooma_horn`: 沃玛教主掉落任务信物（§6），创建行会道具本作不用，仅存档
- `book_basic_sword`: task13 已有 id，主源回填
- `book_fireball`: task13 已有 id，主源回填
- `book_healing`: task13 已有 id，主源回填
- `book_hell_lightning`: MVP §4 法师 30 级技能配套书（任务清单外补充）
- `book_holy_word`: MVP §4 法师 32 级技能配套书（任务清单外补充）
- `book_meteor_shower`: 1.8+ 技能（§4 排除清单）：书道具按任务规格存档，技能本体不进 MVP
- `book_clone`: 1.8+ 技能：书道具按任务规格存档，技能本体不进 MVP

### 双源三方对照抽检（门槛 + 攻上限；≥15 件达标）

| 物品 | 门槛(本方) | 门槛(Crystal) | 门槛(OM2) | 攻上限 本方/Cr/OM2 |
| --- | --- | --- | --- | --- |
| 木剑 | Lv1 | Lv1 | Lv1 | 5/4/5 |
| 乌木剑 | Lv1 | Lv4 | Lv1 | 8/8/8 |
| 青铜剑 | Lv5 | Lv5 | Lv5 | 7/7/7 |
| 铁剑 | Lv10 | Lv10 | Lv10 | 9/9/9 |
| 八荒 | Lv15 | Lv15 | Lv15 | 12/12/12 |
| 凌风 | Lv19 | Lv19 | Lv19 | 12/12/12 |
| 斩马刀 | Lv20 | Lv20 | Lv20 | 15/15/15 |
| 修罗 | Lv22 | Lv22 | Lv22 | 20/20/20 |
| 凝霜 | Lv25 | Lv25 | Lv25 | 13/13/13 |
| 炼狱 | Lv26 | Lv26 | Lv26 | 25/27/25 |
| 井中月 | Lv28 | DC30 | Lv28 | 22/22/22 |
| 海魂 | Lv15 | Lv15 | Lv15 | 10/10/10 |
| 偃月 | Lv20 | Lv20 | Lv20 | 10/10/10 |
| 魔杖 | Lv26 | Lv26 | Lv26 | 9/9/9 |
| 降魔 | Lv20 | Lv20 | Lv20 | 11/11/11 |
| 银蛇 | Lv26 | Lv26 | Lv26 | 14/14/14 |
| 重盔甲 | Lv22 | 缺名 | Lv22 | 0/-1/0 |
| 魔法长袍 | Lv22 | 缺名 | Lv22 | 0/-1/0 |
| 灵魂战衣 | Lv22 | 缺名 | Lv22 | 0/-1/0 |
| 龙之戒指 | DC37 | DC37 | DC37 | 5/5/5 |
| 力量戒指 | DC46 | DC46 | DC46 | 6/6/6 |
| 泰坦戒指 | SC24 | 缺名 | SC24 | 0/-1/0 |

共比对 22 件（≥15 达标）；「本方=OM2 ≠ Crystal」列即主源单服变体证据链。

### 转换器字段映射样本（5 件抽查：原始记录 → JSON）

#### 裁决之杖（verdict_staff.json）

```
OM2 raw     : Id=223 Name=裁决之杖 StdMode=6 Weight=80 DuraMax=32000 Dc=0-30 Mc=0-0 Sc=0-0 Need=0 NeedLevel=30 Price=50000
map         : kind←StdMode need_level←Need/NeedLevel(0) dc_min/max←Dc/DcMax mc_min/max←Mc/McMax sc_min/max←Sc/ScMax weight←Weight max_durability←DuraMax buy←Price sell←Price//2
```

| 字段 | 值 |
| --- | --- |
| id | verdict_staff |
| name | 裁决之杖 |
| kind | weapon |
| need_level | 30 |
| need_strength | 0 |
| need_magic | 0 |
| need_taoism | 0 |
| weight | 80 |
| stackable | False |
| max_durability | 32000 |
| sell_price | 25000 |
| buy_price | 50000 |
| stats | {'dc_max': 30} |
| prefix_max | 1 |

#### 骨玉权杖（bone_jade_staff.json）

```
OM2 raw     : Id=234 Name=骨玉权杖 StdMode=6 Weight=20 DuraMax=18000 Dc=6-12 Mc=2-6 Sc=0-0 Need=0 NeedLevel=35 Price=50000
map         : kind←StdMode need_level←Need/NeedLevel(0) dc_min/max←Dc/DcMax mc_min/max←Mc/McMax sc_min/max←Sc/ScMax weight←Weight max_durability←DuraMax buy←Price sell←Price//2
```

| 字段 | 值 |
| --- | --- |
| id | bone_jade_staff |
| name | 骨玉权杖 |
| kind | weapon |
| need_level | 35 |
| need_strength | 0 |
| need_magic | 0 |
| need_taoism | 0 |
| weight | 20 |
| stackable | False |
| max_durability | 18000 |
| sell_price | 25000 |
| buy_price | 50000 |
| stats | {'dc_min': 6, 'dc_max': 12, 'mc_min': 2, 'mc_max': 6} |
| prefix_max | 1 |

#### 龙纹剑（dragonvein_sword.json）

```
OM2 raw     : Id=230 Name=龙纹剑 StdMode=5 Weight=40 DuraMax=22000 Dc=8-20 Mc=0-0 Sc=3-6 Need=0 NeedLevel=35 Price=50000
map         : kind←StdMode need_level←Need/NeedLevel(0) dc_min/max←Dc/DcMax mc_min/max←Mc/McMax sc_min/max←Sc/ScMax weight←Weight max_durability←DuraMax buy←Price sell←Price//2
```

| 字段 | 值 |
| --- | --- |
| id | dragonvein_sword |
| name | 龙纹剑 |
| kind | weapon |
| need_level | 35 |
| need_strength | 0 |
| need_magic | 0 |
| need_taoism | 0 |
| weight | 40 |
| stackable | False |
| max_durability | 22000 |
| sell_price | 25000 |
| buy_price | 50000 |
| stats | {'dc_min': 8, 'dc_max': 20, 'sc_min': 3, 'sc_max': 6} |
| prefix_max | 1 |

#### 天魔神甲（sky_demon_armor.json）

```
OM2 raw     : Id=115 Name=天魔神甲 StdMode=10 Weight=62 DuraMax=35000 Dc=1-2 Mc=0-0 Sc=0-0 Need=0 NeedLevel=40 Price=35000
map         : kind←StdMode need_level←Need/NeedLevel(0) dc_min/max←Dc/DcMax mc_min/max←Mc/McMax sc_min/max←Sc/ScMax weight←Weight max_durability←DuraMax buy←Price sell←Price//2
```

| 字段 | 值 |
| --- | --- |
| id | sky_demon_armor |
| name | 天魔神甲 |
| kind | armor |
| need_level | 40 |
| need_strength | 0 |
| need_magic | 0 |
| need_taoism | 0 |
| weight | 62 |
| stackable | False |
| max_durability | 35000 |
| sell_price | 17500 |
| buy_price | 35000 |
| stats | {'ac_min': 5, 'ac_max': 12, 'mac_min': 4, 'mac_max': 7, 'dc_min': 1, 'dc_max': 2} |
| prefix_max | 1 |

#### 无极棍（wuji_cudgel.json）

```
OM2 raw     : Id=229 Name=无极棍 StdMode=5 Weight=15 DuraMax=25000 Dc=8-16 Mc=0-0 Sc=3-5 Need=3 NeedLevel=25 Price=40000
map         : kind←StdMode need_level←Need/NeedLevel(0) dc_min/max←Dc/DcMax mc_min/max←Mc/McMax sc_min/max←Sc/ScMax weight←Weight max_durability←DuraMax buy←Price sell←Price//2
```

| 字段 | 值 |
| --- | --- |
| id | wuji_cudgel |
| name | 无极棍 |
| kind | weapon |
| need_level | 28 |
| need_strength | 0 |
| need_magic | 0 |
| need_taoism | 0 |
| weight | 15 |
| stackable | False |
| max_durability | 25000 |
| sell_price | 20000 |
| buy_price | 40000 |
| stats | {'dc_min': 8, 'dc_max': 16, 'sc_min': 3, 'sc_max': 5} |
| prefix_max | 1 |


## 3. OpenMir2 第二源抽检差异报告（HP/经验/攻上限/物防）

两源为独立发行版，数值差异属预期；主源取数后差异 >30% 的字段列入 M5 校准观察。

| 怪物 | 等级(本方/OM2) | HP 本方/OM2(差%) | 经验 本方/OM2(差%) | 攻上限 本方/OM2(差%) | 物防 本方/OM2(差%) |
| --- | --- | --- | --- | --- | --- |
| 掷斧骷髅 (18/18) | 100/100(+0.0%) | 140/90(-35.7%) | 9/9(+0.0%) | 0/0(+0.0%) |
| 蝙蝠 (5/5) | 3/3(+0.0%) | 5/5(+0.0%) | 22/22(+0.0%) | 0/0(+0.0%) |
| 山洞蝙蝠 (16/20) | 80/25(-68.8%) | 65/25(-61.5%) | 12/6(-50.0%) | 3/2(-33.3%) |
| 洞蛆 (21/21) | 80/65(-18.8%) | 122/60(-50.8%) | 8/8(+0.0%) | 0/0(+0.0%) |
| 蜈蚣 (26/26) | 210/230(+9.5%) | 310/230(-25.8%) | 18/17(-5.6%) | 0/0(+0.0%) |
| 鸡 (5/5) | 5/5(+0.0%) | 9/5(-44.4%) | 1/1(+0.0%) | 0/0(+0.0%) |
| 尸王 (50/45) | 500/500(+0.0%) | 1200/800(-33.3%) | 38/36(-5.3%) | 3/3(+0.0%) |
| 鹿 (12/12) | 25/25(+0.0%) | 18/15(-16.7%) | 4/4(+0.0%) | 0/0(+0.0%) |
| 邪恶钳虫 (60/50) | 2000/1000(-50.0%) | 3600/1400(-61.1%) | 50/45(-10.0%) | 20/20(+0.0%) |
| 恶灵尸王 (33/35) | 350/500(+42.9%) | 940/420(-55.3%) | 26/38(+46.2%) | 6/3(-50.0%) |
| 火焰沃玛 (31/31) | 240/340(+41.7%) | 350/290(-17.1%) | 26/26(+0.0%) | 0/0(+0.0%) |
| 森林雪人 (16/16) | 36/36(+0.0%) | 38/30(-21.1%) | 10/10(+0.0%) | 2/2(+0.0%) |
| 多钩猫 (13/13) | 30/30(+0.0%) | 23/17(-26.1%) | 4/4(+0.0%) | 0/0(+0.0%) |
| 角蝇 (30/30) | 200/200(+0.0%) | 480/300(-37.5%) | 20/0(-100.0%) | 0/0(+0.0%) |
| 跳跳蜂 (26/26) | 180/210(+16.7%) | 275/210(-23.6%) | 18/18(+0.0%) | 3/3(+0.0%) |
| 食人花 (20/15) | 28/28(+0.0%) | 36/28(-22.2%) | 9/9(+0.0%) | 2/2(+0.0%) |
| 半兽人 (15/15) | 30/30(+0.0%) | 34/20(-41.2%) | 9/9(+0.0%) | 1/1(+0.0%) |
| 半兽勇士 (28/28) | 300/300(+0.0%) | 740/300(-59.5%) | 32/32(+0.0%) | 0/0(+0.0%) |
| 半兽战士 (22/22) | 100/100(+0.0%) | 130/90(-30.8%) | 12/12(+0.0%) | 3/3(+0.0%) |
| 毒蜘蛛 (16/16) | 65/42(-35.4%) | 108/42(-61.1%) | 9/9(+0.0%) | 2/2(+0.0%) |
| 钉耙猫 (13/13) | 32/32(+0.0%) | 27/18(-33.3%) | 4/4(+0.0%) | 0/0(+0.0%) |
| 红蛇 (17/17) | 60/50(-16.7%) | 70/50(-28.6%) | 12/12(+0.0%) | 0/0(+0.0%) |
| 稻草人 (10/10) | 25/25(+0.0%) | 15/12(-20.0%) | 2/2(+0.0%) | 0/0(+0.0%) |
| 蝎子 (18/18) | 45/45(+0.0%) | 85/45(-47.1%) | 16/16(+0.0%) | 2/2(+0.0%) |
| 蝎蛇 (35/35) | 300/330(+10.0%) | 550/360(-34.5%) | 28/28(+0.0%) | 5/5(+0.0%) |
| 骷髅 (18/18) | 90/90(+0.0%) | 140/85(-39.3%) | 10/10(+0.0%) | 0/0(+0.0%) |
| 骷髅精灵 (50/40) | 500/500(+0.0%) | 960/600(-37.5%) | 24/24(+0.0%) | 5/5(+0.0%) |
| 骷髅战士 (19/19) | 105/105(+0.0%) | 150/95(-36.7%) | 15/15(+0.0%) | 0/0(+0.0%) |
| 虎蛇 (18/18) | 60/50(-16.7%) | 98/50(-49.0%) | 11/11(+0.0%) | 0/0(+0.0%) |
| 蛤蟆 (12/12) | 20/20(+0.0%) | 20/15(-25.0%) | 5/5(+0.0%) | 0/0(+0.0%) |
| 触龙神 (99/50) | 3000/1000(-66.7%) | 6000/1000(-83.3%) | 90/35(-61.1%) | 20/0(-100.0%) |
| 楔蛾 (32/32) | 220/220(+0.0%) | 540/350(-35.2%) | 18/18(+0.0%) | 2/0(-100.0%) |
| 黑野猪 (35/35) | 280/310(+10.7%) | 650/380(-41.5%) | 26/26(+0.0%) | 10/10(+0.0%) |
| 红野猪 (32/32) | 300/330(+10.0%) | 600/320(-46.7%) | 25/25(+0.0%) | 0/0(+0.0%) |
| 白野猪 (50/50) | 2000/1000(-50.0%) | 3600/1600(-55.6%) | 55/55(+0.0%) | 15/15(+0.0%) |
| 沃玛卫士 (50/50) | 1000/1000(+0.0%) | 2400/1200(-50.0%) | 42/42(+0.0%) | 8/8(+0.0%) |
| 沃玛战士 (30/30) | 285/265(-7.0%) | 340/260(-23.5%) | 28/28(+0.0%) | 3/3(+0.0%) |
| 沃玛战将 (30/30) | 285/285(+0.0%) | 280/280(+0.0%) | 29/29(+0.0%) | 3/3(+0.0%) |
| 沃玛教主 (60/60) | 3000/2200(-26.7%) | 6000/2500(-58.3%) | 90/60(-33.3%) | 25/17(-32.0%) |
| 沃玛勇士 (30/30) | 285/285(+0.0%) | 340/280(-17.6%) | 28/28(+0.0%) | 3/3(+0.0%) |
| 祖玛弓箭手 (39/40) | 900/385(-57.2%) | 1900/370(-80.5%) | 30/18(-40.0%) | 6/10(+66.7%) |
| 祖玛卫士 (50/43) | 1100/495(-55.0%) | 2400/480(-80.0%) | 44/34(-22.7%) | 18/15(-16.7%) |
| 祖玛雕像 (50/42) | 1000/495(-50.5%) | 2200/450(-79.5%) | 38/32(-15.8%) | 13/12(-7.7%) |
| 祖玛教主 (60/60) | 12000/3000(-75.0%) | 12000/3000(-75.0%) | 100/65(-35.0%) | 35/20(-42.9%) |

共比对 44 只（≥10 达标）、176 个字段值，其中差异 >30% 的 55 个列入 M5 校准观察。

## 4. 转换器字段映射样本（5 只抽查怪：原始记录 → JSON）

### 半兽战士（orc_warrior.json）

```
Crystal raw : index=50 name=半兽战士 level=22 hp=100 MinAC=3 MaxAC=3 MinMAC=1 MaxMAC=1 MinDC=5 MaxDC=12 Accuracy=10 Agility=15 AttackSpeed=2000ms MoveSpeed=1500ms Exp=130
map         : level←level hp←hp attack←MaxDC defense←MaxAC magic_defense←MaxMAC accuracy←Accuracy agility←Agility exp←Exp attack_speed←round(AttackSpeed/1000) move_speed←round(MoveSpeed/1000)
```

| 字段 | 值 |
| --- | --- |
| id | orc_warrior |
| name | 半兽战士 |
| level | 22 |
| hp | 100 |
| attack | 12 |
| defense | 3 |
| magic_defense | 1 |
| accuracy | 10 |
| agility | 15 |
| exp | 130 |
| attack_speed | 2 |
| move_speed | 2 |
| undead | False |
| boss | False |
| respawn_seconds | 120 |

### 尸王（corpse_king.json）

```
Crystal raw : index=118 name=尸王 level=50 hp=500 MinAC=3 MaxAC=3 MinMAC=3 MaxMAC=3 MinDC=18 MaxDC=38 Accuracy=15 Agility=15 AttackSpeed=2800ms MoveSpeed=1500ms Exp=1200
map         : level←level hp←hp attack←MaxDC defense←MaxAC magic_defense←MaxMAC accuracy←Accuracy agility←Agility exp←Exp attack_speed←round(AttackSpeed/1000) move_speed←round(MoveSpeed/1000)
```

| 字段 | 值 |
| --- | --- |
| id | corpse_king |
| name | 尸王 |
| level | 50 |
| hp | 500 |
| attack | 38 |
| defense | 3 |
| magic_defense | 3 |
| accuracy | 15 |
| agility | 15 |
| exp | 1200 |
| attack_speed | 3 |
| move_speed | 2 |
| undead | True |
| boss | True |
| respawn_seconds | 3600 |

### 白野猪（wild_boar_white.json）

```
Crystal raw : index=174 name=白野猪 level=50 hp=2000 MinAC=13 MaxAC=15 MinMAC=25 MaxMAC=25 MinDC=30 MaxDC=55 Accuracy=17 Agility=22 AttackSpeed=1800ms MoveSpeed=800ms Exp=3600
map         : level←level hp←hp attack←MaxDC defense←MaxAC magic_defense←MaxMAC accuracy←Accuracy agility←Agility exp←Exp attack_speed←round(AttackSpeed/1000) move_speed←round(MoveSpeed/1000)
```

| 字段 | 值 |
| --- | --- |
| id | wild_boar_white |
| name | 白野猪 |
| level | 50 |
| hp | 2000 |
| attack | 55 |
| defense | 15 |
| magic_defense | 25 |
| accuracy | 17 |
| agility | 22 |
| exp | 3600 |
| attack_speed | 2 |
| move_speed | 1 |
| undead | True |
| boss | True |
| respawn_seconds | 3600 |

### 沃玛教主（wooma_taurus.json）

```
Crystal raw : index=256 name=沃玛教主 level=60 hp=3000 MinAC=25 MaxAC=25 MinMAC=40 MaxMAC=40 MinDC=35 MaxDC=90 Accuracy=25 Agility=30 AttackSpeed=2500ms MoveSpeed=1800ms Exp=6000
map         : level←level hp←hp attack←MaxDC defense←MaxAC magic_defense←MaxMAC accuracy←Accuracy agility←Agility exp←Exp attack_speed←round(AttackSpeed/1000) move_speed←round(MoveSpeed/1000)
```

| 字段 | 值 |
| --- | --- |
| id | wooma_taurus |
| name | 沃玛教主 |
| level | 60 |
| hp | 3000 |
| attack | 90 |
| defense | 25 |
| magic_defense | 40 |
| accuracy | 25 |
| agility | 30 |
| exp | 6000 |
| attack_speed | 3 |
| move_speed | 2 |
| undead | True |
| boss | True |
| respawn_seconds | 3600 |

### 祖玛教主（zuma_taurus.json）

```
Crystal raw : index=205 name=祖玛教主 level=60 hp=12000 MinAC=35 MaxAC=35 MinMAC=40 MaxMAC=40 MinDC=40 MaxDC=100 Accuracy=40 Agility=32 AttackSpeed=1000ms MoveSpeed=300ms Exp=12000
map         : level←level hp←hp attack←MaxDC defense←MaxAC magic_defense←MaxMAC accuracy←Accuracy agility←Agility exp←Exp attack_speed←round(AttackSpeed/1000) move_speed←round(MoveSpeed/1000)
```

| 字段 | 值 |
| --- | --- |
| id | zuma_taurus |
| name | 祖玛教主 |
| level | 60 |
| hp | 12000 |
| attack | 100 |
| defense | 35 |
| magic_defense | 40 |
| accuracy | 40 |
| agility | 32 |
| exp | 12000 |
| attack_speed | 1 |
| move_speed | 1 |
| undead | True |
| boss | True |
| respawn_seconds | 3600 |
