"""
chainlink_feed.py
GHOST ORACLE v5.0 :: Ajan 2 — Chainlink BTC/USD tuketicisi (arka plan).

stream:chainlink'i asenkron dinler, son Chainlink BTC/USD fiyatini bellekte tutar.
Brain bunu 5dk pencere acilisinda "Price to Beat" olarak yakalar (Polymarket'in
cozum kaynagi Chainlink'tir). On-chain feed seyrek gunceller -> genis tazelik.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("brain.chainlink")

STREAM_CHAINLINK = "stream:chainlink"


class ChainlinkFeed:
    def __init__(self, client) -> None:
        self._client = client
        self.price: float = 0.0
        self.ts: float = 0.0  # UnixMilli (Redis'e yazildigi an)
        self._last_id = "$"

    def snapshot(self, max_stale_ms: float = 20000) -> tuple[float, bool]:
        """(price, fresh). On-chain ~30s'de gunceller ama poll 5s -> 20s tolerans."""
        if self.ts <= 0:
            return 0.0, False
        return self.price, (time.time() * 1000 - self.ts) <= max_stale_ms

    async def run(self, stop) -> None:
        log.info("[CHAINLINK] feed tuketicisi basladi (%s)", STREAM_CHAINLINK)
        while not stop.is_set():
            try:
                resp = await self._client.xread(
                    {STREAM_CHAINLINK: self._last_id}, count=10, block=1000
                )
            except Exception as exc:
                log.error("[CHAINLINK] xread hatasi: %s", exc)
                continue
            if not resp:
                continue
            for _stream, entries in resp:
                for entry_id, fields in entries:
                    self._last_id = entry_id
                    try:
                        self.price = float(fields.get("price", "0"))
                        self.ts = float(fields.get("ts", "0"))
                    except (TypeError, ValueError):
                        continue
