"""
spread_model.py
GHOST ORACLE v5.0 :: Ajan 2.4 — Sentetik fiyat <-> Polymarket spread makasi.

Polymarket fiyati bir olasiliktir (0..1). Sentetik CEX fiyati (P_cex, orn ~65000)
ile kiyaslamak icin P_cex bir "fair olasilik"a esitlenir.

!!! PLACEHOLDER MODEL !!!
Buradaki lineer esleme GERCEK bir fiyatlama modeli DEGILDIR — yalnizca plumbing'i
calistirmak icindir. Gercek strateji, ilgili Polymarket sorusuna gore bir model
(orn. bir esik/strike'a gore lojistik/opsiyon fiyatlamasi) kullanmalidir.
LIVE'a gecmeden bu fonksiyon gercek modelle degistirilmelidir.
"""
from __future__ import annotations


def fair_probability(p_cex: float, base: float, scale: float) -> float:
    """
    P_cex -> fair olasilik [0,1] (lineer PLACEHOLDER).
      fair = clip(scale * (p_cex - base), 0, 1)
    base : olasiligin 0 oldugu referans fiyat
    scale: fiyat basina olasilik egimi
    """
    fair = scale * (p_cex - base)
    if fair < 0.0:
        return 0.0
    if fair > 1.0:
        return 1.0
    return fair


def cross_spread(p_cex: float, p_poly: float, base: float, scale: float) -> float:
    """
    Isaretli capraz-pazar makasi = fair(P_cex) - P_poly.
      > 0 : model, piyasadan daha yuksek olasilik veriyor (YES ucuz -> LONG egilimi)
      < 0 : model daha dusuk (YES pahali -> SHORT egilimi)
    """
    return fair_probability(p_cex, base, scale) - p_poly
