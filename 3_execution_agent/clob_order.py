"""
clob_order.py
GHOST ORACLE v5.0 :: Ajan 3.4 - Gercek Polymarket CLOB emri.

Polymarket emirleri off-chain CLOB'a EIP-712 imzali Order struct'i olarak
gonderilir. Bu modul:
  - build_order()  : sinyalden Order dict'i kurar
  - order_hash()   : EIP-712 mesaj hash'i uretir
  - sign_order()   : EIP-712 imzasi uretir
  - submit_order() : imzali emri CLOB API'ye POST eder
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time

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
    """
    Build a Polymarket CLOB order.

    maker is the Polymarket funder/proxy wallet when present; signer is the EOA
    that signs the order. If signer is not given, maker is used for both fields.
    """
    side = 0 if direction == "LONG" else 1
    signer = signer or maker
    price = max(min(price, 1.0), 1e-6)

    tokens = int((size_usdc / price) * USDC_DECIMALS)
    collateral = int(size_usdc * USDC_DECIMALS)
    if side == 0:
        maker_amt, taker_amt = collateral, tokens
    else:
        maker_amt, taker_amt = tokens, collateral

    return {
        "salt": int.from_bytes(secrets.token_bytes(32), "big") >> 8,
        "maker": maker,
        "signer": signer,
        "taker": ZERO_ADDR,
        "tokenId": int(token_id) if token_id else 0,
        "makerAmount": maker_amt,
        "takerAmount": taker_amt,
        "expiration": 0,
        "nonce": 0,
        "feeRateBps": env_int("POLY_FEE_BPS", 0),
        "side": side,
        "signatureType": env_int("SIGNATURE_TYPE", 0),
    }


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
        "message": order,
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


def _l2_headers(address: str, method: str, path: str, body: str) -> dict:
    api_key = env("POLY_API_KEY", "")
    secret = env("POLY_API_SECRET", "")
    passphrase = env("POLY_API_PASSPHRASE", "")
    if not (api_key and secret and passphrase):
        raise RuntimeError("POLY_API_KEY/SECRET/PASSPHRASE eksik - CLOB LIVE kapali.")

    ts = str(int(time.time()))
    msg = ts + method + path + body
    sig = base64.urlsafe_b64encode(
        hmac.new(base64.urlsafe_b64decode(secret), msg.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "POLY_ADDRESS": address,
        "POLY_SIGNATURE": sig,
        "POLY_TIMESTAMP": ts,
        "POLY_API_KEY": api_key,
        "POLY_PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }


async def submit_order(order: dict, signature: str, address: str, timeout_sec: float = 3.0) -> dict:
    import asyncio
    import json

    import requests

    base = env("CLOB_API", "https://clob.polymarket.com")
    path = "/order"
    payload = {
        "order": {**order, "signature": signature},
        "owner": env("POLY_API_KEY", ""),
        "orderType": "GTC",
    }
    body = json.dumps(payload, separators=(",", ":"))
    headers = _l2_headers(address, "POST", path, body)

    def _post() -> dict:
        r = requests.post(base + path, data=body, headers=headers, timeout=timeout_sec)
        if r.status_code >= 400:
            detail = (r.text or "").strip().replace("\r", " ").replace("\n", " ")
            if len(detail) > 240:
                detail = detail[:237] + "..."
            raise RuntimeError(f"{r.status_code} {r.reason} {detail}".strip())
        return r.json()

    return await asyncio.wait_for(asyncio.to_thread(_post), timeout=timeout_sec + 1)
