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


def share_quantity(stake: float, entry_price: float) -> float:
    if entry_price > 0:
        return stake / entry_price
    return 0.0


def payout_profit(stake: float, entry_price: float, won: bool) -> float:
    if won and entry_price > 0:
        return stake * (1.0 / entry_price - 1.0)
    return -stake


class StrongObiSimulator:
    """What-if simulator: enter once per 5m window when strong OBI supports a reversal."""

    def __init__(self, stake: float = 1.0, obi_entry: float = 0.25,
                 min_entry: float = 0.02, max_entry: float = 0.20,
                 min_sec_left: int = 45, max_sec_left: int = 90,
                 distance_max_usd: float = 200.0, spot_min: float = 0.0,
                 whale_min: float = 0.0, perp_against_max: float = 0.08) -> None:
        self.stake = stake
        self.obi_entry = obi_entry
        self.min_entry = min_entry
        self.max_entry = max_entry
        self.min_sec_left = min_sec_left
        self.max_sec_left = max_sec_left
        self.distance_max_usd = distance_max_usd
        self.spot_min = spot_min
        self.whale_min = whale_min
        self.perp_against_max = perp_against_max
        self.pending: list[dict] = []
        self.open_windows: set[int] = set()
        self.settled_windows: set[int] = set()
        self._to_publish: list[dict] = []
        self.pnl = 0.0
        self.trades = 0
        self.wins = 0
        self.losses = 0
        self.entry_sum = 0.0
        self.bands = [
            {"label": "0-10c", "lo": 0.0, "hi": 0.10, "n": 0, "wins": 0, "net": 0.0},
            {"label": "10-20c", "lo": 0.10, "hi": 0.20, "n": 0, "wins": 0, "net": 0.0},
            {"label": "20c+", "lo": 0.20, "hi": 1.01, "n": 0, "wins": 0, "net": 0.0},
        ]

    def update(self, win_ts: int, now_sec: int, obi: float, poly_up: float,
               spot: float, strike: float, closed: dict, whale: float = 0.0,
               context: dict | None = None) -> None:
        context = context or {}
        self._settle_pending(closed)
        if win_ts in self.open_windows or win_ts in self.settled_windows:
            return
        if poly_up is None or poly_up < 0 or spot <= 0 or strike <= 0:
            return
        sec_left = max(0, win_ts + WINDOW_SEC - now_sec)
        if sec_left < self.min_sec_left or sec_left > self.max_sec_left:
            return
        if abs(obi) < self.obi_entry:
            return
        distance = abs(spot - strike)
        if self.distance_max_usd > 0 and distance > self.distance_max_usd:
            return
        # Reversal direction must point back toward price-to-beat.
        if spot > strike and obi >= 0:
            return
        if spot < strike and obi <= 0:
            return
        direction = "LONG" if obi > 0 else "SHORT"
        spot_obi = float(context.get("spot_obi", 0.0) or 0.0)
        perp_delta = float(context.get("perp_obi_delta", 0.0) or 0.0)
        if direction == "LONG" and spot_obi < self.spot_min:
            return
        if direction == "SHORT" and spot_obi > -self.spot_min:
            return
        if direction == "LONG" and whale < self.whale_min:
            return
        if direction == "SHORT" and whale > -self.whale_min:
            return
        if direction == "LONG" and perp_delta < -self.perp_against_max:
            return
        if direction == "SHORT" and perp_delta > self.perp_against_max:
            return
        price = poly_up if direction == "LONG" else 1.0 - poly_up
        if price < self.min_entry or price > self.max_entry:
            return
        rec = {
            "status": "OPEN",
            "win": win_ts,
            "dir": direction,
            "outcome": "",
            "share": "UP" if direction == "LONG" else "DOWN",
            "result": "",
            "market_label": "BTC Up/Down 5m",
            "p_cex": spot,
            "entry": price,
            "entry_cents": price * 100.0,
            "share_qty": share_quantity(self.stake, price),
            "won": False,
            "profit": 0.0,
            "pnl_after": self.pnl,
            "obi": obi,
            "beat_path_obi": context.get("beat_path_obi", obi),
            "spot_obi": spot_obi,
            "perp_obi_delta": perp_delta,
            "distance_to_beat": distance,
            "sec_left": sec_left,
            "entry_score": context.get("entry_score", 0.0),
            "entry_reason": context.get("entry_reason", ""),
            "whale": whale,
        }
        self.open_windows.add(win_ts)
        self.pending.append(rec)
        self._to_publish.append(rec.copy())
        log.info("[STRONG_OBI] WHAT-IF GIRIS %s @ %.3f | OBI=%+.3f distance=$%.1f kalan=%ds",
                 rec["share"], price, obi, distance, sec_left)

    def _settle_pending(self, closed: dict) -> None:
        still = []
        for rec in self.pending:
            win_ts = rec["win"]
            end_ts = win_ts + WINDOW_SEC
            if end_ts not in closed:
                still.append(rec)
                continue
            o, c = closed[end_ts]
            outcome = "LONG" if c >= o else "SHORT"
            won = rec["dir"] == outcome
            profit = payout_profit(self.stake, rec["entry"], won)
            self.pnl += profit
            self.trades += 1
            self.wins += 1 if won else 0
            self.losses += 0 if won else 1
            self.entry_sum += rec["entry"]
            self.settled_windows.add(win_ts)
            settled = {**rec, "status": "SETTLED", "outcome": outcome,
                       "result": "UP" if outcome == "LONG" else "DOWN",
                       "won": won, "profit": profit, "pnl_after": self.pnl}
            self._add_band(rec["entry"], won, profit)
            self._to_publish.append(settled)
            log.info("[STRONG_OBI] SETTLE %d: tahmin=%s sonuc=%s -> %s %+.3f$ | net=%.3f$",
                     win_ts, rec["share"], settled["result"], "KAZANDI" if won else "KAYBETTI", profit, self.pnl)
        self.pending = still

    def _add_band(self, entry: float, won: bool, profit: float) -> None:
        entry = round(float(entry), 6)
        for band in self.bands:
            if band["lo"] <= entry < band["hi"]:
                band["n"] += 1
                band["wins"] += 1 if won else 0
                band["net"] += profit
                return

    def drain(self) -> list[dict]:
        recs, self._to_publish = self._to_publish, []
        return recs

    def snapshot(self) -> dict:
        hit = (self.wins / self.trades * 100.0) if self.trades else 0.0
        avg_entry = (self.entry_sum / self.trades) if self.trades else 0.0
        data = {
            "trades": str(self.trades),
            "wins": str(self.wins),
            "losses": str(self.losses),
            "hit": f"{hit:.1f}",
            "pnl": f"{self.pnl:.4f}",
            "avg_entry": f"{avg_entry:.4f}",
            "open": str(len(self.pending)),
        }
        for idx, band in enumerate(self.bands):
            n = band["n"]
            data[f"band{idx}_label"] = band["label"]
            data[f"band{idx}_n"] = str(n)
            data[f"band{idx}_wins"] = str(band["wins"])
            data[f"band{idx}_hit"] = f"{(band['wins'] / n * 100.0) if n else 0.0:.1f}"
            data[f"band{idx}_net"] = f"{band['net']:.4f}"
            data[f"band{idx}_ev"] = f"{(band['net'] / n) if n else 0.0:.4f}"
        return data


class PaperTrader:
    def __init__(self, stake: float = 1.0, obi_entry: float = 0.25,
                 value_max: float = 0.90, min_entry: float = 0.05,
                 lock_at_sec: int = 90, strategy: str = "dip",
                 dip_max: float = 0.30, reversal_window_sec: int = 60,
                 distance_max_usd: float = 80.0, margin_max: float = 0.0012) -> None:
        self.stake = stake
        self.strategy = strategy      # "dip" (OBI-teyitli reversal) | "obi" (derinlik yonu)
        self.obi_entry = obi_entry    # reversal teyidi icin |OBI| esigi
        self.value_max = value_max
        self.min_entry = min_entry    # cok dusuk = bayat/degenere -> girme
        self.dip_max = dip_max        # ucuz taraf bu ustundeyse dip degil (market ~50/50)
        self.lock_at = lock_at_sec    # obi modu: karar zamani
        self.rev_win = reversal_window_sec  # dip: son N sn'de reversal ara
        self.distance_max_usd = distance_max_usd  # |spot-beat| USD mesafesi bu altindaysa flip mumkun
        self.margin_max = margin_max              # legacy fallback: distance_max_usd <= 0 ise kullanilir
        self.pnl = 0.0
        self.trades = 0
        self.wins = 0
        self.losses = 0
        self.open_trade: dict | None = None   # aktif pencere slotu
        self.pending: list[dict] = []         # settle bekleyen (girilmis) islemler
        self.last: dict | None = None         # son settle sonucu (gosterim)
        self._to_publish: list[dict] = []     # yeni settle edilenler (stream:trades icin)

    def update(self, win_ts: int, now_sec: int, obi: float, poly_up: float,
               spot: float, strike: float, closed: dict, whale: float = 0.0,
               context: dict | None = None) -> None:
        context = context or {}
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
            distance = abs(spot - strike)
            margin = distance / strike
            max_distance = self.distance_max_usd if self.distance_max_usd > 0 else strike * self.margin_max
            up_price, down_price = poly_up, 1.0 - poly_up
            cheap_dir = 1 if up_price <= down_price else -1
            cheap_price = up_price if cheap_dir == 1 else down_price
            obi_dir = 1 if obi >= 0 else -1
            # KURULUM: ucuz taraf=kaybeden yon + OBI donusu teyit + mesafe yakin + oran dip
            setup = (cheap_dir == -price_dir
                     and obi_dir == cheap_dir
                     and abs(obi) >= self.obi_entry
                     and self.min_entry <= cheap_price <= self.dip_max
                     and distance <= max_distance)
            if setup:
                slot["locked"] = True
                slot["dir"] = "LONG" if cheap_dir == 1 else "SHORT"
                slot["entry_price"] = cheap_price
                # TESHIS (filtreyi DEGISTIRMEZ): giris anindaki baglam.
                slot["entry_margin"] = abs(spot - strike)      # USD
                slot["entry_spot"] = spot
                slot["entry_obi"] = obi
                slot["entry_whale"] = whale                    # balina CVD (giris ani)
                slot["entry_sec_left"] = WINDOW_SEC - sec_in
                slot["entry_context"] = context.copy()
                self._to_publish.append(self._open_record(slot))
                log.info("[PAPER] REVERSAL GIRIS %s @ %.3f | OBI=%+.3f distance=$%.1f margin=%%%.4f kalan=%ds",
                         "UP" if cheap_dir == 1 else "DOWN", cheap_price, obi,
                         distance, margin * 100, WINDOW_SEC - sec_in)
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
                        slot["entry_spot"] = spot
                        slot["entry_obi"] = obi
                        slot["entry_whale"] = whale
                        slot["entry_sec_left"] = WINDOW_SEC - sec_in
                        slot["entry_context"] = context.copy()
                        self._to_publish.append(self._open_record(slot))
                        log.info("[PAPER] OBI GIRIS %s @ %.3f (OBI=%+.3f)",
                                 "UP" if direction == "LONG" else "DOWN", price, obi)
                    else:
                        slot["dir"] = "PAS"
                else:
                    slot["dir"] = "PAS"

    def _open_record(self, tr: dict) -> dict:
        p = tr["entry_price"]
        ctx = tr.get("entry_context", {})
        share = "UP" if tr["dir"] == "LONG" else "DOWN"
        return {
            "status": "OPEN",
            "win": tr["win"], "dir": tr["dir"], "outcome": "",
            "share": share, "result": "",
            "market_label": "BTC Up/Down 5m",
            "p_cex": tr.get("entry_spot", 0.0),
            "entry_cents": p * 100.0,
            "share_qty": share_quantity(self.stake, p),
            "won": False, "profit": 0.0, "entry": p, "pnl_after": self.pnl,
            "margin": tr.get("entry_margin", -1.0),
            "obi": tr.get("entry_obi", 0.0),
            "whale": tr.get("entry_whale", 0.0),
            "sec_left": tr.get("entry_sec_left", -1),
            "distance_to_beat": ctx.get("distance_to_beat", -1.0),
            "required_velocity": ctx.get("required_velocity", 0.0),
            "realized_velocity": ctx.get("realized_velocity", 0.0),
            "perp_obi": ctx.get("perp_obi", 0.0),
            "spot_obi": ctx.get("spot_obi", 0.0),
            "perp_obi_delta": ctx.get("perp_obi_delta", 0.0),
            "spot_obi_delta": ctx.get("spot_obi_delta", 0.0),
            "dex_flow": ctx.get("dex_flow", 0.0),
            "entry_score": ctx.get("entry_score", 0.0),
            "entry_reason": ctx.get("entry_reason", ""),
        }


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
            qty = share_quantity(self.stake, p)
            profit = payout_profit(self.stake, p, won)
            self.pnl += profit
            self.trades += 1
            self.wins += 1 if won else 0
            self.losses += 0 if won else 1
            ctx = tr.get("entry_context", {})
            share = "UP" if tr["dir"] == "LONG" else "DOWN"
            result = "UP" if outcome == "LONG" else "DOWN"
            rec = {
                "status": "SETTLED",
                "win": w, "dir": tr["dir"], "outcome": outcome,
                "share": share, "result": result,
                "market_label": "BTC Up/Down 5m",
                "p_cex": tr.get("entry_spot", 0.0),
                "entry_cents": p * 100.0,
                "share_qty": qty,
                "won": won, "profit": profit, "entry": p, "pnl_after": self.pnl,
                "margin": tr.get("entry_margin", -1.0),   # giris baglami (teshis)
                "obi": tr.get("entry_obi", 0.0),
                "whale": tr.get("entry_whale", 0.0),
                "sec_left": tr.get("entry_sec_left", -1),
                "distance_to_beat": ctx.get("distance_to_beat", -1.0),
                "required_velocity": ctx.get("required_velocity", 0.0),
                "realized_velocity": ctx.get("realized_velocity", 0.0),
                "perp_obi": ctx.get("perp_obi", 0.0),
                "spot_obi": ctx.get("spot_obi", 0.0),
                "perp_obi_delta": ctx.get("perp_obi_delta", 0.0),
                "spot_obi_delta": ctx.get("spot_obi_delta", 0.0),
                "dex_flow": ctx.get("dex_flow", 0.0),
                "entry_score": ctx.get("entry_score", 0.0),
                "entry_reason": ctx.get("entry_reason", ""),
            }
            self.last = rec
            self._to_publish.append(rec)
            log.info("[PAPER] SETTLE pencere %d: tahmin=%s sonuc=%s -> %s %+.3f$ | net=%.3f$",
                     w, "UP" if tr["dir"] == "LONG" else "DOWN",
                     "UP" if outcome == "LONG" else "DOWN",
                     "KAZANDI" if won else "KAYBETTI", profit, self.pnl)
        self.pending = still

    def drain(self) -> list[dict]:
        """Son update'ten bu yana acilan/settle edilen islemleri dondurur (yayin icin)."""
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
