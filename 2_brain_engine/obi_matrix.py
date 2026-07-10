"""
obi_matrix.py
GHOST ORACLE v5.0 :: Ajan 2.3 — Order Book Imbalance (OBI).

KISIT: Sadece NumPy vektorizasyonu (C-level). Python 'for' YASAK.
N-seviyeli defter icin uyumlu: Ajan 1 su an tek seviye gonderiyor,
diziler tek elemanli olabilir; kod cok seviyeye hazir.
"""
from __future__ import annotations

import numpy as np


def compute_obi(bid_vols: np.ndarray, ask_vols: np.ndarray) -> float:
    """
    Hacim dengesizligi skoru [-1.0, 1.0].
      +1.0 -> tamamen alis baskisi (bid agir)
      -1.0 -> tamamen satis baskisi (ask agir)
       0.0 -> denge

    OBI = (sum(bid) - sum(ask)) / (sum(bid) + sum(ask))

    Tamamen vektorel; sifir bolme korumali (np.divide + where).
    """
    bid_sum = np.sum(bid_vols, dtype=np.float64)
    ask_sum = np.sum(ask_vols, dtype=np.float64)
    total = bid_sum + ask_sum

    obi = np.divide(
        bid_sum - ask_sum,
        total,
        out=np.zeros((), dtype=np.float64),  # 0-boyutlu: float()'a guvenli cevrilir
        where=(total != 0.0),
    )
    # skoru [-1, 1] araligina sabitle (numerik guvenlik).
    return float(np.clip(obi, -1.0, 1.0))
