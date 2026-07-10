"""
synthetic_oracle.py
GHOST ORACLE v5.0 :: Ajan 2.2 — Sentetik Kuresel Fiyat (P_cex).

KISIT: Sadece NumPy vektorizasyonu (C-level). Python 'for' YASAK.
Hacim agirlikli ortalama (VWAP mantigi). Su an tek kaynak (Binance) gelse de
cok kaynak/cok seviye icin uyumlu yazilmistir.
"""
from __future__ import annotations

import numpy as np


def compute_pcex(prices: np.ndarray, vols: np.ndarray) -> float:
    """
    Hacim agirlikli sentetik fiyat:
        P_cex = sum(price_i * vol_i) / sum(vol_i)

    prices ve vols ayni uzunlukta NumPy dizileri (bid+ask seviyeleri birlesik).
    Vektorel np.dot; sifir hacim korumali.
    """
    total_vol = np.sum(vols, dtype=np.float64)
    if total_vol == 0.0:
        # hacim yoksa duz aritmetik ortalamaya dus (yine vektorel).
        return float(np.mean(prices)) if prices.size else 0.0

    weighted = np.dot(prices.astype(np.float64), vols.astype(np.float64))
    return float(weighted / total_vol)
