import pathlib
import unittest


class DashboardTradeContractTest(unittest.TestCase):
    def setUp(self):
        self.html = pathlib.Path("index.html").read_text(encoding="utf-8")

    def test_live_order_history_is_primary_table(self):
        for text in ("Canli Emir Gecmisi", "Execution'a ulasan emir", "LIVE_BLOCKED", "EMIR GONDERILDI", "EMIR REDDEDILDI", "FILL OLDU", "Market Sonucu", "Adet", "CLOB / Fill"):
            self.assertIn(text, self.html)
        for token in ("liveExecList", "pushLiveExec", "Giden Emir Logu", "shareQty", "displayStatus", "windowTs", "execStatus", "_stream_id"):
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

    def test_strong_obi_panel_contract(self):
        for text in ("Guclu OBI Girseydik", "strongObi", "strong_obi", "strong_obi_trade", "CLOB / Fill"):
            self.assertIn(text, self.html)


if __name__ == "__main__":
    unittest.main()
