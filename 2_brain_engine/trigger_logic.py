"""
trigger_logic.py
GHOST ORACLE v5.0 :: Ajan 2.4 — Sinyal tetikleyici (DRY_RUN).

Kural: |OBI| esigi ASILMIS  VE  spread esigi ASILMIS ise sinyal uret.
  OBI > +0.6  -> LONG   (alis baskisi)
  OBI < -0.6  -> SHORT  (satis baskisi)

Sinyal uretildiginde:
  1) konsola log basar,
  2) Ajan 3'un tuketmesi icin Redis 'stream:signals' kanalina async XADD
     (MAXLEN ~10, bellek dostu).
DRY_RUN oldugu icin gercek emir YOK.

NOT: Gercek Polymarket spread makasi Ajan 3'te gelecek. Su an intra-book
spread (ask-bid)/mid proxy olarak kullanilir.
"""
from __future__ import annotations

import logging
import time

STREAM_SIGNALS = "stream:signals"
SIGNAL_MAXLEN = 10  # 2GB RAM — bellek dostu trim

log = logging.getLogger("brain.trigger")


async def evaluate(
    p_cex: float,
    obi: float,
    spread: float,
    obi_threshold: float = 0.6,
    spread_threshold: float = 0.0,
    redis_client=None,
) -> str | None:
    """
    Sinyal degerlendirir. Uretilirse yon ("LONG"/"SHORT") dondurur, aksi None.

    redis_client verilirse (redis.asyncio.Redis), sinyal 'stream:signals'e
    MAXLEN ~10 ile async XADD edilir. None ise sadece loglar (test/backward-compat).
    """
    if abs(obi) <= obi_threshold:
        return None
    if spread < spread_threshold:
        return None

    direction = "LONG" if obi > 0 else "SHORT"
    log.info(
        "[SINYAL URETILDI] Yon: %s, P_cex: %.4f  (OBI=%.3f, spread=%.5f)",
        direction,
        p_cex,
        obi,
        spread,
    )

    if redis_client is not None:
        try:
            await redis_client.xadd(
                STREAM_SIGNALS,
                {
                    "dir": direction,
                    "p_cex": f"{p_cex:.8f}",
                    "obi": f"{obi:.6f}",
                    "spread": f"{spread:.8f}",
                    "ts": str(int(time.time() * 1000)),
                },
                maxlen=SIGNAL_MAXLEN,
                approximate=True,  # MAXLEN ~10
            )
        except Exception as exc:  # sinyal kaybi kritik degil, akis sursun
            log.error("[SINYAL] stream:signals XADD hatasi: %s", exc)

    return direction
