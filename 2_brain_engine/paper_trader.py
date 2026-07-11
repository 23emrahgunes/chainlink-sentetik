"""
paper_trader.py
GHOST ORACLE v5.0 :: Ajan 2 — Kagit-ustu (DRY_RUN) trade + net PnL.

Her 5dk pencerede $1 ile bir tahmin kilitler:
  KURAL (Momentum + Polymarket teyidi): bizim momentum yonumuz (spot pencere
  acilisina gore) Polymarket oran yonuyle AYNIYSA islem ac; degilse PAS.

Pencere kapaninca gercek sonucla (Polymarket open/close -> Chainlink) teyit eder.
PnL (gercekci Polymarket ikili modeli):
  dogru  : +stake * (1/giris_fiyati - 1)
  yanlis : -stake
"""
from __future__ import annotations

import logging

log = logging.getLogger("brain.paper")

WINDOW_SEC = 300  # kapanmis pencereler END zamaniyla anahtarli (start+300)


class PaperTrader:
    def __init__(self, stake: float = 1.0, lock_before_close: int = 30,
                 min_move: float = 0.0003, value_max: float = 0.90) -> None:
        self.stake = stake
        self.lock_before_close = lock_before_close  # kapanisa kac sn kala karar
        self.min_move = min_move                    # net hareket esigi (baz gurultusunu asmali)
        self.value_max = value_max                  # oran bu ustundeyse edge yok (pahali)
        self.pnl = 0.0
        self.trades = 0
        self.wins = 0
        self.losses = 0
        self.open_trade: dict | None = None   # aktif pencere slotu
        self.pending: list[dict] = []         # settle bekleyen (girilmis) islemler
        self.last: dict | None = None         # son settle sonucu (gosterim)
        self._to_publish: list[dict] = []     # yeni settle edilenler (stream:trades icin)

    def update(self, win_ts: int, now_sec: int, spot_ref: float,
               strike: float, poly_up: float, closed: dict) -> None:
        # 1) settle: sonucu hazir olan bekleyen islemler
        self._settle_pending(closed)

        # 2) yeni pencere: onceki slotu (girildiyse) pending'e tasi
        if self.open_trade is None or self.open_trade["win"] != win_ts:
            prev = self.open_trade
            if prev and prev.get("dir") not in (None, "PAS"):
                self.pending.append(prev)
            self.open_trade = {"win": win_ts, "dir": None,
                               "entry_price": 0.0, "locked": False}

        # 3) LATENCY-ARB: pencere KAPANISINA lock_before_close kala karar
        slot = self.open_trade
        if not slot["locked"] and (now_sec - win_ts) >= (WINDOW_SEC - self.lock_before_close):
            slot["locked"] = True
            if strike > 0 and spot_ref > 0 and poly_up is not None and poly_up >= 0:
                move = (spot_ref - strike) / strike
                direction = "LONG" if move >= 0 else "SHORT"   # kapanisa yakin ~belli
                price = poly_up if direction == "LONG" else (1.0 - poly_up)
                # net hareket (baz gurultusunu asan) + oran hala ucuz (edge) ise ac
                if abs(move) >= self.min_move and 0.0 < price <= self.value_max:
                    slot["dir"] = direction
                    slot["entry_price"] = price
                    log.info("[PAPER] ARB GIRIS %s @ %.3f | move=%+.4f%% (spot %.2f vs beat %.2f)",
                             "UP" if direction == "LONG" else "DOWN",
                             price, move * 100, spot_ref, strike)
                else:
                    slot["dir"] = "PAS"
                    log.info("[PAPER] PAS: move=%+.4f%% price=%.3f (esik move>=%.4f%%, price<=%.2f)",
                             move * 100, price, self.min_move * 100, self.value_max)
            else:
                slot["dir"] = "PAS"

    def _settle_pending(self, closed: dict) -> None:
        still = []
        for tr in self.pending:
            w = tr["win"]
            w_end = w + WINDOW_SEC          # kapanis END zamaniyla anahtarli
            if w_end not in closed:
                still.append(tr)  # sonuc henuz yok, bekle
                continue
            o, c = closed[w_end]
            outcome = "LONG" if c >= o else "SHORT"
            p = tr["entry_price"]
            won = (tr["dir"] == outcome)
            profit = self.stake * (1.0 / p - 1.0) if (won and p > 0) else -self.stake
            self.pnl += profit
            self.trades += 1
            self.wins += 1 if won else 0
            self.losses += 0 if won else 1
            rec = {
                "win": w, "dir": tr["dir"], "outcome": outcome,
                "won": won, "profit": profit, "entry": p, "pnl_after": self.pnl,
            }
            self.last = rec
            self._to_publish.append(rec)
            log.info("[PAPER] SETTLE pencere %d: tahmin=%s sonuc=%s -> %s %+.3f$ | net=%.3f$",
                     w, "UP" if tr["dir"] == "LONG" else "DOWN",
                     "UP" if outcome == "LONG" else "DOWN",
                     "KAZANDI" if won else "KAYBETTI", profit, self.pnl)
        self.pending = still

    def drain(self) -> list[dict]:
        """Son update'ten bu yana settle edilen islemleri dondurur (yayin icin)."""
        recs, self._to_publish = self._to_publish, []
        return recs

    def snapshot(self) -> dict:
        wr = (self.wins / self.trades * 100.0) if self.trades else 0.0
        d = {
            "pnl": f"{self.pnl:.4f}",
            "trades": str(self.trades),
            "wins": str(self.wins),
            "losses": str(self.losses),
            "win_rate": f"{wr:.1f}",
            "open": "1" if (self.open_trade and self.open_trade.get("dir") not in (None, "PAS")) else "0",
        }
        if self.last:
            d["last_dir"] = self.last["dir"]
            d["last_outcome"] = self.last["outcome"]
            d["last_won"] = "1" if self.last["won"] else "0"
            d["last_profit"] = f"{self.last['profit']:.4f}"
        return d
