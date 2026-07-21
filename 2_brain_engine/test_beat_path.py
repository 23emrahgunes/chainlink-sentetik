import sys
import types
import unittest

redis_pkg = types.ModuleType("redis")
redis_asyncio_pkg = types.ModuleType("redis.asyncio")
redis_pkg.asyncio = redis_asyncio_pkg
sys.modules.setdefault("redis", redis_pkg)
sys.modules.setdefault("redis.asyncio", redis_asyncio_pkg)

from main_brain import _beat_path_metrics


class BeatPathTest(unittest.TestCase):
    def test_above_strike_down_reversal_goes_negative_when_ask_pressure_wins(self):
        quotes = {
            "venue": {
                "ts": 1000,
                "bids": [(99.0, 1.0), (95.0, 1.0), (80.0, 20.0)],
                "asks": [(101.0, 5.0), (120.0, 1.0)],
            }
        }
        m = _beat_path_metrics(quotes, spot=100.0, strike=90.0, now_ms=1000, ttl=1000, pad_usd=25)
        self.assertLess(m["obi"], 0)
        self.assertEqual(m["mode"], "above")
        self.assertEqual(m["n"], 1)

    def test_below_strike_up_reversal_goes_positive_when_bid_support_wins(self):
        quotes = {
            "venue": {
                "ts": 1000,
                "bids": [(99.0, 5.0), (80.0, 1.0)],
                "asks": [(101.0, 1.0), (105.0, 1.0), (130.0, 20.0)],
            }
        }
        m = _beat_path_metrics(quotes, spot=100.0, strike=110.0, now_ms=1000, ttl=1000, pad_usd=25)
        self.assertGreater(m["obi"], 0)
        self.assertEqual(m["mode"], "below")
        self.assertEqual(m["n"], 1)


if __name__ == "__main__":
    unittest.main()
