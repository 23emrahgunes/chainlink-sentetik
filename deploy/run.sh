#!/usr/bin/env bash
# =============================================================
#  GHOST ORACLE v5.0 :: TEK KOMUTLA tum sistemi (yeniden) baslatir.
#  Calistir:  bash deploy/run.sh
#  - Redis'i garantiler (docker compose)
#  - Go ingestion binary yoksa derler
#  - Eski tmux 'ghost' oturumunu kapatir
#  - 4 ajani temiz baslatir (ingest/brain/exec/dash) DRY_RUN
#
#  Izle:    tmux attach -t ghost     (Ctrl+b sonra n = pencere gec)
#  Durdur:  tmux kill-session -t ghost
# =============================================================
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH=$PATH:/usr/local/go/bin

echo ">>> [1/4] Redis (in-memory) kontrol"
docker compose up -d

echo ">>> [2/4] Go ingestion binary kontrol"
if [ ! -x 1_ingestion_agents/ghost-ingestion ]; then
  echo "    binary yok — derleniyor..."
  ( cd 1_ingestion_agents && go mod tidy && go build -o ghost-ingestion . )
fi

echo ">>> [3/4] Eski tmux oturumu kapatiliyor (varsa)"
tmux kill-session -t ghost 2>/dev/null || true

echo ">>> [4/4] 4 ajan 'ghost' oturumunda baslatiliyor"
tmux new-session  -d -s ghost -c "$ROOT/1_ingestion_agents" -n ingest
tmux send-keys    -t ghost:ingest 'export PATH=$PATH:/usr/local/go/bin; ./ghost-ingestion' C-m

tmux new-window   -t ghost -c "$ROOT/2_brain_engine" -n brain
tmux send-keys    -t ghost:brain '.venv/bin/python main_brain.py' C-m

tmux new-window   -t ghost -c "$ROOT/3_execution_agent" -n exec
tmux send-keys    -t ghost:exec '.venv/bin/python main_execution.py' C-m

tmux new-window   -t ghost -c "$ROOT/4_dashboard" -n dash
tmux send-keys    -t ghost:dash '.venv/bin/python server.py' C-m

echo ""
echo ">>> HAZIR. 4 ajan calisiyor (DRY_RUN)."
echo "    Izle:      tmux attach -t ghost   (Ctrl+b sonra n = pencere gec)"
echo "    Dashboard: http://45.155.125.155:8000"
echo "    Durdur:    tmux kill-session -t ghost"
