"""
obi_compare_meter.py
GHOST ORACLE v5.0 :: Ajan 2 — Perp vs Spot OBI kiyas olcumu (islemsiz).

Soru: derinlik/OBI sinyalini VADELI (perp) defterden mi yoksa SPOT defterden mi
almak daha iyi tahmin ediyor? Her 5dk pencerede sabit noktada (sample_at) uc OBI'yi
ornekler; pencere kapaninca gercek sonucla (Polymarket open/close) kiyaslar:
  - perp : Binance(20 sv perp) + Bybit(perp) derinlik OBI
  - spot : Coinbase + Kraken + OKX(spot) OBI
  - mix  : trader'in kullandigi karma OBI (referans)
%55+ isabet -> edge var. Perp belirgin ustunse -> perp defterine yatirim mantikli.

ISLEM ACMAZ, trader'i ETKILEMEZ — sadece olcum.
"""
from __future__ import annotations

import logging

log = logging.getLogger("brain.obicmp")

WINDOW_SEC = 300
KINDS = ("perp", "spot", "mix")


class ObiCompareMeter:
    def __init__(self, sample_at_sec: int = 90) -> None:
        self.sample_at = sample_at_sec
        self.stats = {k: [0, 0] for k in KINDS}   # k -> [hits, total]
        self.pending: dict[int, dict] = {}          # win_ts -> {perp,spot,mix}
        self.last: dict | None = None

    def update(self, win_ts: int, now_sec: int,
               obi_perp: float, obi_spot: float, obi_mix: float,
               closed: dict) -> None:
        self._settle(closed)
        if win_ts not in self.pending and (now_sec - win_ts) >= self.sample_at:
            self.pending[win_ts] = {"perp": obi_perp, "spot": obi_spot, "mix": obi_mix}

    def _settle(self, closed: dict) -> None:
        done = []
        for w, rec in self.pending.items():
            we = w + WINDOW_SEC
            if we not in closed:
                continue
            o, c = closed[we]
            outcome = 1 if c >= o else -1          # UP / DOWN
            for k in KINDS:
                d = 1 if rec[k] >= 0 else -1
                self.stats[k][1] += 1
                self.stats[k][0] += 1 if d == outcome else 0
            self.last = {"win": w, "outcome": outcome, **rec}
            log.info("[OBICMP] pencere %d sonuc=%s | perp=%+.3f spot=%+.3f mix=%+.3f",
                     w, "UP" if outcome > 0 else "DOWN",
                     rec["perp"], rec["spot"], rec["mix"])
            done.append(w)
        for w in done:
            del self.pending[w]

    def snapshot(self) -> dict:
        def rate(k):
            h, t = self.stats[k]
            return f"{h / t * 100:.1f}" if t else "0.0"
        d = {}
        for k in KINDS:
            d[f"{k}_hit"] = rate(k)
            d[f"{k}_n"] = str(self.stats[k][1])
        return d


# --------------------------------------------------------------------- smoke test
if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    m = ObiCompareMeter(sample_at_sec=90)
    # 3 pencere: perp hep dogru, spot hep yanlis (uc durumu ayirt edebiliyor muyuz)
    #   win 1000: sonuc UP  (close>open). perp=+ (dogru), spot=- (yanlis), mix=+
    #   win 1300: sonuc DOWN.            perp=- (dogru), spot=+ (yanlis), mix=-
    #   win 1600: sonuc UP.             perp=+ (dogru), spot=- (yanlis), mix=+
    cases = [
        (1000, +0.5, -0.5, +0.3, 64000.0, 64010.0),  # UP
        (1300, -0.5, +0.5, -0.3, 64000.0, 63990.0),  # DOWN
        (1600, +0.5, -0.5, +0.3, 64000.0, 64010.0),  # UP
    ]
    closed = {}
    for w, p, s, mix, o, c in cases:
        m.update(w, w + 95, p, s, mix, closed)        # ornekle (sample_at=90)
        closed[w + WINDOW_SEC] = (o, c)
    # settle-only tur: sec_in=0 (ornek eklemez), sadece pending'i sonuclarla kapatir
    m.update(999999, 999999, 0, 0, 0, closed)
    snap = m.snapshot()
    print("snapshot:", snap)
    assert snap["perp_hit"] == "100.0", snap
    assert snap["spot_hit"] == "0.0", snap
    assert snap["perp_n"] == "3", snap
    print("OBICMP ASSERT GECTI [OK]")
