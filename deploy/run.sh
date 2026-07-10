#!/usr/bin/env bash
# =============================================================
#  GHOST ORACLE v5.0 :: 4 ajani tmux'ta baslatir (DRY_RUN).
#  Calistir:  bash deploy/run.sh
#  Izle:      tmux attach -t ghost   (pencereler: ingest/brain/exec/dash)
#  Durdur:    tmux kill-session -t ghost
# =============================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

tmux kill-session -t ghost 2>/dev/null || true

tmux new-session -d -s ghost -c "$ROOT/1_ingestion_agents" -n ingest
tmux send-keys -t ghost:ingest 'export PATH=$PATH:/usr/local/go/bin; ./ghost-ingestion' C-m

tmux new-window -t ghost -c "$ROOT/2_brain_engine" -n brain
tmux send-keys -t ghost:brain '.venv/bin/python main_brain.py' C-m

tmux new-window -t ghost -c "$ROOT/3_execution_agent" -n exec
tmux send-keys -t ghost:exec '.venv/bin/python main_execution.py' C-m

tmux new-window -t ghost -c "$ROOT/4_dashboard" -n dash
tmux send-keys -t ghost:dash '.venv/bin/python server.py' C-m

echo ">>> 4 ajan tmux 'ghost' oturumunda calisiyor."
echo "    Izle:  tmux attach -t ghost   (Ctrl+b n ile pencere gec)"
echo "    Dashboard: http://45.155.125.155:8000  (VPS firewall'da 8000 acik olmali)"
