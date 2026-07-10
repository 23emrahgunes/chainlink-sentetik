"""
risk_guard.py
GHOST ORACLE v5.0 :: Ajan 3.2 — Slippage (Kayma) Guard.

Hedeflenen fiyat ile tahtadaki likiditeyi kiyaslar. Emir boyutu, mevcut
likidite derinligine gore bir fiyat etkisi (price impact) yaratir; hesaplanan
kayma esigi (%1) asarsa islem REDDEDILIR.

DRY_RUN'da gercek Polymarket defteri yok; likidite derinligi .env'den simule
edilir (POLY_LIQUIDITY_USDC). Canli entegrasyonda defter on-chain/CLOB'dan gelir.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("exec.risk")


@dataclass
class RiskDecision:
    approved: bool
    slippage: float          # oransal (0.008 = %0.8)
    est_fill_price: float
    reason: str

    @property
    def label(self) -> str:
        return "APPROVED" if self.approved else "REJECTED"


def _price_impact(order_usdc: float, liquidity_usdc: float) -> float:
    """
    Lineer fiyat-etkisi modeli: impact = order / likidite (kucuk emirler icin
    gercekci, sezgisel). Derin likiditede kucuk, sig likiditede buyuk kayma.
    [0, 1] araligina sabitlenir.
    """
    if liquidity_usdc <= 0:
        return 1.0  # likidite yok -> maksimum etki
    return min(order_usdc / liquidity_usdc, 1.0)


def check_slippage(
    direction: str,
    target_price: float,
    order_usdc: float,
    liquidity_usdc: float,
    threshold: float = 0.01,
) -> RiskDecision:
    """
    Slippage guard. threshold (varsayilan %1) asilirsa REJECTED doner.
    LONG'da fiyat yukari, SHORT'ta asagi kayar; mutlak kayma esikle kiyaslanir.
    """
    if target_price <= 0:
        return RiskDecision(False, 1.0, 0.0, "gecersiz hedef fiyat")

    impact = _price_impact(order_usdc, liquidity_usdc)
    # Yone gore fill fiyati (LONG alirken pahalanir, SHORT satarken ucuzlar).
    if direction == "LONG":
        est_fill = target_price * (1.0 + impact)
    else:
        est_fill = target_price * (1.0 - impact)

    slippage = abs(est_fill - target_price) / target_price
    approved = slippage <= threshold
    reason = (
        f"kayma %{slippage*100:.3f} <= esik %{threshold*100:.2f}"
        if approved
        else f"kayma %{slippage*100:.3f} > esik %{threshold*100:.2f} - RED"
    )
    return RiskDecision(approved, slippage, est_fill, reason)
