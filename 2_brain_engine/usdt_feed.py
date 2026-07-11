"""
usdt_feed.py
GHOST ORACLE v5.0 :: Ajan 2 — USDT/USD kuru (Tether -> USD donusumu).

Binance/Bybit/OKX BTC/USDT fiyatlar; USDT tam $1 degil (~0.9993). Sentetik
fiyati gercek USD'ye cevirmek icin bu kur kullanilir. Coinbase spot API (hafif,
~30s poll). Erisilemezse makul varsayilan (0.9993) korunur.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.request

log = logging.getLogger("brain.usdt")

COINBASE_USDT_USD = "https://api.coinbase.com/v2/prices/USDT-USD/spot"


class UsdtUsdFeed:
    def __init__(self, poll_sec: float = 30.0) -> None:
        self.rate: float = 0.9993   # makul varsayilan (fetch olana kadar)
        self._poll = poll_sec

    def _fetch(self) -> float:
        req = urllib.request.Request(COINBASE_USDT_USD, headers={"User-Agent": "ghost-oracle"})
        d = json.load(urllib.request.urlopen(req, timeout=8))
        return float(d["data"]["amount"])

    async def run(self, stop) -> None:
        log.info("[USDT] USDT/USD kuru fetcher basladi")
        while not stop.is_set():
            try:
                r = await asyncio.to_thread(self._fetch)
                if 0.9 < r < 1.1:      # akil kontrolu
                    self.rate = r
            except Exception as exc:
                log.error("[USDT] fetch hatasi: %s (kur=%.5f korunuyor)", exc, self.rate)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._poll)
            except asyncio.TimeoutError:
                pass
