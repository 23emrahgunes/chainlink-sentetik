"""
poly_router.py
GHOST ORACLE v5.0 :: Ajan 3.4 — Polygon/Polymarket Web3 yonlendirici.

AsyncWeb3 ile Polygon RPC'ye baglanir; dummy router adresi + minimal ABI ile
emir Tx'i insa eder, imzalar ve gonderir.

KISIT: Private key ASLA koda gomulmez — yalnizca .env'den (WALLET_PRIVATE_KEY).
       Bu modul SADECE LIVE modda cagrilir.
"""
from __future__ import annotations

import logging

from web3 import AsyncWeb3, AsyncHTTPProvider

log = logging.getLogger("exec.router")

POLYGON_CHAIN_ID = 137

# Minimal dummy ABI — gercek Polymarket CLOB/CTF ABI'si canli fazda gelecek.
ROUTER_ABI = [
    {
        "name": "placeOrder",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "price", "type": "uint256"},
            {"name": "size", "type": "uint256"},
            {"name": "isLong", "type": "bool"},
        ],
        "outputs": [],
    }
]


class PolyRouter:
    def __init__(self, rpc_url: str, router_addr: str, private_key: str) -> None:
        if not private_key:
            raise ValueError("WALLET_PRIVATE_KEY bos — LIVE mod icin gerekli.")
        self.w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
        # Adres yoksa cagri seviyesinde yakalanir; checksum guvenligi:
        self.router_addr = (
            AsyncWeb3.to_checksum_address(router_addr) if router_addr else None
        )
        self.account = self.w3.eth.account.from_key(private_key)
        self._pk = private_key  # bellekte tutulur, loglanmaz

    async def is_connected(self) -> bool:
        try:
            return await self.w3.is_connected()
        except Exception:
            return False

    async def build_transaction(
        self,
        direction: str,
        price: int,
        size: int,
        gas: dict,
    ) -> dict:
        """
        placeOrder cagrisi icin EIP-1559 Tx dict'i insa eder.
        gas: gas_booster.compute_gas ciktisi (maxFeePerGas / maxPriorityFeePerGas).
        """
        if self.router_addr is None:
            raise ValueError("POLYMARKET_CONTRACT bos — LIVE mod icin gerekli.")

        contract = self.w3.eth.contract(address=self.router_addr, abi=ROUTER_ABI)
        nonce = await self.w3.eth.get_transaction_count(self.account.address)

        tx = await contract.functions.placeOrder(
            int(price), int(size), direction == "LONG"
        ).build_transaction(
            {
                "chainId": POLYGON_CHAIN_ID,
                "from": self.account.address,
                "nonce": nonce,
                "maxFeePerGas": gas["maxFeePerGas"],
                "maxPriorityFeePerGas": gas["maxPriorityFeePerGas"],
            }
        )
        return tx

    async def sign_and_send(self, tx: dict, timeout_sec: float = 3.0) -> str:
        """
        Tx'i imzalar, gonderir ve makbuzu timeout_sec icinde bekler.
        Zaman asiminda TimeoutError firlatir (cagiran tarafta cancel/loglama).
        Doner: tx hash (hex).
        """
        signed = self.account.sign_transaction(tx)
        # web3 v6: rawTransaction | v7: raw_transaction
        raw = getattr(signed, "raw_transaction", None) or getattr(
            signed, "rawTransaction"
        )
        tx_hash = await self.w3.eth.send_raw_transaction(raw)
        hex_hash = tx_hash.hex()
        log.info("[ROUTER] Tx gonderildi: %s (makbuz bekleniyor <%.1fs)",
                 hex_hash, timeout_sec)

        import asyncio

        await asyncio.wait_for(
            self.w3.eth.wait_for_transaction_receipt(tx_hash, poll_latency=0.2),
            timeout=timeout_sec,
        )
        return hex_hash
