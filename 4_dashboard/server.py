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
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as redis
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# .env: kok dizin (diger ajanlarla ayni desen), sonra yerel.
load_dotenv("../.env")
load_dotenv(".env")

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

# stream anahtari -> istemciye gidecek mesaj tipi
STREAMS = {
    "stream:synthetic": "synthetic",
    "stream:polymarket": "poly",
    "stream:pnl": "pnl",
    "stream:trades": "trade",
    "stream:signals": "signal",
    "stream:executions": "execution",
    "stream:cex_l2": "cex",
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
        rows = await client.xrange("stream:trades", count=50)
        for _id, fields in rows:
            await ws.send_json({"type": "trade", "data": fields})
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
