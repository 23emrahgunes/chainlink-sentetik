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


class PaperTrader:
    def __init__(self, stake: float = 1.0, lock_after_sec: int = 60) -> None:
        self.stake = stake
        self.lock_after = lock_after_sec
        self.pnl = 0.0
        self.trades = 0
        self.wins = 0
        self.losses = 0
        self.open_trade: dict | None = None   # aktif pencere slotu
        self.pending: list[dict] = []         # settle bekleyen (girilmis) islemler
        self.last: dict | None = None         # son settle sonucu (gosterim)

    def update(self, win_ts: int, now_sec: int, our_dir: str,
               poly_up: float, spot_open: float, closed: dict) -> None:
        # 1) settle: sonucu hazir olan bekleyen islemler
        self._settle_pending(closed)

        # 2) yeni pencere: onceki slotu (girildiyse) pending'e tasi
        if self.open_trade is None or self.open_trade["win"] != win_ts:
            prev = self.open_trade
            if prev and prev.get("dir") not in (None, "PAS"):
                self.pending.append(prev)
            self.open_trade = {"win": win_ts, "dir": None,
                               "entry_price": 0.0, "open_price": spot_open,
                               "locked": False}

        # 3) giris: pencereye lock_after sn gecti, henuz kilitlenmedi
        slot = self.open_trade
        if not slot["locked"] and (now_sec - win_ts) >= self.lock_after:
            if poly_up is not None and poly_up >= 0:
                slot["locked"] = True
                poly_dir = "LONG" if poly_up >= 0.5 else "SHORT"
                if our_dir == poly_dir:  # momentum + Polymarket UYUSTU -> ac
                    slot["dir"] = our_dir
                    slot["entry_price"] = poly_up if our_dir == "LONG" else (1.0 - poly_up)
                    log.info("[PAPER] GIRIS %s @ %.3f (pencere %d)",
                             "UP" if our_dir == "LONG" else "DOWN",
                             slot["entry_price"], win_ts)
                else:                    # uyusmadi -> PAS (bu pencere islem yok)
                    slot["dir"] = "PAS"

    def _settle_pending(self, closed: dict) -> None:
        still = []
        for tr in self.pending:
            w = tr["win"]
            if w not in closed:
                still.append(tr)  # sonuc henuz yok, bekle
                continue
            o, c = closed[w]
            outcome = "LONG" if c >= o else "SHORT"
            p = tr["entry_price"]
            won = (tr["dir"] == outcome)
            profit = self.stake * (1.0 / p - 1.0) if (won and p > 0) else -self.stake
            self.pnl += profit
            self.trades += 1
            self.wins += 1 if won else 0
            self.losses += 0 if won else 1
            self.last = {
                "win": w, "dir": tr["dir"], "outcome": outcome,
                "won": won, "profit": profit, "entry": p,
            }
            log.info("[PAPER] SETTLE pencere %d: tahmin=%s sonuc=%s -> %s %+.3f$ | net=%.3f$",
                     w, "UP" if tr["dir"] == "LONG" else "DOWN",
                     "UP" if outcome == "LONG" else "DOWN",
                     "KAZANDI" if won else "KAYBETTI", profit, self.pnl)
        self.pending = still

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
