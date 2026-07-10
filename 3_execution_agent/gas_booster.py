"""
gas_booster.py
GHOST ORACLE v5.0 :: Ajan 3.3 — Dinamik Gas Booster (EIP-1559).

Agdaki guncel base_fee'yi Web3 uzerinden ceker, front-run rekabeti icin
uzerine dinamik Priority Fee (+5 / +10 Gwei) ekler.

RPC erisilemezse DRY_RUN'in cokmemesi icin fallback base_fee kullanilir.
"""
from __future__ import annotations

import logging

log = logging.getLogger("exec.gas")

GWEI = 10**9

# RPC yoksa DRY_RUN simulasyonu icin makul Polygon fallback (Gwei).
FALLBACK_BASE_GWEI = 40


async def compute_gas(w3, priority_gwei: int = 5) -> dict:
    """
    EIP-1559 gas parametrelerini hesaplar.

    Doner:
      {
        "base_fee": <wei>,
        "maxPriorityFeePerGas": <wei>,
        "maxFeePerGas": <wei>,          # base*2 + priority (spike tamponu)
        "source": "chain" | "fallback",
      }
    """
    priority = int(priority_gwei) * GWEI
    source = "chain"

    try:
        block = await w3.eth.get_block("latest")
        base_fee = block.get("baseFeePerGas")
        if base_fee is None:
            raise ValueError("baseFeePerGas yok (pre-EIP1559 blok?)")
    except Exception as exc:
        log.warning("[GAS] base_fee cekilemedi (%s) — fallback %d Gwei",
                    exc, FALLBACK_BASE_GWEI)
        base_fee = FALLBACK_BASE_GWEI * GWEI
        source = "fallback"

    # Base fee sonraki blokta %12.5 artabilir; 2x tampon guvenli.
    max_fee = base_fee * 2 + priority

    return {
        "base_fee": base_fee,
        "maxPriorityFeePerGas": priority,
        "maxFeePerGas": max_fee,
        "source": source,
    }


def gwei(wei: int) -> float:
    """Wei -> Gwei (loglama icin)."""
    return wei / GWEI
