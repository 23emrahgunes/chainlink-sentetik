import pathlib
import unittest


class DashboardTradeContractTest(unittest.TestCase):
    def setUp(self):
        self.html = pathlib.Path("index.html").read_text(encoding="utf-8")

    def test_paper_trade_table_is_not_rendered(self):
        for text in ("Kagit Islem Gecmisi", "Paper Giriş", "Paper Giris", "Market Sonucu"):
            self.assertNotIn(text, self.html)

    def test_live_order_table_is_primary_history(self):
        for text in ("Giden Emirler", "LIVE_SENT CLOB'a gonderildi", "LIVE_BLOCKED"):
            self.assertIn(text, self.html)
        for token in ("liveExecList", "pushLiveExec"):
            self.assertIn(token, self.html)

    def test_market_window_uses_polymarket_et(self):
        for token in ("formatMarketWindowET", "America/New_York", " ET"):
            self.assertIn(token, self.html)

    def test_trade_parser_still_accepts_paper_stream_for_metrics(self):
        for token in ("d.status || 'SETTLED'", "findIndex(t => t.win === win)"):
            self.assertIn(token, self.html)

    def test_live_mode_controls_are_present(self):
        for text in ("CANLI MODA GEÇ", "CANLI AKTİF", "DURDUR", "LIVE yaz"):
            self.assertIn(text, self.html)
        for endpoint in ("/api/live/status", "/api/live/arm", "/api/live/disarm"):
            self.assertIn(endpoint, self.html)


if __name__ == "__main__":
    unittest.main()
