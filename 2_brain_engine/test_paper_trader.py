import unittest

from paper_trader import PaperTrader, StrongObiSimulator, SpotObiSimulator, payout_profit, share_quantity


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

    def test_strong_obi_simulator_tracks_bands_and_pnl(self):
        sim = StrongObiSimulator(stake=1.0, obi_entry=0.25, min_entry=0.02, max_entry=0.20, min_sec_left=45, max_sec_left=90)
        sim.update(
            win_ts=0,
            now_sec=230,
            obi=-0.30,
            poly_up=0.90,
            spot=120.0,
            strike=100.0,
            closed={},
            context={"beat_path_obi": -0.30, "spot_obi": -0.10, "perp_obi_delta": 0.0, "entry_score": 70},
        )
        opened = sim.drain()
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0]["share"], "DOWN")
        self.assertAlmostEqual(opened[0]["entry"], 0.10)
        sim.update(
            win_ts=300,
            now_sec=530,
            obi=0.0,
            poly_up=0.50,
            spot=90.0,
            strike=100.0,
            closed={300: (120.0, 90.0)},
        )
        settled = sim.drain()
        self.assertEqual(settled[-1]["status"], "SETTLED")
        self.assertTrue(settled[-1]["won"])
        snap = sim.snapshot()
        self.assertEqual(snap["trades"], "1")
        self.assertEqual(snap["wins"], "1")
        self.assertEqual(snap["band1_n"], "1")
        self.assertAlmostEqual(float(snap["pnl"]), 9.0)

    def test_strong_obi_simulator_one_entry_per_window(self):
        sim = StrongObiSimulator(stake=1.0, obi_entry=0.25, min_entry=0.02, max_entry=0.20, min_sec_left=45, max_sec_left=90)
        kwargs = dict(win_ts=0, now_sec=230, obi=-0.30, poly_up=0.90, spot=120.0, strike=100.0, closed={}, whale=-1.0, context={"spot_obi": -0.10, "perp_obi_delta": 0.0})
        sim.update(**kwargs)
        sim.update(**kwargs)
        self.assertEqual(len(sim.drain()), 1)

    def test_strong_obi_requires_spot_and_whale_confirmation(self):
        sim = StrongObiSimulator(stake=1.0, obi_entry=0.25, min_entry=0.02, max_entry=0.20, min_sec_left=45, max_sec_left=90)
        base = dict(win_ts=0, now_sec=230, obi=-0.30, poly_up=0.90, spot=120.0, strike=100.0, closed={})
        sim.update(**base, whale=-1.0, context={"spot_obi": 0.10, "perp_obi_delta": 0.0})
        self.assertEqual(sim.drain(), [])
        sim.update(**base, whale=1.0, context={"spot_obi": -0.10, "perp_obi_delta": 0.0})
        self.assertEqual(sim.drain(), [])

    def test_strong_obi_blocks_perp_against_warning(self):
        sim = StrongObiSimulator(stake=1.0, obi_entry=0.25, min_entry=0.02, max_entry=0.20, min_sec_left=45, max_sec_left=90, perp_against_max=0.08)
        sim.update(
            win_ts=0, now_sec=230, obi=-0.30, poly_up=0.90, spot=120.0, strike=100.0,
            closed={}, whale=-1.0, context={"spot_obi": -0.10, "perp_obi_delta": 0.20},
        )
        self.assertEqual(sim.drain(), [])

    def test_spot_obi_simulator_tracks_paper_pnl(self):
        sim = SpotObiSimulator(stake=1.0, obi_entry=0.25, min_entry=0.02, max_entry=0.20, min_sec_left=45, max_sec_left=90)
        sim.update(
            win_ts=0,
            now_sec=230,
            obi=-0.10,
            poly_up=0.90,
            spot=120.0,
            strike=100.0,
            closed={},
            context={"spot_obi": -0.35, "beat_path_obi": -0.20, "entry_score": 60},
        )
        opened = sim.drain()
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0]["share"], "DOWN")
        self.assertAlmostEqual(opened[0]["entry"], 0.10)
        sim.update(win_ts=300, now_sec=530, obi=0.0, poly_up=0.50, spot=90.0, strike=100.0, closed={300: (120.0, 90.0)})
        settled = sim.drain()
        self.assertEqual(settled[-1]["status"], "SETTLED")
        self.assertTrue(settled[-1]["won"])
        self.assertEqual(sim.snapshot()["wins"], "1")

    def test_spot_obi_simulator_one_entry_per_window(self):
        sim = SpotObiSimulator(stake=1.0, obi_entry=0.25, min_entry=0.02, max_entry=0.20, min_sec_left=45, max_sec_left=90)
        kwargs = dict(win_ts=0, now_sec=230, obi=0.0, poly_up=0.90, spot=120.0, strike=100.0, closed={}, context={"spot_obi": -0.35})
        sim.update(**kwargs)
        sim.update(**kwargs)
        self.assertEqual(len(sim.drain()), 1)

    def test_spot_obi_reversal_only_blocks_same_side(self):
        sim = SpotObiSimulator(stake=1.0, obi_entry=0.25, min_entry=0.02, max_entry=0.20, min_sec_left=45, max_sec_left=90)
        sim.update(win_ts=0, now_sec=230, obi=0.0, poly_up=0.10, spot=120.0, strike=100.0, closed={}, context={"spot_obi": 0.35})
        self.assertEqual(sim.drain(), [])
    def test_pnl_after_sequence(self):
        pnl = 0.0
        pnl += payout_profit(1.0, 0.025, True)
        pnl += payout_profit(1.0, 0.10, False)
        self.assertAlmostEqual(pnl, 38.0)


if __name__ == "__main__":
    unittest.main()
