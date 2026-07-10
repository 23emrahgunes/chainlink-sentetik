#!/usr/bin/env bash
# =============================================================
#  GHOST ORACLE v5.0 :: VPS bootstrap (Ubuntu 24.04, root)
#  Docker + Go 1.22 + Python venv kurar, Redis'i ayaga kaldirir,
#  Go ingestion'i derler, Python venv'leri hazirlar.
#  Calistir:  bash deploy/bootstrap.sh
# =============================================================
set -euo pipefail
cd "$(dirname "$0")/.."            # repo koku
ROOT="$(pwd)"
echo ">>> GHOST ORACLE bootstrap @ $ROOT"

echo "[1/6] apt bagimliliklari"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl git tmux python3 python3-venv python3-pip

echo "[2/6] Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

echo "[3/6] Go 1.22"
if ! command -v go >/dev/null 2>&1 && [ ! -x /usr/local/go/bin/go ]; then
  GOVER=1.22.5
  curl -fsSL "https://go.dev/dl/go${GOVER}.linux-amd64.tar.gz" -o /tmp/go.tgz
  rm -rf /usr/local/go && tar -C /usr/local -xzf /tmp/go.tgz
  grep -q '/usr/local/go/bin' /root/.bashrc || echo 'export PATH=$PATH:/usr/local/go/bin' >> /root/.bashrc
fi
export PATH=$PATH:/usr/local/go/bin

echo "[4/6] Redis (docker compose, zero-disk)"
docker compose up -d
docker compose ps

echo "[5/6] Go ingestion derleme"
( cd 1_ingestion_agents && go mod tidy && go build -o ghost-ingestion . )
echo "    -> 1_ingestion_agents/ghost-ingestion hazir"

echo "[6/6] Python venv'ler + bagimliliklar"
for d in 2_brain_engine 3_execution_agent 4_dashboard; do
  python3 -m venv "$ROOT/$d/.venv"
  "$ROOT/$d/.venv/bin/pip" install -q --upgrade pip
  "$ROOT/$d/.venv/bin/pip" install -q -r "$ROOT/$d/requirements.txt"
  echo "    -> $d venv hazir"
done

echo ""
echo ">>> BOOTSTRAP TAMAM. Servisleri baslatmak icin: bash deploy/run.sh"
