#!/usr/bin/env bash
# =============================================================
#  GHOST ORACLE v5.0 :: VPS one-command deploy/run
#
#  Common use on VPS:
#    bash deploy/run.sh --pull
#
#  What it does:
#    - optionally git pull origin/main
#    - starts Docker Redis (ghost-redis)
#    - runs Go/Python smoke tests unless disabled
#    - builds 1_ingestion_agents/ghost-ingestion
#    - restarts tmux session "ghost" with ingest/brain/exec/dash windows
#    - prints health checks and useful follow-up commands
#
#  Options:
#    --pull        git pull origin <current-branch> before build
#    --install     install/update Python requirements before run
#    --no-tests    skip Go/Python tests
#    --no-exec     do not start 3_execution_agent
#    --status      only print current health/status
#    --stop        stop tmux session only
#    --help        show usage
# =============================================================
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="${GHOST_TMUX_SESSION:-ghost}"
REDIS_CONTAINER="${REDIS_CONTAINER:-ghost-redis}"
DASHBOARD_URL="${DASHBOARD_URL:-http://45.155.125.155:8000}"

DO_PULL=0
DO_INSTALL=0
DO_TESTS=1
DO_EXEC=1
ONLY_STATUS=0
ONLY_STOP=0

usage() {
  sed -n '1,36p' "$0"
}

log() { printf '\n>>> %s\n' "$*"; }
warn() { printf '\n!!! %s\n' "$*" >&2; }
need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    warn "Missing command: $1"
    exit 1
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --pull) DO_PULL=1 ;;
    --install) DO_INSTALL=1 ;;
    --no-tests) DO_TESTS=0 ;;
    --no-exec) DO_EXEC=0 ;;
    --status) ONLY_STATUS=1 ;;
    --stop) ONLY_STOP=1 ;;
    --help|-h) usage; exit 0 ;;
    *) warn "Unknown option: $1"; usage; exit 1 ;;
  esac
  shift
done

cd "$ROOT"
export PATH="$PATH:/usr/local/go/bin"

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    warn "Docker Compose is missing. Install Docker with compose plugin."
    exit 1
  fi
}

py_for() {
  local d="$1"
  if [ -x "$ROOT/$d/.venv/bin/python" ]; then
    printf '%s\n' "$ROOT/$d/.venv/bin/python"
  elif [ -x "$ROOT/.venv/bin/python" ]; then
    printf '%s\n' "$ROOT/.venv/bin/python"
  else
    printf '%s\n' "python3"
  fi
}

pip_for() {
  local d="$1"
  if [ -x "$ROOT/$d/.venv/bin/pip" ]; then
    printf '%s\n' "$ROOT/$d/.venv/bin/pip"
  elif [ -x "$ROOT/.venv/bin/pip" ]; then
    printf '%s\n' "$ROOT/.venv/bin/pip"
  else
    printf '%s\n' "python3 -m pip"
  fi
}

redis_cli() {
  if command -v redis-cli >/dev/null 2>&1; then
    redis-cli "$@"
  else
    docker exec "$REDIS_CONTAINER" redis-cli "$@"
  fi
}

random_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 24 | tr -d '\n'
  else
    python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(24), end='')
PY
  fi
}

set_env_kv() {
  local key="$1"
  local value="$2"
  local file="$ROOT/.env"
  touch "$file"
  chmod 600 "$file" 2>/dev/null || true
  if grep -q "^${key}=" "$file"; then
    python3 - "$file" "$key" "$value" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text(encoding='utf-8').splitlines()
for i, line in enumerate(lines):
    if line.startswith(key + '='):
        lines[i] = f'{key}={value}'
        break
path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
PY
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

get_env_kv() {
  local key="$1"
  local file="$ROOT/.env"
  [ -f "$file" ] || return 1
  grep -E "^${key}=" "$file" | tail -n 1 | cut -d= -f2-
}


get_env_any() {
  local primary="$1"
  shift
  local value
  value="$(get_env_kv "$primary" || true)"
  if [ -n "$value" ]; then
    printf '%s\n' "$value"
    return 0
  fi
  for key in "$@"; do
    value="$(get_env_kv "$key" || true)"
    if [ -n "$value" ]; then
      printf '%s\n' "$value"
      return 0
    fi
  done
  return 1
}

normalize_mode() {
  local raw
  raw="$(printf '%s' "${1:-DRY_RUN}" | tr '[:lower:]' '[:upper:]')"
  case "$raw" in
    DRY|DRYRUN|DRY_RUN|PAPER) printf 'DRY_RUN\n' ;;
    LIVE|REAL|TRUE|1) printf 'LIVE\n' ;;
    *) printf '%s\n' "$raw" ;;
  esac
}

ensure_dashboard_auth() {
  local user pass generated
  user="$(get_env_kv DASHBOARD_USER || true)"
  pass="$(get_env_kv DASHBOARD_PASS || true)"
  generated=0
  if [ -z "$user" ]; then
    user="admin"
    set_env_kv DASHBOARD_USER "$user"
  fi
  if [ -z "$pass" ]; then
    pass="$(random_secret)"
    set_env_kv DASHBOARD_PASS "$pass"
    generated=1
  fi
  log "Dashboard auth"
  echo "User: $user"
  if [ "$generated" -eq 1 ]; then
    echo "Password generated and saved to $ROOT/.env"
    echo "Password: $pass"
  else
    echo "Password: configured in $ROOT/.env"
  fi
}



ensure_python_envs() {
  local d pip created
  for d in 2_brain_engine 3_execution_agent 4_dashboard; do
    created=0
    if [ ! -x "$ROOT/$d/.venv/bin/python" ]; then
      log "Creating Python venv: $d/.venv"
      python3 -m venv "$ROOT/$d/.venv"
      created=1
    fi
    pip="$ROOT/$d/.venv/bin/pip"
    if [ "$created" -eq 1 ] || [ "$DO_INSTALL" -eq 1 ]; then
      log "Installing Python requirements: $d"
      "$pip" install -q --upgrade pip
      if [ -f "$ROOT/$d/requirements.txt" ]; then
        "$pip" install -q -r "$ROOT/$d/requirements.txt"
      fi
    fi
  done
}

ensure_live_defaults() {
  local mode armed
  mode="$(get_env_any TRADING_MODE PM_EDGE_MOMENTUM_EXECUTION_MODE || true)"
  mode="$(normalize_mode "${mode:-DRY_RUN}")"
  armed="$(get_env_kv LIVE_ARMED || true)"
  if [ -z "$mode" ]; then
    mode="DRY_RUN"
    set_env_kv TRADING_MODE "$mode"
  fi
  if [ -z "$armed" ]; then
    armed="0"
    set_env_kv LIVE_ARMED "$armed"
  fi
  redis_cli HSET state:live armed "$armed" action DEPLOY_SYNC trading_mode "$mode" ts "$(date +%s%3N)" >/dev/null || true
  redis_cli XADD stream:control MAXLEN '~' 50 '*' action DEPLOY_SYNC armed "$armed" trading_mode "$mode" ts "$(date +%s%3N)" >/dev/null || true
  log "Live safety"
  echo "TRADING_MODE=$mode"
  echo "LIVE_ARMED=$armed"
}

print_status() {
  log "Docker Redis"
  docker ps --filter "name=$REDIS_CONTAINER" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' || true
  redis_cli ping || true

  log "tmux sessions"
  tmux ls 2>/dev/null || true

  log "processes"
  ps aux | grep -Ei 'ghost-ingestion|main_brain.py|main_execution.py|server.py' | grep -v grep || true

  log "ports"
  ss -ltnp 2>/dev/null | grep -E '6379|8000|8787|5000' || true

  log "Redis streams"
  for s in stream:cex_l2 stream:synthetic stream:trades stream:polymarket stream:signals stream:executions; do
    printf '%-22s ' "$s"
    redis_cli XLEN "$s" 2>/dev/null || true
  done
}

stop_session() {
  log "Stopping tmux session: $SESSION"
  tmux kill-session -t "$SESSION" 2>/dev/null || true
}

if [ "$ONLY_STOP" -eq 1 ]; then
  need tmux
  stop_session
  exit 0
fi

if [ "$ONLY_STATUS" -eq 1 ]; then
  need docker
  need tmux
  print_status
  exit 0
fi

need git
need docker
need tmux
need go
need python3

log "Repo: $ROOT"

if [ "$DO_PULL" -eq 1 ]; then
  branch="$(git branch --show-current)"
  if [ -z "$branch" ]; then
    warn "Cannot detect current git branch."
    exit 1
  fi
  log "Pulling origin/$branch"
  git pull origin "$branch"
fi

log "Starting Docker Redis"
compose up -d redis-in-memory

log "Waiting for Redis health"
for i in $(seq 1 20); do
  if redis_cli ping 2>/dev/null | grep -q PONG; then
    echo "Redis: PONG"
    break
  fi
  sleep 1
  if [ "$i" -eq 20 ]; then
    warn "Redis did not become ready. Check: docker logs $REDIS_CONTAINER"
    exit 1
  fi
done

ensure_python_envs

ensure_dashboard_auth
ensure_live_defaults

log "Building Go ingestion"
( cd "$ROOT/1_ingestion_agents" && go mod tidy && go build -o ghost-ingestion . )

if [ "$DO_TESTS" -eq 1 ]; then
  log "Running Go tests"
  ( cd "$ROOT/1_ingestion_agents" && go test ./... )

  log "Running Python tests"
  brain_py="$(py_for 2_brain_engine)"
  dash_py="$(py_for 4_dashboard)"
  ( cd "$ROOT/2_brain_engine" && "$brain_py" -m unittest discover -p "test_*.py" && "$brain_py" -m py_compile *.py )
  ( cd "$ROOT/4_dashboard" && "$dash_py" -m unittest discover -p "test_*.py" && "$dash_py" -m py_compile *.py )
  if [ "$DO_EXEC" -eq 1 ]; then
    exec_py="$(py_for 3_execution_agent)"
    ( cd "$ROOT/3_execution_agent" && "$exec_py" -m unittest discover -p "test_*.py" && "$exec_py" -m py_compile *.py )
  fi
fi

log "Restarting tmux session: $SESSION"
stop_session

tmux new-session -d -s "$SESSION" -c "$ROOT/1_ingestion_agents" -n ingest
tmux send-keys -t "$SESSION:ingest" 'export PATH=$PATH:/usr/local/go/bin; ./ghost-ingestion' C-m

brain_py="$(py_for 2_brain_engine)"
tmux new-window -t "$SESSION" -c "$ROOT/2_brain_engine" -n brain
tmux send-keys -t "$SESSION:brain" "'$brain_py' main_brain.py" C-m

if [ "$DO_EXEC" -eq 1 ]; then
  exec_py="$(py_for 3_execution_agent)"
  tmux new-window -t "$SESSION" -c "$ROOT/3_execution_agent" -n exec
  tmux send-keys -t "$SESSION:exec" "'$exec_py' main_execution.py" C-m
fi

dash_py="$(py_for 4_dashboard)"
tmux new-window -t "$SESSION" -c "$ROOT/4_dashboard" -n dash
tmux send-keys -t "$SESSION:dash" "'$dash_py' server.py" C-m

sleep 3
print_status

log "READY"
echo "Attach:    tmux attach -t $SESSION"
echo "Detach:    Ctrl+b then d"
echo "Stop:      bash deploy/run.sh --stop"
echo "Status:    bash deploy/run.sh --status"
echo "Dashboard: $DASHBOARD_URL"
