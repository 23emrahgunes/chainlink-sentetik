"""
main_execution.py
GHOST ORACLE v5.0 :: Ajan 3 Orkestrator.

stream:signals'i async dinler; her sinyal icin Risk Guard + Gas Booster calisir.
  DRY_RUN : imza/gonderim YOK — sadece simulasyon logu.
  LIVE    : poly_router ile imzala/gonder, 3s Tx timeout korumasi.

Graceful shutdown (SIGINT/SIGTERM; Windows KeyboardInterrupt fallback).
KISIT: Private key yalnizca .env'den (poly_router icinde), koda gomulmez.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time

import redis.asyncio as redis
from dotenv import load_dotenv
from web3 import AsyncWeb3, AsyncHTTPProvider

from clob_order import build_order, order_hash, sign_order, submit_order
from gas_booster import compute_gas, gwei
from risk_guard import check_slippage

# .env: once kok dizin (diger ajanlarla ayni desen), sonra yerel.
load_dotenv("../.env")
load_dotenv(".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("exec.main")

STREAM_SIGNALS = "stream:signals"
STREAM_EXECUTIONS = "stream:executions"
STREAM_POLY = "stream:polymarket"
BLOCK_MS = 1000
# Bayat sinyal koruması: bundan eski karar uygulanmaz.
SIGNAL_MAX_STALE_MS = 2000


def _f(field: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(field.get(key, default))
    except (TypeError, ValueError):
        return default


async def _latest_poly_mid(client) -> float | None:
    """stream:polymarket'ten son mid fiyati (0..1) tek seferlik oku. Yoksa None."""
    try:
        rows = await client.xrevrange(STREAM_POLY, count=1)
    except Exception:
        return None
    if not rows:
        return None
    _id, fields = rows[0]
    try:
        return float(fields.get("mid", "0")) or None
    except (TypeError, ValueError):
        return None


async def handle_signal(field: dict, cfg: dict, router) -> None:
    """Tek bir sinyali isler: risk + gas, ardindan mod'a gore karar."""
    direction = field.get("dir", "?")
    target = _f(field, "p_cex")

    # --- Risk Guard (Slippage) ---
    decision = check_slippage(
        direction=direction,
        target_price=target,
        order_usdc=cfg["order_usdc"],
        liquidity_usdc=cfg["liquidity_usdc"],
        threshold=cfg["slippage_thr"],
    )

    # --- Gas Booster ---
    gas = await compute_gas(cfg["w3"], cfg["priority_gwei"])
    karar = "ONAYLI" if decision.approved else "RED"

    # --- Dashboard yayini: karari stream:executions'a bas (pass-through, MAXLEN ~10) ---
    try:
        await cfg["client"].xadd(
            STREAM_EXECUTIONS,
            {
                "dir": direction,
                "p_cex": f"{target:.8f}",
                "karar": karar,
                "slippage": f"{decision.slippage:.6f}",
                "gas_gwei": f"{gwei(gas['maxFeePerGas']):.1f}",
                "ts": str(int(time.time() * 1000)),
            },
            maxlen=10,
            approximate=True,
        )
    except Exception as exc:  # gozlem kaybi kritik degil, akis sursun
        log.error("[EXEC] stream:executions XADD hatasi: %s", exc)

    # Emrin fiyati = gercek Polymarket mid (0..1). Yoksa 0.5 fallback (DRY gosterim).
    pm_mid = await _latest_poly_mid(cfg["client"])
    order_price = pm_mid if pm_mid is not None else 0.5

    if cfg["mode"] != "LIVE":
        # ---------- DRY_RUN ----------
        log.info(
            "DRY RUN: Tx simule edildi. "
            "Gas: %.1f Gwei (base %.1f + prio %.1f, %s), P_cex: %.4f, Karar: %s",
            gwei(gas["maxFeePerGas"]),
            gwei(gas["base_fee"]),
            gwei(gas["maxPriorityFeePerGas"]),
            gas["source"],
            target,
            karar,
        )
        log.info("       Yon: %s | %s | est_fill=%.4f",
                 direction, decision.reason, decision.est_fill_price)
        # Gercek CLOB emrini kur + EIP-712 hash'i (imzasiz, anahtar gerekmez).
        if decision.approved and cfg["token_id"]:
            order = build_order(direction, cfg["token_id"], order_price, cfg["order_usdc"])
            log.info("       CLOB emri hazir (imzasiz): price=%.4f (PM=%s) hash=%s",
                     order_price, "yok" if pm_mid is None else f"{pm_mid:.4f}",
                     order_hash(order))
        return

    # ---------- LIVE ----------
    if not decision.approved:
        log.warning("LIVE: Slippage guard REDDETTI (%s) — emir atlaniyor.", decision.reason)
        return
    if router is None:
        log.error("LIVE: router yok (WALLET_PRIVATE_KEY eksik).")
        return
    if not cfg["token_id"]:
        log.error("LIVE: POLYMARKET_TOKEN_ID eksik — CLOB emri kurulamaz.")
        return
    if pm_mid is None:
        log.error("LIVE: Polymarket fiyati (stream:polymarket) yok — emir fiyatlanamaz.")
        return

    try:
        addr = router.account.address
        order = build_order(direction, cfg["token_id"], order_price, cfg["order_usdc"], maker=addr)
        signature = sign_order(order, os.getenv("WALLET_PRIVATE_KEY", ""))
        resp = await submit_order(order, signature, addr, cfg["tx_timeout"])
        log.info("LIVE: CLOB emri gonderildi | Yon: %s price: %.4f resp: %s",
                 direction, order_price, resp)
    except asyncio.TimeoutError:
        log.error("LIVE: CLOB %ss icinde yanit vermedi — TIMEOUT.", cfg["tx_timeout"])
    except Exception as exc:
        log.error("LIVE: CLOB emir hatasi: %s", exc)


async def run(stop: asyncio.Event) -> None:
    mode = os.getenv("TRADING_MODE", "DRY_RUN")
    addr = os.getenv("REDIS_ADDR", "127.0.0.1:6379")
    password = os.getenv("REDIS_PASSWORD", "")
    rpc = os.getenv("POLYGON_RPC", "https://polygon-rpc.com")

    cfg = {
        "mode": mode,
        "priority_gwei": int(os.getenv("PRIORITY_FEE_GWEI", "5")),
        "slippage_thr": float(os.getenv("SLIPPAGE_THRESHOLD", "0.01")),
        "order_usdc": float(os.getenv("ORDER_SIZE_USDC", "100")),
        "liquidity_usdc": float(os.getenv("POLY_LIQUIDITY_USDC", "50000")),
        "tx_timeout": float(os.getenv("TX_TIMEOUT_SEC", "3")),
        "token_id": os.getenv("POLYMARKET_TOKEN_ID", ""),
        # Read-only w3 — gas base_fee icin (key gerektirmez).
        "w3": AsyncWeb3(AsyncHTTPProvider(rpc)),
    }

    log.info("=== GHOST ORACLE v5.0 :: Execution Sniper ===")
    log.info("TRADING_MODE = %s | prio=%d Gwei | slippage esik=%%%.2f",
             mode, cfg["priority_gwei"], cfg["slippage_thr"] * 100)

    # LIVE ise router'i kur (private key .env'den; DRY_RUN'da hic dokunulmaz).
    router = None
    if mode == "LIVE":
        from poly_router import PolyRouter
        try:
            router = PolyRouter(
                rpc,
                os.getenv("POLYMARKET_CONTRACT", ""),
                os.getenv("WALLET_PRIVATE_KEY", ""),
            )
            log.info("[ROUTER] LIVE cuzdan: %s", router.account.address)
        except Exception as exc:
            log.error("[ROUTER] LIVE kurulum hatasi: %s — DRY simulasyona dusuluyor.", exc)
            cfg["mode"] = "DRY_RUN"

    # Redis baglantisi.
    host, _, port = addr.partition(":")
    client = redis.Redis(
        host=host or "127.0.0.1", port=int(port or 6379),
        password=password or None, decode_responses=True, max_connections=8,
    )
    if not await client.ping():
        log.error("[REDIS] ping BASARISIZ @ %s", addr)
        return
    cfg["client"] = client  # dashboard yayini icin (stream:executions)
    log.info("[REDIS] baglandi @ %s — stream:signals dinleniyor.", addr)

    last_id = "$"  # sadece yeni sinyaller
    dropped = 0
    try:
        while not stop.is_set():
            read = asyncio.ensure_future(
                client.xread({STREAM_SIGNALS: last_id}, count=10, block=BLOCK_MS)
            )
            stop_task = asyncio.ensure_future(stop.wait())
            done, _p = await asyncio.wait(
                {read, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if stop_task in done:
                read.cancel()
                break
            stop_task.cancel()

            resp = read.result()
            if not resp:
                continue

            now_ms = int(time.time() * 1000)
            for _stream, entries in resp:
                for entry_id, field in entries:
                    last_id = entry_id
                    ts = _f(field, "ts")
                    if ts and now_ms - ts > SIGNAL_MAX_STALE_MS:
                        dropped += 1
                        continue  # bayat sinyal — atla
                    await handle_signal(field, cfg, router)
    finally:
        await client.aclose()
        log.info("[EXEC] kapatildi. Atilan(bayat) sinyal: %d", dropped)


def _install_signals(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    def _trigger() -> None:
        log.info("[EXEC] kapatma sinyali alindi, durduruluyor...")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _trigger)
        except NotImplementedError:
            pass  # Windows -> KeyboardInterrupt fallback


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop = asyncio.Event()
    _install_signals(loop, stop)
    try:
        loop.run_until_complete(run(stop))
    except KeyboardInterrupt:
        log.info("[EXEC] KeyboardInterrupt — kapaniyor.")
    finally:
        loop.close()
        sys.exit(0)


if __name__ == "__main__":
    main()
