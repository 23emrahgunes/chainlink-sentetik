"""
dex_feed.py
GHOST ORACLE v5.0 :: Ajan 2 — DEX alim/satim akisi (Uniswap V3, GeckoTerminal).

On-chain pool okuma (RPC + ABI) yerine GeckoTerminal REST: DEX havuzunun
BTC-yonlu alim/satim islem akisi + fiyat + likidite. RPC/ABI riski yok.

DEX flow: (BTC_alim - BTC_satim)/(toplam), base token WBTC degilse yon cevrilir.
NOT: DEX BTC sinyali CEX'ten zayiftir (WBTC arb ile pegli; DEX CEX'i takip eder) —
bilgi/teyit amacli; ana sinyal CEX OBI.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.request

log = logging.getLogger("brain.dex")


class DexFeed:
    def __init__(self, network: str = "arbitrum",
                 pool: str = "0x0e4831319a50228b9e450861297ab92dee15b44f",
                 timeframe: str = "m15", poll_sec: float = 20.0) -> None:
        self.network = network
        self.pool = pool
        self.tf = timeframe
        self._poll = poll_sec
        self.flow = 0.0        # BTC-yonlu akis imbalance (-1..+1)
        self.price = 0.0       # WBTC USD
        self.liquidity = 0.0   # havuz rezerv USD
        self.buys = 0          # BTC alim islem sayisi
        self.sells = 0

    def _fetch(self) -> tuple:
        url = (f"https://api.geckoterminal.com/api/v2/networks/{self.network}"
               f"/pools/{self.pool}?include=base_token,quote_token")
        req = urllib.request.Request(url, headers={"User-Agent": "ghost-oracle",
                                                   "Accept": "application/json"})
        d = json.load(urllib.request.urlopen(req, timeout=8))
        a = d["data"]["attributes"]
        inc = {i["id"]: i["attributes"].get("symbol", "") for i in d.get("included", [])}
        base_id = d["data"]["relationships"]["base_token"]["data"]["id"]
        base_sym = inc.get(base_id, "")
        btc_is_base = "BTC" in base_sym.upper()

        tx = a.get("transactions", {}).get(self.tf, {})
        buys = int(tx.get("buys") or 0)
        sells = int(tx.get("sells") or 0)
        # BTC yonune cevir (base USDC ise buys=BTC satimi)
        btc_buys = buys if btc_is_base else sells
        btc_sells = sells if btc_is_base else buys
        tot = btc_buys + btc_sells
        flow = (btc_buys - btc_sells) / tot if tot else 0.0

        price_key = "base_token_price_usd" if btc_is_base else "quote_token_price_usd"
        price = float(a.get(price_key) or 0.0)
        liq = float(a.get("reserve_in_usd") or 0.0)
        return flow, price, liq, btc_buys, btc_sells

    async def run(self, stop) -> None:
        log.info("[DEX] GeckoTerminal akis fetcher basladi (%s/%s)", self.network, self.pool[:10])
        while not stop.is_set():
            try:
                f, p, l, b, s = await asyncio.to_thread(self._fetch)
                self.flow, self.price, self.liquidity, self.buys, self.sells = f, p, l, b, s
            except Exception as exc:
                log.error("[DEX] fetch hatasi: %s", exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._poll)
            except asyncio.TimeoutError:
                pass
