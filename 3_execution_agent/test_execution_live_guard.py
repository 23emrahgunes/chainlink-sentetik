import unittest
from types import SimpleNamespace

try:
    from main_execution import _is_stale_signal, _latest_poly_snapshot, _live_block_reason
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
            "max_daily_loss_usdc": 10.0,
            "max_open_positions": 1,
            "token_id": "token",
        }
        cfg.update(overrides)
        return cfg

    def _decision(self, approved=True):
        return SimpleNamespace(approved=approved, reason="ok")

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
        client = FakePolyClient([("1-0", {"mid": "0.42", "token": "12345"})])
        mid, token = __import__("asyncio").run(_latest_poly_snapshot(client))
        self.assertEqual(mid, 0.42)
        self.assertEqual(token, "12345")

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
