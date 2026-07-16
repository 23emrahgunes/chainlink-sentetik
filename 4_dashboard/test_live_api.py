import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from fastapi import HTTPException
    import server
except ModuleNotFoundError as exc:
    HTTPException = None
    server = None
    MISSING = exc.name
else:
    MISSING = ""


class FakeRequest:
    headers = {}

    def __init__(self, body=None):
        self._body = body or {}

    async def json(self):
        return self._body


@unittest.skipIf(server is None, f"missing dependency: {MISSING}")
class LiveApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_env_path = server.ENV_PATH
        self.old_auth_on = server.AUTH_ON
        self.old_publish = server._publish_live_state
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env_path = Path(tmp.name) / ".env"
        self.env_path.write_text("TRADING_MODE=LIVE\nLIVE_ARMED=0\n", encoding="utf-8")
        server.ENV_PATH = self.env_path
        server.AUTH_ON = False

        async def fake_publish(action, armed):
            return server._live_payload(armed)

        server._publish_live_state = fake_publish

    async def asyncTearDown(self):
        server.ENV_PATH = self.old_env_path
        server.AUTH_ON = self.old_auth_on
        server._publish_live_state = self.old_publish

    async def test_arm_requires_live_confirmation(self):
        with self.assertRaises(HTTPException) as ctx:
            await server.live_arm(FakeRequest({"confirm": "NO"}))
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_arm_and_disarm_update_env(self):
        armed = await server.live_arm(FakeRequest({"confirm": "LIVE"}))
        self.assertTrue(armed["live_armed"])
        self.assertIn("LIVE_ARMED=1", self.env_path.read_text(encoding="utf-8"))

        disarmed = await server.live_disarm(FakeRequest())
        self.assertFalse(disarmed["live_armed"])
        self.assertIn("LIVE_ARMED=0", self.env_path.read_text(encoding="utf-8"))

    async def test_legacy_pm_edge_env_aliases_status_payload(self):
        self.env_path.write_text(
            "PM_EDGE_MOMENTUM_EXECUTION_MODE=live\n"
            "PM_EDGE_MOMENTUM_NOTIONAL_USDC=1.50\n"
            "PM_EDGE_MOMENTUM_MAX_LIVE_NOTIONAL_USDC=2.00\n"
            "LIVE_ARMED=0\n",
            encoding="utf-8",
        )
        payload = server._live_payload()
        self.assertEqual(payload["trading_mode"], "LIVE")
        self.assertEqual(payload["order_usdc"], 1.5)
        self.assertEqual(payload["max_order_usdc"], 2.0)

    async def test_auth_helper_blocks_missing_credentials(self):
        with self.assertRaises(HTTPException) as ctx:
            server._require_auth(SimpleNamespace(headers={}))
        self.assertEqual(ctx.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
