# INSTALL — mir-176-h5 部署到 dell1（生产）

前置：task 33 已完成环境准备（PG mir_app/mir_prod、自签证书、/opt/mir-176-h5 骨架、
nginx/uv/CPython 3.12）。复现见 `deploy/dell1-setup.sh`，说明见 `deploy/README.md`。

本文件覆盖：env 配置 → release 落位 → nginx site → systemd unit → 迁移 → 冒烟。

## 1. 构建产物（本机/dev 机）

```bash
# 前端：react-router history 模式 → nginx 已配 SPA 兜底
cd frontend && corepack pnpm install --frozen-lockfile && corepack pnpm build   # → frontend/dist/

# 后端：源码 + uv.lock 原样入 release（依赖在远端用 uv 装，锁定一致）
```

## 2. 环境变量（dell1，一次性）

```bash
ssh dell1 'cp /opt/mir-176-h5/current/deploy/env.template /opt/mir-176-h5/shared/backend.env 2>/dev/null || true'
# 首次：模板文件随首个 release 落位后才存在；也可本地 scp deploy/env.template 上去。
ssh dell1 'chmod 600 /opt/mir-176-h5/shared/backend.env'
# 编辑：REPLACE_AT_DEPLOY → task 33 的 mir_app 48hex 口令；SECRET_KEY → openssl rand -hex 32
```

模板各键含义见 `deploy/env.template` 内联注释。BIND_HOST/BIND_PORT 必须与
`deploy/nginx.conf` 的 proxy_pass（127.0.0.1:8000）一致。

## 3. Release 落位 + 依赖安装（dell1）

```bash
TS=$(date +%Y%m%d%H%M%S)
rsync -a --delete backend/   dell1:/opt/mir-176-h5/releases/$TS/backend/
rsync -a --delete frontend/dist/ dell1:/opt/mir-176-h5/releases/$TS/dist/
ssh dell1 "cd /opt/mir-176-h5/releases/$TS/backend && uv sync --frozen"
# .venv 解释器 = uv-managed CPython 3.12（systemd unit ExecStart 直执行它）
ssh dell1 "ln -sfn releases/$TS /opt/mir-176-h5/current"   # symlink-swap = 回滚点
```

## 4. nginx site（dell1，一次性）

```bash
scp deploy/nginx.conf dell1:/tmp/mir.nginx.conf
ssh dell1 'sudo -n cp /tmp/mir.nginx.conf /etc/nginx/sites-available/mir \
  && sudo -n ln -sfn /etc/nginx/sites-available/mir /etc/nginx/sites-enabled/mir \
  && sudo -n nginx -t && sudo -n systemctl reload nginx'
```

不动既有 10 个 site；mir 独占 8443。将来换正式证书只改 site 里两行
`ssl_certificate(_key)` 路径后 reload（acme.sh 链路已通，见 README.md §4）。

## 5. systemd unit（dell1，一次性）

```bash
scp deploy/mir-backend.service dell1:/tmp/mir-backend.service
ssh dell1 'sudo -n cp /tmp/mir-backend.service /etc/systemd/system/mir-backend.service \
  && sudo -n systemd-analyze verify /etc/systemd/system/mir-backend.service \
  && sudo -n systemctl daemon-reload \
  && sudo -n systemctl enable --now mir-backend'
```

红线复核：`systemctl cat mir-backend | grep -- '--workers 1'` 必须命中且不大于 1。

## 6. 数据库迁移（dell1，每次发版含迁移时）

```bash
ssh dell1 'cd /opt/mir-176-h5/current/backend \
  && set -a && . /opt/mir-176-h5/shared/backend.env && set +a \
  && .venv/bin/alembic upgrade head'
```

## 7. 冒烟验收

```bash
# nginx 配置本地预检（dev 机，需本地 nginx + stub 证书；包装配置因 site 片段需 http 上下文）：
mkdir -p /tmp/mir-nginx-t && printf 'events {}\nhttp { include %s/deploy/nginx.conf; }\n' "$PWD" > /tmp/mir-nginx-t/nginx.conf
sudo -n mkdir -p /etc/nginx/ssl && sudo -n openssl req -x509 -nodes -days 1 -newkey rsa:2048 \
  -keyout /etc/nginx/ssl/mir.key -out /etc/nginx/ssl/mir.crt -subj "/CN=stub" 2>/dev/null
nginx -t -c /tmp/mir-nginx-t/nginx.conf -p /tmp/mir-nginx-t/

# dell1 端到端：
curl -k https://dell1:8443/api/health                                   # REST 反代
curl -k -N --max-time 5 https://dell1:8443/api/stream                   # SSE（应见事件流/心跳）
curl -kI https://dell1:8443/                                            # 静态 + TLS
ssh dell1 'systemctl status mir-backend --no-pager | head -5'
ssh dell1 'journalctl -u mir-backend -n 20 --no-pager'
```

## 8. 回滚

```bash
ssh dell1 'ln -sfn releases/<上一个时间戳> /opt/mir-176-h5/current \
  && sudo -n systemctl restart mir-backend && sudo -n systemctl reload nginx'
```

数据库迁移不回滚（alembic downgrade 按需单独评估）。
