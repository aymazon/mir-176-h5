#!/usr/bin/env bash
# M0 ④ restart-persistence gate (W1T8).
#
# Flow:
#   1. fail fast if 127.0.0.1:8000 is already in use
#   2. start uvicorn (SAVE_INTERVAL_S=2 so the W1T5 online flush ticks fast
#      enough to persist movement during this short test; PID saved)
#   3. python phase "before": unique account + warrior, WS in, walk
#      (150,150) -> (146,150), wait until the DB holds the moved position,
#      record `before`, then keep the WS open (character ONLINE)
#   4. SIGTERM the server while the character is still connected:
#      graceful shutdown runs persist_worker.stop() + close_pool (W1T5)
#   5. restart uvicorn, wait for /api/health
#   6. python phase "verify": re-login, read the character back from
#      char_ready as `after`, assert before == after (diff empty)
#   7. write scripts/artifacts/restart_test.json, exit 0 only when diff is {}
#
# Run from anywhere (repo layout is resolved from this file's location).
# The script leaves no process behind on success or failure: the EXIT trap
# kills the uvicorn it started by saved PID and verifies port 8000 is closed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"
HOST=127.0.0.1
PORT=8000
PYTHON="${BACKEND_DIR}/.venv/bin/python"
STATE_FILE="/tmp/test_restart_state.json"
LOG="/tmp/test_restart_uvicorn.log"
BEFORE_LOG="/tmp/test_restart_before.log"
UVICORN_PID=""
BEFORE_PID=""

log() { echo "restart-gate: $*" >&2; }

die() { log "ERROR: $*"; exit 1; }

port_in_use() {
  "${PYTHON}" - "${PORT}" <<'EOF'
import socket, sys
sock = socket.socket()
sock.settimeout(0.5)
try:
    sys.exit(0 if sock.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
finally:
    sock.close()
EOF
}

health_ok() {
  "${PYTHON}" - <<'EOF'
import httpx
try:
    response = httpx.get("http://127.0.0.1:8000/api/health", timeout=1.0)
    raise SystemExit(0 if response.json() == {"ok": True} else 1)
except SystemExit:
    raise
except Exception:
    raise SystemExit(1)
EOF
}

listener_pid() {
  # PID of the process currently listening on the port (robust against the
  # nohup wrapper: $! is not always the server process itself).
  ss -ltnp 2>/dev/null | awk -v port=":${PORT}$" '
    $4 ~ port {
      if (match($0, /pid=[0-9]+/)) {
        print substr($0, RSTART + 4, RLENGTH - 4)
        exit
      }
    }'
}

wait_for_health() {
  local deadline=$((SECONDS + 30))
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    if health_ok; then
      return 0
    fi
    sleep 0.2
  done
  tail -n 20 "${LOG}" >&2 || true
  die "uvicorn did not become healthy within 30s (log above)"
}

start_server() {
  (
    cd "${BACKEND_DIR}" && \
    SAVE_INTERVAL_S=2 setsid nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 \
      < /dev/null > "${LOG}" 2>&1 &
  )
  wait_for_health
  UVICORN_PID="$(listener_pid)"
  if [ -z "${UVICORN_PID}" ]; then
    tail -n 20 "${LOG}" >&2 || true
    die "could not identify the uvicorn PID listening on port ${PORT}"
  fi
  log "uvicorn running (pid ${UVICORN_PID})"
}

stop_server() {
  # Kill ONLY the uvicorn this script started, by saved PID (never pkill -f:
  # the pattern would self-match this shell's own command line).
  if [ -z "${UVICORN_PID}" ]; then
    return 0
  fi
  if kill -0 "${UVICORN_PID}" 2>/dev/null; then
    kill -TERM "${UVICORN_PID}" 2>/dev/null || true
    local deadline=$((SECONDS + 15))
    while kill -0 "${UVICORN_PID}" 2>/dev/null; do
      if [ "${SECONDS}" -ge "${deadline}" ]; then
        log "uvicorn ${UVICORN_PID} ignored SIGTERM for 15s; sending SIGKILL"
        kill -KILL "${UVICORN_PID}" 2>/dev/null || true
        break
      fi
      sleep 0.1
    done
  fi
  UVICORN_PID=""
  if port_in_use; then
    log "WARNING: port ${PORT} is still in use after stopping our uvicorn"
  fi
}

cleanup() {
  local status=$?
  if [ -n "${BEFORE_PID}" ] && kill -0 "${BEFORE_PID}" 2>/dev/null; then
    kill -KILL "${BEFORE_PID}" 2>/dev/null || true
  fi
  stop_server
  exit "${status}"
}
trap cleanup EXIT

# --- 1) fail fast on a busy port --------------------------------------------
if port_in_use; then
  die "port ${PORT} is already in use; stop the existing server first"
fi

rm -f "${STATE_FILE}"

# --- 2) start server, wait for health ---------------------------------------
start_server
wait_for_health
log "server healthy"

# --- 3) phase "before" (character walks, DB confirms, WS stays open) --------
"${PYTHON}" "${SCRIPT_DIR}/test_restart.py" before > "${BEFORE_LOG}" 2>&1 &
BEFORE_PID=$!
  log "phase before running (pid ${BEFORE_PID})"
  deadline=$((SECONDS + 90))
  while [ ! -f "${STATE_FILE}" ]; do
    if ! kill -0 "${BEFORE_PID}" 2>/dev/null; then
      cat "${BEFORE_LOG}" >&2
      die "phase before exited early (log above)"
    fi
    if [ "${SECONDS}" -ge "${deadline}" ]; then
      kill -KILL "${BEFORE_PID}" 2>/dev/null || true
      cat "${BEFORE_LOG}" >&2
      die "phase before did not record state within 90s (log above)"
    fi
    sleep 0.1
  done
log "before recorded: $(cat "${STATE_FILE}")"

# --- 4) SIGTERM while the character is still ONLINE --------------------------
log "sending SIGTERM to uvicorn ${UVICORN_PID} (character still connected)"
kill -TERM "${UVICORN_PID}"
deadline=$((SECONDS + 15))
while kill -0 "${UVICORN_PID}" 2>/dev/null; do
  if [ "${SECONDS}" -ge "${deadline}" ]; then
    kill -KILL "${UVICORN_PID}" 2>/dev/null || true
    tail -n 20 "${LOG}" >&2 || true
    die "uvicorn did not exit within 15s of SIGTERM (log above)"
  fi
  sleep 0.1
done
log "uvicorn exited after SIGTERM"
UVICORN_PID=""

# The before-phase process must exit once the server closed its WS; bound the wait.
deadline=$((SECONDS + 15))
while kill -0 "${BEFORE_PID}" 2>/dev/null; do
  if [ "${SECONDS}" -ge "${deadline}" ]; then
    kill -KILL "${BEFORE_PID}" 2>/dev/null || true
    die "phase before process still alive 15s after server stop"
  fi
  sleep 0.1
done
wait "${BEFORE_PID}" 2>/dev/null || {
  cat "${BEFORE_LOG}" >&2
  die "phase before process exited non-zero (log above)"
}

# --- 5) restart server -------------------------------------------------------
start_server
wait_for_health
log "restarted server healthy"

# --- 6) phase "verify" -------------------------------------------------------
if ! "${PYTHON}" "${SCRIPT_DIR}/test_restart.py" verify; then
  cat "${BEFORE_LOG}" >&2 || true
  die "verify phase failed"
fi

# --- 7) clean stop, verify the port is closed --------------------------------
stop_server
exit 0
