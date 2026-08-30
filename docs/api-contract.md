# API 契约 — mir-176-h5 MVP

> **唯一事实源**：前端（与 bot）只以本文档为依据对接后端。
> 与代码不一致时：先改本文档或先改代码，二者不允许长期漂移
> （验收脚本会 grep FastAPI 路由表 vs 本文档端点清单）。
>
> 来源：`backend/app/main.py` 注册的 10 个 router（auth / character / consumable / health /
> inventory / quest / shop / skill / stream / teleport），
> 对应 todo 3（鉴权）、todo 5（SSE）、todo 23/25–30（外围系统）。后续 todo 新增端点必须同步更新本文档。
> 对应 todo 3（鉴权）、todo 5（SSE）。后续 todo 新增端点必须同步更新本文档。

## 通用约定

- Base URL：dev 下前端经 vite proxy `/api → http://127.0.0.1:8000`；bot 直连 `http://127.0.0.1:8000`（可用 `--base-url` / `MIR_BASE_URL` 覆盖）。
- 请求/响应体均为 `application/json; charset=utf-8`（SSE 除外）。
- 鉴权：**HttpOnly Cookie 会话**。登录成功后服务端下发 `session_id`（`Set-Cookie: session_id=<hex>; HttpOnly; SameSite=Lax; Path=/`），后续请求带 cookie 即可（浏览器 `credentials: 'include'`）。会话存服务端内存表，**进程重启即失效**（MVP 已知限制）→ 客户端收到 401 应清登录态跳登录页。
- 错误体统一 FastAPI 默认格式：
  - 业务错误：`{"detail": "<人话>"}`；
  - 请求体校验失败：422 `{"detail": [{loc, msg, type}, ...]}`。
- 未登录 / 会话失效：401 `{"detail": "未登录或会话已失效"}` + `WWW-Authenticate: Bearer`。

---

# REST

## POST /api/auth/register

注册账号。

- request: `{"username": string(1..64), "password": string(1..128)}`
- response:
  - `201` `{"id": int, "username": string}`
  - `409` `{"detail": "用户名已被注册"}`
  - `422` 校验失败

## POST /api/auth/login

登录，成功时 Set-Cookie（见通用约定）。

- request: `{"username": string, "password": string}`
- response:
  - `200` `{"id": int, "username": string}` + `Set-Cookie: session_id=...`
  - `401` `{"detail": "用户名或密码错误"}`（用户不存在与密码错误统一 401，不泄露账号存在性）

## POST /api/auth/logout

登出：销毁服务端 session 并清 cookie。幂等（无 cookie 也 204）。

- request: 无 body
- response: `204` 空 body

## GET /api/health

DB 探活。**公开端点，无需登录**。

- response:
  - `200` `{"status": "ok"}`
  - `503` `{"detail": "database unavailable"}`

## GET /api/skill/list

技能清单（todo 28）。需登录且已建号。

- response:
  - `200` `{"learned": [{skill_id, name, level, exp, max_level, next_level_exp}], "available": [{skill_id, name, learn_level, mp_cost, requires_book}]}`
    （`next_level_exp`=0 为满级哨兵；`requires_book`=true 的 ≥35 级技能只能走技能书习得）
  - `401` 未登录

## POST /api/skill/learn

显式按级习得（<35 级技能；≥35 级必须技能书）。需登录。

- request: `{"skill_id": string}`
- response:
  - `200` `{"skill_id": string, "name": string, "level": 0, "exp": 0, "source": "learn"}`
  - `409` `{"detail": "已学会 <名>"}`
  - `422` 等级不足 / 需技能书 / 未知技能

## POST /api/skill/learn_from_book

技能书习得（35 级技能唯一入口）：扣一本书 + 写技能行。需登录。

- request: `{"item_instance_id": int}`
- response:
  - `200` LearnOut（`source`: `"book"`）
  - `404` 物品不存在；`409` 已学会 / 数量不足；`422` 非技能书 / 职业不符 / 等级不足 / 技能未开放

## POST /api/skill/use

施放技能（三道闸：未习得/冷却/MP）；成功扣蓝 +1 熟练，升级推 SSE `skill_levelup`。需登录。

- request: `{"skill_id": string, "target_id": string|null}`（MVP 自动锁定首怪，target_id 预留不消费）
- response:
  - `200` `{"skill_id", "success", "reason": "ok|not_learned|cooldown|insufficient_mp", "mp_after", "exp_gained", "level_before", "level_after", "leveled_up", "pet_id"}`
  - `409` 行进中无法施放

## GET /api/quest/list

全部任务按状态分组（todo 29）。需登录且已建号。

- response:
  - `200` `{"not_started": QuestOut[], "in_progress": QuestOut[], "completed": QuestOut[], "delivered": QuestOut[]}`

### QuestOut schema

```jsonc
{
  "quest_id": string,
  "name": string,
  "kind": "kill" | "collect" | "dialogue" | "profession",
  "status": "not_started" | "in_progress" | "completed" | "delivered",
  "level_req": int,
  "required_class": string | null,
  "target_id": string,          // kill=怪物 id / collect=物品 id / dialogue=NPC id
  "target_count": int,
  "count": int,                 // 当前进度
  "need": int,                  // = target_count
  "reward_gold": int,
  "reward_exp": int,
  "reward_items": string[],
  "dialogue": object,
  "arena_map_id": string | null  // boss_instance 任务竞技场图 id
}
```

## POST /api/quest/accept

接任务（未接→进行）；boss_instance 任务接取即传送进竞技场。需登录。

- request: `{"quest_id": string}`
- response:
  - `200` `{"quest": QuestOut, "message": string, "map_id": string|null}`
  - `404` 任务不存在；`409` 已接取；`422` 等级/职业不符

## POST /api/quest/progress

推进进度：kill/boss +1；collect 缴纳扣背包一次交割；dialog 同图 NPC 对话。需登录。

- request: `{"quest_id": string, "action": "kill"|"collect"|"dialog"|"boss"}`
- response:
  - `200` `{"quest_id", "count", "need", "status", "message"}`
  - `404` 任务不存在；`409` 已交付/已完成待交付；`422` 未接取 / action 与类型不配 / 物品不足 / 不同图 / 不在竞技场

## POST /api/quest/complete

交付任务（completed→delivered 终态）：发金币/经验/物品奖励。需登录。

- request: `{"quest_id": string}`
- response:
  - `200` `{"quest_id", "name", "status": "delivered", "gold", "exp", "rewards": {"gold", "exp", "items": [{item_id, name, quantity}]}}`
  - `404` 任务不存在；`409` 已交付；`422` 未接取 / 进度未满 / 背包已满

## GET /api/teleport/panel

传送面板（todo 30）：当前图 + 可达目的地费率清单 + 行进中状态。需登录。

- response:
  - `200` `{"current_map_id", "current_map_name", "bind_city", "traveling": bool, "travel_remaining_seconds": float|null, "travel_target_map_id": string|null, "destinations": [{"map_id", "name", "fee": int, "kind": "route"|"cave", "depth": int|null}]}`

## POST /api/teleport/warp

付费传送：扣金币 + 登记 traveling_until 倒计时（5–20s 随机）；到点由 tick_loop 切图并推 SSE `zone_change(reason=travel)`。需登录。

- request: `{"target_map_id": string, "layer": int|null}`（洞穴层传 layer 深度档，缺省第 1 层）
- response:
  - `200` `{"target_map_id", "layer", "fee", "gold_after", "travel_seconds", "traveling_until"}`
  - `404` 目的地不存在；`409` 行进中；`422` 已在目的地 / 不可达 / 金币不足

## POST /api/character/policy（⚠️ 前端契约期望，后端未实现）

吃药策略存会话（todo 32 前端表单对接）。**todo 26 只落了 sim 层 `session.potion_policy` 字段，未提供 REST 端点**——当前调用返回 404，前端降级为 localStorage 本地持久化。后端补端点后此处转正式契约。

- request: `{"hp_pct": float, "urgent_hp_pct": float, "mp_pct": float, "recall_free_slots": int}`
  （前三键与 combat/potion.py policy 覆盖键同源；语义：HP<X% 喝回血药 / HP<Y% 危险线优先高级药 / MP<Z% 喝回蓝药 / 背包空格<N 自动回城，0=关闭）
- response（预期）: `200` `{"ok": true}`

---

## POST /api/character

建号（单账号单角色）。需登录。

- request: `{"name": string(1..32), "class": "warrior" | "wizard" | "taoist"}`
  （JSON 键为 `class`；Python 侧别名 `class_`）
- 建号默认值：`level=1, exp=0, gold=0, hp=0, mp=0, bag_capacity=40, map_id=bind_city="yin-xing-cun"`（BIRTH_MAP_ID 稳定常量），`last_active_at=now()`
- response:
  - `201` CharacterOut
  - `401` 未登录
  - `409` `{"detail": "该账号已拥有角色"}` 或 `{"detail": "角色名已被占用"}`

## GET /api/character/me

查自己的角色。需登录。

- response:
  - `200` CharacterOut
  - `401` 未登录
  - `404` `{"detail": "尚未建号"}`

### CharacterOut schema

```jsonc
{
  "id": int,
  "name": string,            // ≤32 字符，全局唯一
  "class": "warrior" | "wizard" | "taoist",
  "level": int,
  "exp": int,
  "hp": int,
  "mp": int,
  "gold": int,
  "map_id": string,          // gamedata map id
  "bind_city": string,       // 回城绑定城（gamedata map id）
  "bag_capacity": int        // 默认 40
}
```

---

# SSE

## GET /api/stream

战斗事件流（`text/event-stream`）。需登录且已建号（否则 401 / 404）。

- 请求头可选 `Last-Event-ID: <int>`：断线重连补发；非法值（非数字/空/负数）按全新连接处理，绝不 500。
- 服务端 ping：sse-starlette 注释行 `: ping`（间隔 `SSE_PING_INTERVAL`=15s），客户端（EventSource / httpx-sse）自动忽略。
- **事件 id = 每会话单调递增 int**（从 1 起）。gap 触发 epoch 重置后 id 从 1 重新递增——客户端必须先重置去重水位线（见 gap 语义）。

### 事件类型清单

| event | id | data（JSON 字符串） | 状态 |
|---|---|---|---|
| `message` | 有 | `{"text": string}` 战斗日志文本 | ✅ 已实现（dev 模拟源 1Hz；todo 15 起为真实 tick 日志） |
| `log` | 有 | `{"text": string, ...}` 结构化日志行 | ✅ 已实现（todo 15 起） |
| `stat` | 有 | `{"tick", "hp", "max_hp", "mp", "max_mp", "exp", "gold", "reason"?, "panel"?}` 属性快照 | ✅ 已实现（todo 15 起） |
| `drop` | 有 | `{"loot": [{item_id, qty, picked, drop_id, gold?}]}` 掉落 | ✅ 已实现（todo 15 起） |
| `exp` | 有 | `{"exp": int, "level": int, ...}` 经验/升级 | ✅ 已实现（todo 15 起） |
| `zone_change` | 有 | `{"map_id": string, "reason": string}` 换图 | ✅ 已实现（todo 15/26/29/30） |
| `scene` | 有 | 见下方 YAML payload | ✅ 已实现 |
| `travel_start` | 有 | `{"target_map_id", "layer", "fee", "gold_after", "seconds", "traveling_until"}` 传送出发 | ✅ 已实现（todo 30） |
| `disconnect` | 有 | `{}` 会话终止通知 | 预留（todo 15） |
| `gap` | **无** | `{"reason": "buffer_overflow"}` | ✅ 已实现 |

前端 `frontend/src/api/sse.ts` 的 `SSE_EVENT_NAMES` 注册监听：`zone_change`、`scene`、`travel_start`、`pet_summon`、`pet_died`、`pet_dispersed`；其余事件类型可安全忽略。

### event: scene 语义

```yaml
scene:
  payload:
    map_id: str
    monsters:
      - id: str
        monster_id: str
        name: str
        hp: int
        max_hp: int
        boss: bool
        is_target: bool
    pets:
      - id: str
        type: str  # 'skull' | 'dog' | 'charm'
        name: str
        hp: int
        max_hp: int
  semantics: |
    Dual-channel publish:
    (a) New connection → authoritative full snapshot pushed immediately after subscribe
        (self-healing anchor for page refresh / reconnect / buffer-overflow gap).
    (b) Tick on-change diff → only when current payload differs from last published.
```

### log 事件结构化字段

`log` 事件的 `kind` 字段标识具体类型，每种 kind 额外携带不同字段：

| kind | 额外字段 | 说明 |
|---|---|---|
| `attack` | `target_id`, `target_name`, `dmg`, `remaining_hp`, `skill_id`? | 玩家命中怪（skill_id=null 为平砍） |
| `miss` | `target_id`, `target_name`, `skill_id`? | 玩家未命中 |
| `kill` | `target_id`, `target_name`, `monster_id`, `exp` | 玩家击杀怪物 |
| `defend` | `attacker_id`, `attacker_name`, `dmg` | 怪命中玩家 |
| `dodge` | `attacker_id`, `attacker_name` | 怪未命中玩家 |
| `skill` | `skill_id` | 施放技能 |
| `potion` | `item_id`, `hp_restore`, `mp_restore` | 自动喝药 |
| `pet_miss` | `pet_id`, `target_id` | 宠物未命中 |
| `pet_kill` | `pet_id`, `target_id`, `monster_id`, `exp` | 宠物击杀 |
| `skill_learn` | — | 习得技能（通过 `_log_event` 发出，非独立 SSE 事件） |
| `skill_levelup` | — | 技能熟练度提升（通过 `_log_event` 发出，非独立 SSE 事件） |

### event: gap 语义（关键契约）

- **gap 事件不带 id**。
- 触发条件：重连携带的 `Last-Event-ID` 所需事件已滚出 ring buffer（容量 `SSE_BUFFER_SIZE`=1200）。
- 客户端处理顺序（原子）：**先重置本地去重水位线，再插入「期间部分日志丢失」分隔行**。
  否则 gap 后新会话的小 id（从 1 重新递增）会被旧水位线误判为重复而丢弃。
- gap 之后服务端视同全新连接：epoch 重置、事件 id 从 1 重新递增。

### 重放规则（服务端行为，供参考）

| Last-Event-ID 状态 | 行为 |
|---|---|
| 缺失 / 非法 | 全新连接：不重放、不 gap |
| ≥ 服务端最大 id | 已是最新：不重放 |
| 所需事件完整在 buffer 内 | 原 id 原序补发 |
| 已滚出 buffer | 先发 `gap`（无 id），epoch 重置，其后 id 从 1 起 |

---

## dev 模拟源说明

`settings.ENV == "dev"` 时，首个 SSE 连接懒启动每角色一个模拟日志任务（立即产首条，之后每 `SSE_MOCK_INTERVAL`=1s 一条 `message`），跨断线存活以支撑 Last-Event-ID 重放测试；prod 绝不启动。`scripts/bot_client.py` 的冒烟路径即依赖该源收 10 条 `message` 并断言 id 单调递增。
