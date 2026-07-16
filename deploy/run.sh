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

if [ "$DO_INSTALL" -eq 1 ]; then
  log "Installing Python requirements"
  for d in 2_brain_engine 3_execution_agent 4_dashboard; do
    pip_cmd="$(pip_for "$d")"
    if [ -f "$ROOT/$d/requirements.txt" ]; then
      # shellcheck disable=SC2086
      $pip_cmd install -r "$ROOT/$d/requirements.txt"
    fi
  done
fi

log "Building Go ingestion"
( cd "$ROOT/1_ingestion_agents" && go mod tidy && go build -o ghost-ingestion . )

if [ "$DO_TESTS" -eq 1 ]; then
  log "Running Go tests"
  ( cd "$ROOT/1_ingestion_agents" && go test ./... )

  log "Running Python tests"
  brain_py="$(py_for 2_brain_engine)"
  dash_py="$(py_for 4_dashboard)"
  ( cd "$ROOT/2_brain_engine" && "$brain_py" -m unittest test_paper_trader.py && "$brain_py" -m py_compile *.py )
  ( cd "$ROOT/4_dashboard" && "$dash_py" -m unittest test_dashboard_contract.py && "$dash_py" -m py_compile *.py )
  if [ "$DO_EXEC" -eq 1 ]; then
    exec_py="$(py_for 3_execution_agent)"
    ( cd "$ROOT/3_execution_agent" && "$exec_py" -m py_compile *.py )
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
