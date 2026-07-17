import pathlib
import unittest


class DashboardTradeContractTest(unittest.TestCase):
    def setUp(self):
        self.html = pathlib.Path("index.html").read_text(encoding="utf-8")

    def test_trade_history_columns_are_explicit(self):
        for text in ("Alınan Share", "Adet", "Market Sonucu", "Paper Giriş"):
            self.assertIn(text, self.html)

    def test_paper_entry_is_not_labeled_as_real_fill(self):
        self.assertIn("gercek fill/buy maliyeti degildir", self.html)

    def test_trade_parser_supports_new_and_legacy_fields(self):
        for token in ("d.share ||", "d.result ||", "d.entry_cents", "d.share_qty", "d.market_label"):
            self.assertIn(token, self.html)

    def test_market_window_uses_polymarket_et(self):
        for token in ("formatMarketWindowET", "America/New_York", " ET"):
            self.assertIn(token, self.html)
    def test_trade_history_supports_open_updates(self):
        for token in ("d.status || 'SETTLED'", "t.status === 'OPEN'", "findIndex(t => t.win === win)"):
            self.assertIn(token, self.html)
    def test_live_mode_controls_are_present(self):
        for text in ("CANLI MODA GEÇ", "CANLI AKTİF", "DURDUR", "LIVE yaz"):
            self.assertIn(text, self.html)
        for endpoint in ("/api/live/status", "/api/live/arm", "/api/live/disarm"):
            self.assertIn(endpoint, self.html)


if __name__ == "__main__":
    unittest.main()
