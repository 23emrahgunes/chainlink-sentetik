"""
trigger_logic.py
GHOST ORACLE v5.0 :: Ajan 2.4 — Sinyal emitter (BTC Up/Down 5m).

Yon (UP/DOWN) ve tetik karari CAGIRAN tarafta (main_brain) verilir; bu modul
sadece sinyali loglar ve Redis 'stream:signals'e (MAXLEN ~10) async XADD eder.

Sinyal alanlari: dir, p_cex, strike (price to beat), poly_up (Polymarket Up
olasiligi 0..1), move (acilisa gore oransal hareket), ts.
DRY_RUN: gercek emir YOK.
"""
from __future__ import annotations

import logging
import time

STREAM_SIGNALS = "stream:signals"
SIGNAL_MAXLEN = 10

log = logging.getLogger("brain.trigger")


async def emit(
    direction: str,
    p_cex: float,
    strike: float,
    poly_up: float,
    move: float,
    redis_client=None,
) -> str:
    """
    Sinyali loglar ve stream:signals'e yayinlar. direction: 'LONG'(Up)/'SHORT'(Down).
    poly_up < 0 ise Polymarket verisi yok demektir.
    """
    yon = "UP" if direction == "LONG" else "DOWN"
    pm = "yok" if poly_up < 0 else f"{poly_up:.3f}"
    log.info("[SINYAL] %s | P_cex=%.2f strike=%.2f move=%+.4f%% | Polymarket_Up=%s",
             yon, p_cex, strike, move * 100, pm)

    if redis_client is not None:
        try:
            await redis_client.xadd(
                STREAM_SIGNALS,
                {
                    "dir": direction,
                    "p_cex": f"{p_cex:.8f}",
                    "strike": f"{strike:.8f}",
                    "poly_up": f"{poly_up:.6f}",
                    "move": f"{move:.8f}",
                    "ts": str(int(time.time() * 1000)),
                },
                maxlen=SIGNAL_MAXLEN,
                approximate=True,
            )
        except Exception as exc:  # sinyal kaybi kritik degil
            log.error("[SINYAL] stream:signals XADD hatasi: %s", exc)

    return direction
