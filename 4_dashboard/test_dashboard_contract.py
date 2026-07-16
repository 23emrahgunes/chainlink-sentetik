import pathlib
import unittest


class DashboardTradeContractTest(unittest.TestCase):
    def setUp(self):
        self.html = pathlib.Path("index.html").read_text(encoding="utf-8")

    def test_trade_history_columns_are_explicit(self):
        for text in ("Alınan Share", "Market Sonucu", "Giriş (maliyet)"):
            self.assertIn(text, self.html)

    def test_trade_parser_supports_new_and_legacy_fields(self):
        for token in ("d.share ||", "d.result ||", "d.entry_cents", "d.market_label"):
            self.assertIn(token, self.html)


if __name__ == "__main__":
    unittest.main()
