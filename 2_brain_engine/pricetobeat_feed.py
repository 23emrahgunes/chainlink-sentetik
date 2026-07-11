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
# Aktif pencere: state.data icinde, Time oneki YOK -> "openPrice":X,"closePrice":null
_RX_ACTIVE = re.compile(r'"openPrice":([0-9.]+),"closePrice":null')
# Kapanmis pencereler: Time + openPrice + closePrice(sayi)
_RX_CLOSED = re.compile(
    r'Time":"(20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d)[^"]*",'
    r'"openPrice":([0-9.]+),"closePrice":([0-9.]+)'
)


class PriceToBeatFeed:
    def __init__(self, poll_sec: float = 3.0) -> None:
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

        # Aktif pencere openPrice (Price to Beat) — closePrice:null, Time oneki yok.
        am = _RX_ACTIVE.search(html)
        active = float(am.group(1)) if am else 0.0

        # Kapanmis pencereler (settlement icin).
        closed: dict[int, tuple] = {}
        for t, o, c in _RX_CLOSED.findall(html):
            ts = calendar.timegm(time.strptime(t, "%Y-%m-%dT%H:%M:%S"))
            closed[ts] = (float(o), float(c))

        # Rollover'da null-close girisi hazir degilse: bu pencerenin open'i =
        # win_ts'te KAPANAN pencerenin close'u (zincirleme). Gecikmeyi ortadan kaldirir.
        if active == 0.0 and win_ts in closed:
            active = closed[win_ts][1]
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
            # Adaptif: bu pencerenin open'ini henuz alamadiysak hizli tekrar dene (1s),
            # aldiysak normal poll. Rollover gecikmesini minimize eder.
            fast = self.window_ts != win_ts
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0 if fast else self._poll)
            except asyncio.TimeoutError:
                pass
