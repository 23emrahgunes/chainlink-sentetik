# 👻 GHOST ORACLE v5.0 — `chainlink-sentetik`

Yüksek frekanslı (HFT) **sentetik fiyat arbitraj** sistemi. Sentetik CEX küresel fiyatı (`P_cex`)
ile Polymarket (Polygon) fiyatı arasındaki makası (spread) yakalayıp tetikler.

**Hedef donanım:** Banana Pi M2 Pro (2GB RAM, Amlogic S905X3, ARM64)
**Kritik kısıt:** Zero-Disk I/O — tüm veri akışı RAM (Redis) üzerinde başlar ve biter.

---

## Mimari

```
                 ┌────────────────────────────────────────────────────────┐
                 │                      REDIS (in-memory)                   │
   CEX/DEX WSS   │   stream:cex_l2  ·  stream:signals  ·  stream:executions │
        │        └────────────────────────────────────────────────────────┘
        ▼             ▲  MAXLEN ~10 (zero-disk)   ▲               ▲   ▲
 ┌─────────────┐      │                           │               │   │
 │  1) INGEST  │──────┘ stream:cex_l2             │               │   │
 │   (Go)      │                                  │               │   │
 └─────────────┘                                  │               │   │
 ┌─────────────┐  okur cex_l2 → NumPy OBI/VWAP    │ yazar signals │   │
 │  2) BRAIN   │──────────────────────────────────┘               │   │
 │  (Python)   │                                                   │   │
 └─────────────┘                                                   │   │
 ┌─────────────┐  okur signals → risk+gas → DRY/LIVE   yazar executions │
 │  3) EXEC    │──────────────────────────────────────────────────┘   │
 │  (Python)   │                                                       │
 └─────────────┘                                                       │
 ┌─────────────┐  3 stream'i dinler → WebSocket → tarayıcı (Zero-CPU)  │
 │  4) DASHBOARD├──────────────────────────────────────────────────────┘
 │ (FastAPI/JS)│
 └─────────────┘
```

| # | Ajan | Dil | Görev |
|---|------|-----|-------|
| 1 | `1_ingestion_agents/` | Go | **5 CEX** WSS (Binance/Bybit/OKX/Coinbase/Kraken) + gerçek Polygon mempool (`eth_subscribe`) + Polymarket CLOB feed → `stream:cex_l2` / `stream:dex_mempool` / `stream:polymarket` (`sync.Pool`, `MAXLEN ~10`) |
| 2 | `2_brain_engine/` | Python | NumPy: OBI + hacim-ağırlıklı `P_cex`; Polymarket feed ile çapraz-pazar spread → `stream:signals` |
| 3 | `3_execution_agent/` | Python | Slippage guard + gas booster; **gerçek Polymarket CLOB emri (EIP-712 imza)**; DRY_RUN simüle / LIVE POST → `stream:executions` |
| 4 | `4_dashboard/` | FastAPI + Alpine.js | 3 stream'i WebSocket ile tarayıcıya pass-through; Zero-CPU render |

---

## Çalışma Modları (`.env → TRADING_MODE`)

- **`DRY_RUN`** (varsayılan): Execution sinyali alır, slippage + gas hesaplar ama Web3'e **emir göndermez**. Tam simülasyon.
- **`LIVE`**: Sinyaller Polygon'da gerçek cüzdan imzasıyla on-chain işlenir (3s Tx timeout korumalı).
  Private key **yalnızca `.env`'den** okunur, koda asla gömülmez.

---

## Kurulum & Çalıştırma

### 0. Ön koşullar
Docker, Go 1.22+, Python 3.11+. `.env` dosyasını kontrol et (`TRADING_MODE=DRY_RUN` ile gelir).

### 1. In-memory Redis (kök dizin)
```bash
docker compose up -d          # redis:alpine, --save "" + --appendonly no, mem 600M
docker compose ps             # ghost-redis "healthy" olmalı
```

### 2. Ajan 1 — Ingestion (Go)
```bash
cd 1_ingestion_agents
go mod tidy
go run .                      # veya: go build -o ghost-ingestion . && ./ghost-ingestion
# ARM64 cross-compile: GOOS=linux GOARCH=arm64 go build -o ghost-ingestion .
```

### 3. Ajan 2 — Brain (Python)
```bash
cd 2_brain_engine
python -m venv .venv && source .venv/bin/activate   # Win: .venv\Scripts\activate
pip install -r requirements.txt
python main_brain.py
```

### 4. Ajan 3 — Execution (Python)
```bash
cd 3_execution_agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main_execution.py
```

### 5. Ajan 4 — Dashboard (Python)
```bash
cd 4_dashboard
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python server.py              # tarayıcı: http://localhost:8000
```

---

## Redis Stream Haritası

| Stream | Yazan | Okuyan | Alanlar |
|--------|-------|--------|---------|
| `stream:cex_l2` | Ajan 1 (5 CEX) | Ajan 2, 4 | `src, bid_p, bid_q, ask_p, ask_q, ts` |
| `stream:dex_mempool` | Ajan 1 (mempool) | (rezerv) | `src, tx_hash, ts` |
| `stream:polymarket` | Ajan 1 (CLOB feed) | Ajan 2, 3 | `src, token, bid_p, ask_p, mid, ts` |
| `stream:signals` | Ajan 2 | Ajan 3, 4 | `dir, p_cex, obi, spread, ts` |
| `stream:executions` | Ajan 3 | Ajan 4 | `dir, p_cex, karar, slippage, gas_gwei, ts` |

Tüm stream'ler `MAXLEN ~10` ile kırpılır (RAM sabit, zero-disk).

---

## Hızlı Test (Redis çalışırken, ajanlar olmadan)

```bash
NOW=$(($(date +%s%3N)))
# Sinyal enjekte et → Brain/Execution/Dashboard tepkisi
docker exec -it ghost-redis redis-cli XADD stream:signals '*' \
  dir LONG p_cex 65000 obi 0.8 spread 0.001 ts $NOW
# Execution kararı enjekte et → Dashboard tablosu
docker exec -it ghost-redis redis-cli XADD stream:executions '*' \
  dir LONG p_cex 65000 karar ONAYLI slippage 0.002 gas_gwei 85 ts $NOW
```

Beklenen: Execution logunda `DRY RUN: ... Karar: ONAYLI`, Dashboard'da "Son 5 Sinyal" satırı ve gecikme (ms).

---

## Uygulanan Kısıtlar

- **Zero-Disk I/O:** Redis `--save "" --appendonly no`; tüm akış RAM'de.
- **Bellek (2GB):** Her stream `MAXLEN ~10`; Go'da `sync.Pool`; Python'da döngü-yerel array'ler.
- **NumPy-only:** Brain'de ağır matematik %100 vektörel — Python `for` yasak.
- **TTL güvenlik ağı:** Brain 1000ms, Execution 2000ms üzeri bayat veriyi drop eder.
- **Zero-CPU UI:** `server.py` saf pass-through (sıfır matematik); render istemci GPU'sunda (Tailwind/Alpine).
- **Güvenlik:** Private key sadece `.env`; DRY_RUN'da hiçbir on-chain işlem yok.

---

## Notlar & LIVE Öncesi Doğrulama Borçları
- **5 CEX** ayrı WSS protokolleriyle bağlanır (her biri kendi subscribe + `sync.Pool`). Semboller USDT paritesi; venue bir pariteyi reddederse ilgili `cex_*.go` içindeki sembol sabitini değiştir.
- `agent_dex.go` gerçek `eth_subscribe(newPendingTransactions)` kullanır; `DEX_SAMPLE_MS` ile örneklenir. Bazı public node'lar aboneliği kısıtlar → abonelik destekleyen WSS sağlayıcı gerekebilir.
- Brain'in OBI/VWAP fonksiyonları N-seviyeli deftere ileriye dönük uyumludur.
- **`spread_model.py` PLACEHOLDER'dır** — `P_cex → fair olasılık` lineer eşlemesi gerçek fiyatlama modeli değildir; LIVE öncesi değiştir (`PM_FAIR_BASE/SCALE`).
- **`clob_order.py` gerçek EIP-712 Order imzası üretir** (DRY_RUN'da imzasız hash loglar). ⚠️ **VERIFY:** `POLYMARKET_EXCHANGE` adresi, maker/taker amount ölçeği ve L2 auth (`POLY_*`) şeması resmi `py-clob-client` ile teyit edilmeli. Yanlış parametre fon kaybına yol açar.
- `poly_router.py` on-chain (CTFExchange) alternatifi olarak durur; birincil yol off-chain CLOB'dur.
