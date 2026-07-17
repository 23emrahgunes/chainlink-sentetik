"""Environment alias helpers for legacy PM_EDGE_* settings."""
from __future__ import annotations

import os

ALIASES = {
    "TRADING_MODE": ("PM_EDGE_MOMENTUM_EXECUTION_MODE",),
    "ORDER_USDC": ("PM_EDGE_MOMENTUM_NOTIONAL_USDC",),
    "MAX_ORDER_USDC": ("PM_EDGE_MOMENTUM_MAX_LIVE_NOTIONAL_USDC",),
    "MAX_LIVE_ENTRY_CENTS": ("PM_EDGE_MAX_LIVE_ENTRY_CENTS", "PM_EDGE_MOMENTUM_MAX_ENTRY_CENTS"),
    "WALLET_PRIVATE_KEY": ("PM_EDGE_PRIVATE_KEY",),
    "FUNDER_ADDRESS": ("PM_EDGE_FUNDER_ADDRESS",),
    "POLY_API_KEY": ("PM_EDGE_CLOB_API_KEY", "POLY_CLOB_API_KEY"),
    "POLY_API_SECRET": ("PM_EDGE_CLOB_API_SECRET", "POLY_CLOB_SECRET"),
    "POLY_API_PASSPHRASE": ("PM_EDGE_CLOB_API_PASSPHRASE", "POLY_CLOB_PASSPHRASE"),
    "CLOB_API": ("PM_EDGE_CLOB_HOST",),
    "POLYGON_CHAIN_ID": ("PM_EDGE_CHAIN_ID",),
    "TX_TIMEOUT_SEC": ("PM_EDGE_CLOB_HTTP_TIMEOUT_SECONDS",),
    "POLYMARKET_CONTRACT": ("PM_EDGE_POLYMARKET_CONTRACT", "PM_EDGE_EXCHANGE_CONTRACT"),
    "POLYMARKET_EXCHANGE": ("PM_EDGE_POLYMARKET_EXCHANGE", "PM_EDGE_EXCHANGE_CONTRACT"),
    "POLYMARKET_TOKEN_ID": ("PM_EDGE_POLYMARKET_TOKEN_ID",),
    "POLY_FEE_BPS": ("PM_EDGE_FEE_BPS",),
    "SIGNATURE_TYPE": ("PM_EDGE_SIGNATURE_TYPE",),
}


def env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value not in (None, ""):
        return value
    for alias in ALIASES.get(name, ()):  # first legacy value wins
        value = os.getenv(alias)
        if value not in (None, ""):
            return value
    return default


def env_float(name: str, default: float = 0.0) -> float:
    try:
        return float(env(name, str(default)))
    except (TypeError, ValueError):
        return default


def env_int(name: str, default: int = 0) -> int:
    try:
        return int(float(env(name, str(default))))
    except (TypeError, ValueError):
        return default


def normalized_mode(default: str = "DRY_RUN") -> str:
    raw = env("TRADING_MODE", default).strip().upper()
    if raw in {"DRY", "DRYRUN", "DRY_RUN", "PAPER"}:
        return "DRY_RUN"
    if raw in {"LIVE", "REAL", "TRUE", "1"}:
        return "LIVE"
    return raw or default
