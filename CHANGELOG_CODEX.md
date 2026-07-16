# CHANGELOG_CODEX

## 2026-07-16 - Reversal depth model and trade telemetry

### What changed
- Added multi-level orderbook state support in Go ingestion with snapshot/delta handling, size=0 level deletion, and top 5/20/50 volume totals.
- Upgraded Bybit from `orderbook.1.BTCUSDT` to `orderbook.50.BTCUSDT` on the linear/perp feed.
- Added OKX swap as a separate `okx_swap` perp source while keeping OKX spot.
- Upgraded Coinbase from ticker best bid/ask to `BTC-USD level2_batch` local book state.
- Upgraded Kraken from ticker best bid/ask to spot `book` depth 25 local book state.
- Extended Redis CEX messages while preserving legacy fields: `bid_vol`, `ask_vol`, `bid_p`, `ask_p`, `bid_q`, `ask_q` still exist.
- Added optional fields: `market_type`, `bid_vol_5`, `ask_vol_5`, `bid_vol_20`, `ask_vol_20`, `bid_vol_50`, `ask_vol_50`.

### Brain / strategy telemetry
- Brain now prefers the deepest available volume field and falls back to legacy volume fields.
- Default perp OBI sources are now `binance,bybit,okx_swap`.
- Default spot OBI sources are now `coinbase,kraken`.
- Added live synthetic stream fields: `perp_obi`, `spot_obi`, `mix_obi`, OBI deltas, `seconds_left`, `distance_to_beat`, `required_velocity`, `realized_velocity`, `cheap_side_price`, `entry_score`, and `entry_reason`.
- DEX remains a confirmation signal only. It does not become a standalone trigger.

### Paper trade / PnL telemetry
- PnL formula is unchanged: winner = `stake * (1 / entry - 1)`, loser = `-stake`.
- Added `payout_profit()` for testable PnL behavior.
- `stream:trades` keeps legacy fields and adds `market_label`, `share`, `result`, `entry_cents`, velocity, OBI, DEX, and score context fields.
- Dashboard trade history now separates taken share from market result: `Alınan Share` and `Market Sonucu`.

### Tests run
- `C:\Tmp\go1.22.12\go\bin\go.exe test ./...` from `1_ingestion_agents`.
- `python -B -m unittest test_paper_trader.py` from `2_brain_engine`.
- `python -B -m unittest test_dashboard_contract.py` from `4_dashboard`.
- Python syntax compile across `2_brain_engine`, `3_execution_agent`, and `4_dashboard`.

### Notes for Claude / next agent
- Do not remove the legacy Redis fields; dashboard and downstream consumers depend on them.
- Bybit/Coinbase/Kraken now require local orderbook correctness. If changing feed depth, update `book_state_test.go` or add a venue-specific fixture test.
- OKX spot and OKX swap intentionally publish as different sources: `okx` and `okx_swap`.
- LIVE CLOB order execution was not changed in this pass. Validate with official Polymarket client before using real funds.

## 2026-07-16 - Live arm/disarm safety plan implementation

- Added dashboard live controls backed by `/api/live/status`, `/api/live/arm`, and `/api/live/disarm`.
- Live arming writes `LIVE_ARMED` to the VPS-local `.env`, syncs Redis `state:live`, and emits `stream:control`.
- Execution now blocks LIVE orders unless `TRADING_MODE=LIVE`, runtime `LIVE_ARMED=1`, order/risk limits pass, router/token/mid are present, and slippage is approved.
- `deploy/run.sh` now syncs `.env` live state to Redis on every start and runs all `test_*.py` suites.
- Added `deploy/live.env.example` for VPS live configuration without secrets.

## 2026-07-16 - Legacy PM_EDGE env aliases

- Added `3_execution_agent/env_alias.py` so older working `PM_EDGE_*` configuration names map to the current execution/CLOB settings.
- Supported aliases include private key, CLOB API credentials, CLOB host, chain id, notional size, max live notional, timeout, and signature type.
- Updated deploy live env example and tests for old-to-new env compatibility.
