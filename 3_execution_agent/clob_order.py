"""
clob_order.py
GHOST ORACLE v5.0 :: Ajan 3.4 - Polymarket CLOB order helpers.

DRY paths can still build/hash an order-like dict for observability. LIVE submit
uses Polymarket's official py-clob-client-v2 so the wire payload and signature
schema stay aligned with the exchange API.
"""
from __future__ import annotations

import logging
import secrets

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak, to_hex

from env_alias import env, env_int

log = logging.getLogger("exec.clob")

ZERO_ADDR = "0x0000000000000000000000000000000000000000"

ORDER_TYPES = {
    "Order": [
        {"name": "salt", "type": "uint256"},
        {"name": "maker", "type": "address"},
        {"name": "signer", "type": "address"},
        {"name": "taker", "type": "address"},
        {"name": "tokenId", "type": "uint256"},
        {"name": "makerAmount", "type": "uint256"},
        {"name": "takerAmount", "type": "uint256"},
        {"name": "expiration", "type": "uint256"},
        {"name": "nonce", "type": "uint256"},
        {"name": "feeRateBps", "type": "uint256"},
        {"name": "side", "type": "uint8"},
        {"name": "signatureType", "type": "uint8"},
    ]
}

USDC_DECIMALS = 10**6


def _domain() -> dict:
    return {
        "name": "Polymarket CTF Exchange",
        "version": "1",
        "chainId": env_int("POLYGON_CHAIN_ID", 137),
        "verifyingContract": env(
            "POLYMARKET_EXCHANGE", "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
        ),
    }


def build_order(
    direction: str,
    token_id: str,
    price: float,
    size_usdc: float,
    maker: str = ZERO_ADDR,
    signer: str | None = None,
) -> dict:
    """Build an order-like dict. Live orders always buy the selected outcome token."""
    signer = signer or maker
    price = max(min(price, 1.0), 1e-6)
    shares = size_usdc / price
    collateral = int(size_usdc * USDC_DECIMALS)
    tokens = int(shares * USDC_DECIMALS)

    return {
        "salt": int.from_bytes(secrets.token_bytes(32), "big") >> 8,
        "maker": maker,
        "signer": signer,
        "taker": ZERO_ADDR,
        "tokenId": int(token_id) if token_id else 0,
        "makerAmount": collateral,
        "takerAmount": tokens,
        "expiration": 0,
        "nonce": 0,
        "feeRateBps": env_int("POLY_FEE_BPS", 0),
        "side": 0,
        "signatureType": env_int("SIGNATURE_TYPE", 0),
        "_direction": direction,
        "_price": price,
        "_size_usdc": size_usdc,
        "_shares": shares,
    }


def _signed_order_fields(order: dict) -> dict:
    return {k: v for k, v in order.items() if not k.startswith("_")}


def _typed_message(order: dict) -> dict:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            **ORDER_TYPES,
        },
        "primaryType": "Order",
        "domain": _domain(),
        "message": _signed_order_fields(order),
    }


def order_hash(order: dict) -> str:
    signable = encode_typed_data(full_message=_typed_message(order))
    return to_hex(keccak(b"\x19\x01" + signable.header + signable.body))


def sign_order(order: dict, private_key: str) -> str:
    if not private_key:
        raise ValueError("WALLET_PRIVATE_KEY bos - LIVE imza icin gerekli.")
    signable = encode_typed_data(full_message=_typed_message(order))
    signed = Account.sign_message(signable, private_key=private_key)
    return signed.signature.hex()


def _api_creds():
    key = env("POLY_API_KEY", "")
    secret = env("POLY_API_SECRET", "")
    passphrase = env("POLY_API_PASSPHRASE", "")
    if not (key and secret and passphrase):
        return None
    try:
        from py_clob_client_v2 import ApiCreds
        return ApiCreds(api_key=key, api_secret=secret, api_passphrase=passphrase)
    except Exception:
        return {"api_key": key, "api_secret": secret, "api_passphrase": passphrase}


def _sdk_submit_sync(order: dict) -> dict:
    from py_clob_client_v2 import ClobClient, OrderArgs, OrderType, PartialCreateOrderOptions, Side

    host = env("CLOB_API", "https://clob.polymarket.com")
    key = env("WALLET_PRIVATE_KEY", "")
    if not key:
        raise RuntimeError("WALLET_PRIVATE_KEY bos - CLOB LIVE kapali.")

    client = ClobClient(
        host,
        key=key,
        chain_id=env_int("POLYGON_CHAIN_ID", 137),
        creds=_api_creds(),
        signature_type=env_int("SIGNATURE_TYPE", 0),
        funder=env("FUNDER_ADDRESS", "") or order.get("maker") or None,
    )
    if _api_creds() is None:
        client.set_api_creds(client.create_or_derive_api_key())

    token_id = str(order["tokenId"])
    condition_id = ""
    try:
        parent = client.get_market_by_token(token_id)
        condition_id = parent.get("condition_id", "") if isinstance(parent, dict) else ""
    except Exception as exc:
        log.warning("CLOB market-by-token lookup failed: %s", exc)

    tick_size = env("POLY_TICK_SIZE", "0.01")
    neg_risk = str(env("POLY_NEG_RISK", "false")).strip().lower() in {"1", "true", "yes", "on"}
    if condition_id:
        try:
            market = client.get_market(condition_id)
            tick_size = str(market.get("minimum_tick_size") or tick_size)
            neg_risk = bool(market.get("neg_risk", neg_risk))
        except Exception as exc:
            log.warning("CLOB get_market lookup failed: %s", exc)

    return client.create_and_post_order(
        OrderArgs(
            token_id=token_id,
            price=float(order["_price"]),
            size=float(order["_shares"]),
            side=Side.BUY,
        ),
        options=PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk),
        order_type=OrderType.GTC,
    )


async def submit_order(order: dict, signature: str, address: str, timeout_sec: float = 3.0) -> dict:
    import asyncio

    return await asyncio.wait_for(asyncio.to_thread(_sdk_submit_sync, order), timeout=timeout_sec + 3)
