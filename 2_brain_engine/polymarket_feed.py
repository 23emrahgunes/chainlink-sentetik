"""
polymarket_feed.py
GHOST ORACLE v5.0 :: Ajan 2 — Polymarket fiyat tuketicisi (arka plan).

stream:polymarket'i asenkron dinler ve en son mid fiyati (0..1) bellekte tutar.
Brain ana dongusu bu son degeri okuyup capraz-pazar spread'ini hesaplar.
TTL: snapshot() tazelik kontrolu yapar (bayat veri kullanilmaz).
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("brain.poly")

STREAM_POLY = "stream:polymarket"


class PolyFeed:
    """stream:polymarket'ten son mid fiyati tutan hafif arka plan tuketicisi."""

    def __init__(self, client) -> None:
        self._client = client
        self.mid: float = 0.0
        self.ts: float = 0.0  # UnixMilli
        self._last_id = "$"

    def snapshot(self, max_stale_ms: float = 2000) -> tuple[float, bool]:
        """(mid, fresh) dondurur. fresh=False ise veri yok/bayat."""
        if self.ts <= 0:
            return 0.0, False
        fresh = (time.time() * 1000 - self.ts) <= max_stale_ms
        return self.mid, fresh

    async def run(self, stop) -> None:
        """Arka plan gorevi: task olarak baslatilir, kapatilirken cancel edilir."""
        log.info("[POLY] feed tuketicisi basladi (%s)", STREAM_POLY)
        while not stop.is_set():
            try:
                resp = await self._client.xread(
                    {STREAM_POLY: self._last_id}, count=10, block=1000
                )
            except Exception as exc:
                log.error("[POLY] xread hatasi: %s", exc)
                continue
            if not resp:
                continue
            for _stream, entries in resp:
                for entry_id, fields in entries:
                    self._last_id = entry_id
                    try:
                        self.mid = float(fields.get("mid", "0"))
                        self.ts = float(fields.get("ts", "0"))
                    except (TypeError, ValueError):
                        continue
