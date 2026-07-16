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

Detach from tmux with `Ctrl+b`, then `d`.

## Optional modes

```bash
bash deploy/run.sh --pull --install
bash deploy/run.sh --pull --no-tests
bash deploy/run.sh --pull --no-exec
```

`--install` installs Python requirements before restart. Use it after dependency changes.

## Redis note

This repo uses Docker Redis (`ghost-redis`). Do not start Ubuntu's `redis-server.service` on the same port, because Docker already owns `6379`.
