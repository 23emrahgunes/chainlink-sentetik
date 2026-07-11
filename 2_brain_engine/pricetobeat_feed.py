"""
pricetobeat_feed.py
GHOST ORACLE v5.0 :: Ajan 2 — Polymarket "Price to Beat" (openPrice) cekici.

Polymarket'in BTC up/down 5dk marketleri "Price to Beat" = pencere acilis
referans fiyati (openPrice). Bu deger gamma API'de YOK ama event sayfasina
gomulu React Query verisinde var:
    {"openPrice":64124.28,"closePrice":null}   <- aktif pencere (hala acik)

Bu ajan aktif pencerenin openPrice'ini cekip bellekte tutar; pencere degisince
(rollover) yeni degeri hemen ceker. HTTP bloklama asyncio.to_thread ile izole.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import urllib.request

log = logging.getLogger("brain.p2b")

WINDOW_SEC = 300
# Aktif pencere: closePrice null olan openPrice = Price to Beat.
_RE = re.compile(r'"openPrice":([0-9.]+),"closePrice":null')


class PriceToBeatFeed:
    def __init__(self, poll_sec: float = 3.0) -> None:
        self.price: float = 0.0
        self.window_ts: int = 0
        self._poll = poll_sec

    def _fetch(self, win_ts: int) -> float:
        """Bloklayan HTTP — to_thread ile cagrilir. openPrice doner (0=bulunamadi)."""
        slug = f"btc-updown-5m-{win_ts}"
        url = f"https://polymarket.com/event/{slug}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "ignore")
        html = html.replace(chr(92), "")  # escaped JSON (\" -> ")
        m = _RE.search(html)
        return float(m.group(1)) if m else 0.0

    async def run(self, stop) -> None:
        log.info("[P2B] Polymarket price-to-beat fetcher basladi")
        last_win = -1
        while not stop.is_set():
            win = (int(time.time()) // WINDOW_SEC) * WINDOW_SEC
            if win != last_win:
                try:
                    p = await asyncio.to_thread(self._fetch, win)
                    if p > 0:
                        self.price, self.window_ts, last_win = p, win, win
                        log.info("[P2B] price-to-beat=%.2f (pencere %d, Polymarket)", p, win)
                    else:
                        log.warning("[P2B] openPrice henuz yok (pencere %d) — tekrar denenecek", win)
                except Exception as exc:
                    log.error("[P2B] fetch hatasi: %s", exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._poll)
            except asyncio.TimeoutError:
                pass
