# Gamedata JSON 格式规范

静态游戏数据全部以版本化 JSON 存放于 `backend/gamedata/`，启动时由
`app.gamedata_loader.load_all()` 用 Pydantic 加载校验进内存；校验失败拒绝启动。

```
backend/gamedata/
├── maps/       # 地图定义
├── monsters/   # 怪物定义
├── items/      # 物品定义
├── skills/     # 技能定义
├── npcs/       # NPC 与商店清单
├── quests/     # 任务定义
├── drops/      # 怪物掉落表（加权随机）
└── spawns/     # 各地图刷怪点
```

## 通用规则

- **一文件一条目**：每个 `.json` 文件含一个对象；目录内所有 `.json` 都会被 schema 解析，不允许 orphan 文件。
- **一切交叉引用走 id，禁止按名字引用**（换皮映射前提，D17）。
- id 为 slug：小写字母/数字/中划线/下划线，正则 `^[a-z0-9][a-z0-9_-]*$`。
- 所有模型 `extra="forbid"`：未声明字段直接报错，防字段漂移。
- 标 **↦** 的字段是交叉引用，加载期校验目标 id 必须存在，悬空 id 拒绝通过。
- 索引键：maps/monsters/items/skills/npcs/quests 以 `id` 为键；drops 以 `monster_id` 为键（一怪一表）；spawns 以 `{map_id}:{monster_id}` 为键。同键重复报错。
- 校验命令：`uv run python -m app.gamedata_cli validate [--strict]`，退出码 0=通过 / 1=失败（明细含文件+字段路径）。
- `stats` / `effect_params` 为自由属性包（jsonb），键为属性名、值为数值。

---

## maps — `MapDef`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| id | slug | 是 | 地图唯一 id（出生城固定为 `yin-xing-cun`） |
| name | string | 是 | 显示名（中文，M5 换皮映射） |
| level_min | int ≥0 | 是 | 等级带下限 |
| level_max | int ≥0 | 是 | 等级带上限 |
| region | string | 是 | 区域名（如 `newbie`/`biqi`） |
| adjacent | list[slug] ↦ maps | 否（默认 `[]`） | 连通邻接地图 id |
| boss_spawns | list[slug] ↦ monsters | 否（默认 `[]`） | 本图 BOSS 怪 id |

```json
{
  "id": "yin-xing-cun",
  "name": "银杏村",
  "level_min": 0,
  "level_max": 7,
  "region": "newbie",
  "adjacent": [],
  "boss_spawns": []
}
```

## monsters — `MonsterDef`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| id | slug | 是 | 怪物唯一 id |
| name | string | 是 | 显示名 |
| level | int ≥1 | 是 | 怪物等级 |
| hp | int ≥1 | 是 | 生命值 |
| attack | int ≥0 | 是 | 攻击 |
| defense | int ≥0 | 是 | 防御 |
| magic_defense | int ≥0 | 是 | 魔防 |
| accuracy | int ≥0 | 是 | 准确 |
| agility | int ≥0 | 是 | 敏捷 |
| exp | int ≥0 | 是 | 击杀经验 |
| attack_speed | int ≥0 | 是 | 攻击速度（tick 间隔档） |
| move_speed | int ≥0 | 是 | 移动速度（tick 间隔档） |
| undead | bool | 是 | 是否不死系（影响圣言等） |
| boss | bool | 是 | 是否 BOSS |
| respawn_seconds | int ≥0 \| null | 否（默认 null） | 重生秒数；null=不重生 |

```json
{
  "id": "chicken",
  "name": "鸡",
  "level": 1,
  "hp": 5,
  "attack": 2,
  "defense": 0,
  "magic_defense": 0,
  "accuracy": 5,
  "agility": 5,
  "exp": 5,
  "attack_speed": 1,
  "move_speed": 1,
  "undead": false,
  "boss": false,
  "respawn_seconds": 60
}
```

## items — `ItemDef`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| id | slug | 是 | 物品唯一 id |
| name | string | 是 | 显示名 |
| kind | enum | 是 | `weapon`/`armor`/`helmet`/`necklace`/`bracelet`/`ring`/`belt`/`boots`/`potion`/`scroll`/`material`/`book` |
| need_level | int ≥0 | 是 | 需要等级 |
| need_strength | int ≥0 | 否（默认 0） | 需要腕力 |
| need_magic | int ≥0 | 否（默认 0） | 需要魔法力 |
| need_taoism | int ≥0 | 否（默认 0） | 需要道术 |
| weight | int ≥0 | 是 | 重量 |
| stackable | bool | 是 | 是否可堆叠 |
| max_durability | int ≥0 | 否（默认 0） | 最大耐久；0=无耐久概念 |
| sell_price | int ≥0 | 是 | 玩家卖给商店的回收价 |
| buy_price | int ≥0 | 是 | 商店出售价 |
| stats | dict[str,int] | 否（默认 `{}`） | 属性包（如 `dc_min`/`dc_max`/`mc_min` 等） |
| prefix_max | int ≥0 | 否（默认 0） | 极品词缀上限条数 |

```json
{
  "id": "wooden_sword",
  "name": "木剑",
  "kind": "weapon",
  "need_level": 1,
  "need_strength": 0,
  "need_magic": 0,
  "need_taoism": 0,
  "weight": 7,
  "stackable": false,
  "max_durability": 4,
  "sell_price": 50,
  "buy_price": 100,
  "stats": { "dc_min": 2, "dc_max": 5 },
  "prefix_max": 1
}
```

## skills — `SkillDef`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| id | slug | 是 | 技能唯一 id |
| name | string | 是 | 显示名 |
| class | enum | 是 | `warrior`/`wizard`/`taoist`/`all` |
| learn_level | int ≥0 | 是 | 习得等级 |
| mp_cost | int ≥0 | 是 | 单次耗蓝 |
| cooldown_seconds | int ≥0 | 否（默认 0） | 冷却秒数 |
| max_level | int ≥1 | 是 | 技能满级（176 为 3） |
| level_exp_required | list[int] | 是 | 各级所需熟练经验，**长度必须 == max_level** |
| target | enum | 是 | `self`/`enemy`/`ally`/`ground`/`aoe` |
| effect_params | dict[str,number] | 否（默认 `{}`） | 效果参数包（倍率/dot/召唤等） |

```json
{
  "id": "basic_sword",
  "name": "基本剑术",
  "class": "warrior",
  "learn_level": 7,
  "mp_cost": 0,
  "cooldown_seconds": 0,
  "max_level": 3,
  "level_exp_required": [100, 200, 300],
  "target": "enemy",
  "effect_params": { "accuracy_bonus": 3, "damage_multiplier": 1.05 }
}
```

## npcs — `NpcDef`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| id | slug | 是 | NPC 唯一 id |
| name | string | 是 | 显示名 |
| map_id | slug ↦ maps | 是 | 所在地图 id |
| kind | enum | 是 | `vendor`/`quest`/`task`/`storage`/`teleport` |
| shop_items | list[slug] ↦ items | 否（默认 `[]`） | 商店售卖物品 id（非 vendor 一般为空） |
| dialogue | list[string] | 否（默认 `[]`） | 闲聊/招呼台词 |

```json
{
  "id": "healer_npc",
  "name": "村庄医师",
  "map_id": "yin-xing-cun",
  "kind": "vendor",
  "shop_items": ["wooden_sword"],
  "dialogue": ["欢迎来到银杏村。", "需要点什么吗？"]
}
```

## quests — `QuestDef`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| id | slug | 是 | 任务唯一 id |
| name | string | 是 | 显示名 |
| kind | enum | 是 | `kill`/`collect`/`dialogue`/`profession` |
| level_req | int ≥0 | 否（默认 0） | 接取等级要求 |
| target_id | slug ↦ 按 kind 定向 | 是 | kill→monsters，collect→items，dialogue→npcs，profession→skills |
| target_count | int ≥1 | 否（默认 1） | 目标数量（杀几只/收几件） |
| reward_gold | int ≥0 | 否（默认 0） | 奖励金币 |
| reward_exp | int ≥0 | 否（默认 0） | 奖励经验 |
| reward_items | list[slug] ↦ items | 否（默认 `[]`） | 奖励物品 id |
| dialogue | dict[str,string] | 否（默认 `{}`） | 阶段文案（accept/progress/complete 等） |

```json
{
  "id": "newbie_kill_5",
  "name": "除害新手村",
  "kind": "kill",
  "level_req": 1,
  "target_id": "chicken",
  "target_count": 5,
  "reward_gold": 100,
  "reward_exp": 50,
  "reward_items": ["wooden_sword"],
  "dialogue": {
    "accept": "村外的鸡太多了，帮我杀 5 只。",
    "progress": "还没杀够，继续加油。",
    "complete": "干得漂亮，这是你的奖励。"
  }
}
```

## drops — `DropEntry`

一怪一张掉落表，索引键 = `monster_id`。掉落为加权随机：每次从 `entries` 按 `weight` 抽一项。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| monster_id | slug ↦ monsters | 是 | 所属怪物 id |
| entries | list[DropItem] | 是（≥1 项） | 掉落项列表 |

`DropItem`：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| item_id | slug ↦ items \| null | 是 | **null=掉金币**（用 gold_min/gold_max）；否则掉物品（用 min_qty/max_qty） |
| weight | int ≥1 | 是 | 权重 |
| min_qty | int ≥0 | 否（默认 0） | 物品数量下限 |
| max_qty | int ≥0 | 否（默认 0） | 物品数量上限 |
| gold_min | int ≥0 | 否（默认 0） | 金币下限 |
| gold_max | int ≥0 | 否（默认 0） | 金币上限 |

```json
{
  "monster_id": "chicken",
  "entries": [
    { "item_id": null, "weight": 50, "min_qty": 0, "max_qty": 0, "gold_min": 5, "gold_max": 20 },
    { "item_id": "wooden_sword", "weight": 1, "min_qty": 1, "max_qty": 1, "gold_min": 0, "gold_max": 0 }
  ]
}
```

## spawns — `SpawnEntry`

索引键 = `{map_id}:{monster_id}`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| map_id | slug ↦ maps | 是 | 刷怪地图 id |
| monster_id | slug ↦ monsters | 是 | 刷的怪物 id |
| density | int ≥1 | 是 | 密度档（每点位的拥挤度系数） |
| respawn_seconds | int ≥0 | 是 | 重生间隔秒数 |
| count | int ≥1 | 是 | 同屏目标数量 |
| locations | list[{x,y}] | 是（≥1 个） | 刷怪点位坐标（x/y ≥0） |

```json
{
  "map_id": "yin-xing-cun",
  "monster_id": "chicken",
  "density": 5,
  "respawn_seconds": 60,
  "count": 10,
  "locations": [
    { "x": 100, "y": 100 },
    { "x": 120, "y": 110 }
  ]
}
```
