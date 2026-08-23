# API 契约 — mir-176-h5 MVP

> **唯一事实源**：前端（与 bot）只以本文档为依据对接后端。
> 与代码不一致时：先改本文档或先改代码，二者不允许长期漂移
> （验收脚本会 grep FastAPI 路由表 vs 本文档端点清单）。
>
> 来源：`backend/app/main.py` 注册的 4 个 router（auth / character / health / stream），
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
| `log` | 有 | `{"text": string, ...}` 结构化日志行 | 预留（todo 15） |
| `stat` | 有 | `{"hp": int, "mp": int, ...}` 属性快照 | 预留（todo 15） |
| `drop` | 有 | `{"item_id": string, "quantity": int, ...}` 掉落 | 预留（todo 15） |
| `exp` | 有 | `{"exp": int, "level": int, ...}` 经验/升级 | 预留（todo 15） |
| `disconnect` | 有 | `{}` 会话终止通知 | 预留（todo 15） |
| `gap` | **无** | `{"reason": "buffer_overflow"}` | ✅ 已实现 |

前端监听全部具名事件（见 `frontend/src/api/sse.ts` 的 `SSE_EVENT_NAMES`）；未知事件类型可安全忽略。

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
