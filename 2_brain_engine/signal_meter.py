"""
signal_meter.py
GHOST ORACLE v5.0 :: Ajan 2 — OBI diverjans/isabet olcumu (islemsiz).

Her 5dk pencerede, sabit bir noktada (SIGNAL_SAMPLE_SEC) OBI yonunu kaydeder;
pencere kapaninca gercek sonucla (Polymarket open/close) kiyaslar. Boylece OBI'nin
gercek tahmin gucunu ISLEMSIZ olcer:
  - obi_hit    : OBI yonu sonucu tuttu mu (genel)
  - strong_hit : |OBI| guclu iken isabet
  - contra_hit : OBI market oraniyla TERS iken isabet (kontra edge var mi?)
%55+ isabet -> edge var; ~%50 -> yok.
"""
from __future__ import annotations

import logging

log = logging.getLogger("brain.meter")

WINDOW_SEC = 300


class SignalMeter:
    def __init__(self, sample_at_sec: int = 90, strong: float = 0.25) -> None:
        self.sample_at = sample_at_sec
        self.strong = strong
        self.total = self.hits = 0
        self.strong_total = self.strong_hits = 0
        self.contra_total = self.contra_hits = 0
        self.pending: dict[int, dict] = {}   # win_ts -> {obi, poly_up}
        self.last: dict | None = None

    def update(self, win_ts: int, now_sec: int, obi: float,
               poly_up: float, closed: dict) -> None:
        self._settle(closed)
        # Pencere basindan sample_at sn sonra OBI'yi bir kez ornekle.
        if win_ts not in self.pending and (now_sec - win_ts) >= self.sample_at:
            self.pending[win_ts] = {"obi": obi, "poly_up": poly_up}

    def _settle(self, closed: dict) -> None:
        done = []
        for w, rec in self.pending.items():
            we = w + WINDOW_SEC
            if we not in closed:
                continue
            o, c = closed[we]
            outcome = 1 if c >= o else -1          # UP / DOWN
            obi_dir = 1 if rec["obi"] >= 0 else -1
            hit = (obi_dir == outcome)
            self.total += 1
            self.hits += 1 if hit else 0
            if abs(rec["obi"]) >= self.strong:
                self.strong_total += 1
                self.strong_hits += 1 if hit else 0
            if rec["poly_up"] is not None and rec["poly_up"] >= 0:
                mkt_dir = 1 if rec["poly_up"] >= 0.5 else -1
                if obi_dir != mkt_dir:             # OBI market'e ters (kontra)
                    self.contra_total += 1
                    self.contra_hits += 1 if hit else 0
            self.last = {"win": w, "obi": rec["obi"], "outcome": outcome, "hit": hit}
            log.info("[METER] pencere %d: OBI=%+.3f yon=%s sonuc=%s -> %s (isabet %d/%d %%%.1f)",
                     w, rec["obi"], "UP" if obi_dir > 0 else "DOWN",
                     "UP" if outcome > 0 else "DOWN", "TUTTU" if hit else "TUTMADI",
                     self.hits, self.total, self.hits / self.total * 100)
            done.append(w)
        for w in done:
            del self.pending[w]

    def snapshot(self) -> dict:
        def rate(h, t):
            return f"{h / t * 100:.1f}" if t else "0.0"
        return {
            "obi_hit": rate(self.hits, self.total), "obi_n": str(self.total),
            "strong_hit": rate(self.strong_hits, self.strong_total),
            "strong_n": str(self.strong_total),
            "contra_hit": rate(self.contra_hits, self.contra_total),
            "contra_n": str(self.contra_total),
        }
