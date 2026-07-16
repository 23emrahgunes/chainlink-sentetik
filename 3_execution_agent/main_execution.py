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
from env_alias import env, env_float, env_int, normalized_mode
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
LIVE_STATE_KEY = "state:live"
RISK_STATE_KEY = "state:risk"
BLOCK_MS = 1000
# Bayat sinyal koruması: bundan eski karar uygulanmaz.
SIGNAL_MAX_STALE_MS = int(os.getenv("SIGNAL_MAX_STALE_MS", "2000"))



def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "live"}



async def _emit_execution(cfg: dict, direction: str, target: float, karar: str,
                          decision=None, gas=None, reason: str = "") -> None:
    fields = {
        "dir": direction,
        "p_cex": f"{target:.8f}",
        "karar": karar,
        "reason": reason,
        "mode": cfg.get("mode", "DRY_RUN"),
        "live_armed": "1" if cfg.get("live_armed") else "0",
        "ts": str(int(time.time() * 1000)),
    }
    if decision is not None:
        fields["slippage"] = f"{decision.slippage:.6f}"
    if gas is not None:
        fields["gas_gwei"] = f"{gwei(gas['maxFeePerGas']):.1f}"
    try:
        await cfg["client"].xadd(STREAM_EXECUTIONS, fields, maxlen=20, approximate=True)
    except Exception as exc:
        log.error("[EXEC] stream:executions XADD hatasi: %s", exc)


async def _runtime_live_armed(client, env_default: bool) -> bool:
    try:
        state = await client.hgetall(LIVE_STATE_KEY)
        if state and "armed" in state:
            return _truthy(state.get("armed"))
    except Exception:
        pass
    return env_default


async def _runtime_risk_state(client) -> dict:
    try:
        state = await client.hgetall(RISK_STATE_KEY) or {}
    except Exception:
        state = {}

    def f(key: str, env_key: str, default: float = 0.0) -> float:
        raw = state.get(key, os.getenv(env_key, str(default)))
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    return {
        "daily_loss_usdc": f("daily_loss_usdc", "DAILY_LOSS_USDC", 0.0),
        "open_positions": int(f("open_positions", "OPEN_POSITIONS", 0.0)),
    }


def _live_block_reason(cfg: dict, decision, router, pm_mid, risk: dict) -> str | None:
    if cfg.get("mode") != "LIVE":
        return None
    if not cfg.get("live_armed"):
        return "LIVE_ARMED=0"
    if not decision.approved:
        return f"slippage red: {decision.reason}"
    if cfg["order_usdc"] > cfg["max_order_usdc"]:
        return f"ORDER_USDC {cfg['order_usdc']:.2f} > MAX_ORDER_USDC {cfg['max_order_usdc']:.2f}"
    if risk.get("daily_loss_usdc", 0.0) >= cfg["max_daily_loss_usdc"]:
        return f"daily loss {risk.get('daily_loss_usdc', 0.0):.2f} >= max {cfg['max_daily_loss_usdc']:.2f}"
    if risk.get("open_positions", 0) >= cfg["max_open_positions"]:
        return f"open positions {risk.get('open_positions', 0)} >= max {cfg['max_open_positions']}"
    if router is None:
        return "router yok veya WALLET_PRIVATE_KEY eksik"
    if not cfg["token_id"]:
        return "POLYMARKET_TOKEN_ID eksik"
    if pm_mid is None:
        return "Polymarket mid yok"
    return None

def _f(field: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(field.get(key, default))
    except (TypeError, ValueError):
        return default


def _is_stale_signal(ts_ms: float, now_ms: int, max_age_ms: int = SIGNAL_MAX_STALE_MS) -> bool:
    return bool(ts_ms and now_ms - ts_ms > max_age_ms)


async def _latest_poly_snapshot(client) -> tuple[float | None, str]:
    """Return latest Polymarket mid and outcome token from stream:polymarket."""
    try:
        rows = await client.xrevrange(STREAM_POLY, count=1)
    except Exception:
        return None, ""
    if not rows:
        return None, ""
    _id, fields = rows[0]
    token = str(fields.get("token", "") or "")
    try:
        mid = float(fields.get("mid", "0")) or None
    except (TypeError, ValueError):
        mid = None
    return mid, token


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

    # --- Dashboard yayini: her sinyal icin ilk karar gorunsun. ---
    cfg["live_armed"] = await _runtime_live_armed(cfg["client"], cfg.get("env_live_armed", False))
    visible_karar = "DRY_RUN" if cfg["mode"] != "LIVE" else karar
    await _emit_execution(cfg, direction, target, visible_karar, decision, gas, decision.reason)

    # Emrin fiyati = gercek Polymarket mid (0..1). Yoksa 0.5 fallback (DRY gosterim).
    pm_mid, live_token_id = await _latest_poly_snapshot(cfg["client"])
    token_id = cfg["token_id"] or live_token_id
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
        if decision.approved and token_id:
            order = build_order(direction, token_id, order_price, cfg["order_usdc"])
            log.info("       CLOB emri hazir (imzasiz): price=%.4f (PM=%s) hash=%s",
                     order_price, "yok" if pm_mid is None else f"{pm_mid:.4f}",
                     order_hash(order))
        return

    # ---------- LIVE ----------
    risk = await _runtime_risk_state(cfg["client"])
    live_cfg = {**cfg, "token_id": token_id}
    block_reason = _live_block_reason(live_cfg, decision, router, pm_mid, risk)
    if block_reason:
        await _emit_execution(cfg, direction, target, "LIVE_BLOCKED", decision, gas, block_reason)
        log.warning("LIVE_BLOCKED: %s", block_reason)
        return

    try:
        addr = router.account.address
        order = build_order(direction, token_id, order_price, cfg["order_usdc"], maker=addr)
        signature = sign_order(order, env("WALLET_PRIVATE_KEY", ""))
        resp = await submit_order(order, signature, addr, cfg["tx_timeout"])
        log.info("LIVE: CLOB emri gonderildi | Yon: %s price: %.4f resp: %s",
                 direction, order_price, resp)
        await _emit_execution(cfg, direction, target, "LIVE_SENT", decision, gas, "order submitted")
    except asyncio.TimeoutError:
        reason = f"CLOB timeout {cfg['tx_timeout']}s"
        await _emit_execution(cfg, direction, target, "LIVE_BLOCKED", decision, gas, reason)
        log.error("LIVE: %s.", reason)
    except Exception as exc:
        reason = f"CLOB emir hatasi: {exc}"
        await _emit_execution(cfg, direction, target, "LIVE_BLOCKED", decision, gas, reason)
        log.error("LIVE: %s", reason)


async def run(stop: asyncio.Event) -> None:
    mode = normalized_mode()
    addr = os.getenv("REDIS_ADDR", "127.0.0.1:6379")
    password = os.getenv("REDIS_PASSWORD", "")
    rpc = env("POLYGON_RPC", "https://polygon-rpc.com")

    cfg = {
        "mode": mode,
        "priority_gwei": env_int("PRIORITY_FEE_GWEI", 5),
        "slippage_thr": env_float("SLIPPAGE_THRESHOLD", 0.01),
        "order_usdc": env_float("ORDER_USDC", 1.0),
        "max_order_usdc": env_float("MAX_ORDER_USDC", 1.0),
        "max_daily_loss_usdc": env_float("MAX_DAILY_LOSS_USDC", 10.0),
        "max_open_positions": env_int("MAX_OPEN_POSITIONS", 1),
        "env_live_armed": _truthy(os.getenv("LIVE_ARMED", "0")),
        "live_armed": False,
        "liquidity_usdc": env_float("POLY_LIQUIDITY_USDC", 50000.0),
        "tx_timeout": env_float("TX_TIMEOUT_SEC", 3.0),
        "token_id": env("POLYMARKET_TOKEN_ID", ""),
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
                env("POLYMARKET_CONTRACT", ""),
                env("WALLET_PRIVATE_KEY", ""),
            )
            log.info("[ROUTER] LIVE cuzdan: %s", router.account.address)
        except Exception as exc:
            log.error("[ROUTER] LIVE kurulum hatasi: %s - emirler LIVE_BLOCKED olacak.", exc)
            router = None

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
