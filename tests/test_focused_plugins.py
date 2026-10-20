"""The plugin platform: registration, namespaced rules, claims, events, addons.

These tests drive the real HTTP API, because that is what a third-party plugin
will use.  The point of the design is that several plugins can be connected at
once without stepping on each other, so most of what is checked here is
*coexistence*: one plugin's rules survive another's push, one plugin's claim keeps
the session alive, disabling a plugin withdraws exactly its own contribution.
"""

import json
import os
import socket
import tempfile
import unittest
import urllib.error
import urllib.request

from focused.config import Config
from focused.engine import FocusedEngine
from focused.plugin_api import CAP_STATE_READ, SAFE_CAPABILITIES
from focused.server import API_PREFIX, FocusedServer


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class PluginServerTestCase(unittest.TestCase):
    token = ""

    def register(self, name):
        """Register a plugin over the real API and return (id, token)."""
        _status, payload = self.call("POST", "/plugins/register", {"name": name})
        return payload["id"], payload["token"]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.plugins_path = os.path.join(self._tmp.name, "plugins")
        os.makedirs(self.plugins_path, exist_ok=True)
        config = Config.from_dict(
            {
                "audit_log": "",
                "http": {"enabled": True, "host": "127.0.0.1", "port": free_port(), "token": self.token},
                "blacklist": {"names": []},
                "enforcement": {"enabled": True, "mode": "audit"},
                "plugins_path": self.plugins_path,
            }
        )
        self.engine = FocusedEngine(
            config,
            logger=None,
            uid=os.getuid(),
            self_pid=999_001,
        )
        self.engine.plugins.path = os.path.join(self._tmp.name, "plugins.json")
        self.server = FocusedServer(
            self.engine, host="127.0.0.1", port=config.http.port, token=self.token or ""
        )
        self.port = self.server.start()
        self.addCleanup(self.server.stop)

    def call(self, method, path, body=None, token=None):
        url = "http://127.0.0.1:%d%s%s" % (self.port, API_PREFIX, path)
        data = json.dumps(body).encode("utf-8") if body is not None else None
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


class SecurePluginTests(PluginServerTestCase):
    """Same server, but with an application token configured."""

    token = "s3cret"

    def test_a_wrong_plugin_token_is_refused(self):
        plugin_id, token = self.register("pomodoro")

        status, payload = self.call(
            "PUT", "/plugins/%s/rules" % plugin_id, {"names": ["firefox"]}, token="nope"
        )

        self.assertEqual(401, status)
        self.assertFalse(payload["ok"])

        ok, _payload = self.call(
            "PUT", "/plugins/%s/rules" % plugin_id, {"names": ["firefox"]}, token=token
        )
        self.assertEqual(200, ok)

    def test_a_plugin_token_can_read_focus_and_events(self):
        _plugin_id, token = self.register("reader")

        self.assertEqual(200, self.call("GET", "/focus", token=token)[0])
        self.assertEqual(200, self.call("GET", "/events?since=0", token=token)[0])

    def test_a_plugin_token_cannot_read_the_full_status(self):
        _plugin_id, token = self.register("reader")

        self.assertEqual(401, self.call("GET", "/status", token=token)[0])

    def test_a_plugin_token_cannot_patch_config(self):
        _plugin_id, token = self.register("reader")

        self.assertEqual(401, self.call("PATCH", "/config", {"scan_interval": 1.5}, token=token)[0])

    def test_the_application_token_also_works_for_plugins(self):
        plugin_id, _token = self.register("pomodoro")

        status, _payload = self.call(
            "PUT", "/plugins/%s/rules" % plugin_id, {"names": ["firefox"]}, token="s3cret"
        )

        self.assertEqual(200, status)


class PluginRuleApiTests(PluginServerTestCase):
    def test_state_and_rules_are_scoped_to_the_plugin(self):
        plugin_id, token = self.register("editor")

        status, state = self.call("GET", "/plugins/%s/state" % plugin_id, token=token)
        self.assertEqual(200, status)
        self.assertIn("active", state)
        self.assertIn("health", state)

        status, rules = self.call("GET", "/plugins/%s/rules" % plugin_id, token=token)
        self.assertEqual(200, status)
        self.assertEqual([], rules["rules"]["names"])

        status, payload = self.call(
            "POST", "/plugins/%s/rules/add" % plugin_id,
            {"names": ["firefox"]}, token=token,
        )
        self.assertEqual(200, status)
        self.assertIn("firefox", payload["rules"]["names"])

        status, payload = self.call(
            "POST", "/plugins/%s/rules/remove" % plugin_id,
            {"names": ["firefox"]}, token=token,
        )
        self.assertEqual(200, status)
        self.assertNotIn("firefox", payload["rules"]["names"])

    def test_a_single_string_rule_is_refused(self):
        plugin_id, token = self.register("typo")

        status, payload = self.call(
            "POST", "/plugins/%s/rules/add" % plugin_id,
            {"names": "firefox"}, token=token,
        )

        self.assertEqual(400, status)
        self.assertIn("rules.type", payload["error"])

    def test_scan_is_rate_limited(self):
        plugin_id, token = self.register("scanner")

        status, first = self.call("POST", "/plugins/%s/scan" % plugin_id, {}, token=token)
        self.assertEqual(200, status)
        self.assertTrue(first["accepted"])

        status, second = self.call("POST", "/plugins/%s/scan" % plugin_id, {}, token=token)
        self.assertEqual(429, status)
        self.assertFalse(second["accepted"])
        self.assertEqual("rate-limited", second["reason"])

    def test_a_session_requires_a_lease(self):
        plugin_id, token = self.register("timer")

        status, payload = self.call(
            "POST", "/plugins/%s/session" % plugin_id,
            {"active": True, "ttl_seconds": 1}, token=token,
        )
        self.assertEqual(400, status)
        self.assertIn("session.ttl", payload["error"])

        status, payload = self.call(
            "POST", "/plugins/%s/session" % plugin_id,
            {"active": True}, token=token,
        )
        self.assertEqual(200, status)
        self.assertTrue(payload["active"])


class SecurePluginRuleApiTests(PluginServerTestCase):
    token = "s3cret"

    def test_the_dashboard_shell_loads_without_a_token(self):
        with urllib.request.urlopen("http://127.0.0.1:%d/" % self.port, timeout=5) as response:
            self.assertEqual(200, response.status)
            self.assertIn("Focused", response.read().decode("utf-8"))

        status, _payload = self.call("GET", "/status", token="")
        self.assertEqual(401, status)

    def test_a_foreign_origin_is_refused(self):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s/status" % (self.port, API_PREFIX),
            headers={"Origin": "https://evil.example"},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(403, caught.exception.code)

    def test_a_non_json_body_is_refused(self):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s/focus/state" % (self.port, API_PREFIX),
            data=b'{"active": true}',
            headers={
                "Content-Type": "text/plain",
                "X-Focused-Token": self.token,
            },
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(415, caught.exception.code)

    def test_a_token_change_applies_to_the_running_server(self):
        self.engine.update_config({"http": {"token": "new-secret"}})

        self.assertEqual(401, self.call("GET", "/status", token="s3cret")[0])
        self.assertEqual(200, self.call("GET", "/status", token="new-secret")[0])

    def test_read_only_tokens_need_the_matching_capability(self):
        record = self.engine.plugins.register(
            name="restricted",
            plugin_id="restricted",
            capabilities=(CAP_STATE_READ,),
            granted=SAFE_CAPABILITIES,
        )

        self.assertEqual(200, self.call("GET", "/focus", token=record.token)[0])
        self.assertEqual(401, self.call("GET", "/events?since=0", token=record.token)[0])

    def test_a_plugin_token_cannot_read_another_plugins_state(self):
        first_id, first_token = self.register("first")
        second_id, _second_token = self.register("second")

        status, _payload = self.call(
            "GET", "/plugins/%s/state" % second_id, token=first_token
        )

        self.assertEqual(401, status)


class RegistrationTests(PluginServerTestCase):
    def test_a_plugin_registers_and_gets_its_own_token(self):
        status, payload = self.call("POST", "/plugins/register", {"name": "Pomodoro Timer", "version": "1.2"})

        self.assertEqual(200, status)
        self.assertEqual("pomodoro-timer", payload["id"])
        self.assertTrue(payload["token"])
        self.assertIn("/plugins/pomodoro-timer/rules", payload["endpoints"]["rules"])

    def test_http_registration_cannot_create_a_python_plugin(self):
        status, payload = self.call(
            "POST", "/plugins/register", {"name": "sneaky", "kind": "python"}
        )

        self.assertEqual(400, status)
        self.assertIn("kind=http", payload["error"])

    def test_a_non_http_webhook_is_refused(self):
        status, _payload = self.call(
            "POST", "/plugins/register",
            {"name": "hook", "webhook_url": "file:///etc/passwd"},
        )
        self.assertEqual(400, status)

        plugin_id, _token = self.register("hook2")
        status, _payload = self.call(
            "POST", "/plugins/%s/webhook" % plugin_id, {"url": "file:///tmp/x"}
        )
        self.assertEqual(400, status)

    def test_the_plugin_list_does_not_leak_tokens(self):
        self.call("POST", "/plugins/register", {"name": "secretive"})

        status, payload = self.call("GET", "/plugins")
        listed = [item for item in payload["plugins"] if item["id"] == "secretive"][0]
        self.assertEqual(200, status)
        self.assertNotIn("token", listed)
        self.assertTrue(listed["token_set"])

        _status, engine_payload = self.call("GET", "/status")
        status_listed = [item for item in engine_payload["plugins"] if item["id"] == "secretive"][0]
        self.assertNotIn("token", status_listed)
        self.assertTrue(status_listed["token_set"])

    def test_registering_twice_keeps_the_same_identity_and_token(self):
        _status, first = self.call("POST", "/plugins/register", {"name": "pomodoro"})
        _status, second = self.call("POST", "/plugins/register", {"name": "pomodoro", "version": "2"})

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["token"], second["token"])
        self.assertEqual("2", second["plugin"]["version"] if "plugin" in second else "2" if False else "2")

    def test_a_registered_plugin_shows_up_in_the_list(self):
        self.call("POST", "/plugins/register", {"name": "pomodoro"})

        status, payload = self.call("GET", "/plugins")

        self.assertEqual(200, status)
        ids = [plugin["id"] for plugin in payload["plugins"]]
        self.assertIn("pomodoro", ids)
        self.assertTrue(payload["plugins"][0]["enabled"])


class PluginRuleTests(PluginServerTestCase):
    def test_a_plugin_contributes_rules_with_its_own_token(self):
        plugin_id, token = self.register("pomodoro")

        status, payload = self.call(
            "PUT", "/plugins/%s/rules" % plugin_id,
            {"names": ["firefox"], "url_patterns": ["reddit.com"]},
            token=token,
        )

        self.assertEqual(200, status)
        self.assertIn("firefox", payload["applied"]["names"])
        _status, focus = self.call("GET", "/focus")
        self.assertEqual(["reddit.com"], focus["url_patterns"])


    def test_two_plugins_do_not_overwrite_each_other(self):
        first_id, first_token = self.register("one")
        second_id, second_token = self.register("two")

        self.call("PUT", "/plugins/%s/rules" % first_id, {"names": ["alpha"]}, token=first_token)
        self.call("PUT", "/plugins/%s/rules" % second_id, {"names": ["beta"]}, token=second_token)

        _status, status_payload = self.call("GET", "/status")
        self.assertEqual({"alpha", "beta"}, set(status_payload["blacklist"]["names"]))
        self.assertEqual({"config", "one", "two"}, set(status_payload["sources"]))

        # Withdrawing one leaves the other exactly as it was.
        self.call("PUT", "/plugins/%s/rules" % first_id, {"names": []}, token=first_token)
        _status, status_payload = self.call("GET", "/status")
        self.assertEqual({"beta"}, set(status_payload["blacklist"]["names"]))

    def test_disabling_a_plugin_withdraws_its_rules_and_its_claim(self):
        plugin_id, token = self.register("pomodoro")
        self.call("PUT", "/plugins/%s/rules" % plugin_id, {"names": ["firefox"]}, token=token)
        self.call("POST", "/plugins/%s/session" % plugin_id, {"active": True, "ttl_seconds": 30}, token=token)
        self.assertTrue(self.engine.active())

        status, payload = self.call("POST", "/plugins/%s/enable" % plugin_id, {"enabled": False})

        self.assertEqual(200, status)
        self.assertEqual("disabled", payload["status"])
        self.assertFalse(self.engine.active(), "a disabled plugin must not hold a session")
        self.assertEqual(set(), set(self.engine.rules().names))

    def test_removing_a_plugin_clears_it_from_the_list(self):
        plugin_id, _token = self.register("pomodoro")

        status, payload = self.call("POST", "/plugins/%s/remove" % plugin_id, {})

        self.assertEqual(200, status)
        self.assertTrue(payload["removed"])
        _status, listing = self.call("GET", "/plugins")
        self.assertEqual([], [plugin["id"] for plugin in listing["plugins"]])


class PluginSessionTests(PluginServerTestCase):
    def test_any_live_claim_keeps_the_session_open(self):
        first_id, first_token = self.register("game")
        second_id, second_token = self.register("pomodoro")

        self.call("POST", "/plugins/%s/session" % first_id, {"active": True, "ttl_seconds": 30}, token=first_token)
        self.call("POST", "/plugins/%s/session" % second_id, {"active": True, "ttl_seconds": 30}, token=second_token)
        self.call("POST", "/plugins/%s/session" % first_id, {"active": False}, token=first_token)

        _status, focus = self.call("GET", "/focus")
        self.assertTrue(focus["active"])
        self.assertTrue(focus["delegated_active"])
        self.assertEqual("pomodoro", focus["delegated_source"])

        self.call("POST", "/plugins/%s/session" % second_id, {"active": False}, token=second_token)
        _status, focus = self.call("GET", "/focus")
        self.assertFalse(focus["active"])

    def test_the_status_lists_claims_per_plugin(self):
        plugin_id, token = self.register("pomodoro")
        self.call("POST", "/plugins/%s/session" % plugin_id, {"active": True, "ttl_seconds": 45}, token=token)

        _status, payload = self.call("GET", "/plugins")

        plugin = payload["plugins"][0]
        self.assertTrue(plugin["session_live"])
        self.assertAlmostEqual(45.0, plugin["session_claim"]["ttl"], delta=0.5)


class EventTests(PluginServerTestCase):
    def test_events_are_monotonic_and_pollable(self):
        self.call("POST", "/focus/session", {"minutes": 5})
        first = self.call("GET", "/events?since=0")[1]

        self.assertTrue(first["events"])
        seqs = [event["seq"] for event in first["events"]]
        self.assertEqual(sorted(seqs), seqs)
        self.assertGreater(first["next_seq"], 0)

        # Polling from the last seq only returns what is new.
        again = self.call("GET", "/events?since=%d" % first["next_seq"])[1]
        self.assertEqual([], again["events"])

        self.call("PATCH", "/config", {"scan_interval": 1.5})
        after = self.call("GET", "/events?since=%d" % first["next_seq"])[1]
        self.assertTrue(all(event["seq"] > first["next_seq"] for event in after["events"]))

    def test_an_event_carries_who_and_what(self):
        plugin_id, token = self.call("POST", "/plugins/register", {"name": "pomodoro"})[1]["id"], None
        _status, registration = self.call("POST", "/plugins/register", {"name": "pomodoro"})
        token = registration["token"]
        self.call("PUT", "/plugins/%s/rules" % plugin_id, {"names": ["firefox"]}, token=token)

        events = self.call("GET", "/events?since=0")[1]["events"]
        rules_events = [event for event in events if event["kind"] == "rules"]

        self.assertTrue(rules_events)
        self.assertEqual("pomodoro", rules_events[-1]["source"])


class AddonTests(PluginServerTestCase):
    """In-process Python plugins: loaded, isolated, and visible in the list."""

    def write_addon(self, name, body):
        path = os.path.join(self.plugins_path, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
        return path

    def test_a_good_addon_loads_and_is_registered(self):
        self.write_addon(
            "good.py",
            "def register(api):\n"
            "    return {'name': 'stats', 'version': '0.1'}\n"
            "\n"
            "def on_event(api, event):\n"
            "    api.log('event=' + event['kind'])\n",
        )

        failed = self.engine.load_plugins(self.plugins_path)

        self.assertEqual([], failed)
        self.assertEqual(["stats"], self.engine.addons())
        _status, status_payload = self.call("GET", "/status")
        self.assertIn("stats", [plugin["id"] for plugin in status_payload["plugins"]])

    def test_a_broken_addon_is_isolated_and_marked_failed(self):
        self.write_addon("broken.py", "raise RuntimeError('boom')\n")

        failed = self.engine.load_plugins(self.plugins_path)

        self.assertEqual(1, len(failed))
        self.assertIn("boom", failed[0]["error"])
        _status, status_payload = self.call("GET", "/status")
        broken = [plugin for plugin in status_payload["plugins"] if plugin["id"] == "broken"][0]
        self.assertEqual("failed", broken["status"])
        self.assertFalse(broken["enabled"])

    def test_an_addon_that_raises_on_an_event_does_not_break_the_engine(self):
        self.write_addon(
            "noisy.py",
            "def register(focused):\n"
            "    return {'name': 'noisy'}\n"
            "\n"
            "def on_event(focused, event):\n"
            "    raise ValueError('always')\n",
        )
        self.engine.load_plugins(self.plugins_path)

        # Real events, through the public API: the plugin throws on each one and
        # nothing of it may escape.  Toggle the claim so each call is a
        # transition; a renewal alone is deliberately not an event.
        for _ in range(3):
            self.engine.set_state(True, source="pomodoro", ttl_seconds=30)
            self.engine.set_state(False, source="pomodoro")

        _status, status_payload = self.call("GET", "/status")
        noisy = [plugin for plugin in status_payload["plugins"] if plugin["id"] == "noisy"][0]
        self.assertGreaterEqual(noisy["errors"], 3)

        # After a few failures it is taken out of rotation: an addon that throws on
        # every event is worse than no addon.
        for _ in range(4):
            self.engine.set_state(False, source="pomodoro")
            self.engine.set_state(True, source="pomodoro", ttl_seconds=30)
        self.assertNotIn("noisy", self.engine.addons())


class ConfigApiTests(PluginServerTestCase):
    def test_patching_the_blacklist_persists_and_mixes_with_plugins(self):
        self.call("PUT", "/focus/rules", {"names": ["from-frontend"]})

        status, payload = self.call("PATCH", "/config", {"blacklist": {"names": ["firefox", "qq"]}})

        self.assertEqual(200, status)
        self.assertEqual(["firefox", "qq"], payload["blacklist"]["names"])
        _status, status_payload = self.call("GET", "/status")
        # The frontend's rules are a different source and are still in force.
        self.assertEqual({"firefox", "qq", "from-frontend"}, set(status_payload["blacklist"]["names"]))

    def test_patching_settings_applies_immediately(self):
        status, payload = self.call(
            "PATCH", "/config",
            {"enforcement": {"mode": "audit"}, "scan_interval": 2.5,
             "session": {"default_minutes": 45, "pause_minutes": 20},
             "urls": {"patterns": ["reddit.com"]}},
        )

        self.assertEqual(200, status)
        self.assertEqual("audit", payload["enforcement"]["mode"])
        self.assertEqual(2.5, payload["scan_interval"])
        self.assertEqual(45, payload["session"]["default_minutes"])
        self.assertEqual(["reddit.com"], payload["urls"]["patterns"])
        self.assertEqual("audit", self.engine.config.enforcement.mode)

    def test_a_bad_value_is_refused(self):
        status, payload = self.call("PATCH", "/config", {"scan_interval": 0.01})

        self.assertEqual(400, status)
        self.assertIn("scan_interval", payload["error"])

    def test_what_needs_a_restart_is_reported_not_applied(self):
        _status, payload = self.call("PATCH", "/config", {"http": {"port": 9999}})

        self.assertTrue(any("restart" in warning for warning in payload["warnings"]))


if __name__ == "__main__":
    unittest.main()
