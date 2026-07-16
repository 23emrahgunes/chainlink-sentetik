import os
import unittest
from unittest.mock import patch

from env_alias import env, env_float, env_int, normalized_mode


class EnvAliasTest(unittest.TestCase):
    def test_pm_edge_private_key_alias(self):
        with patch.dict(os.environ, {"PM_EDGE_PRIVATE_KEY": "secret"}, clear=True):
            self.assertEqual(env("WALLET_PRIVATE_KEY"), "secret")

    def test_pm_edge_funder_alias(self):
        with patch.dict(os.environ, {"PM_EDGE_FUNDER_ADDRESS": "0xfunder"}, clear=True):
            self.assertEqual(env("FUNDER_ADDRESS"), "0xfunder")

    def test_new_name_wins_over_legacy_alias(self):
        with patch.dict(os.environ, {"WALLET_PRIVATE_KEY": "new", "PM_EDGE_PRIVATE_KEY": "old"}, clear=True):
            self.assertEqual(env("WALLET_PRIVATE_KEY"), "new")

    def test_notional_and_chain_aliases(self):
        with patch.dict(os.environ, {"PM_EDGE_MOMENTUM_NOTIONAL_USDC": "1.25", "PM_EDGE_CHAIN_ID": "137"}, clear=True):
            self.assertAlmostEqual(env_float("ORDER_USDC"), 1.25)
            self.assertEqual(env_int("POLYGON_CHAIN_ID"), 137)

    def test_legacy_dry_mode_normalizes(self):
        with patch.dict(os.environ, {"PM_EDGE_MOMENTUM_EXECUTION_MODE": "dry"}, clear=True):
            self.assertEqual(normalized_mode(), "DRY_RUN")

    def test_legacy_live_mode_normalizes(self):
        with patch.dict(os.environ, {"PM_EDGE_MOMENTUM_EXECUTION_MODE": "live"}, clear=True):
            self.assertEqual(normalized_mode(), "LIVE")


if __name__ == "__main__":
    unittest.main()
