# Deploy — dell1 生产环境

目标主机：`dell1`（192.168.1.12，Debian 12 bookworm，x86_64）。SSH 用户 `aymazon`，
免密 ssh + 免密 sudo（`sudo -n true` exit 0）均已验证（task 33）。

## 1. SSH 配置

本机 `/etc/hosts` 已有 `192.168.1.12 dell1` 解析；`~/.ssh/config` 另加了显式别名
（防 /etc/hosts 漂移，幂等）：

```
Host dell1
    HostName 192.168.1.12
    User aymazon
```

验证：`ssh -o BatchMode=yes dell1 'echo reachable'` → `reachable`。

## 2. 远端软件清单（task 33 安装/确认）

| 软件 | 版本 | 来源 | 备注 |
|---|---|---|---|
| PostgreSQL server | 18.6 | **既有 docker 容器** `postgres`（postgres:18，0.0.0.0:5432，restart=always） | 复用，未新建；与 dev 机同模式。超管口令与 dev 相同 |
| psql client | 15.19 | apt `postgresql-client` | 客户端 15 ↔ server 18 基本查询兼容 |
| nginx | 1.22.1 | apt `nginx` | enabled + active；**既有 10 个 site 未动**（见 §5） |
| uv | 0.12.7 | astral 官方脚本（用户级 ~/.local/bin） | 已 symlink 到 /usr/local/bin |
| CPython | 3.12.14 | `uv python install 3.12`（uv-managed） | 系统 python3 是 3.11，不满足 backend `requires-python>=3.12` |

非交互 ssh 的 PATH 为 `/usr/local/bin:/usr/bin:/bin:...`，故做了三个 symlink
（幂等，deploy 脚本与非交互 ssh 都依赖）：

```
sudo ln -sf ~/.local/bin/uv  /usr/local/bin/uv
sudo ln -sf ~/.local/bin/uvx /usr/local/bin/uvx
sudo ln -sf /usr/sbin/nginx  /usr/local/bin/nginx
```

验证（task 33 验收命令，exit 0）：

```
ssh dell1 'psql --version && nginx -v && uv --version'
```

## 3. PostgreSQL：mir_prod + 专用账号

- 库：`mir_prod`，owner = `mir_app`；`REVOKE CONNECT ON DATABASE mir_prod FROM PUBLIC`
- 账号：`mir_app`（**非** superuser，无 createrole/createdb），仅拥有 mir_prod
- 口令：48 hex 随机串，存于 `.omo/evidence/task-33-mir-176-h5-mvp.log`（`.omo/` 已 gitignore），
  不进入本仓库；todo 35 部署时写入 dell1 的 `/opt/mir-176-h5/shared/.env`
- 生产 DATABASE_URL 形态（asyncpg，hex 口令无需 percent-encode）：

  ```
  postgresql+asyncpg://mir_app:<hex48>@127.0.0.1:5432/mir_prod
  ```

  backend 与 PG 同机（dell1），用 127.0.0.1 即可；LAN 侧验证用 `host=dell1`。

DDL（可重入执行见 `deploy/dell1-setup.sh`）：

```sql
CREATE USER mir_app WITH PASSWORD '<hex48>';
CREATE DATABASE mir_prod OWNER mir_app;
REVOKE CONNECT ON DATABASE mir_prod FROM PUBLIC;
```

验收（本机执行，已通过）：`PGPASSWORD=... psql 'host=dell1 port=5432 user=mir_app dbname=mir_prod' -tAc 'select 1'` → `1|mir_app|mir_prod`

## 4. TLS 决策：**自签证书**（D16 默认路径）

结论：用户使用 IP/主机名直连，未提供 mir 专用域名 → 自签证书。

- 证书：`/etc/nginx/ssl/mir.crt`（644, root）
- 私钥：`/etc/nginx/ssl/mir.key`（600, root）
- 参数：`openssl req -x509 -nodes -days 365 -newkey rsa:2048`，
  `CN=192.168.1.12`，SAN = `IP:192.168.1.12, DNS:dell1`
- 有效期：2026-08-29 → 2027-08-29（365 天；到期前重跑同命令续期）

**切换 certbot/正式证书的路径**（未来若分配域名）：
dell1 上已存在 `fanzhiqiang.cn` 的 acme 证书（/etc/nginx/ssl/{fullchain.cer,*.key}，
matrix 等 site 在用），说明 acme.sh 链路已通。给 mir 分配域名后：
① DNS A 记录 → 192.168.1.12（或公网 IP）；② acme.sh 签发新域名证书；
③ nginx site 的 `ssl_certificate(_key)` 换路径并重载。自签 → 正式证书只改两行配置。

## 5. nginx 现状与 mir 的接入约束

dell1 已有 10 个启用中的 site（`ls /etc/nginx/sites-enabled/`）：
`default(80/443 default_server)`、`alist`、`matrix`、`matrix-admin`、`wekan`、`radicale`、
`ai-pc6-7890/7891/7892`、`ai-pc7-oc`。80/443/8448 均已被占。

**约束（todo 35 必须遵守）**：
- 不动任何既有 site；mir 新建独立 `sites-available/mir` + symlink
- 443 的 default_server 已被占，且自签证书与既有 SNI 站点混用易踩坑 →
  **mir 监听专用端口 8443（ssl http2）**，server_name `dell1 192.168.1.12`；
  若将来上域名再迁 443 SNI
- 上线前 `sudo nginx -t && sudo systemctl reload nginx`（reload 不断既有站点）

## 6. 部署目录骨架（已建）

```
/opt/mir-176-h5/            owner=aymazon:aymazon（免 sudo 部署）
├── releases/               每次发布一个 releases/<timestamp>/
│   └── bootstrap/          占位空 release（非代码，仅让 current 可指）
├── backups/                PG 备份（todo 36 起用）
├── shared/                 跨 release 共享：.env、日志等（todo 35 写入）
└── current -> releases/bootstrap   symlink-swap 回滚点（todo 35 换指）
```

## 7. 重复执行

`deploy/dell1-setup.sh` 把本任务全部步骤幂等化（已存在则跳过）：

```
./deploy/dell1-setup.sh            # 全流程
MIR_APP_PASSWORD=<hex48> ./deploy/dell1-setup.sh   # 显式指定 mir_app 口令
```

## 8. 操作日志（task 33 时序摘要）

1. ssh/sudo 验证 → OK；`~/.ssh/config` 加 Host dell1 别名
2. 发现：docker postgres:18 既有（复用决策）；psql/nginx/uv/python3.12 缺失
3. apt 安装 `nginx postgresql-client`；astral 脚本装 uv 0.12.7；`uv python install 3.12` → 3.12.14
4. /usr/local/bin symlink ×3 修非交互 PATH
5. 建 mir_app + mir_prod（首次因 `docker exec` 漏 `-i` 静默丢 stdin，重试成功）
6. 自签证书 mir.crt/mir.key
7. /opt/mir-176-h5 骨架 + current symlink
8. 全量验收通过，证据：`.omo/evidence/task-33-mir-176-h5-mvp.log`

## 9. 部署日志

### 2026-08-29 08:54 — releases/20260829-085045（首版，task 35）

- 备份：`docker exec postgres pg_dump -U postgres mir_prod` → `backups/20260829-085045.sql`（空库基线）
- 内容：backend（uv.lock 锁定，远端 `uv sync --frozen`，37 包）+ frontend/dist + deploy/
- DB：alembic upgrade → `0004_traveling_until (head)`，exit 0
- 切换：`current -> releases/20260829-085045`；systemd unit + nginx site（8443）安装启用
- 验收：health 200 / index.html 200 / SPA 深链 200 / PG 停→503→恢复 200；journal 零 error
- 环境修复记录：dell1 ufw 增 `allow 8443/tcp`（原 DROP 拦截）；工作站 curl 需 `--noproxy '*'`（本机 8180 代理拦截 LAN）
- 回滚点：`releases/bootstrap`；证据 `.omo/evidence/task-35-mir-176-h5-mvp.log`

### 2026-08-29 09:13 — 部署实证验收（task 36，三项全绿）

- **SSE /api/stream 逐条到达**（proxy_buffering off 实证）：全新账号注册/登录/建号后，
  httpx 手解 SSE 帧计时，21 条事件（11 条 stat 心跳，id 1..21 单调唯一），
  stat-to-stat 间隔 mean=1.008s / pvariance=0.0002 / min=0.966 / max=1.015，
  总时长 10.08s 渐进到达（若攒批，~1KB 样本会被默认 4k proxy buffer 全扣住、末尾一次冲出）。
  注：同 tick 的 log/stat/drop 共享到达时刻是应用层同批 publish，跨 tick 心跳才是 buffering 判据。
- **单 worker**：`ssh dell1 'sudo -n ss -ltnp | grep ":8000\b"'` → 恰 1 PID
  （uvicorn 2507742，`--workers 1` 字面硬编码于 ExecStart，无 supervisor）。
- **真机冒烟**：`frontend/tests/e2e/dell1-smoke.spec.ts`（test.use 覆盖 baseURL=https://dell1:8443
  + ignoreHTTPSErrors），chromium-mobile 390×844，注册→登录→建号→战斗页挂机 30s
  （日志行数递增 + stat-hp + monster-list）→ 截图，`playwright test dell1-smoke.spec.ts` 1 passed。
- 证据：`.omo/evidence/task-36-mir-176-h5-mvp.log` + `-battle.png` + 两份 raw log。
- 坑位记录：SSE 计时必须用**全新账号**（buffer 为空）——断线宽限期内旧会话 buffer 会
  被无 Last-Event-ID 的新连接全量重放，瞬时到达的历史事件会把逐条计时打成假 FAIL。


## 10. GPL 合规姿态（task 38 复查结论，draft D13）

**结论：本项目为私有仓库、仅私有 LAN 部署（dell1 192.168.1.12:8443，自签证书、无公网
分发），不向外部分发源码或二进制 → GPL 义务不触发。**

复查要点：
- `backend/gamedata/` 的 JSON 由 GPL 许可的 Mir2 服务端数据源（GeeM2 / Crystal /
  OpenMir2，详见 docs/data-coverage.md）转换而来，属**内部开发数据**，仅存在于私有仓库，
  不分发；工程红线「不提交任何 GPL 原始 DB 文件入库」自 todo 13 起持续遵守。
- 数值（HP/经验/习得等级/门槛/费率）为事实性数据，不受版权保护——保留不改；
  显示文案（地图/怪物/物品/技能/NPC/任务名与对话）已全量换皮为自造词，
  映射与豁免清单见 `docs/reskin-mapping.md`（scripts/reskin.py 机械执行，
  词表 git grep 零命中验证于 2026-08-29，证据 .omo/evidence/task-38-mir-176-h5-mvp.log）。
- **若未来改变分发姿态**（公开发布源码/镜像/向他人提供服务端副本）：整个项目须整体
  按 GPL v2 开源并在根 README 显著声明；并需另行评估 Mir 商标/素材风险。
  本结论届时必须重查。