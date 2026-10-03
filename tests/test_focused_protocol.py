"""Focused's HTTP protocol, against a real server on a real port.

``docs/focused-protocol.md`` is the contract; these tests are what keeps the
implementation honest, including the handful of fields the browser extension
parses and the precedence rules ChillFocus relies on when it hands a session over.
"""

import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from focused.config import Config
from focused.engine import FocusedEngine
from focused.server import API_PREFIX, FocusedServer


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ServerTestCase(unittest.TestCase):
    token = ""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # A concrete free port: Focused's own validation refuses port 0, because a
        # user writing 0 in the config would be asking for something odd.
        self.port_hint = free_port()
        config = Config.from_dict(
            {
                "audit_log": "",
                "http": {
                    "enabled": True,
                    "host": "127.0.0.1",
                    "port": self.port_hint,
                    "token": self.token,
                },
                "blacklist": {"names": ["firefox"]},
                "urls": {"patterns": ["reddit.com", "*twitter*"]},
                "freeze": {"enabled": True, "state_path": os.path.join(self._tmp.name, "frozen.json")},
            }
        )
        self.engine = FocusedEngine(
            config,
            logger=None,
            uid=os.getuid(),
            self_pid=999_001,
            self_cgroup="/user.slice/user-1000.slice/user@1000.service/app.slice/focusedd.service",
            unified_cgroup2=True,
        )
        self.server = FocusedServer(
            self.engine, host="127.0.0.1", port=self.port_hint, token=self.token or ""
        )
        self.port = self.server.start()
        self.addCleanup(self.server.stop)

    def call(self, method, path, body=None, raw=None, token=None):
        url = "http://127.0.0.1:%d%s%s" % (self.port, API_PREFIX, path)
        data = raw if raw is not None else (
            json.dumps(body).encode("utf-8") if body is not None else None
        )
        headers = {"Content-Type": "application/json"} if data is not None else {}
        effective = self.token if token is None else token
        if effective:
            headers["X-Focused-Token"] = effective
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read().decode("utf-8"))
            exc.close()
            return exc.code, payload


class StateProtocolTests(ServerTestCase):
    def test_status_reports_the_engine(self):
        status, payload = self.call("GET", "/status")

        self.assertEqual(200, status)
        self.assertTrue(payload["ok"])
        self.assertEqual(["firefox"], payload["blacklist"]["names"])
        self.assertEqual(["reddit.com", "*twitter*"], payload["url_patterns"])
        self.assertFalse(payload["active"])

    def test_the_focus_endpoint_has_what_the_extension_parses(self):
        status, payload = self.call("GET", "/focus")

        self.assertEqual(200, status)
        for field in ("ok", "active", "since", "pause_until", "url_patterns", "version"):
            self.assertIn(field, payload, field)
        self.assertIsInstance(payload["url_patterns"], list)

    def test_a_handed_over_session_turns_active_and_back(self):
        status, payload = self.call("POST", "/focus/state", {"active": True, "source": "chillfocus"})
        self.assertEqual(200, status)
        self.assertTrue(payload["active"])

        _status, focus = self.call("GET", "/focus")
        self.assertTrue(focus["active"])
        self.assertTrue(focus["delegated"])

        _status, payload = self.call("POST", "/focus/state", {"active": False, "source": "chillfocus"})
        self.assertFalse(payload["active"])
        _status, focus = self.call("GET", "/focus")
        self.assertFalse(focus["active"])

    def test_a_handed_over_session_can_carry_a_lease(self):
        status, payload = self.call(
            "POST", "/focus/state",
            {"active": True, "source": "chillfocused", "ttl_seconds": 45},
        )

        self.assertEqual(200, status)
        self.assertTrue(payload["active"])
        self.assertAlmostEqual(45.0, payload["delegated_ttl"], delta=0.5)

        _status, focus = self.call("GET", "/focus")
        self.assertTrue(focus["delegated_active"])
        self.assertGreater(focus["delegated_until"], 0)

    def test_a_lease_must_be_a_number(self):
        status, payload = self.call(
            "POST", "/focus/state", {"active": True, "ttl_seconds": "soon"}
        )

        self.assertEqual(400, status)
        self.assertIn("ttl_seconds", payload["error"])

    def test_scan_can_be_triggered(self):
        status, payload = self.call("POST", "/scan", {})

        self.assertEqual(200, status)
        self.assertIn("frozen", payload)

    def test_a_pomodoro_can_be_started_with_its_own_durations(self):
        status, payload = self.call(
            "POST", "/focus/session",
            {"mode": "pomodoro", "work_minutes": 50, "break_minutes": 10, "cycles": 3},
        )

        self.assertEqual(200, status)
        session = payload["session"]
        self.assertEqual("pomodoro", session["mode"])
        self.assertEqual("work", session["phase"])
        self.assertEqual(3, session["cycles"])
        self.assertEqual(50.0, session["work_minutes"])
        _status, focus = self.call("GET", "/focus")
        self.assertEqual("pomodoro", focus["session_mode"])
        self.assertEqual(1, focus["session_cycle"])

    def test_a_stopwatch_can_be_started_and_has_no_deadline(self):
        status, payload = self.call("POST", "/focus/session", {"mode": "stopwatch"})

        self.assertEqual(200, status)
        self.assertEqual("stopwatch", payload["session"]["mode"])
        self.assertEqual(0.0, payload["session"]["until"])

    def test_a_bad_mode_is_refused(self):
        status, payload = self.call("POST", "/focus/session", {"mode": "whenever"})

        self.assertEqual(400, status)
        self.assertIn("mode", payload["error"])

    def test_time_can_be_added_and_a_break_skipped(self):
        self.call("POST", "/focus/session", {"mode": "pomodoro", "work_minutes": 5,
                                             "break_minutes": 5, "cycles": 4})
        before = self.call("GET", "/status")[1]["session_state"]["remaining"]

        status, payload = self.call("POST", "/focus/session", {"extend_minutes": 5})

        self.assertEqual(200, status)
        self.assertAlmostEqual(before + 300, payload["session"]["remaining"], delta=2)

        # Not in a break yet, so skipping is a no-op rather than an error.
        self.assertEqual(200, self.call("POST", "/focus/session", {"skip_break": True})[0])

    def test_the_session_defaults_are_editable(self):
        status, payload = self.call(
            "PATCH", "/config",
            {"session": {"mode": "pomodoro", "pomodoro_work_minutes": 40,
                         "pomodoro_break_minutes": 8, "pomodoro_cycles": 0}},
        )

        self.assertEqual(200, status)
        self.assertEqual("pomodoro", payload["session"]["mode"])
        self.assertEqual(40, payload["session"]["pomodoro_work_minutes"])
        self.assertEqual(0, payload["session"]["pomodoro_cycles"])

        # ...and starting without arguments now uses them.
        _status, started = self.call("POST", "/focus/session", {})
        self.assertEqual(40.0, started["session"]["work_minutes"])
        self.assertEqual(8.0, started["session"]["break_minutes"])
        self.assertEqual(0, started["session"]["cycles"])

    def test_a_manual_session_can_be_started_and_stopped(self):
        status, payload = self.call("POST", "/focus/session", {"minutes": 15})
        self.assertEqual(200, status)
        self.assertTrue(payload["active"])
        self.assertGreater(payload["until"], 0)

        _status, focus = self.call("GET", "/focus")
        self.assertTrue(focus["manual_session"])

        _status, payload = self.call("POST", "/focus/session", {"stop": True})
        self.assertTrue(payload["stopped"])
        _status, focus = self.call("GET", "/focus")
        self.assertFalse(focus["active"])

    def test_a_pause_is_reported_to_the_browser(self):
        self.call("POST", "/focus/session", {"minutes": 5})

        status, payload = self.call("POST", "/focus/pause", {"seconds": 300})

        self.assertEqual(200, status)
        self.assertGreater(payload["pause_until"], 0)
        _status, focus = self.call("GET", "/focus")
        self.assertTrue(focus["paused"])

    def test_pause_rejects_junk(self):
        status, payload = self.call("POST", "/focus/pause", {"seconds": "soon"})

        self.assertEqual(400, status)
        self.assertFalse(payload["ok"])

    def test_the_frozen_list_starts_empty_and_is_a_list(self):
        status, payload = self.call("GET", "/focus/frozen")

        self.assertEqual(200, status)
        self.assertEqual(0, payload["count"])
        self.assertEqual([], payload["frozen"])

    def test_thaw_all_answers_even_with_nothing_frozen(self):
        status, payload = self.call("POST", "/thaw", {})

        self.assertEqual(200, status)
        self.assertEqual(0, payload["thawed"])
        self.assertIn("detail", payload)


class RuleProtocolTests(ServerTestCase):
    def test_rules_are_accepted_over_put(self):
        status, payload = self.call(
            "PUT", "/focus/rules",
            {"names": ["dv"], "cmdline_substrings": ["--distract"], "pids": [],
             "protect_names": ["my-shell"], "protect_cmdline_substrings": []},
        )

        self.assertEqual(200, status)
        # The blacklist is the union of every source: this fixture's config file
        # also lists firefox, and a push must not make it disappear.
        self.assertIn("dv", payload["applied"]["names"])

        _status, status_payload = self.call("GET", "/status")
        self.assertIn("dv", status_payload["blacklist"]["names"])
        self.assertIn("firefox", status_payload["blacklist"]["names"])
        self.assertEqual(["--distract"], status_payload["blacklist"]["cmdline_substrings"])
        self.assertGreaterEqual(status_payload["protect_count"], 1)

    def test_an_empty_protect_list_never_strips_the_rails(self):
        before = self.call("GET", "/status")[1]["protect_count"]

        self.call("PUT", "/focus/rules", {"names": [], "protect_names": []})

        after = self.call("GET", "/status")[1]["protect_count"]
        self.assertEqual(before, after)

    def test_url_patterns_can_be_published(self):
        status, payload = self.call("POST", "/urls", {"patterns": ["reddit.com", "reddit.com"]})

        self.assertEqual(200, status)
        self.assertEqual(["reddit.com"], payload["url_patterns"])


class AuthTests(ServerTestCase):
    token = "s3cret"

    def test_a_missing_token_is_refused(self):
        status, payload = self.call("GET", "/status", token="")

        self.assertEqual(401, status)
        self.assertFalse(payload["ok"])

    def test_the_right_token_is_accepted(self):
        status, _payload = self.call("GET", "/status", token="s3cret")

        self.assertEqual(200, status)


class DashboardTests(ServerTestCase):
    def test_the_dashboard_is_served(self):
        with urllib.request.urlopen("http://127.0.0.1:%d/" % self.port, timeout=5) as response:
            body = response.read().decode("utf-8")

        self.assertEqual(200, response.status)
        self.assertIn("Focused", body)
        # The dashboard is a real UI: navigation, a plugin page, the session
        # buttons and the escape hatch all have to be there.
        for needle in ('id="tab-plugins"', 'id="tab-apps"', 'id="modes"', 'data-mode="pomodoro"',
                       'data-mode="stopwatch"', 'id="workMinutes"', 'id="breakMinutes"',
                       "renderSessionClock", "renderHeaderActions", "立即恢复全部",
                       "/focus/session", "屏蔽模式", "保护模式", "details.fold"):
            self.assertIn(needle, body, needle)


class ErrorTests(ServerTestCase):
    def test_a_bad_body_is_a_400(self):
        status, payload = self.call("POST", "/focus/state", raw=b"{not json")

        self.assertEqual(400, status)
        self.assertFalse(payload["ok"])

    def test_a_wrong_type_is_a_400(self):
        status, payload = self.call("POST", "/focus/state", {"active": "yes"})

        self.assertEqual(400, status)
        self.assertIn("must be true or false", payload["error"])

    def test_an_unknown_endpoint_is_a_404(self):
        status, payload = self.call("GET", "/nope")

        self.assertEqual(404, status)
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()
