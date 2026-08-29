#!/usr/bin/env bash
# dell1-setup.sh — task 33 环境准备的幂等复现脚本。
# 在本机执行；通过 ssh dell1 完成全部远端操作。已存在的组件一律跳过。
# 不部署任何代码（那是 todo 35）；不动 dell1 既有 nginx site；不装 redis/docker-compose（红线）。
set -euo pipefail

HOST="${DELL1_HOST:-dell1}"
DEPLOY_ROOT=/opt/mir-176-h5
MIR_APP_PASSWORD="${MIR_APP_PASSWORD:-$(openssl rand -hex 24)}"
PG_SU_PASSWORD="${PG_SU_PASSWORD:-qWe_1@3}"   # dell1 docker postgres 超管口令（与 dev 同）

say() { printf '\n== %s\n' "$*"; }

say "0. ssh + sudo 可达性"
ssh -o BatchMode=yes -o ConnectTimeout=5 "$HOST" 'echo reachable; sudo -n true'

say "1. apt: nginx + postgresql-client"
ssh "$HOST" 'command -v nginx >/dev/null && command -v psql >/dev/null && echo "already installed" || { sudo -n apt-get update -qq && sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx postgresql-client; }'

say "2. uv + uv-managed CPython 3.12"
ssh "$HOST" 'export PATH="$HOME/.local/bin:$PATH"; command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh; uv python find 3.12 >/dev/null 2>&1 || uv python install 3.12; uv python find 3.12'

say "3. /usr/local/bin symlink（修非交互 ssh PATH）"
ssh "$HOST" 'sudo -n ln -sf $HOME/.local/bin/uv /usr/local/bin/uv; sudo -n ln -sf $HOME/.local/bin/uvx /usr/local/bin/uvx; sudo -n ln -sf /usr/sbin/nginx /usr/local/bin/nginx'
ssh "$HOST" 'psql --version && nginx -v && uv --version'

say "4. PG: mir_app 用户 + mir_prod 库（docker exec -i，缺了 -i 会静默丢 stdin）"
if ssh "$HOST" "docker exec -e PGPASSWORD='$PG_SU_PASSWORD' postgres psql -U postgres -h 127.0.0.1 -tAc \"select 1 from pg_roles where rolname='mir_app'\"" | grep -q 1; then
  echo "mir_app 已存在，跳过 DDL（如需重置口令请手工 ALTER USER）"
else
  ssh "$HOST" "docker exec -i -e PGPASSWORD='$PG_SU_PASSWORD' postgres psql -U postgres -h 127.0.0.1 -v ON_ERROR_STOP=1" <<SQL
CREATE USER mir_app WITH PASSWORD '$MIR_APP_PASSWORD';
CREATE DATABASE mir_prod OWNER mir_app;
REVOKE CONNECT ON DATABASE mir_prod FROM PUBLIC;
SQL
  echo "mir_app 口令（请立即存入 /opt/mir-176-h5/shared/.env，勿提交仓库）: $MIR_APP_PASSWORD"
fi

say "5. TLS 自签证书（已存在则跳过；上域名后切 acme，见 deploy/README.md §4）"
ssh "$HOST" 'test -f /etc/nginx/ssl/mir.crt && echo "cert exists" || { sudo -n mkdir -p /etc/nginx/ssl && sudo -n openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout /etc/nginx/ssl/mir.key -out /etc/nginx/ssl/mir.crt -subj "/CN=192.168.1.12" -addext "subjectAltName=IP:192.168.1.12,DNS:dell1" && sudo -n chmod 600 /etc/nginx/ssl/mir.key && sudo -n chmod 644 /etc/nginx/ssl/mir.crt; }'

say "6. 部署根骨架 $DEPLOY_ROOT"
ssh "$HOST" "sudo -n mkdir -p $DEPLOY_ROOT/releases/bootstrap $DEPLOY_ROOT/backups $DEPLOY_ROOT/shared && sudo -n chown -R aymazon:aymazon $DEPLOY_ROOT && ln -sfn releases/bootstrap $DEPLOY_ROOT/current && ls -la $DEPLOY_ROOT"

say "DONE — 验收: ssh $HOST 'psql --version && nginx -v && uv --version && sudo -n true'"
