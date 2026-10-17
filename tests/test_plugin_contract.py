"""The wire contract between Focused and the game plugin.

The plugin is C# and cannot be exercised from here, but the *shape* it depends on
can: its DTO field names are parsed out of the source and checked against the
payload Focused actually serves.  Drift here is the expensive kind -- Unity's
JsonUtility does not complain about a field it cannot find, it leaves it at its
default, which once shipped as a checkbox that was stuck on.
"""

import os
import re
import socket
import tempfile
import unittest

from focused.config import Config
from focused.engine import FocusedEngine
from focused.server import API_PREFIX, FocusedServer

WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DTO_PATH = os.environ.get(
    "CHILLFOCUS_DTO_PATH",
    os.path.join(WORKSPACE, "chillfocus", "ChillFocused", "Core", "FocusedProtocol.cs"),
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def dto_fields(class_name: str):
    """Field names of one ``[Serializable]`` class in the plugin's DTO file."""
    with open(DTO_PATH, "r", encoding="utf-8") as handle:
        source = handle.read()
    match = re.search(
        r"class\s+%s\s*\{(?P<body>.*?)\n    \}" % re.escape(class_name),
        source,
        re.S,
    )
    if match is None:
        raise AssertionError("class %s not found in %s" % (class_name, DTO_PATH))
    fields = []
    for line in match.group("body").splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("///") or line.startswith("["):
            continue
        field = re.match(r"public\s+[\w\[\]<>\.]+\s+(\w+)\s*;", line)
        if field:
            fields.append(field.group(1))
    return fields


class PluginPayloadContractTests(unittest.TestCase):
    """Every field the plugin reads must exist in the payload it is reading."""

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
        self.server = FocusedServer(
            self.engine, host="127.0.0.1", port=config.http.port, token=""
        )
        self.port = self.server.start()
        self.addCleanup(self.server.stop)

    def payload(self, path="/focus"):
        import json
        import urllib.request

        with urllib.request.urlopen(
            "http://127.0.0.1:%d%s%s" % (self.port, API_PREFIX, path), timeout=5
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_the_focus_payload_carries_every_field_the_plugin_binds(self):
        payload = self.payload()
        needed = dto_fields("FocusStateResponse")

        self.assertTrue(needed, "the DTO parse found no fields; did the file move?")
        missing = [field for field in needed if field not in payload]
        self.assertEqual([], missing, "plugin fields missing from /focus: %s" % missing)

    def test_the_process_payload_carries_every_field_the_picker_binds(self):
        payload = self.payload("/processes")
        needed = dto_fields("ProcessListResponse")

        missing = [field for field in needed if field not in payload]
        self.assertEqual([], missing, "picker fields missing from /processes: %s" % missing)

    def test_the_focus_payload_stays_flat(self):
        # JsonUtility binds scalars and primitive arrays; a nested object would
        # arrive null, which is exactly the bug this test exists to prevent.
        payload = self.payload()

        nested = [
            key
            for key, value in payload.items()
            if isinstance(value, dict)
        ]
        self.assertEqual([], nested, "nested objects cannot be read by the plugin")

    def test_the_rule_payload_the_plugin_sends_is_accepted(self):
        # Field-for-field what FocusSettings.ToRulePayload() produces.
        status = self._put_rules(
            {
                "names": ["firefox"],
                "cmdline_substrings": ["--distract"],
                "pids": [],
                "protect_names": ["my-shell"],
                "protect_cmdline_substrings": [],
            }
        )

        self.assertTrue(status["ok"])
        self.assertIn("firefox", status["applied"]["names"])

    def test_the_session_heartbeat_the_plugin_sends_is_accepted(self):
        payload = self._post_state(
            {"active": True, "dry_run": False, "source": "game-timer", "ttl_seconds": 30}
        )

        self.assertTrue(payload["ok"])
        focus = self.payload("/focus")
        self.assertTrue(focus["delegated_active"])
        self.assertEqual("game-timer", focus["delegated_source"])

    # -- helpers -----------------------------------------------------------

    def _put_rules(self, body):
        import json
        import urllib.request

        request = urllib.request.Request(
            "http://127.0.0.1:%d%s/focus/rules" % (self.port, API_PREFIX),
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_state(self, body):
        import json
        import urllib.request

        request = urllib.request.Request(
            "http://127.0.0.1:%d%s/focus/state" % (self.port, API_PREFIX),
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
