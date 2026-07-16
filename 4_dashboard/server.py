"""
server.py
GHOST ORACLE v5.0 :: Ajan 4 — Observer Dashboard backend.

SAF PASS-THROUGH KOPRU: Redis stream'lerini (signals / executions / cex_l2)
dinler ve gelen kaydi OLDUGU GIBI WebSocket ile tarayiciya broadcast eder.
KISIT: Veri uzerinde HICBIR matematiksel islem yapilmaz.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as redis
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# .env: repo koku + yerel (mutlak yol — CWD ne olursa olsun bulunur).
_HERE = Path(__file__).parent
load_dotenv(_HERE.parent / ".env")
load_dotenv(_HERE / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [dash] %(message)s")
log = logging.getLogger("dash")

REDIS_ADDR = os.getenv("REDIS_ADDR", "127.0.0.1:6379")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

# --- Dashboard erisim korumasi (Basic auth). DASHBOARD_PASS bos ise auth KAPALI. ---
DASH_USER = os.getenv("DASHBOARD_USER", "admin")
DASH_PASS = os.getenv("DASHBOARD_PASS", "")
AUTH_ON = bool(DASH_PASS)
WS_TOKEN = secrets.token_urlsafe(24)  # her baslangicta yeni; sayfaya enjekte edilir


def _require_auth(request: Request) -> None:
    """Basic auth kontrolu (AUTH_ON ise). Basarisizsa 401 + tarayici sifre sorar."""
    hdr = request.headers.get("Authorization", "")
    if hdr.startswith("Basic "):
        try:
            u, p = base64.b64decode(hdr[6:]).decode("utf-8", "ignore").split(":", 1)
        except Exception:
            u = p = ""
        if secrets.compare_digest(u, DASH_USER) and secrets.compare_digest(p, DASH_PASS):
            return
    raise HTTPException(status_code=401, detail="Yetkisiz",
                        headers={"WWW-Authenticate": 'Basic realm="GHOST ORACLE"'})
PORT = int(os.getenv("DASHBOARD_PORT", "8000"))
HERE = Path(__file__).parent
ENV_PATH = HERE.parent / ".env"
LIVE_STATE_KEY = "state:live"
STREAM_CONTROL = "stream:control"

ENV_ALIASES = {
    "TRADING_MODE": ("PM_EDGE_MOMENTUM_EXECUTION_MODE",),
    "ORDER_USDC": ("PM_EDGE_MOMENTUM_NOTIONAL_USDC",),
    "MAX_ORDER_USDC": ("PM_EDGE_MOMENTUM_MAX_LIVE_NOTIONAL_USDC",),
    "TX_TIMEOUT_SEC": ("PM_EDGE_CLOB_HTTP_TIMEOUT_SECONDS",),
}

# stream anahtari -> istemciye gidecek mesaj tipi


def _truthy(value: str | bool | int | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "live"}


def _normalize_mode(value: str) -> str:
    raw = str(value or "DRY_RUN").strip().upper()
    if raw in {"DRY", "DRYRUN", "DRY_RUN", "PAPER"}:
        return "DRY_RUN"
    if raw in {"LIVE", "REAL", "TRUE", "1"}:
        return "LIVE"
    return raw


def _env_get(key: str, default: str = "") -> str:
    keys = (key, *ENV_ALIASES.get(key, ()))
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
        for candidate in keys:
            for line in lines:
                if line.startswith(candidate + "="):
                    return line.split("=", 1)[1]
    for candidate in keys:
        value = os.getenv(candidate)
        if value not in (None, ""):
            return value
    return default


def _env_set(key: str, value: str) -> None:
    ENV_PATH.touch(mode=0o600, exist_ok=True)
    try:
        ENV_PATH.chmod(0o600)
    except OSError:
        pass
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    done = False
    out = []
    for line in lines:
        if line.startswith(key + "="):
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def _live_payload(armed: bool | None = None) -> dict:
    if armed is None:
        armed = _truthy(_env_get("LIVE_ARMED", "0"))
    return {
        "trading_mode": _normalize_mode(_env_get("TRADING_MODE", "DRY_RUN")),
        "live_armed": bool(armed),
        "order_usdc": float(_env_get("ORDER_USDC", _env_get("ORDER_SIZE_USDC", "1")) or 0),
        "max_order_usdc": float(_env_get("MAX_ORDER_USDC", "1") or 0),
        "max_daily_loss_usdc": float(_env_get("MAX_DAILY_LOSS_USDC", "10") or 0),
        "max_open_positions": int(float(_env_get("MAX_OPEN_POSITIONS", "1") or 0)),
    }


async def _publish_live_state(action: str, armed: bool) -> dict:
    payload = _live_payload(armed)
    client = _make_client()
    try:
        await client.hset(LIVE_STATE_KEY, mapping={
            "armed": "1" if armed else "0",
            "action": action,
            "ts": str(int(time.time() * 1000)),
            "trading_mode": payload["trading_mode"],
        })
        await client.xadd(
            STREAM_CONTROL,
            {
                "action": action,
                "armed": "1" if armed else "0",
                "trading_mode": payload["trading_mode"],
                "ts": str(int(time.time() * 1000)),
            },
            maxlen=50,
            approximate=True,
        )
    finally:
        await client.aclose()
    return payload

STREAMS = {
    "stream:synthetic": "synthetic",
    "stream:polymarket": "poly",
    "stream:pnl": "pnl",
    "stream:measure": "measure",
    "stream:straddle": "straddle",
    "stream:obicmp": "obicmp",
    "stream:trades": "trade",
    "stream:signals": "signal",
    "stream:executions": "execution",
    "stream:cex_l2": "cex",
    "stream:control": "control",
}


class ConnectionManager:
    """Bagli WebSocket istemcilerini tutar ve broadcast eder."""

    def __init__(self) -> None:
        self.active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    async def broadcast(self, message: dict) -> None:
        # Kopan soketleri toplayip ayikla.
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.discard(ws)


manager = ConnectionManager()


def _make_client() -> redis.Redis:
    host, _, port = REDIS_ADDR.partition(":")
    return redis.Redis(
        host=host or "127.0.0.1",
        port=int(port or 6379),
        password=REDIS_PASSWORD or None,
        decode_responses=True,
        max_connections=4,
    )


async def redis_pump() -> None:
    """
    Tek arka plan gorevi: 3 stream'i BLOCK ile dinler, her kaydi broadcast eder.
    Pass-through — veri hic degistirilmez, sadece {type, data} sarmalanir.
    """
    client = _make_client()
    # Sadece simdiden sonra gelenler ("$").
    last_ids = {name: "$" for name in STREAMS}
    while True:
        try:
            resp = await client.xread(last_ids, count=20, block=1000)
            if not resp:
                continue
            for stream_name, entries in resp:
                msg_type = STREAMS.get(stream_name, "unknown")
                for entry_id, fields in entries:
                    last_ids[stream_name] = entry_id
                    await manager.broadcast({"type": msg_type, "data": fields})
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("redis_pump hatasi: %s — 1s sonra yeniden", exc)
            await asyncio.sleep(1)
    await client.aclose()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(redis_pump())
    log.info("Dashboard hazir :: http://localhost:%d", PORT)
    yield
    task.cancel()


app = FastAPI(title="GHOST ORACLE Dashboard", lifespan=lifespan)


@app.get("/")
async def index(request: Request) -> HTMLResponse:
    if AUTH_ON:
        _require_auth(request)
    html = (HERE / "index.html").read_text(encoding="utf-8")
    html = html.replace("__WS_TOKEN__", WS_TOKEN)  # WS icin token enjekte et
    return HTMLResponse(html)


@app.get("/straddle")
async def straddle(request: Request) -> HTMLResponse:
    """STRADDLE LAB — cift-limit olcum sayfasi (ayri sayfa, ayni WS)."""
    if AUTH_ON:
        _require_auth(request)
    html = (HERE / "straddle.html").read_text(encoding="utf-8")
    html = html.replace("__WS_TOKEN__", WS_TOKEN)
    return HTMLResponse(html)


@app.get("/api/live/status")
async def live_status(request: Request) -> dict:
    if AUTH_ON:
        _require_auth(request)
    client = _make_client()
    armed = None
    try:
        state = await client.hgetall(LIVE_STATE_KEY)
        if state and "armed" in state:
            armed = _truthy(state.get("armed"))
    except Exception:
        armed = None
    finally:
        await client.aclose()
    return _live_payload(armed)


@app.post("/api/live/arm")
async def live_arm(request: Request) -> dict:
    if AUTH_ON:
        _require_auth(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if str(body.get("confirm", "")).strip() != "LIVE":
        raise HTTPException(status_code=400, detail="LIVE onayi gerekli")
    _env_set("LIVE_ARMED", "1")
    return await _publish_live_state("ARM_LIVE", True)


@app.post("/api/live/disarm")
async def live_disarm(request: Request) -> dict:
    if AUTH_ON:
        _require_auth(request)
    _env_set("LIVE_ARMED", "0")
    return await _publish_live_state("DISARM_LIVE", False)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    if AUTH_ON and not secrets.compare_digest(ws.query_params.get("token", ""), WS_TOKEN):
        await ws.close(code=1008)  # policy violation
        return
    await manager.connect(ws)
    # Baglanti aninda Redis durumu + islem gecmisi (stream:trades) gonder.
    client = _make_client()
    try:
        redis_ok = bool(await client.ping())
    except Exception:
        redis_ok = False
    await ws.send_json({"type": "status", "redis": redis_ok})
    try:
        # Gecmis islemleri en eskiden yeniye sirayla gonder (tablo dolsun).
        rows = await client.xrange("stream:trades", count=500)
        for _id, fields in rows:
            await ws.send_json({"type": "trade", "data": fields})
        # Son STRADDLE snapshot (sayfa bos acilmasin).
        srows = await client.xrevrange("stream:straddle", count=1)
        for _id, fields in srows:
            await ws.send_json({"type": "straddle", "data": fields})
    except Exception:
        pass
    finally:
        await client.aclose()

    try:
        # Istemciden veri beklemiyoruz; baglanti acik kalsin diye dinle.
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
