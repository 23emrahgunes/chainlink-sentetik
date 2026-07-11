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

from obi_matrix import compute_obi
from paper_trader import PaperTrader
from polymarket_feed import PolyFeed
from pricetobeat_feed import PriceToBeatFeed
from redis_consumer import RedisConsumer
from usdt_feed import UsdtUsdFeed
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
    move_band = float(os.getenv("SIGNAL_MOVE_BAND", "0.00005"))  # olu bolge (~%0.005 = $3)
    signal_cooldown_ms = float(os.getenv("SIGNAL_COOLDOWN_MS", "2000"))
    quote_ttl_ms = float(os.getenv("QUOTE_TTL_MS", "4000"))  # borsa "taze" sayilma penceresi
    obi_alpha = float(os.getenv("OBI_EMA_ALPHA", "0.2"))     # OBI yumusatma (0..1)
    obi_entry = float(os.getenv("OBI_ENTRY", "0.25"))        # |OBI| bu esigi asinca tahmin
    # USD-spot borsalar: Chainlink/Polymarket referansina en yakin (perp primi yok).
    spot_sources = set(os.getenv("SPOT_SOURCES", "coinbase,kraken").split(","))
    # USDT-quote borsalar: fiyatlari USDT/USD kuruyla USD'ye cevrilir.
    usdt_sources = set(os.getenv("USDT_SOURCES", "binance,bybit,okx").split(","))

    log.info("=== GHOST ORACLE v5.0 :: Analytical Brain ===")
    log.info("TRADING_MODE = %s", mode)

    consumer = RedisConsumer(addr, password)
    if not await consumer.ping():
        log.error("[REDIS] ping BASARISIZ @ %s (docker compose up -d?)", addr)
        return
    strike_mode = "sabit" if fixed_strike > 0 else "dinamik(%ddk pencere)" % (window_sec // 60)
    log.info("[REDIS] baglandi @ %s | price-to-beat=%s | move band=%%%.3f",
             addr, strike_mode, move_band * 100)

    # Polymarket + Chainlink fiyat beslemeleri (arka plan gorevler).
    poly = PolyFeed(consumer.client)
    poly_task = asyncio.ensure_future(poly.run(stop))
    # Price to Beat: Polymarket'in aktif pencere openPrice'i (birebir kaynak).
    p2b = PriceToBeatFeed()
    p2b_task = asyncio.ensure_future(p2b.run(stop))
    # USDT/USD kuru (Tether piyasalarini USD'ye cevirmek icin).
    usdt = UsdtUsdFeed()
    usdt_task = asyncio.ensure_future(usdt.run(stop))
    # Kagit-ustu trader (DRY_RUN, $1): OBI-suruculu — derinlik baskisini sezip
    # fiyat kirilmadan once yon tahmini.
    trader = PaperTrader(
        stake=float(os.getenv("PAPER_STAKE", "1.0")),
        obi_entry=obi_entry,
        value_max=float(os.getenv("OBI_VALUE_MAX", "0.90")))
    last_pnl_pub = 0.0

    # 5 borsanin en son kotasyonu (src -> quote). Sentetik kuresel fiyat icin.
    quotes: dict[str, dict] = {}
    last_synth_pub = 0.0
    last_dir = ""          # sinyal spam onleme: son yon
    last_emit_ms = 0.0     # son sinyal zamani
    obi_ema = 0.0          # yumusatilmis OBI (order book imbalance)
    cur_win = -1           # aktif 5dk pencere indeksi
    spot_open = 0.0        # pencere acilisinda bizim spot fiyat (tutarli hareket referansi)

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

            # --- Bu borsanin son kotasyonunu + DERINLIK hacmini kaydet ---
            src = field.get("src", "?")
            bid_p, bid_q = _f(field, "bid_p"), _f(field, "bid_q")
            ask_p, ask_q = _f(field, "ask_p"), _f(field, "ask_q")
            # Tether piyasalarini (Binance/Bybit/OKX) USDT/USD kuruyla USD'ye cevir.
            if src in usdt_sources:
                bid_p *= usdt.rate
                ask_p *= usdt.rate
            # derinlik hacmi (tum seviyeler); yoksa top seviyeye dus
            bid_vol = _f(field, "bid_vol") or bid_q
            ask_vol = _f(field, "ask_vol") or ask_q
            now_ms = time.time() * 1000.0
            quotes[src] = {"bid_p": bid_p, "bid_q": bid_q,
                           "ask_p": ask_p, "ask_q": ask_q,
                           "bid_vol": bid_vol, "ask_vol": ask_vol, "ts": now_ms}

            # --- SENTETIK KURESEL FIYAT: 5 borsanin TAZE kotasyonlari (hacim-agirlikli) ---
            # Diziler <=10 elemanlik veri toplamadir; asil hesap (VWAP) NumPy C-level.
            fresh = [q for s, q in quotes.items() if now_ms - q["ts"] <= quote_ttl_ms]
            prices = np.array([x for q in fresh for x in (q["bid_p"], q["ask_p"])], dtype=np.float64)
            vols = np.array([x for q in fresh for x in (q["bid_q"], q["ask_q"])], dtype=np.float64)
            p_cex = compute_pcex(prices, vols)      # 5 borsa sentetik (perp dahil)
            n_src = len(fresh)

            # --- OBI (Order Book Imbalance): 5 borsanin DERINLIK hacmi -> yon baskisi ---
            # +1 = alim baskisi (fiyat yukari egilimli), -1 = satis baskisi.
            bid_vols = np.array([q["bid_vol"] for q in fresh], dtype=np.float64)
            ask_vols = np.array([q["ask_vol"] for q in fresh], dtype=np.float64)
            obi = compute_obi(bid_vols, ask_vols)
            obi_ema = obi_alpha * obi + (1.0 - obi_alpha) * obi_ema  # yumusatma (gurultu ↓)

            # --- PIYASA REFERANSI (spot): sadece USD-spot borsalar (Coinbase/Kraken) ---
            # Chainlink/Polymarket'in kullandigi spot fiyata en yakin; perp primi yok.
            spot_q = [q for s, q in quotes.items()
                      if s in spot_sources and now_ms - q["ts"] <= quote_ttl_ms]
            if spot_q:
                sp = np.array([x for q in spot_q for x in (q["bid_p"], q["ask_p"])], dtype=np.float64)
                sv = np.array([x for q in spot_q for x in (q["bid_q"], q["ask_q"])], dtype=np.float64)
                spot_ref = compute_pcex(sp, sv)
            else:
                spot_ref = p_cex   # spot yoksa sentetige dus

            # --- PENCERE: her 5dk basinda kendi spot acilisimizi yakala (tutarli hareket) ---
            win = int(now_ms // 1000 // window_sec)
            if win != cur_win:
                cur_win = win
                spot_open = spot_ref
                log.info("[PENCERE] yeni %ddk -> spot-acilis=%.2f | Polymarket P2B=%.2f",
                         window_sec // 60, spot_open, p2b.price)

            # --- PRICE TO BEAT (gosterim): Polymarket openPrice (yoksa kendi spot acilis) ---
            if fixed_strike > 0:
                strike = fixed_strike
            elif p2b.price > 0:
                strike = p2b.price          # Polymarket'in birebir Price to Beat'i
            else:
                strike = spot_open

            # --- YON/HAREKET: KENDI spot acilisimiza gore (kaynak-tutarli, baz yok) ---
            move = (spot_ref - spot_open) / spot_open if spot_open > 0 else 0.0
            cand_dir = "LONG" if move >= 0 else "SHORT"   # LONG=Up, SHORT=Down

            # Polymarket Up olasiligi (taze degilse -1 = veri yok)
            poly_up, poly_fresh = poly.snapshot(max_stale_ms=3000)
            if not poly_fresh:
                poly_up = -1.0

            # Dashboard yayini (throttle ~300ms, MAXLEN ~10).
            if now_ms - last_synth_pub >= 300:
                last_synth_pub = now_ms
                try:
                    await consumer.client.xadd(
                        "stream:synthetic",
                        {"p_cex": f"{p_cex:.4f}", "spot_ref": f"{spot_ref:.4f}",
                         "sources": str(n_src), "strike": f"{strike:.2f}",
                         "obi": f"{obi_ema:.4f}", "usdt": f"{usdt.rate:.5f}",
                         "ts": str(int(now_ms))},
                        maxlen=10, approximate=True,
                    )
                except Exception as exc:
                    log.error("[SYNTH] xadd hatasi: %s", exc)

            # --- Sinyal: olu bolge disinda + (yon degisti VEYA cooldown doldu) ---
            if abs(move) >= move_band and (cand_dir != last_dir
                                           or now_ms - last_emit_ms > signal_cooldown_ms):
                emitted = await emit(cand_dir, spot_ref, strike, poly_up, move,
                                     consumer.client)
                last_dir, last_emit_ms = emitted, now_ms

            # --- Kagit-ustu trade + PnL (momentum + Polymarket teyidi) ---
            win_ts = win * window_sec
            now_sec = int(now_ms // 1000)
            trader.update(win_ts, now_sec, obi_ema, poly_up, p2b.closed)
            # Settle edilen islemleri gecmis tablosu icin yayinla.
            for rec in trader.drain():
                try:
                    await consumer.client.xadd("stream:trades", {
                        "win": str(rec["win"]),
                        "dir": rec["dir"],
                        "outcome": rec["outcome"],
                        "won": "1" if rec["won"] else "0",
                        "profit": f"{rec['profit']:.4f}",
                        "entry": f"{rec['entry']:.4f}",
                        "pnl_after": f"{rec['pnl_after']:.4f}",
                    }, maxlen=50, approximate=True)
                except Exception as exc:
                    log.error("[TRADES] xadd hatasi: %s", exc)
            if now_ms - last_pnl_pub >= 1000:
                last_pnl_pub = now_ms
                try:
                    await consumer.client.xadd("stream:pnl", trader.snapshot(),
                                               maxlen=10, approximate=True)
                except Exception as exc:
                    log.error("[PNL] xadd hatasi: %s", exc)
    finally:
        poly_task.cancel()
        p2b_task.cancel()
        usdt_task.cancel()
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
