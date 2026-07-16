import pathlib
import unittest


class DashboardTradeContractTest(unittest.TestCase):
    def setUp(self):
        self.html = pathlib.Path("index.html").read_text(encoding="utf-8")

    def test_trade_history_columns_are_explicit(self):
        for text in ("Alınan Share", "Adet", "Market Sonucu", "Giriş (maliyet)"):
            self.assertIn(text, self.html)

    def test_trade_parser_supports_new_and_legacy_fields(self):
        for token in ("d.share ||", "d.result ||", "d.entry_cents", "d.share_qty", "d.market_label"):
            self.assertIn(token, self.html)

    def test_live_mode_controls_are_present(self):
        for text in ("CANLI MODA GEÇ", "CANLI AKTİF", "DURDUR", "LIVE yaz"):
            self.assertIn(text, self.html)
        for endpoint in ("/api/live/status", "/api/live/arm", "/api/live/disarm"):
            self.assertIn(endpoint, self.html)


if __name__ == "__main__":
    unittest.main()
