"""
redis_consumer.py
GHOST ORACLE v5.0 :: Ajan 2.1 — Asenkron Redis Stream tuketicisi.

- redis.asyncio ile non-blocking XREAD BLOCK.
- stream:cex_l2 kanalini dinler.
- TTL Guvenlik Agi: ts (UnixMilli) su andan >1000ms eski ise kaydi DROP eder.
"""
from __future__ import annotations

import time
from typing import AsyncIterator

import redis.asyncio as redis

STREAM_CEX = "stream:cex_l2"
STREAM_SIGNALS = "stream:signals"

# TTL Guvenlik Agi esigi (ms). Bundan eski veri islenmez.
MAX_STALE_MS = 1000

# XREAD BLOCK suresi (ms). 0 = sonsuz; 1000 = 1sn'de bir loop'a nefes aldirir.
BLOCK_MS = 1000


class RedisConsumer:
    """stream:cex_l2 uzerinden temiz (taze) L2 kayitlarini async yield eder."""

    def __init__(self, addr: str, password: str = "") -> None:
        host, _, port = addr.partition(":")
        self._client = redis.Redis(
            host=host or "127.0.0.1",
            port=int(port or 6379),
            password=password or None,
            decode_responses=True,  # alanlar str olarak gelsin
            max_connections=8,
        )
        # Sadece en yeni veriyi istiyoruz; "$" = simdiden sonra gelenler.
        self._last_id = "$"
        self.dropped = 0  # gecikme nedeniyle atilan kayit sayaci

    @property
    def client(self) -> "redis.Redis":
        """Alttaki async Redis client'i (tek baglanti yeniden kullanimi)."""
        return self._client

    async def ping(self) -> bool:
        return bool(await self._client.ping())

    async def stream(self) -> AsyncIterator[dict]:
        """
        Sonsuz async generator. Her taze kayit icin parse edilmis dict verir.
        Gecikmis (>1000ms) kayitlar sessizce DROP edilir (yield edilmez).
        """
        while True:
            # BLOCK ile non-blocking bekleme; CPU spin yok.
            resp = await self._client.xread(
                {STREAM_CEX: self._last_id}, count=10, block=BLOCK_MS
            )
            if not resp:
                continue  # timeout — yeni veri yok, tekrar bekle

            now_ms = int(time.time() * 1000)
            # resp: [(stream_name, [(id, {field: val}), ...])]
            for _stream, entries in resp:
                for entry_id, fields in entries:
                    self._last_id = entry_id  # bir sonraki XREAD'in kursoru

                    # --- TTL Guvenlik Agi ---
                    try:
                        ts = int(fields.get("ts", 0))
                    except (TypeError, ValueError):
                        self.dropped += 1
                        continue
                    if now_ms - ts > MAX_STALE_MS:
                        self.dropped += 1
                        continue  # bayat veri — DROP

                    yield fields  # taze kayit

    async def close(self) -> None:
        await self._client.aclose()
