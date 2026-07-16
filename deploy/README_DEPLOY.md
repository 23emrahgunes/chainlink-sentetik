# GHOST ORACLE VPS Deploy

Use this on the VPS from the repo root.

## First-time or after a GitHub update

```bash
cd ~/chainlink-sentetik
git pull origin main
bash deploy/run.sh
```

After this update, future runs can do pull + build + restart in one command:

```bash
bash deploy/run.sh --pull
```

## What `run.sh` does

- Starts Docker Redis container `ghost-redis` from `docker-compose.yml`.
- Waits until Redis replies `PONG`.
- Builds `1_ingestion_agents/ghost-ingestion`.
- Runs Go/Python smoke tests by default.
- Restarts tmux session `ghost`.
- Starts these windows:
  - `ingest`: Go ingestion binary
  - `brain`: `2_brain_engine/main_brain.py`
  - `exec`: `3_execution_agent/main_execution.py`
  - `dash`: `4_dashboard/server.py`
- Prints process, port, and Redis stream status.

## Useful commands

```bash
bash deploy/run.sh --status
bash deploy/run.sh --stop
tmux attach -t ghost
```
If you are inside a subdirectory, either return to the repo root first or call the script with the correct relative path:

```bash
cd ~/chainlink-sentetik
bash deploy/run.sh --status
# or from 4_dashboard:
bash ../deploy/run.sh --status
```


Detach from tmux with `Ctrl+b`, then `d`.

## Optional modes

```bash
bash deploy/run.sh --pull --install
bash deploy/run.sh --pull --no-tests
bash deploy/run.sh --pull --no-exec
```

`--install` installs Python requirements before restart. Use it after dependency changes.


## Dashboard password

`run.sh` protects the public dashboard with Basic Auth. On the first run it creates these values in the VPS-local `.env` file:

```bash
DASHBOARD_USER=admin
DASHBOARD_PASS=<generated-password>
```

The generated password is printed once during the first run. To change it later:

```bash
cd ~/chainlink-sentetik
python3 - <<'PY'
from pathlib import Path
p = Path('.env')
lines = p.read_text().splitlines()
out = []
for line in lines:
    if line.startswith('DASHBOARD_PASS='):
        out.append('DASHBOARD_PASS=your-new-password')
    else:
        out.append(line)
p.write_text('\n'.join(out) + '\n')
PY
bash deploy/run.sh --no-tests
```


## Live mode safety

The deploy runner syncs `.env` into Redis on every start. If `LIVE_ARMED` is missing, it writes `LIVE_ARMED=0` and keeps the bot disarmed.

Minimum live configuration on the VPS:

```env
TRADING_MODE=LIVE
LIVE_ARMED=0
ORDER_USDC=1
MAX_ORDER_USDC=1
MAX_DAILY_LOSS_USDC=10
MAX_OPEN_POSITIONS=1
SIGNAL_MAX_STALE_MS=2000
SLIPPAGE_THRESHOLD=0.01
TX_TIMEOUT_SEC=3
```

After the system is running, use the dashboard button to arm/disarm live trading. Do not set `LIVE_ARMED=1` manually unless you intentionally want the bot armed immediately after deploy.

## Redis note

This repo uses Docker Redis (`ghost-redis`). Do not start Ubuntu's `redis-server.service` on the same port, because Docker already owns `6379`.
