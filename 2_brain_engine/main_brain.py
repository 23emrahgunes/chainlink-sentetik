"""
main_brain.py
GHOST ORACLE v5.0 :: Ajan 2 Orkestrator.

.env -> TRADING_MODE okur -> asyncio event loop -> redis_consumer akisini
obi_matrix, synthetic_oracle, trigger_logic'e sirasiyla besler.
Graceful shutdown (SIGINT/SIGTERM; Windows'ta KeyboardInterrupt fallback).

KISIT: Agir matematik yalnizca NumPy. Ara array'ler dongu-yerel kalir (GC).
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

import numpy as np
from dotenv import load_dotenv

from obi_matrix import compute_obi
from polymarket_feed import PolyFeed
from redis_consumer import RedisConsumer
from spread_model import cross_spread
from synthetic_oracle import compute_pcex
from trigger_logic import evaluate

# .env: once kok dizin (Ajan 1 ile ayni desen), sonra yerel.
load_dotenv("../.env")
load_dotenv(".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("brain.main")


def _f(field: dict, key: str) -> float:
    """Alani guvenli float'a cevir."""
    try:
        return float(field.get(key, 0.0))
    except (TypeError, ValueError):
        return 0.0


async def run(stop: asyncio.Event) -> None:
    mode = os.getenv("TRADING_MODE", "DRY_RUN")
    addr = os.getenv("REDIS_ADDR", "127.0.0.1:6379")
    password = os.getenv("REDIS_PASSWORD", "")
    obi_thr = float(os.getenv("OBI_THRESHOLD", "0.6"))
    spread_thr = float(os.getenv("SPREAD_THRESHOLD", "0.0"))
    # Polymarket fair-value esleme parametreleri (PLACEHOLDER model, bkz spread_model.py).
    pm_base = float(os.getenv("PM_FAIR_BASE", "0.0"))
    pm_scale = float(os.getenv("PM_FAIR_SCALE", "0.0"))

    log.info("=== GHOST ORACLE v5.0 :: Analytical Brain ===")
    log.info("TRADING_MODE = %s", mode)

    consumer = RedisConsumer(addr, password)
    if not await consumer.ping():
        log.error("[REDIS] ping BASARISIZ @ %s (docker compose up -d?)", addr)
        return
    log.info("[REDIS] baglandi @ %s | OBI esik=%.2f spread esik=%.5f",
             addr, obi_thr, spread_thr)

    # Polymarket fiyat beslemesi (arka plan; token yoksa stream bos kalir, sorunsuz).
    poly = PolyFeed(consumer.client)
    poly_task = asyncio.ensure_future(poly.run(stop))

    stream = consumer.stream()
    try:
        while not stop.is_set():
            # Akistan bir sonraki taze kaydi al; stop ile yarisa sok.
            next_task = asyncio.ensure_future(stream.__anext__())
            stop_task = asyncio.ensure_future(stop.wait())
            done, _pending = await asyncio.wait(
                {next_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if stop_task in done:
                next_task.cancel()
                break
            stop_task.cancel()

            try:
                field = next_task.result()
            except StopAsyncIteration:
                break

            # --- NumPy vektorel hesap (dongu-yerel array'ler) ---
            bid_p, bid_q = _f(field, "bid_p"), _f(field, "bid_q")
            ask_p, ask_q = _f(field, "ask_p"), _f(field, "ask_q")

            bid_vols = np.array([bid_q], dtype=np.float64)
            ask_vols = np.array([ask_q], dtype=np.float64)
            prices = np.array([bid_p, ask_p], dtype=np.float64)
            vols = np.array([bid_q, ask_q], dtype=np.float64)

            obi = compute_obi(bid_vols, ask_vols)
            p_cex = compute_pcex(prices, vols)

            # --- Spread makasi: Polymarket taze ise gercek capraz-pazar, degilse proxy ---
            p_poly, poly_fresh = poly.snapshot(max_stale_ms=2000)
            if poly_fresh:
                spread = abs(cross_spread(p_cex, p_poly, pm_base, pm_scale))
                spread_src = f"PM(mid={p_poly:.3f})"
            else:
                mid = (ask_p + bid_p) / 2.0            # intra-book fallback
                spread = (ask_p - bid_p) / mid if mid > 0 else 0.0
                spread_src = "intrabook"

            log.info("P_cex=%.4f | OBI=%+.3f | spread=%.5f [%s] | src=%s",
                     p_cex, obi, spread, spread_src, field.get("src", "?"))

            await evaluate(p_cex, obi, spread, obi_thr, spread_thr, consumer.client)
    finally:
        poly_task.cancel()
        await stream.aclose()
        await consumer.close()
        log.info("[BRAIN] tuketici kapatildi. Atilan(bayat) kayit: %d",
                 consumer.dropped)


def _install_signals(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    def _trigger() -> None:
        log.info("[BRAIN] kapatma sinyali alindi, durduruluyor...")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _trigger)
        except NotImplementedError:
            # Windows: add_signal_handler yok -> KeyboardInterrupt ile yakalanir.
            pass


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop = asyncio.Event()
    _install_signals(loop, stop)
    try:
        loop.run_until_complete(run(stop))
    except KeyboardInterrupt:
        log.info("[BRAIN] KeyboardInterrupt — kapaniyor.")
    finally:
        loop.close()
        sys.exit(0)


if __name__ == "__main__":
    main()
