"""
clob_order.py
GHOST ORACLE v5.0 :: Ajan 3.4 — Gercek Polymarket CLOB emri.

Polymarket, emirleri off-chain CLOB'a EIP-712 imzali 'Order' struct'i olarak
gonderir (taker gaz odemez; eslesme operator tarafindan yapilir). Bu modul:
  - build_order()  : sinyalden yapisal olarak dogru Order dict'i kurar
  - order_hash()   : EIP-712 mesaj hash'ini hesaplar (private key GEREKMEZ -> DRY_RUN)
  - sign_order()   : EIP-712 imzasi uretir (LIVE, private key .env'den)
  - submit_order() : imzali emri CLOB API'ye POST eder (LIVE, L2 auth)

!!! DOGRULAMA (VERIFY) NOTLARI — LIVE'a gecmeden teyit et: !!!
  * POLYMARKET_EXCHANGE adresi (CTFExchange vs NegRisk) ve chainId.
  * makerAmount/takerAmount olcegi (USDC 6 hane) ve side kodlamasi.
  * L2 auth header semasi (POLY_* / HMAC) resmi py-clob-client ile.
Yanlis parametre fon kaybina yol acabilir. DRY_RUN plumbing'i tam ve GUVENLIDIR.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak, to_hex

log = logging.getLogger("exec.clob")

ZERO_ADDR = "0x0000000000000000000000000000000000000000"

# EIP-712 Order tipi (Polymarket CTF Exchange sozlesmesiyle ayni alan seti).
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

USDC_DECIMALS = 10**6  # VERIFY: Polymarket collateral (USDC) 6 hane


def _domain() -> dict:
    return {
        "name": "Polymarket CTF Exchange",
        "version": "1",
        "chainId": int(os.getenv("POLYGON_CHAIN_ID", "137")),
        # VERIFY: CTFExchange (Polygon). NegRisk pazarlari icin farkli adres.
        "verifyingContract": os.getenv(
            "POLYMARKET_EXCHANGE", "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
        ),
    }


def build_order(direction: str, token_id: str, price: float, size_usdc: float,
                maker: str = ZERO_ADDR) -> dict:
    """
    Sinyalden Order dict'i kurar.
      direction : "LONG" -> BUY(0), "SHORT" -> SELL(1)
      price     : olasilik 0..1 (Polymarket outcome fiyati)
      size_usdc : notional (USDC)
    VERIFY: maker/taker amount olcegi asagida basitlestirilmistir.
    """
    side = 0 if direction == "LONG" else 1
    price = max(min(price, 1.0), 1e-6)
    # BUY: maker USDC verir, taker token alir. SELL: tersi.
    tokens = int((size_usdc / price) * USDC_DECIMALS)
    collateral = int(size_usdc * USDC_DECIMALS)
    if side == 0:  # BUY
        maker_amt, taker_amt = collateral, tokens
    else:          # SELL
        maker_amt, taker_amt = tokens, collateral

    return {
        "salt": int.from_bytes(secrets.token_bytes(32), "big") >> 8,
        "maker": maker,
        "signer": maker,
        "taker": ZERO_ADDR,
        "tokenId": int(token_id) if token_id else 0,
        "makerAmount": maker_amt,
        "takerAmount": taker_amt,
        "expiration": 0,  # 0 = GTC (suresiz)
        "nonce": 0,
        "feeRateBps": int(os.getenv("POLY_FEE_BPS", "0")),
        "side": side,
        "signatureType": 0,  # 0 = EOA (ECDSA)
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
    """EIP-712 mesaj hash'i (private key GEREKMEZ) — DRY_RUN gosterimi icin."""
    signable = encode_typed_data(full_message=_typed_message(order))
    # EIP-712: keccak(0x1901 || domainSeparator || structHash) = header+body
    return to_hex(keccak(b"\x19\x01" + signable.header + signable.body))


def sign_order(order: dict, private_key: str) -> str:
    """Order'i EIP-712 ile imzalar; imza hex'i doner (LIVE)."""
    if not private_key:
        raise ValueError("WALLET_PRIVATE_KEY bos — LIVE imza icin gerekli.")
    signable = encode_typed_data(full_message=_typed_message(order))
    signed = Account.sign_message(signable, private_key=private_key)
    return signed.signature.hex()


def _l2_headers(address: str, method: str, path: str, body: str) -> dict:
    """
    Polymarket L2 auth header'lari (HMAC-SHA256).
    VERIFY: header adlari/format resmi py-clob-client ile teyit edilmeli.
    """
    api_key = os.getenv("POLY_API_KEY", "")
    secret = os.getenv("POLY_API_SECRET", "")
    passphrase = os.getenv("POLY_API_PASSPHRASE", "")
    if not (api_key and secret and passphrase):
        raise RuntimeError("POLY_API_KEY/SECRET/PASSPHRASE eksik — CLOB LIVE kapali.")

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


async def submit_order(order: dict, signature: str, address: str,
                       timeout_sec: float = 3.0) -> dict:
    """
    Imzali emri CLOB API'ye POST eder (LIVE). requests bloklamasini thread'e atar.
    VERIFY: endpoint/govde semasi resmi dokumana gore teyit edilmeli.
    """
    import asyncio
    import json

    import requests  # yalnizca LIVE'da import edilir

    base = os.getenv("CLOB_API", "https://clob.polymarket.com")
    path = "/order"
    payload = {
        "order": {**order, "signature": signature},
        "owner": os.getenv("POLY_API_KEY", ""),
        "orderType": "GTC",
    }
    body = json.dumps(payload, separators=(",", ":"))
    headers = _l2_headers(address, "POST", path, body)

    def _post() -> dict:
        r = requests.post(base + path, data=body, headers=headers, timeout=timeout_sec)
        r.raise_for_status()
        return r.json()

    return await asyncio.wait_for(asyncio.to_thread(_post), timeout=timeout_sec + 1)
