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
import json
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
STREAM_ENTRIES = "stream:entries"
EXECUTION_STREAM = os.getenv("EXECUTION_STREAM", STREAM_ENTRIES)
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
                          decision=None, gas=None, reason: str = "", extra: dict | None = None) -> None:
    fields = {
        "dir": direction,
        "p_cex": f"{target:.8f}",
        "karar": karar,
        "reason": reason,
        "mode": cfg.get("mode", "DRY_RUN"),
        "live_armed": "1" if cfg.get("live_armed") else "0",
        "ts": str(int(time.time() * 1000)),
    }
    if extra:
        for key, value in extra.items():
            if value is not None:
                fields[str(key)] = str(value)
    if decision is not None:
        fields["slippage"] = f"{decision.slippage:.6f}"
    if gas is not None:
        fields["gas_gwei"] = f"{gwei(gas['maxFeePerGas']):.1f}"
    try:
        await cfg["client"].xadd(STREAM_EXECUTIONS, fields, maxlen=200, approximate=True)
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


def _live_block_reason(cfg: dict, decision, router, pm_mid, risk: dict, order_price: float | None = None) -> str | None:
    if cfg.get("mode") != "LIVE":
        return None
    if not cfg.get("live_armed"):
        return "LIVE_ARMED=0"
    if not decision.approved:
        return f"slippage red: {decision.reason}"
    if cfg["order_usdc"] > cfg["max_order_usdc"]:
        return f"ORDER_USDC {cfg['order_usdc']:.2f} > MAX_ORDER_USDC {cfg['max_order_usdc']:.2f}"
    if order_price is not None and order_price > cfg["max_live_entry_price"]:
        return f"limit fiyat {order_price * 100:.1f}c > max {cfg['max_live_entry_price'] * 100:.1f}c"
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


async def _latest_poly_snapshot(client) -> tuple[float | None, str, str, int]:
    """Return latest Polymarket Up mid plus Up/Down outcome tokens and window."""
    try:
        rows = await client.xrevrange(STREAM_POLY, count=1)
    except Exception:
        return None, "", "", 0
    if not rows:
        return None, "", "", 0
    _id, fields = rows[0]
    up_token = str(fields.get("up_token", "") or fields.get("token", "") or "")
    down_token = str(fields.get("down_token", "") or "")
    try:
        mid = float(fields.get("mid", "0")) or None
    except (TypeError, ValueError):
        mid = None
    try:
        window_ts = int(float(fields.get("window_ts", "0") or 0))
    except (TypeError, ValueError):
        window_ts = 0
    return mid, up_token, down_token, window_ts


def _order_lock_key(window_ts: int, direction: str = "", token_id: str = "") -> str:
    return f"state:order_lock:{int(window_ts or 0)}"


def _order_lock_ttl(window_ts: int, now_sec: int, fallback_sec: int = 360) -> int:
    if window_ts > 0:
        return max(30, int(window_ts + 330 - now_sec))
    return max(30, int(fallback_sec))


async def _acquire_order_lock(client, key: str, ttl_sec: int, value: str) -> bool:
    try:
        return bool(await client.set(key, value, ex=ttl_sec, nx=True))
    except Exception as exc:
        log.error("[EXEC] order lock yazilamadi: %s", exc)
        return False


async def _record_live_order_state(client, lock_key: str, response, token_id: str, direction: str) -> None:
    try:
        result = _clob_response_result(response)
        await client.hset(RISK_STATE_KEY, mapping={
            "last_order_lock": lock_key,
            "last_order_id": result["order_id"],
            "last_order_status": result["status"],
            "last_order_accepted": "1" if result["accepted"] else "0",
            "last_order_error": result["reason"],
            "last_token_id": str(token_id),
            "last_direction": str(direction),
            "last_order_ts": str(int(time.time() * 1000)),
        })
    except Exception as exc:
        log.error("[EXEC] live order state yazilamadi: %s", exc)


def _clob_response_result(response) -> dict:
    """Normalize Polymarket CLOB response into accepted/rejected telemetry."""
    result = {
        "accepted": False,
        "order_id": "",
        "status": "",
        "reason": "CLOB response order id veya success icermiyor",
        "raw": "",
    }
    try:
        result["raw"] = json.dumps(response, ensure_ascii=False, sort_keys=True)[:1200]
    except Exception:
        result["raw"] = str(response)[:1200]

    if not isinstance(response, dict):
        result["reason"] = f"CLOB beklenmeyen response tipi: {type(response).__name__}"
        return result

    order_id = str(response.get("orderID") or response.get("order_id") or response.get("id") or "")
    status = str(response.get("status") or response.get("state") or "")
    error = response.get("error") or response.get("errorMsg") or response.get("error_message") or response.get("message")
    success = response.get("success")

    result["order_id"] = order_id
    result["status"] = status
    if success is False or error:
        result["reason"] = str(error or "CLOB success=false")
        return result
    if order_id or success is True:
        result["accepted"] = True
        result["reason"] = "CLOB accepted"
        return result
    if status.upper() in {"OPEN", "LIVE", "MATCHED", "INSERTED", "ACCEPTED"}:
        result["accepted"] = True
        result["reason"] = "CLOB accepted"
        return result
    return result


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

    cfg["live_armed"] = await _runtime_live_armed(cfg["client"], cfg.get("env_live_armed", False))

    # LONG opens Up token; SHORT opens Down token. The stream mid is Up price.
    pm_mid, up_token_id, down_token_id, window_ts = await _latest_poly_snapshot(cfg["client"])
    signal_window_ts = int(_f(field, "window_ts") or _f(field, "win") or 0)
    effective_window_ts = signal_window_ts or window_ts or int(time.time() // 300 * 300)
    is_short = str(direction).upper() in {"SHORT", "DOWN"}
    live_token_id = down_token_id if is_short else up_token_id
    token_id = cfg["token_id"] or live_token_id
    if pm_mid is None:
        order_price = 0.5
    elif is_short:
        order_price = max(min(1.0 - pm_mid, 1.0), 1e-6)
    else:
        order_price = pm_mid
    entry_price = _f(field, "entry")
    share_qty = cfg["order_usdc"] / order_price if order_price > 0 else 0.0
    extra = {
        "source": field.get("source", EXECUTION_STREAM),
        "entry": f"{entry_price:.6f}" if entry_price else "",
        "entry_cents": field.get("entry_cents", ""),
        "order_price": f"{order_price:.6f}",
        "order_cents": f"{order_price * 100:.2f}",
        "poly_mid": "" if pm_mid is None else f"{pm_mid:.6f}",
        "window_ts": str(effective_window_ts),
        "poly_window_ts": str(window_ts or 0),
        "token_id": str(token_id or ""),
        "share": "DOWN" if is_short else "UP",
        "share_qty": f"{share_qty:.6f}",
        "sec_left": field.get("sec_left", ""),
        "entry_score": field.get("entry_score", ""),
    }
    if cfg["mode"] != "LIVE":
        await _emit_execution(cfg, direction, target, "DRY_RUN", decision, gas, decision.reason, extra)
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
    sec_left_raw = _f(field, "sec_left", -1)
    sec_left = int(sec_left_raw) if sec_left_raw >= 0 else max(0, int(effective_window_ts + 300 - time.time()))
    extra["sec_left"] = str(sec_left)
    if sec_left > cfg["max_live_seconds_left"]:
        reason = f"erken entry: kalan {sec_left}s > max {cfg['max_live_seconds_left']}s"
        await _emit_execution(cfg, direction, target, "LIVE_BLOCKED", decision, gas, reason, extra)
        log.warning("LIVE_BLOCKED: %s", reason)
        return
    if sec_left < cfg["min_live_seconds_left"]:
        reason = f"cok gec entry: kalan {sec_left}s < min {cfg['min_live_seconds_left']}s"
        await _emit_execution(cfg, direction, target, "LIVE_BLOCKED", decision, gas, reason, extra)
        log.warning("LIVE_BLOCKED: %s", reason)
        return
    if signal_window_ts and window_ts and signal_window_ts != window_ts:
        reason = f"market penceresi degisti: entry {signal_window_ts} != poly {window_ts}"
        await _emit_execution(cfg, direction, target, "LIVE_BLOCKED", decision, gas, reason, extra)
        log.warning("LIVE_BLOCKED: %s", reason)
        return
    if entry_price > 0 and order_price - entry_price > cfg["max_entry_drift_price"]:
        reason = f"entry fiyati kacti: live {order_price * 100:.1f}c > paper {entry_price * 100:.1f}c + {cfg['max_entry_drift_price'] * 100:.1f}c"
        await _emit_execution(cfg, direction, target, "LIVE_BLOCKED", decision, gas, reason, extra)
        log.warning("LIVE_BLOCKED: %s", reason)
        return
    risk = await _runtime_risk_state(cfg["client"])
    live_cfg = {**cfg, "token_id": token_id}
    block_reason = _live_block_reason(live_cfg, decision, router, pm_mid, risk, order_price)
    if block_reason:
        await _emit_execution(cfg, direction, target, "LIVE_BLOCKED", decision, gas, block_reason, extra)
        log.warning("LIVE_BLOCKED: %s", block_reason)
        return

    now_sec = int(time.time())
    lock_key = _order_lock_key(effective_window_ts, direction, token_id)
    lock_ttl = _order_lock_ttl(effective_window_ts, now_sec, cfg["order_lock_ttl_sec"])
    lock_value = f"{direction}|{token_id}|{int(time.time() * 1000)}"
    if not await _acquire_order_lock(cfg["client"], lock_key, lock_ttl, lock_value):
        reason = f"order lock active: {lock_key}"
        await _emit_execution(cfg, direction, target, "LIVE_BLOCKED", decision, gas, reason, extra)
        log.warning("LIVE_BLOCKED: %s", reason)
        return

    try:
        addr = router.account.address
        maker_addr = env("FUNDER_ADDRESS", "") or addr
        order = build_order(direction, token_id, order_price, cfg["order_usdc"], maker=maker_addr, signer=addr)
        signature = sign_order(order, env("WALLET_PRIVATE_KEY", ""))
        resp = await submit_order(order, signature, addr, cfg["tx_timeout"])
        result = _clob_response_result(resp)
        extra.update({
            "order_id": result["order_id"],
            "order_status": result["status"],
            "clob_accepted": "1" if result["accepted"] else "0",
            "clob_response": result["raw"],
        })
        await _record_live_order_state(cfg["client"], lock_key, resp, token_id, direction)
        if result["accepted"]:
            log.info("LIVE_SENT: CLOB kabul etti | Yon: %s price: %.4f maker: %s order_id=%s status=%s resp=%s",
                     direction, order_price, maker_addr, result["order_id"] or "-", result["status"] or "-", resp)
            accepted_reason = (
                f"CLOB accepted order_id={result['order_id'] or '-'} "
                f"status={result['status'] or '-'}"
            )
            await _emit_execution(cfg, direction, target, "LIVE_SENT", decision, gas, accepted_reason, extra)
        else:
            reason = f"CLOB emir reddi: {result['reason']}"
            log.error("LIVE_REJECTED: %s | resp=%s", reason, resp)
            await _emit_execution(cfg, direction, target, "LIVE_REJECTED", decision, gas, reason, extra)
    except asyncio.TimeoutError:
        reason = f"CLOB timeout {cfg['tx_timeout']}s"
        await _emit_execution(cfg, direction, target, "LIVE_REJECTED", decision, gas, reason, extra)
        log.error("LIVE_REJECTED: %s.", reason)
    except Exception as exc:
        reason = f"CLOB emir hatasi: {exc}"
        await _emit_execution(cfg, direction, target, "LIVE_REJECTED", decision, gas, reason, extra)
        log.error("LIVE_REJECTED: %s", reason)


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
        "max_live_entry_price": env_float("MAX_LIVE_ENTRY_CENTS", 20.0) / 100.0,
        "max_entry_drift_price": env_float("MAX_ENTRY_DRIFT_CENTS", 5.0) / 100.0,
        "max_live_seconds_left": env_int("MAX_LIVE_SECONDS_LEFT", 90),
        "min_live_seconds_left": env_int("MIN_LIVE_SECONDS_LEFT", 5),
        "max_daily_loss_usdc": env_float("MAX_DAILY_LOSS_USDC", 10.0),
        "max_open_positions": env_int("MAX_OPEN_POSITIONS", 1),
        "env_live_armed": _truthy(os.getenv("LIVE_ARMED", "0")),
        "live_armed": False,
        "liquidity_usdc": env_float("POLY_LIQUIDITY_USDC", 50000.0),
        "tx_timeout": env_float("TX_TIMEOUT_SEC", 3.0),
        "order_lock_ttl_sec": env_int("ORDER_LOCK_TTL_SEC", 360),
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
    log.info("[REDIS] baglandi @ %s - %s dinleniyor.", addr, EXECUTION_STREAM)

    last_id = "$"  # sadece yeni entry/sinyaller
    dropped = 0
    try:
        while not stop.is_set():
            read = asyncio.ensure_future(
                client.xread({EXECUTION_STREAM: last_id}, count=10, block=BLOCK_MS)
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
                        age_ms = int(now_ms - ts)
                        cfg["live_armed"] = await _runtime_live_armed(cfg["client"], cfg.get("env_live_armed", False))
                        reason = f"bayat entry {age_ms}ms > {SIGNAL_MAX_STALE_MS}ms"
                        status = "LIVE_BLOCKED" if cfg["mode"] == "LIVE" else "RED"
                        await _emit_execution(cfg, field.get("dir", "?"), _f(field, "p_cex"), status, reason=reason, extra={"source": EXECUTION_STREAM, "window_ts": field.get("window_ts", field.get("win", "")), "entry_cents": field.get("entry_cents", "")})
                        dropped += 1
                        continue  # bayat entry/sinyal - gonderme
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

