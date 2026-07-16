import unittest

from paper_trader import payout_profit, share_quantity


class PaperPnlTest(unittest.TestCase):
    def test_low_cent_winner_pays_large_positive(self):
        self.assertAlmostEqual(payout_profit(1.0, 0.025, True), 39.0)

    def test_loser_loses_stake(self):
        self.assertAlmostEqual(payout_profit(1.0, 0.025, False), -1.0)

    def test_share_quantity_matches_entry_price(self):
        self.assertAlmostEqual(share_quantity(1.0, 0.095), 10.5263157895)

    def test_pnl_after_sequence(self):
        pnl = 0.0
        pnl += payout_profit(1.0, 0.025, True)
        pnl += payout_profit(1.0, 0.10, False)
        self.assertAlmostEqual(pnl, 38.0)


if __name__ == "__main__":
    unittest.main()
