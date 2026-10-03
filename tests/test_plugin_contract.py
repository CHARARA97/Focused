"""The wire contract the game plugin depends on.

The plugin is C# and lives in its own repository, so this can no longer read its
source.  What it *can* do is hold the plugin's field list to a checked-in fixture
and assert that the payload still carries every field, still flat -- Unity's
JsonUtility binds public fields and primitive arrays only, so a nested object
arrives as null with no error anywhere.  That bug once shipped as a checkbox that
was stuck on for a whole release.
"""

import json
import os
import socket
import tempfile
import unittest
import urllib.request

from focused.config import Config
from focused.engine import FocusedEngine
from focused.server import API_PREFIX, FocusedServer

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "plugin-dto-fields.json")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def plugin_fields(section: str):
    with open(FIXTURE, encoding="utf-8") as handle:
        return json.load(handle)[section]


def post(port, path, body, method="POST"):
    request = urllib.request.Request(
        "http://127.0.0.1:%d%s%s" % (port, API_PREFIX, path),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


class PluginPayloadContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        config = Config.from_dict(
            {
                "audit_log": "",
                "http": {"enabled": True, "host": "127.0.0.1", "port": free_port()},
                "blacklist": {"names": []},
                "freeze": {"enabled": True, "state_path": os.path.join(self._tmp.name, "frozen.json")},
            }
        )
        self.engine = FocusedEngine(config, logger=None, self_pid=4242)
        self.server = FocusedServer(self.engine, host="127.0.0.1", port=config.http.port, token="")
        self.port = self.server.start()
        self.addCleanup(self.server.stop)

    def payload(self, path="/focus"):
        with urllib.request.urlopen(
            "http://127.0.0.1:%d%s%s" % (self.port, API_PREFIX, path), timeout=5
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_the_focus_payload_carries_every_field_the_plugin_binds(self):
        payload = self.payload()
        needed = plugin_fields("focus")

        self.assertTrue(needed, "the fixture is empty; did it get truncated?")
        missing = [field for field in needed if field not in payload]
        self.assertEqual([], missing, "fields the plugin needs are missing from /focus: %s" % missing)

    def test_the_process_payload_carries_every_field_the_picker_binds(self):
        payload = self.payload("/processes")
        needed = plugin_fields("processes")

        missing = [field for field in needed if field not in payload]
        self.assertEqual(
            [], missing, "fields the plugin's process picker needs are missing: %s" % missing
        )

    def test_the_focus_payload_stays_flat(self):
        payload = self.payload()

        nested = [key for key, value in payload.items() if isinstance(value, dict)]
        self.assertEqual([], nested, "nested objects cannot be read by the plugin: %s" % nested)

    def test_the_rule_payload_the_plugin_sends_is_accepted(self):
        # Field for field what the plugin's FocusSettings.ToRulePayload() produces.
        status = post(
            self.port,
            "/focus/rules",
            {
                "names": ["firefox"],
                "cmdline_substrings": ["--distract"],
                "pids": [],
                "protect_names": ["my-shell"],
                "protect_cmdline_substrings": [],
            },
            method="PUT",
        )

        self.assertTrue(status["ok"])
        self.assertIn("firefox", status["applied"]["names"])

    def test_the_session_heartbeat_the_plugin_sends_is_accepted(self):
        payload = post(
            self.port,
            "/focus/state",
            {"active": True, "dry_run": False, "source": "game-timer", "ttl_seconds": 30},
        )

        self.assertTrue(payload["ok"])
        focus = self.payload()
        self.assertTrue(focus["delegated_active"])
        self.assertEqual("game-timer", focus["delegated_source"])


if __name__ == "__main__":
    unittest.main()
