import unittest
from types import SimpleNamespace

try:
    from main_execution import (
        EXECUTION_STREAM, STREAM_ENTRIES, _is_stale_signal,
        _latest_poly_snapshot, _live_block_reason,
        _clob_response_result, _order_lock_key, _order_lock_ttl,
    )
except ModuleNotFoundError as exc:
    _is_stale_signal = None
    _live_block_reason = None
    MISSING = exc.name
else:
    MISSING = ""

class FakePolyClient:
    def __init__(self, rows):
        self.rows = rows

    async def xrevrange(self, stream, count=1):
        return self.rows[:count]

@unittest.skipIf(_live_block_reason is None, f"missing dependency: {MISSING}")
class LiveGuardTest(unittest.TestCase):
    def _cfg(self, **overrides):
        cfg = {
            "mode": "LIVE",
            "live_armed": True,
            "order_usdc": 1.0,
            "max_order_usdc": 1.0,
            "max_live_entry_price": 0.20,
            "max_daily_loss_usdc": 10.0,
            "max_open_positions": 1,
            "token_id": "token",
        }
        cfg.update(overrides)
        return cfg

    def _decision(self, approved=True):
        return SimpleNamespace(approved=approved, reason="ok")

    def test_default_execution_stream_is_entries(self):
        self.assertEqual(EXECUTION_STREAM, STREAM_ENTRIES)

    def test_dry_run_never_blocks_as_live(self):
        reason = _live_block_reason(
            self._cfg(mode="DRY_RUN", live_armed=False),
            self._decision(), router=None, pm_mid=None,
            risk={"daily_loss_usdc": 999, "open_positions": 999},
        )
        self.assertIsNone(reason)

    def test_stale_signal_helper(self):
        self.assertTrue(_is_stale_signal(1000, 4001, max_age_ms=2000))
        self.assertFalse(_is_stale_signal(2500, 4001, max_age_ms=2000))

    def test_latest_poly_snapshot_returns_mid_and_token(self):
        client = FakePolyClient([("1-0", {"mid": "0.42", "up_token": "12345", "down_token": "67890", "window_ts": "300"})])
        mid, up_token, down_token, window_ts = __import__("asyncio").run(_latest_poly_snapshot(client))
        self.assertEqual(mid, 0.42)
        self.assertEqual(up_token, "12345")
        self.assertEqual(down_token, "67890")
        self.assertEqual(window_ts, 300)


    def test_clob_response_accepts_order_id(self):
        result = _clob_response_result({"orderID": "abc", "status": "OPEN"})
        self.assertTrue(result["accepted"])
        self.assertEqual(result["order_id"], "abc")

    def test_clob_response_rejects_error_payload(self):
        result = _clob_response_result({"success": False, "errorMsg": "bad order"})
        self.assertFalse(result["accepted"])
        self.assertIn("bad order", result["reason"])

    def test_clob_response_rejects_empty_payload(self):
        result = _clob_response_result({})
        self.assertFalse(result["accepted"])
        self.assertIn("order id", result["reason"])

    def test_order_lock_key_is_window_scoped(self):
        self.assertEqual(
            _order_lock_key(300, "LONG", "abc"),
            "state:order_lock:300",
        )

    def test_order_lock_ttl_runs_past_window_end(self):
        self.assertEqual(_order_lock_ttl(300, 590, fallback_sec=360), 40)
        self.assertEqual(_order_lock_ttl(0, 590, fallback_sec=120), 120)

    def test_live_requires_armed(self):
        reason = _live_block_reason(
            self._cfg(live_armed=False), self._decision(),
            router=object(), pm_mid=0.5,
            risk={"daily_loss_usdc": 0, "open_positions": 0},
        )
        self.assertIn("LIVE_ARMED=0", reason)

    def test_live_blocks_order_above_max(self):
        reason = _live_block_reason(
            self._cfg(order_usdc=2.0, max_order_usdc=1.0), self._decision(),
            router=object(), pm_mid=0.5,
            risk={"daily_loss_usdc": 0, "open_positions": 0},
        )
        self.assertIn("ORDER_USDC", reason)

    def test_live_blocks_entry_above_cent_cap(self):
        reason = _live_block_reason(
            self._cfg(max_live_entry_price=0.20), self._decision(),
            router=object(), pm_mid=0.5,
            risk={"daily_loss_usdc": 0, "open_positions": 0},
            order_price=0.477,
        )
        self.assertIn("limit fiyat", reason)

    def test_live_blocks_daily_loss_limit(self):
        reason = _live_block_reason(
            self._cfg(), self._decision(), router=object(), pm_mid=0.5,
            risk={"daily_loss_usdc": 10.0, "open_positions": 0},
        )
        self.assertIn("daily loss", reason)

    def test_live_blocks_open_position_limit(self):
        reason = _live_block_reason(
            self._cfg(max_open_positions=1), self._decision(), router=object(), pm_mid=0.5,
            risk={"daily_loss_usdc": 0, "open_positions": 1},
        )
        self.assertIn("open positions", reason)

    def test_live_allows_when_all_guards_pass(self):
        reason = _live_block_reason(
            self._cfg(), self._decision(), router=object(), pm_mid=0.5,
            risk={"daily_loss_usdc": 0, "open_positions": 0},
        )
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
