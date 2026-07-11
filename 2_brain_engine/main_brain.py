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
import time

import numpy as np
from dotenv import load_dotenv

from polymarket_feed import PolyFeed
from redis_consumer import RedisConsumer
from synthetic_oracle import compute_pcex
from trigger_logic import emit

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
    fixed_strike = float(os.getenv("PRICE_TO_BEAT", "0"))     # >0 ise sabit; 0 ise dinamik pencere
    window_sec = int(os.getenv("UPDOWN_WINDOW_SEC", "300"))   # 5dk = 300
    move_band = float(os.getenv("SIGNAL_MOVE_BAND", "0.0001"))  # acilis etrafinda olu bolge (%0.01)
    signal_cooldown_ms = float(os.getenv("SIGNAL_COOLDOWN_MS", "2000"))
    quote_ttl_ms = float(os.getenv("QUOTE_TTL_MS", "4000"))  # borsa "taze" sayilma penceresi

    log.info("=== GHOST ORACLE v5.0 :: Analytical Brain ===")
    log.info("TRADING_MODE = %s", mode)

    consumer = RedisConsumer(addr, password)
    if not await consumer.ping():
        log.error("[REDIS] ping BASARISIZ @ %s (docker compose up -d?)", addr)
        return
    strike_mode = "sabit" if fixed_strike > 0 else "dinamik(%ddk pencere)" % (window_sec // 60)
    log.info("[REDIS] baglandi @ %s | price-to-beat=%s | move band=%%%.3f",
             addr, strike_mode, move_band * 100)

    # Polymarket fiyat beslemesi (arka plan; token yoksa stream bos kalir, sorunsuz).
    poly = PolyFeed(consumer.client)
    poly_task = asyncio.ensure_future(poly.run(stop))

    # 5 borsanin en son kotasyonu (src -> quote). Sentetik kuresel fiyat icin.
    quotes: dict[str, dict] = {}
    last_synth_pub = 0.0
    last_dir = ""          # sinyal spam onleme: son yon
    last_emit_ms = 0.0     # son sinyal zamani
    cur_win = -1           # aktif 5dk pencere indeksi
    strike_dyn = 0.0       # pencere acilis fiyati (dinamik price-to-beat)

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

            # --- Bu borsanin son kotasyonunu kaydet ---
            src = field.get("src", "?")
            bid_p, bid_q = _f(field, "bid_p"), _f(field, "bid_q")
            ask_p, ask_q = _f(field, "ask_p"), _f(field, "ask_q")
            now_ms = time.time() * 1000.0
            quotes[src] = {"bid_p": bid_p, "bid_q": bid_q,
                           "ask_p": ask_p, "ask_q": ask_q, "ts": now_ms}

            # --- SENTETIK KURESEL FIYAT: 5 borsanin TAZE kotasyonlari (hacim-agirlikli) ---
            # Diziler <=10 elemanlik veri toplamadir; asil hesap (VWAP/OBI) NumPy C-level.
            fresh = [q for q in quotes.values() if now_ms - q["ts"] <= quote_ttl_ms]
            prices = np.array([x for q in fresh for x in (q["bid_p"], q["ask_p"])], dtype=np.float64)
            vols = np.array([x for q in fresh for x in (q["bid_q"], q["ask_q"])], dtype=np.float64)

            p_cex = compute_pcex(prices, vols)      # 5 borsa sentetik VWAP
            n_src = len(fresh)

            # --- PRICE TO BEAT: sabit (env) ya da 5dk pencere acilis fiyati ---
            if fixed_strike > 0:
                strike = fixed_strike
            else:
                win = int(now_ms // 1000 // window_sec)
                if win != cur_win:
                    cur_win = win
                    strike_dyn = p_cex        # yeni pencere -> acilis fiyati = hedef
                    log.info("[PENCERE] yeni %ddk pencere -> price-to-beat=%.2f",
                             window_sec // 60, strike_dyn)
                strike = strike_dyn

            # --- YON: acilisa gore yukari/asagi (market'in cozdugu sey) ---
            move = (p_cex - strike) / strike if strike > 0 else 0.0
            cand_dir = "LONG" if move >= 0 else "SHORT"   # LONG=Up, SHORT=Down

            # Polymarket Up olasiligi (taze degilse -1 = veri yok)
            poly_up, poly_fresh = poly.snapshot(max_stale_ms=3000)
            if not poly_fresh:
                poly_up = -1.0

            # Sentetik fiyati dashboard icin yayinla (throttle ~300ms, MAXLEN ~10).
            if now_ms - last_synth_pub >= 300:
                last_synth_pub = now_ms
                try:
                    await consumer.client.xadd(
                        "stream:synthetic",
                        {"p_cex": f"{p_cex:.4f}", "sources": str(n_src),
                         "strike": f"{strike:.2f}", "ts": str(int(now_ms))},
                        maxlen=10, approximate=True,
                    )
                except Exception as exc:
                    log.error("[SYNTH] xadd hatasi: %s", exc)

            # --- Sinyal: olu bolge disinda + (yon degisti VEYA cooldown doldu) ---
            if abs(move) >= move_band and (cand_dir != last_dir
                                           or now_ms - last_emit_ms > signal_cooldown_ms):
                emitted = await emit(cand_dir, p_cex, strike, poly_up, move,
                                     consumer.client)
                last_dir, last_emit_ms = emitted, now_ms
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
