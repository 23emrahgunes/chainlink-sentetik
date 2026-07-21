import unittest

from paper_trader import PaperTrader, payout_profit, share_quantity


class PaperPnlTest(unittest.TestCase):
    def test_low_cent_winner_pays_large_positive(self):
        self.assertAlmostEqual(payout_profit(1.0, 0.025, True), 39.0)

    def test_loser_loses_stake(self):
        self.assertAlmostEqual(payout_profit(1.0, 0.025, False), -1.0)

    def test_share_quantity_matches_entry_price(self):
        self.assertAlmostEqual(share_quantity(1.0, 0.095), 10.5263157895)

    def test_open_trade_is_published_immediately(self):
        trader = PaperTrader(stake=1.0, obi_entry=0.25, min_entry=0.05, dip_max=0.30)
        trader.update(
            win_ts=0,
            now_sec=250,
            obi=-0.30,
            poly_up=0.90,
            spot=100.05,
            strike=100.0,
            closed={},
        )
        recs = trader.drain()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["status"], "OPEN")
        self.assertEqual(recs[0]["share"], "DOWN")
        self.assertAlmostEqual(recs[0]["entry"], 0.10)
        self.assertAlmostEqual(recs[0]["p_cex"], 100.05)

    def test_usd_distance_blocks_far_price_to_beat(self):
        trader = PaperTrader(
            stake=1.0,
            obi_entry=0.25,
            min_entry=0.05,
            dip_max=0.30,
            distance_max_usd=80.0,
        )
        trader.update(
            win_ts=0,
            now_sec=250,
            obi=-0.30,
            poly_up=0.90,
            spot=220.0,
            strike=100.0,
            closed={},
        )
        self.assertEqual(trader.drain(), [])

    def test_pnl_after_sequence(self):
        pnl = 0.0
        pnl += payout_profit(1.0, 0.025, True)
        pnl += payout_profit(1.0, 0.10, False)
        self.assertAlmostEqual(pnl, 38.0)


if __name__ == "__main__":
    unittest.main()
