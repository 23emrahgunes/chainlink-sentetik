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
    def __init__(self, stake: float = 1.0, obi_entry: float = 0.25,
                 value_max: float = 0.90, min_entry: float = 0.05,
                 lock_at_sec: int = 90, strategy: str = "dip",
                 dip_max: float = 0.30, reversal_window_sec: int = 60,
                 margin_max: float = 0.0012) -> None:
        self.stake = stake
        self.strategy = strategy      # "dip" (OBI-teyitli reversal) | "obi" (derinlik yonu)
        self.obi_entry = obi_entry    # reversal teyidi icin |OBI| esigi
        self.value_max = value_max
        self.min_entry = min_entry    # cok dusuk = bayat/degenere -> girme
        self.dip_max = dip_max        # ucuz taraf bu ustundeyse dip degil (market ~50/50)
        self.lock_at = lock_at_sec    # obi modu: karar zamani
        self.rev_win = reversal_window_sec  # dip: son N sn'de reversal ara
        self.margin_max = margin_max  # |spot-beat|/beat bu altindaysa flip mumkun (yakin)
        self.pnl = 0.0
        self.trades = 0
        self.wins = 0
        self.losses = 0
        self.open_trade: dict | None = None   # aktif pencere slotu
        self.pending: list[dict] = []         # settle bekleyen (girilmis) islemler
        self.last: dict | None = None         # son settle sonucu (gosterim)
        self._to_publish: list[dict] = []     # yeni settle edilenler (stream:trades icin)

    def update(self, win_ts: int, now_sec: int, obi: float, poly_up: float,
               spot: float, strike: float, closed: dict, whale: float = 0.0) -> None:
        # 1) settle
        self._settle_pending(closed)

        # 2) yeni pencere rotasyonu
        if self.open_trade is None or self.open_trade["win"] != win_ts:
            prev = self.open_trade
            if prev and prev.get("dir") not in (None, "PAS"):
                self.pending.append(prev)
            self.open_trade = {"win": win_ts, "dir": None,
                               "entry_price": 0.0, "locked": False}

        slot = self.open_trade
        if slot["locked"]:
            return
        sec_in = now_sec - win_ts

        if self.strategy == "dip":
            # REVERSAL: son rev_win sn'de; borsalardaki OBI donusu TEYIT ederse ucuzu al.
            if sec_in < (WINDOW_SEC - self.rev_win):
                return
            if poly_up is None or poly_up < 0 or spot <= 0 or strike <= 0:
                if sec_in >= WINDOW_SEC - 2:
                    slot["locked"], slot["dir"] = True, "PAS"
                return
            price_dir = 1 if spot >= strike else -1          # su an kazanan yon
            margin = abs(spot - strike) / strike
            up_price, down_price = poly_up, 1.0 - poly_up
            cheap_dir = 1 if up_price <= down_price else -1
            cheap_price = up_price if cheap_dir == 1 else down_price
            obi_dir = 1 if obi >= 0 else -1
            # KURULUM: ucuz taraf=kaybeden yon + OBI donusu teyit + mesafe yakin + oran dip
            setup = (cheap_dir == -price_dir
                     and obi_dir == cheap_dir
                     and abs(obi) >= self.obi_entry
                     and self.min_entry <= cheap_price <= self.dip_max
                     and margin <= self.margin_max)
            if setup:
                slot["locked"] = True
                slot["dir"] = "LONG" if cheap_dir == 1 else "SHORT"
                slot["entry_price"] = cheap_price
                # TESHIS (filtreyi DEGISTIRMEZ): giris anindaki baglam.
                slot["entry_margin"] = abs(spot - strike)      # USD
                slot["entry_obi"] = obi
                slot["entry_whale"] = whale                    # balina CVD (giris ani)
                slot["entry_sec_left"] = WINDOW_SEC - sec_in
                log.info("[PAPER] REVERSAL GIRIS %s @ %.3f | OBI=%+.3f margin=%%%.4f kalan=%ds",
                         "UP" if cheap_dir == 1 else "DOWN", cheap_price, obi,
                         margin * 100, WINDOW_SEC - sec_in)
            elif sec_in >= WINDOW_SEC - 2:
                slot["locked"], slot["dir"] = True, "PAS"
        else:  # "obi": derinlik yonu
            if sec_in >= self.lock_at:
                slot["locked"] = True
                if poly_up is not None and poly_up >= 0 and abs(obi) >= self.obi_entry:
                    direction = "LONG" if obi > 0 else "SHORT"
                    price = poly_up if direction == "LONG" else (1.0 - poly_up)
                    if self.min_entry <= price <= self.value_max:
                        slot["dir"], slot["entry_price"] = direction, price
                        slot["entry_margin"] = abs(spot - strike) if strike > 0 else -1.0
                        slot["entry_obi"] = obi
                        slot["entry_whale"] = whale
                        slot["entry_sec_left"] = WINDOW_SEC - sec_in
                        log.info("[PAPER] OBI GIRIS %s @ %.3f (OBI=%+.3f)",
                                 "UP" if direction == "LONG" else "DOWN", price, obi)
                    else:
                        slot["dir"] = "PAS"
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
                "margin": tr.get("entry_margin", -1.0),   # giris baglami (teshis)
                "obi": tr.get("entry_obi", 0.0),
                "whale": tr.get("entry_whale", 0.0),
                "sec_left": tr.get("entry_sec_left", -1),
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
