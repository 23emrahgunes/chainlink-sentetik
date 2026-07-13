"""
whale_feed.py
GHOST ORACLE v5.0 :: Ajan 2 — Balina/akis tuketicisi (Binance aggTrade).

stream:whale'i asenkron dinler; son WHALE_WINDOW_SEC saniyenin AGRESIF akisini tutar:
  cvd      = agresif alim - agresif satim (net balina baskisi; + = alim)
  pressure = alim / (alim + satim)
  balina   = penceredeki en buyuk TEK islem (boyut, yon) — esik ustu ise "balina"
Balina DERINLIKTE (duran emir) degil, burada (gerceklesen agresif islem) gorunur.
ISLEM ACMAZ — sadece besleme/olcum.
"""
from __future__ import annotations

import logging
import time
from collections import deque

log = logging.getLogger("brain.whale")

STREAM_WHALE = "stream:whale"


class WhaleFeed:
    def __init__(self, client, window_sec: int = 30,
                 whale_min_btc: float = 5.0) -> None:
        self._client = client
        self.window_ms = window_sec * 1000.0
        self.whale_min = whale_min_btc
        self._buf: deque = deque()   # (ts_ms, buy, sell, max_buy, max_sell)
        self._last_id = "$"
        self.ts: float = 0.0

    def _prune(self, now_ms: float) -> None:
        while self._buf and now_ms - self._buf[0][0] > self.window_ms:
            self._buf.popleft()

    def signal(self, now_ms: float | None = None) -> float:
        """Olcum sinyali: CVD (net balina baskisi). +=UP tahmini, -=DOWN."""
        now_ms = now_ms if now_ms is not None else time.time() * 1000.0
        self._prune(now_ms)
        buy = sum(x[1] for x in self._buf)
        sell = sum(x[2] for x in self._buf)
        return buy - sell

    def snapshot(self, now_ms: float | None = None) -> dict:
        now_ms = now_ms if now_ms is not None else time.time() * 1000.0
        self._prune(now_ms)
        buy = sum(x[1] for x in self._buf)
        sell = sum(x[2] for x in self._buf)
        tot = buy + sell
        cvd = buy - sell
        pressure = (buy / tot) if tot > 0 else 0.5
        big_buy = max((x[3] for x in self._buf), default=0.0)
        big_sell = max((x[4] for x in self._buf), default=0.0)
        if big_buy >= big_sell:
            wsize, wside = big_buy, "BUY"
        else:
            wsize, wside = big_sell, "SELL"
        return {
            "whale_cvd": f"{cvd:.3f}",
            "whale_buy": f"{buy:.3f}",
            "whale_sell": f"{sell:.3f}",
            "whale_pressure": f"{pressure:.4f}",
            "whale_max": f"{wsize:.3f}",
            "whale_side": wside if wsize >= self.whale_min else "-",
        }

    async def run(self, stop) -> None:
        log.info("[WHALE] feed tuketicisi basladi (%s)", STREAM_WHALE)
        while not stop.is_set():
            try:
                resp = await self._client.xread(
                    {STREAM_WHALE: self._last_id}, count=20, block=1000
                )
            except Exception as exc:
                log.error("[WHALE] xread hatasi: %s", exc)
                continue
            if not resp:
                continue
            for _stream, entries in resp:
                for entry_id, fields in entries:
                    self._last_id = entry_id
                    try:
                        buy = float(fields.get("buy_vol", "0"))
                        sell = float(fields.get("sell_vol", "0"))
                        mb = float(fields.get("max_buy", "0"))
                        ms = float(fields.get("max_sell", "0"))
                        ts = float(fields.get("ts", "0"))
                    except (TypeError, ValueError):
                        continue
                    self._buf.append((ts, buy, sell, mb, ms))
                    self.ts = ts
            self._prune(time.time() * 1000.0)


# --------------------------------------------------------------------- smoke test
if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    w = WhaleFeed(client=None, window_sec=30, whale_min_btc=5.0)
    now = time.time() * 1000.0
    # net alim baskisi + 8 BTC balina alimi
    w._buf.append((now, 12.0, 4.0, 8.0, 1.5))
    w._buf.append((now, 3.0, 2.0, 0.5, 0.5))
    snap = w.snapshot(now)
    print("snapshot:", snap)
    assert abs(w.signal(now) - (15.0 - 6.0)) < 1e-9, w.signal(now)  # cvd=+9
    assert snap["whale_side"] == "BUY", snap        # 8 BTC alim balinasi
    assert float(snap["whale_max"]) == 8.0, snap
    # eski kayit pencereden dusmeli
    w._buf.appendleft((now - 40000, 100.0, 0.0, 100.0, 0.0))
    assert abs(w.signal(now) - 9.0) < 1e-9, "eski kayit prune edilmeli"
    print("WHALE FEED ASSERT GECTI [OK]")
