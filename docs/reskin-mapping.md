# Reskin Mapping — 改名换皮对照表（task 38，M5 收口）

> 三列映射：`id → 旧名 → 新名`。覆盖 gamedata 全量六类（maps/monsters/items/skills/npcs/quests）
> 的显示名，外加 phrases 段用于对话/文案自由文本中的非实体旧词。
>
> **纪律**：
> - 只改 `name` 与 `dialogue` 等展示字段；**不改 id**（引用完整性），**不改数值**
>  （数值事实无版权，数值复查记录见 deploy/README.md §GPL）。
> - 新名原则：自造中性奇幻词，不引用 Mir/176 原作专有名词；风格统一（边境/月华/苍岩城/
>   雾喉/磐石/白骨/荒狼意象族）。
> - NPC 中纯通用职业名（村长/屠夫/杂货店老板/传送员/村庄医师）无 IP 指向，**保留原名**
>  （新名=旧名，词表生成时跳过 old==new 行）。
> - 验收词表由本文件旧名/旧词列**机械生成**（`scripts/reskin.py --word-list`，
>   取 old != new 的全部行），不手写清单。
>
> **词表零命中范围与豁免**（git grep 路径）：`backend/gamedata/ frontend/src/ docs/`，
> 其中三个文件合法保留旧名、显式豁免：
> - `docs/reskin-mapping.md`（本文件，词表来源本身）
> - `docs/MVP/README.md`（上游设计契约，刻意记录仿 176 原始设定；scripts/
>   check_skill_learn_levels.py 经本映射翻译后继续以其 §4 表为习得等级基准）
> - `docs/data-coverage.md`（数据来源/出处台账，必须诚实记录 Crystal/OM2 源原名，
>   改名会伪造溯源记录）
>
> 开发期一次性构建/抽检脚本（backend/scripts/build_*、spotcheck_*）与 backend/tests
> 注释同样保留原名作历史记录，不在游戏面相范围，不入词表 grep 路径。

## maps（13）

| id | 旧名 | 新名 |
|---|---|---|
| biqi-mine | 比奇矿区（僵尸洞） | 灰岩矿区（行尸洞） |
| biqi-wild | 比奇野外 | 边境原野 |
| centipede-cave | 蜈蚣洞（死亡山谷） | 百足洞（寂岭） |
| mengzhong | 盟重土城周边 | 苍岩城周边 |
| shimen | 石墓（猪洞） | 沉岩墓（野猪窟） |
| shouren-gu-1 | 兽人古墓一层 | 荒狼巢穴一层 |
| shouren-gu-2 | 兽人古墓二层 | 荒狼巢穴二层 |
| shouren-gu-3 | 兽人古墓三层 | 荒狼巢穴三层 |
| snake-valley | 毒蛇山谷 | 赤蛇峡谷 |
| wooma-forest | 沃玛森林 | 雾喉森林 |
| wooma-temple | 沃玛寺庙 | 雾喉神庙 |
| yin-xing-cun | 银杏村 | 月华村 |
| zuma-caves | 祖玛寺庙 | 磐石神殿 |

## monsters（60）

| id | 旧名 | 新名 |
|---|---|---|
| ancient_wooma_warrior | 远古沃玛勇士 | 上古雾喉勇士 |
| axe_skeleton | 掷斧骷髅 | 掷斧白骨 |
| bat | 蝙蝠 | 野蝠 |
| black_centipede | 黑色蜈蚣 | 玄甲百足 |
| cave_bat | 山洞蝙蝠 | 石窟蝠 |
| cave_maggot | 洞蛆 | 穴蛆 |
| centipede | 蜈蚣 | 百足虫 |
| chicken | 鸡 | 小野雉 |
| corpse_king | 尸王 | 尸君 |
| corpse_overlord | 尸霸 | 荒冢霸主 |
| deer | 鹿 | 林羚 |
| desert_giant_worm | 沙漠巨型蠕虫 | 沙海巨蠕 |
| evil_clamp_worm | 邪恶钳虫 | 凶钳虫 |
| evil_corpse_king | 恶灵尸王 | 怨灵尸君 |
| flaming_wooma | 火焰沃玛 | 烈焰雾喉 |
| forest_yeti | 森林雪人 | 雾林雪魈 |
| giant_centipede | 巨型蜈蚣 | 巨型百足 |
| giant_scorpion | 巨型蝎子 | 巨螯沙蝎 |
| giant_snake | 巨蛇 | 巨蟒 |
| hook_cat | 多钩猫 | 钩爪狸 |
| horn_fly | 角蝇 | 犄角飞蝇 |
| jumping_bee | 跳跳蜂 | 跳刺蜂 |
| lightning_zombie | 闪电僵尸 | 掣电行尸 |
| man_eating_flower | 食人花 | 噬人藤花 |
| orc | 半兽人 | 蛮族兵 |
| orc_champion | 半兽勇士 | 蛮族勇士 |
| orc_warrior | 半兽战士 | 蛮族战士 |
| poison_spider | 毒蜘蛛 | 毒腺蛛 |
| rake_cat | 钉耙猫 | 耙爪狸 |
| red_snake | 红蛇 | 赤链蛇 |
| scarecrow | 稻草人 | 荒田立偶 |
| scorpion | 蝎子 | 沙蝎 |
| scorpion_snake | 蝎蛇 | 蝎尾蟒 |
| skeleton | 骷髅 | 白骨兵 |
| skeleton_elf | 骷髅精灵 | 白骨精魄 |
| skeleton_warrior | 骷髅战士 | 白骨战士 |
| tiger_snake | 虎蛇 | 斑纹蟒 |
| toad | 蛤蟆 | 泽蛙 |
| toad_fighter | 蛤蟆斗士 | 泽蛙斗士 |
| touch_dragon_god | 触龙神 | 渊底蛰龙 |
| two_head_snake | 双头蛇 | 双首蟒 |
| two_head_snake_demon | 双头蛇魔 | 双首蟒妖 |
| venom_snake | 毒蛇 | 毒涎蛇 |
| venom_snake_fighter | 毒蛇斗士 | 毒涎蛇斗士 |
| wedge_moth | 楔蛾 | 暗纹蛾 |
| wild_boar_black | 黑野猪 | 黑鬃野猪 |
| wild_boar_red | 红野猪 | 赤鬃野猪 |
| wild_boar_white | 白野猪 | 白鬃野猪王 |
| wooma_guardian | 沃玛卫士 | 雾喉卫士 |
| wooma_soldier | 沃玛战士 | 雾喉战士 |
| wooma_tactician | 沃玛战将 | 雾喉战将 |
| wooma_taurus | 沃玛教主 | 雾喉大祭司 |
| wooma_warrior | 沃玛勇士 | 雾喉勇士 |
| zombie | 僵尸 | 行尸 |
| zombie_fighter | 僵尸斗士 | 行尸斗士 |
| zuma_archer | 祖玛弓箭手 | 磐石弓手 |
| zuma_guardian | 祖玛卫士 | 磐石卫士 |
| zuma_guardian_elite | 极品祖玛卫士 | 磐石精锐卫士 |
| zuma_statue | 祖玛雕像 | 磐石雕像 |
| zuma_taurus | 祖玛教主 | 磐石神主 |

## items（128）

| id | 旧名 | 新名 |
|---|---|---|
| amber_necklace | 琥珀项链 | 蜜蜡项链 |
| archmage_cloak | 法神披风 | 大法师披风 |
| bahua_sword | 八荒 | 拓荒刀 |
| big_bracelet | 大手镯 | 阔手镯 |
| black_crystal_necklace | 黑色水晶项链 | 墨晶项链 |
| black_iron_helmet | 黑铁头盔 | 玄铁盔 |
| blessing_oil | 祝福油 | 祈福油 |
| blue_crystal_ring | 蓝色水晶戒指 | 蓝晶戒指 |
| bone_jade_staff | 骨玉权杖 | 骸玉权杖 |
| book_assassination | 刺杀剑术（书） | 贯刺剑术（书） |
| book_basic_sword | 基本剑术（书） | 剑招起手（书） |
| book_clone | 分身术（书） | 幻影诀（书） |
| book_demon_binding | 困魔咒（书） | 缚魔咒（书） |
| book_fire_sword | 烈火剑法（书） | 焚焰剑法（书） |
| book_fireball | 火球术（书） | 炎弹术（书） |
| book_half_moon | 半月弯刀（书） | 弧月斩（书） |
| book_healing | 治愈术（书） | 祈愈术（书） |
| book_hell_lightning | 地狱雷光（书） | 环身雷狱（书） |
| book_holy_word | 圣言术（书） | 净世圣言（书） |
| book_ice_roar | 冰咆哮（书） | 冰霜咆哮（书） |
| book_lightning | 雷电术（书） | 落雷术（书） |
| book_magic_shield | 魔法盾（书） | 魔力护盾（书） |
| book_meteor_shower | 流星火雨（书） | 陨星火雨（书） |
| book_savage_charge | 野蛮冲撞（书） | 悍勇冲撞（书） |
| book_slaying_sword | 攻杀剑术（书） | 破击剑术（书） |
| book_summon_beast | 召唤神兽（书） | 召唤灵兽（书） |
| book_summon_skeleton | 召唤骷髅（书） | 召唤白骨卫（书） |
| bronze_belt | 青铜腰带 | 铜纹腰带 |
| bronze_helmet | 青铜头盔 | 铜纹盔 |
| bronze_sword | 青铜剑 | 铜纹剑 |
| cloth_cap | 布帽 | 布巾帽 |
| cloth_robe | 布衣 | 粗布袍 |
| cloth_shoes | 布鞋 | 麻履 |
| dagger | 匕首 | 短匕 |
| deerskin_boots | 鹿皮靴 | 麋皮靴 |
| demon_bell | 恶魔铃铛 | 邪魔铃铛 |
| demon_robe | 恶魔长袍 | 邪魔长袍 |
| divine_water | 攻击神水 | 蛮力神水 |
| dragon_ring | 龙之戒指 | 龙纹戒指 |
| dragonhand_bracelet | 龙之手镯 | 龙爪手镯 |
| dragonslayer_blade | 屠龙 | 斩龙刃 |
| dragonvein_sword | 龙纹剑 | 龙脉剑 |
| dungeon_escape_scroll | 地牢逃脱卷 | 地穴逃脱卷轴 |
| ebony_sword | 乌木剑 | 黑檀剑 |
| everfrost | 万年雪霜 | 寒髓雪霜 |
| fate_blade | 命运之刃 | 淬锋之刃 |
| ghost_armor | 幽灵战衣 | 幽冥战衣 |
| ghost_gloves | 幽灵手套 | 幽魂手套 |
| ghost_necklace | 幽灵项链 | 幽魂项链 |
| glass_ring | 玻璃戒指 | 琉璃戒 |
| gold_necklace | 金项链 | 赤金链 |
| gray_powder | 灰色药粉 | 灰烬药粉 |
| green_necklace | 绿色项链 | 翠纹项链 |
| heavenly_taoist_robe | 天尊道袍 | 玄天道袍 |
| heavy_armor | 重盔甲 | 重型铠甲 |
| hexagonal_ring | 六角戒指 | 六棱戒指 |
| hide_belt | 兽皮腰带 | 革皮腰带 |
| horn_ring | 牛角戒指 | 犄角戒指 |
| iron_belt | 铁腰带 | 铁扣腰带 |
| iron_sword | 铁剑 | 铁刃剑 |
| knight_bracelet | 骑士手镯 | 骑士腕甲 |
| lantern_necklace | 灯笼项链 | 灯笼坠链 |
| lianyu_blade | 炼狱 | 熔狱刀 |
| life_necklace | 生命项链 | 生灵项链 |
| light_armor | 轻型盔甲 | 轻装皮甲 |
| lingfeng_sword | 凌风 | 逐风剑 |
| maggot_egg | 蛆卵 | 蛆虫卵 |
| magic_helmet | 魔法头盔 | 秘法盔 |
| magic_robe | 魔法长袍 | 秘法长袍 |
| magic_staff | 魔杖 | 咒杖 |
| maneating_flower_fruit | 食人花果 | 噬人藤花果 |
| meat | 肉 | 肉 |
| medium_armor | 中型盔甲 | 镶铁皮甲 |
| mind_bracelet | 心灵手镯 | 心念手镯 |
| moonwell_blade | 井中月 | 潭月刀 |
| ningshuang_sword | 凝霜 | 覆霜剑 |
| paralysis_ring | 麻痹戒指 | 定身戒指 |
| pig_iron_ring | 生铁戒指 | 铸铁戒指 |
| platinum_ring | 铂金戒指 | 铂金指环 |
| potion_hp_large | 金创药（大量） | 止血草药（大） |
| potion_hp_medium | 金创药（中量） | 止血草药（中） |
| potion_hp_small | 金创药（小量） | 止血草药（小） |
| potion_hp_super | 金创药（特量） | 止血草药（特） |
| potion_mp_large | 魔法药（大量） | 回蓝药水（大） |
| potion_mp_medium | 魔法药（中量） | 回蓝药水（中） |
| potion_mp_small | 魔法药（小量） | 回蓝药水（小） |
| potion_mp_super | 魔法药（特量） | 回蓝药水（特） |
| power_ring | 力量戒指 | 蛮力戒指 |
| protection_ring | 护身戒指 | 护身符戒 |
| purple_conch_ring | 紫碧螺 | 紫螺戒 |
| purple_silk_boots | 紫绸靴 | 紫缎靴 |
| random_scroll | 随机传送卷 | 随机挪移卷轴 |
| red_potion | 红色药水 | 赤色药水 |
| resurrection_ring | 复活戒指 | 复生戒指 |
| return_scroll | 回城卷 | 归城卷轴 |
| ruby_ring | 红宝石戒指 | 红宝戒指 |
| seasoul_staff | 海魂 | 潮歌杖 |
| sibeier_bracelet | 思贝儿手镯 | 霜贝手镯 |
| silver_bracelet | 银手镯 | 银纹手镯 |
| silver_snake_sword | 银蛇 | 银鳞蛇剑 |
| skeleton_helmet | 骷髅头盔 | 白骨盔 |
| sky_demon_armor | 天魔神甲 | 苍穹魔甲 |
| small_bracelet | 小手镯 | 细手镯 |
| soul_armor | 灵魂战衣 | 魂铸战衣 |
| soul_eater_staff | 嗜魂法杖 | 噬魂法杖 |
| soul_necklace | 灵魂项链 | 魂晶项链 |
| spider_fang | 蜘蛛牙 | 毒蛛牙 |
| strong_sun_water | 强效太阳水 | 强效日光露 |
| sun_water | 太阳水 | 日光露 |
| super_potion_hp | 超级金创药 | 超级止血草药 |
| super_potion_mp | 超级魔法药 | 超级回蓝药水 |
| taoist_helmet | 道士头盔 | 方士盔 |
| three_eye_bracelet | 三眼手镯 | 三目手镯 |
| tianzhu_necklace | 天珠项链 | 琉珠项链 |
| titan_ring | 泰坦戒指 | 巨灵戒指 |
| traditional_necklace | 传统项链 | 古朴项链 |
| verdict_staff | 裁决之杖 | 断罪之杖 |
| war_god_armor | 战神盔甲 | 斗神铠甲 |
| white_tiger_tooth_necklace | 白色虎齿项链 | 雪虎牙项链 |
| wooden_sword | 木剑 | 木刃 |
| wooma_horn | 沃玛号角 | 雾喉号角 |
| wuji_cudgel | 无极棍 | 浑元棍 |
| xiangmo_sword | 降魔 | 伏魔剑 |
| xiuluo_blade | 修罗 | 戮战刀 |
| xueyin_sword | 血饮 | 饮血剑 |
| yanyue_blade | 偃月 | 掩月刀 |
| yellow_powder | 黄色药粉 | 黄土药粉 |
| zhanma_blade | 斩马刀 | 断岳刀 |

## skills（32）

| id | 旧名 | 新名 |
|---|---|---|
| basic_sword | 基本剑术 | 剑招起手 |
| blast_fire | 爆裂火焰 | 爆裂炎浪 |
| fire_wall | 火墙 | 焰墙术 |
| fireball | 火球术 | 炎弹术 |
| ghost_shield | 幽灵盾 | 幽魂护盾 |
| great_fireball | 大火球 | 巨炎弹 |
| half_moon | 半月弯刀 | 弧月斩 |
| healing | 治愈术 | 祈愈术 |
| hell_fire | 地狱火 | 幽冥火径 |
| holy_armor | 神圣战甲术 | 神佑战甲术 |
| holy_word | 圣言术 | 净世圣言 |
| ice_thunder | 冰咆哮 | 冰霜咆哮 |
| invisibility | 隐身术 | 匿形术 |
| liehuo | 烈火剑法 | 焚焰剑法 |
| lightning | 疾光电影 | 疾光电刃 |
| lightning_exorcism | 地狱雷光 | 环身雷狱 |
| magic_shield | 魔法盾 | 魔力护盾 |
| mass_heal | 群体治愈术 | 群体祈愈术 |
| mass_invisibility | 集体隐身术 | 群体匿形术 |
| poison_amulet | 施毒术 | 淬毒术 |
| repulsion | 抗拒火环 | 斥退火环 |
| soul_fire | 灵魂火符 | 魂火符 |
| soul_shoulder | 野蛮冲撞 | 悍勇冲撞 |
| spirit_sword | 精神力战法 | 凝神战法 |
| summon_beast | 召唤神兽 | 召唤灵兽 |
| summon_skeleton | 召唤骷髅 | 召唤白骨卫 |
| tae_kwon_do | 攻杀剑术 | 破击剑术 |
| teleport | 瞬息移动 | 瞬影移形 |
| temptation | 诱惑之光 | 魅惑之光 |
| thrust | 刺杀剑术 | 贯刺剑术 |
| thunder_bolt | 雷电术 | 落雷术 |
| trap_curse | 困魔咒 | 缚魔咒 |

## npcs（11）

| id | 旧名 | 新名 |
|---|---|---|
| biqi_apothecary | 比奇药店老板 | 边境药店老板 |
| biqi_blacksmith | 比奇武器店老板 | 边境武器店老板 |
| biqi_bookstore | 比奇书店老板 | 边境书店老板 |
| biqi_veteran | 比奇老兵 | 边境老兵 |
| healer_npc | 村庄医师 | 村庄医师 |
| shouren_guide | 古墓向导 | 巢穴向导 |
| yinxing_blacksmith | 银杏铁匠 | 月华铁匠 |
| yinxing_butcher | 屠夫 | 屠夫 |
| yinxing_chief | 村长 | 村长 |
| yinxing_grocer | 杂货店老板 | 杂货店老板 |
| yinxing_teleporter | 传送员 | 传送员 |

## quests（13）

| id | 旧名 | 新名 |
|---|---|---|
| bounty_clamp_worm_15 | 土城悬赏：钳虫 | 苍岩城悬赏：凶钳虫 |
| bounty_orc_champion_10 | 讨伐半兽勇士 | 讨伐蛮族勇士 |
| bounty_skeleton_20 | 清剿骷髅洞 | 清剿白骨洞窟 |
| collect_flower_fruit | 食人花果 | 噬人藤花果 |
| collect_maggot_egg | 蛆卵收购 | 蛆虫卵收购 |
| collect_spider_fang | 蜘蛛牙收购 | 毒蛛牙收购 |
| newbie_butcher_meat | 屠夫的肉 | 屠夫的鲜肉 |
| newbie_kill_5 | 除害新手村 | 村口除害 |
| newbie_letter_bookstore | 送信比奇 | 送信边境 |
| newbie_meet_chief | 拜会村长 | 拜会村长 |
| taoist_summon_beast_guide | 神兽召唤引 | 灵兽召唤引 |
| warrior_fate_blade | 命运之刃 | 淬锋之刃 |
| wizard_ice_roar_guide | 冰咆哮书引 | 冰霜咆哮书引 |

## phrases（自由文本旧词 → 新词，用于 dialogue 等散文本）

| 旧词 | 新词 |
|---|---|
| 骷髅洞 | 白骨洞窟 |
| 死亡山谷 | 寂岭 |
| 土城 | 苍岩城 |
| 比奇 | 边境 |
| 神兽 | 灵兽 |
| 古墓 | 巢穴 |
| 石墓 | 沉岩墓 |
| 沃玛 | 雾喉 |
