"""
straddle_meter.py
GHOST ORACLE v5.0 :: Ajan 2 — Cift-limit STRADDLE olcumu (islemsiz).

Kullanicinin nihai stratejisini PARA RISKE ATMADAN olcer:
  Kapanisa yakin, BTC fiyati Price-to-Beat'e cok yakinken (dar makas) ve piyasa
  iki yonlu sert/kararsiz hareket ederken, UP@0.25 ve DOWN@0.25 iki LIMIT emir
  birak. Whipsaw iki ucu da doldurursa maliyet 50c, garanti $1 -> +50c risksiz.
  Yarim dolarsa tek taraf kalir (yonsel risk).

Her 5dk pencerede, filtreye uyan an "emir birakildi" sayilir; kapanisa dek UP ve
DOWN fiyatlarinin 0.25'e degip degmedigi izlenir. Pencere kapaninca (Polymarket
open/close) gercek sonucla PnL hesaplanir. ISLEM ACILMAZ — sadece olcum.

Iki dolum modeli ayri ayri raporlanir:
  - iyimser (opt) : fiyat <=0.25'e bir an degdiyse doldu say
  - tutucu  (cons): <=0.25'te en az dwell_sec KALDIYSA doldu say

Cikti (kova x model): Denenen N, Iki-bacak %, Tek-bacak kazanma q%, EV/pencere.
  EV/pencere = p_iki*0.50 + p_tek*(q - 0.25)
"""
from __future__ import annotations

import logging
from collections import deque

log = logging.getLogger("brain.straddle")

WINDOW_SEC = 300  # kapanmis pencereler END zamaniyla anahtarli (start+300)
MODELS = ("opt", "cons")  # iyimser / tutucu


class StraddleMeter:
    def __init__(self, buckets=(5, 10, 20, 40), last_sec: int = 30,
                 vol_min: float = 15.0, vol_lookback: int = 20,
                 dwell_sec: float = 2.0, limit_price: float = 0.25) -> None:
        self.buckets = tuple(sorted(float(b) for b in buckets))
        self.last_sec = last_sec              # son N sn'de emir birak
        self.vol_min = vol_min                # min BTC araligi (kararsizlik esigi)
        self.vol_lookback_ms = vol_lookback * 1000.0
        self.dwell_ms = dwell_sec * 1000.0    # tutucu: <=limit'te kalma suresi
        self.limit = limit_price              # her iki bacak limit fiyati
        self.hi = 1.0 - limit_price           # DOWN@limit <=> UP>=hi

        self._vol_buf: deque = deque()        # (ts_ms, btc) — volatilite penceresi
        self.cur: dict | None = None          # aktif pencere durumu
        self.pending: list[dict] = []         # settle bekleyen (emir birakilmis) pencereler
        # Sayaclar: (bucket, model) -> {placed, both, oneup, onedown, none, one_win, profit}
        self.stats: dict = {(b, m): self._zero() for b in self.buckets for m in MODELS}
        self.recent: deque = deque(maxlen=40)  # son settle edilen pencereler (gosterim)
        self.live: dict = {}                   # son canli durum (snapshot icin)

    @staticmethod
    def _zero() -> dict:
        return {"placed": 0, "both": 0, "oneup": 0, "onedown": 0,
                "none": 0, "one_win": 0, "profit": 0.0}

    def _new_bucket_state(self) -> dict:
        return {"placed": False, "place_ts": 0.0,
                "up_min": 2.0, "up_max": -1.0,
                "up_below_since": None, "up_dwell": False,
                "down_below_since": None, "down_dwell": False}

    # ------------------------------------------------------------------ update
    def update(self, win_ts: int, now_sec: int, now_ms: float,
               btc: float, beat: float, poly_up: float, closed: dict) -> None:
        # 1) settle bekleyenler
        self._settle(closed)

        # 2) pencere rotasyonu
        if self.cur is None or self.cur["win"] != win_ts:
            if self.cur is not None and any(bs["placed"] for bs in self.cur["b"].values()):
                self.pending.append(self.cur)
            self.cur = {"win": win_ts,
                        "b": {b: self._new_bucket_state() for b in self.buckets}}
            self._vol_buf.clear()

        # 3) volatilite penceresi (son vol_lookback sn BTC araligi)
        vol = 0.0
        if btc > 0:
            self._vol_buf.append((now_ms, btc))
            while self._vol_buf and now_ms - self._vol_buf[0][0] > self.vol_lookback_ms:
                self._vol_buf.popleft()
            if self._vol_buf:
                prices = [p for _, p in self._vol_buf]
                vol = max(prices) - min(prices)

        sec_in = now_sec - win_ts
        time_ok = sec_in >= (WINDOW_SEC - self.last_sec)
        dist = abs(btc - beat) if (btc > 0 and beat > 0) else -1.0
        up_valid = (poly_up is not None) and (0.0 <= poly_up <= 1.0)
        down = (1.0 - poly_up) if up_valid else -1.0

        zone_flags = {}
        for b in self.buckets:
            bs = self.cur["b"][b]
            in_zone = (time_ok and dist >= 0.0 and dist <= b and vol >= self.vol_min)
            zone_flags[b] = in_zone

            # Emir birak (ilk uyan tick).
            if in_zone and not bs["placed"]:
                bs["placed"] = True
                bs["place_ts"] = now_ms
                if up_valid:
                    bs["up_min"] = bs["up_max"] = poly_up

            # Fill izleme (emir birakildiktan sonra, her gecerli poly_up tick'i).
            if bs["placed"] and up_valid:
                if poly_up < bs["up_min"]:
                    bs["up_min"] = poly_up
                if poly_up > bs["up_max"]:
                    bs["up_max"] = poly_up
                # Tutucu dwell — UP bacagi (<=limit)
                if poly_up <= self.limit:
                    if bs["up_below_since"] is None:
                        bs["up_below_since"] = now_ms
                    elif now_ms - bs["up_below_since"] >= self.dwell_ms:
                        bs["up_dwell"] = True
                else:
                    bs["up_below_since"] = None
                # Tutucu dwell — DOWN bacagi (down<=limit <=> up>=hi)
                if poly_up >= self.hi:
                    if bs["down_below_since"] is None:
                        bs["down_below_since"] = now_ms
                    elif now_ms - bs["down_below_since"] >= self.dwell_ms:
                        bs["down_dwell"] = True
                else:
                    bs["down_below_since"] = None

        # canli durum (dashboard)
        self.live = {
            "btc": btc, "beat": beat, "dist": dist, "vol": vol,
            "up": poly_up if up_valid else -1.0, "down": down,
            "sec_left": max(0, WINDOW_SEC - sec_in), "zone": zone_flags,
        }

    # ------------------------------------------------------------------ settle
    def _fills(self, bs: dict, model: str) -> tuple[bool, bool]:
        """(up_filled, down_filled) — modele gore."""
        if model == "opt":
            return (bs["up_min"] <= self.limit, bs["up_max"] >= self.hi)
        return (bs["up_dwell"], bs["down_dwell"])  # cons

    def _settle(self, closed: dict) -> None:
        still = []
        for w in self.pending:
            win = w["win"]
            w_end = win + WINDOW_SEC
            if w_end not in closed:
                still.append(w)
                continue
            o, c = closed[w_end]
            outcome = "UP" if c >= o else "DOWN"
            for b, bs in w["b"].items():
                if not bs["placed"]:
                    continue
                for m in MODELS:
                    up_f, down_f = self._fills(bs, m)
                    st = self.stats[(b, m)]
                    st["placed"] += 1
                    if up_f and down_f:
                        profit = 1.0 - 2.0 * self.limit   # +0.50 @ 0.25
                        st["both"] += 1
                    elif up_f:
                        won = outcome == "UP"
                        profit = (1.0 - self.limit) if won else -self.limit
                        st["oneup"] += 1
                        st["one_win"] += 1 if won else 0
                    elif down_f:
                        won = outcome == "DOWN"
                        profit = (1.0 - self.limit) if won else -self.limit
                        st["onedown"] += 1
                        st["one_win"] += 1 if won else 0
                    else:
                        profit = 0.0
                        st["none"] += 1
                    st["profit"] += profit
                # gosterim (iyimser modele gore ozet satiri)
                up_f, down_f = self._fills(bs, "opt")
                kind = ("iki" if (up_f and down_f) else
                        "tek-up" if up_f else "tek-down" if down_f else "hic")
                self.recent.append({"win": win, "bucket": b, "outcome": outcome,
                                    "kind": kind})
            log.info("[STRADDLE] settle pencere %d sonuc=%s", win, outcome)
        self.pending = still

    # ---------------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        d: dict = {}
        lv = self.live
        if lv:
            d["btc"] = f"{lv['btc']:.2f}"
            d["beat"] = f"{lv['beat']:.2f}"
            d["dist"] = f"{lv['dist']:.2f}" if lv["dist"] >= 0 else "-1"
            d["vol"] = f"{lv['vol']:.2f}"
            d["up"] = f"{lv['up']:.4f}" if lv["up"] >= 0 else "-1"
            d["down"] = f"{lv['down']:.4f}" if lv["down"] >= 0 else "-1"
            d["sec_left"] = str(lv["sec_left"])
            for b, fl in lv["zone"].items():
                d[f"zone{int(b)}"] = "1" if fl else "0"
        for (b, m), st in self.stats.items():
            n = st["placed"]
            one_tot = st["oneup"] + st["onedown"]
            both_pct = (st["both"] / n * 100.0) if n else 0.0
            q = (st["one_win"] / one_tot * 100.0) if one_tot else 0.0
            ev = (st["profit"] / n) if n else 0.0
            pre = f"b{int(b)}_{m}"
            d[f"{pre}_n"] = str(n)
            d[f"{pre}_both"] = f"{both_pct:.1f}"
            d[f"{pre}_q"] = f"{q:.1f}"
            d[f"{pre}_ev"] = f"{ev:.4f}"
        # son pencereler (kompakt string)
        if self.recent:
            d["recent"] = ";".join(
                f"{r['win']}|{int(r['bucket'])}|{r['outcome']}|{r['kind']}"
                for r in list(self.recent)[-12:])
        return d


# --------------------------------------------------------------------- smoke test
if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)

    def feed(m, win, series, btc_beat, closed_close):
        """series: [(sec_in, poly_up)] @ btc/beat sabit; sonra settle."""
        btc, beat = btc_beat
        t0 = win * 1000.0
        for sec_in, up in series:
            now_sec = win + sec_in
            now_ms = t0 + sec_in * 1000.0
            m.update(win, now_sec, now_ms, btc, beat, up, {})
        # settle: 1) rotasyon (cur -> pending), 2) bir tick sonra _settle pending'i isler
        closed = {win + WINDOW_SEC: (beat, closed_close)}
        nw = win + WINDOW_SEC
        m.update(nw, nw + 1, nw * 1000.0, btc, beat, 0.5, closed)  # rotate
        m.update(nw, nw + 2, nw * 1000.0 + 2000.0, btc, beat, 0.5, closed)  # settle

    # last_sec=30 => filtre sec_in>=270. vol_min=15, beat=64000, btc=64003 (dist=3<=5).
    # Volatiliteyi kurmak icin buf'a hareketli btc lazim; ama update btc sabit aliyor.
    # Bu yuzden testte vol_min=0 (dolum mantigini izole test et).
    m = StraddleMeter(buckets=(5, 10), last_sec=30, vol_min=0.0,
                      vol_lookback=20, dwell_sec=2.0, limit_price=0.25)

    # A) Whipsaw: UP 0.20'ye de DOWN 0.20'ye (up 0.80) de deger -> iki bacak. Sonuc UP.
    feed(m, 1000, [(272, 0.50), (274, 0.20), (276, 0.80), (278, 0.55)],
         (64003.0, 64000.0), 64010.0)
    # B) Sadece UP dip (0.20), DOWN hic (up hep <0.75). Sonuc DOWN -> tek-up KAYBEDER.
    feed(m, 2000, [(272, 0.40), (275, 0.20), (285, 0.30)],
         (64003.0, 64000.0), 63980.0)
    # C) Hicbir uc degmez (up hep ~0.5). -> hic
    feed(m, 3000, [(272, 0.50), (280, 0.52), (290, 0.48)],
         (64003.0, 64000.0), 64010.0)

    print("=== $5 kova / iyimser ===")
    st = m.stats[(5.0, "opt")]
    print(st)
    assert st["placed"] == 3, st
    assert st["both"] == 1, st       # A
    assert st["oneup"] == 1, st      # B
    assert st["none"] == 1, st       # C
    assert st["one_win"] == 0, st    # B tek-up ama sonuc DOWN -> kayip
    # EV = (0.50 + (-0.25) + 0.0)/3
    assert abs(st["profit"] - 0.25) < 1e-9, st
    print("profit_sum=%.4f  EV/pencere=%.4f" % (st["profit"], st["profit"] / 3))

    print("\n=== $5 kova / tutucu (dwell 2s) ===")
    stc = m.stats[(5.0, "cons")]
    print(stc)
    # A: up 0.20 @274, sonra 0.80 @276 -> up <=0.25 sadece 1 tick (dwell YOK).
    #    down (up>=0.75) sadece @276 1 tick -> dwell YOK. => tutucu: hic dolmaz.
    assert stc["both"] == 0, stc
    assert stc["none"] >= 1, stc
    print("tutucu daha az dolum (beklendigi gibi) — OK")

    print("\nsnapshot ornek:")
    snap = m.snapshot()
    for k in sorted(snap):
        print(f"  {k} = {snap[k]}")
    print("\nTUM ASSERT GECTI [OK]")
