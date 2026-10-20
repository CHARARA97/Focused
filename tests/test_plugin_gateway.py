"""PluginGateway: the single capability/ownership door for plugins."""

import os
import tempfile
import unittest

from focused.app.plugin_gateway import PluginGateway
from focused.config import Config
from focused.engine import FocusedEngine
from focused.plugin_api import (
    CAP_EVENTS_READ,
    CAP_RULES_READ,
    CAP_RULES_WRITE,
    CAP_SCAN_REQUEST,
    CAP_SESSION_CLAIM,
    CAP_STATE_HISTORY,
    CAP_STATE_READ,
    PluginError,
    SAFE_CAPABILITIES,
)


def make_engine(tmp):
    config = Config.from_dict(
        {
            "audit_log": "",
            "enforcement": {"enabled": True, "mode": "audit"},
            "registry_path": os.path.join(tmp, "plugins.json"),
        }
    )
    return FocusedEngine(
        config,
        logger=None,
        uid=os.getuid(),
        self_pid=999_001,
    )


class GatewayTests(unittest.TestCase):
    def gateway(self, tmp):
        engine = make_engine(tmp)
        return engine, engine.plugin_gateway

    def register(self, engine, plugin_id, declared, granted=SAFE_CAPABILITIES):
        return engine.plugins.register(
            name=plugin_id,
            plugin_id=plugin_id,
            capabilities=declared,
            granted=granted,
        )

    def test_rules_are_scoped_and_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, gateway = self.gateway(tmp)
            self.register(
                engine,
                "reader",
                (CAP_RULES_WRITE, CAP_RULES_READ, CAP_STATE_READ),
            )

            gateway.add_plugin_rules("reader", {"names": ["firefox"]})

            self.assertEqual(["firefox"], gateway.list_plugin_rules("reader")["names"])
            self.assertIn("firefox", engine.rules().names)
            events = engine.events_since(0, 100)
            self.assertTrue(any(event["kind"] == "plugin.rules.add" for event in events))

    def test_a_missing_capability_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, gateway = self.gateway(tmp)
            self.register(engine, "limited", (CAP_STATE_READ,))

            with self.assertRaises(PluginError) as caught:
                gateway.add_plugin_rules("limited", {"names": ["firefox"]})
            self.assertEqual("capability.missing", caught.exception.code)

    def test_a_disabled_plugin_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, gateway = self.gateway(tmp)
            self.register(engine, "off", (CAP_STATE_READ,))
            engine.plugins.set_status("off", "disabled")

            with self.assertRaises(PluginError) as caught:
                gateway.plugin_status_snapshot("off")
            self.assertEqual("plugin.disabled", caught.exception.code)

    def test_session_claim_and_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, gateway = self.gateway(tmp)
            self.register(engine, "timer", (CAP_SESSION_CLAIM, CAP_STATE_READ))

            self.assertTrue(gateway.claim_plugin_session("timer", 30))
            self.assertIn("timer", engine.live_claims())
            status = gateway.plugin_session_status("timer")
            self.assertTrue(status["active"])
            self.assertIsNotNone(status["my_claim"])
            gateway.release_plugin_session("timer")
            self.assertNotIn("timer", engine.live_claims())

    def test_scan_is_rate_limited_per_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, gateway = self.gateway(tmp)
            self.register(engine, "scanner", (CAP_SCAN_REQUEST,))

            first = gateway.request_plugin_scan("scanner")
            second = gateway.request_plugin_scan("scanner")

            self.assertTrue(first.accepted)
            self.assertFalse(second.accepted)
            self.assertEqual("rate-limited", second.reason)

    def test_state_and_events_are_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, gateway = self.gateway(tmp)
            self.register(engine, "observer", (CAP_STATE_READ, CAP_STATE_HISTORY, CAP_EVENTS_READ))

            summary = gateway.plugin_status_snapshot("observer")
            self.assertIn("active", summary)
            self.assertIn("health", summary)
            self.assertEqual([], gateway.plugin_history_snapshot("observer"))
            self.assertIsInstance(gateway.recent_events("observer", 0, 10), list)

    def test_dangerous_capabilities_are_not_granted_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, gateway = self.gateway(tmp)
            self.register(
                engine,
                "sneaky",
                ("rules.write", "process.control"),
                granted=SAFE_CAPABILITIES,
            )
            effective = engine.plugins.capabilities_for("sneaky")
            self.assertNotIn("process.control", effective)


if __name__ == "__main__":
    unittest.main()
