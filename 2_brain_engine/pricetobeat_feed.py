"""
pricetobeat_feed.py
GHOST ORACLE v5.0 :: Ajan 2 — Polymarket openPrice + pencere sonuclari.

Polymarket BTC up/down 5dk marketlerinin event sayfasina gomulu crypto-prices
verisinden:
  - aktif pencere openPrice (closePrice:null) = "Price to Beat"
  - kapanmis pencereler: {win_ts: (openPrice, closePrice)} -> outcome (settlement)

HTTP bloklama asyncio.to_thread ile izole. ~5s'de bir yenilenir.
"""
from __future__ import annotations

import asyncio
import calendar
import logging
import re
import time
import urllib.request

log = logging.getLogger("brain.p2b")

WINDOW_SEC = 300
# time + openPrice + closePrice birlikte (kapanmis/aktif tum pencereler)
_RX = re.compile(
    r'Time":"(20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d)[^"]*",'
    r'"openPrice":([0-9.]+),"closePrice":(null|[0-9.]+)'
)


class PriceToBeatFeed:
    def __init__(self, poll_sec: float = 5.0) -> None:
        self.price: float = 0.0            # aktif pencere openPrice (Price to Beat)
        self.window_ts: int = 0            # aktif pencere ts
        self.closed: dict[int, tuple] = {} # {win_ts: (open, close)} kapanmis pencereler
        self._poll = poll_sec

    def _fetch(self, win_ts: int) -> tuple[float, dict]:
        """(aktif_open, {ts:(open,close)}) doner. Bloklayan — to_thread ile cagrilir."""
        slug = f"btc-updown-5m-{win_ts}"
        url = f"https://polymarket.com/event/{slug}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "ignore")
        html = html.replace(chr(92), "")  # escaped JSON (\" -> ")

        active = 0.0
        closed: dict[int, tuple] = {}
        for t, o, c in _RX.findall(html):
            ts = calendar.timegm(time.strptime(t, "%Y-%m-%dT%H:%M:%S"))
            op = float(o)
            if c == "null":
                if ts == win_ts:
                    active = op
            else:
                closed[ts] = (op, float(c))
        if active == 0.0 and win_ts in closed:
            active = closed[win_ts][0]
        return active, closed

    async def run(self, stop) -> None:
        log.info("[P2B] Polymarket openPrice + sonuc fetcher basladi")
        while not stop.is_set():
            win_ts = (int(time.time()) // WINDOW_SEC) * WINDOW_SEC
            try:
                active, closed = await asyncio.to_thread(self._fetch, win_ts)
                if active > 0:
                    if win_ts != self.window_ts:
                        log.info("[P2B] price-to-beat=%.2f (pencere %d, Polymarket)", active, win_ts)
                    self.price, self.window_ts = active, win_ts
                if closed:
                    self.closed.update(closed)
                    # bellek: sadece son ~50 pencereyi tut
                    if len(self.closed) > 50:
                        for k in sorted(self.closed)[:-50]:
                            del self.closed[k]
            except Exception as exc:
                log.error("[P2B] fetch hatasi: %s", exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._poll)
            except asyncio.TimeoutError:
                pass
