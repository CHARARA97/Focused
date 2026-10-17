"""PluginAPI: the narrow contract, and a real load through the engine."""

import os
import tempfile
import unittest

from focused.config import Config
from focused.engine import FocusedEngine
from focused.plugin_api import PluginAPI, PluginError, PluginManifest


class FakeHost:
    def __init__(self):
        self.rules = []
        self.added = []
        self.removed = []
        self.claims = []
        self.releases = 0
        self.logs = []
        self.scans = []
        self.frozen_calls = 0
        self.events = []

    def replace_plugin_rules(self, plugin_id, payload):
        self.rules.append((plugin_id, payload))

    def add_plugin_rules(self, plugin_id, payload):
        self.added.append((plugin_id, payload))

    def remove_plugin_rules(self, plugin_id, payload):
        self.removed.append((plugin_id, payload))

    def list_plugin_rules(self, plugin_id):
        return {"names": ["firefox"]}

    def claim_plugin_session(self, plugin_id, ttl):
        self.claims.append((plugin_id, ttl))

    def release_plugin_session(self, plugin_id):
        self.releases += 1

    def plugin_session_status(self, plugin_id):
        return {"active": True, "my_claim": {"ttl": 30}}

    def plugin_status_snapshot(self, plugin_id=None):
        return {"ok": True, "active": True}

    def plugin_frozen_snapshot(self, plugin_id=None):
        self.frozen_calls += 1
        return [{"name": "firefox"}]

    def request_plugin_scan(self, plugin_id):
        self.scans.append(plugin_id)
        return {"accepted": True, "frozen": 1, "reason": "ok"}

    def recent_events(self, plugin_id, since, limit):
        self.events.append((plugin_id, since, limit))
        return [{"kind": "freeze"}]

    def log_plugin(self, plugin_id, level, message):
        self.logs.append((plugin_id, level, message))


class PluginApiUnitTests(unittest.TestCase):
    def test_registration_calls_are_buffered_until_bind(self):
        host = FakeHost()
        api = PluginAPI("provisional", host)

        api.replace_rules(names=["firefox"])
        api.claim_session(ttl_seconds=30)
        self.assertEqual([], host.rules)
        self.assertEqual([], host.claims)

        api._bind("real", PluginManifest(id="real", name="Real"))

        self.assertEqual("real", api.id)
        self.assertEqual(
            [("real", {"names": ("firefox",), "cmdline_substrings": (),
                       "pids": (), "protect_names": (),
                       "protect_cmdline_substrings": ()})],
            host.rules,
        )
        self.assertEqual([("real", 30.0)], host.claims)

    def test_a_single_string_is_refused(self):
        api = PluginAPI("p", FakeHost())

        with self.assertRaises(PluginError) as caught:
            api.replace_rules(names="firefox")

        self.assertEqual("rules.type", caught.exception.code)

    def test_non_string_rules_are_refused(self):
        api = PluginAPI("p", FakeHost())

        with self.assertRaises(PluginError):
            api.replace_rules(names=[1, 2])

    def test_session_ttl_is_bounded(self):
        api = PluginAPI("p", FakeHost())

        with self.assertRaises(PluginError) as caught:
            api.claim_session(ttl_seconds=1)
        self.assertEqual("session.ttl", caught.exception.code)

        with self.assertRaises(PluginError):
            api.claim_session(ttl_seconds="soon")

    def test_a_capability_must_be_declared(self):
        manifest = PluginManifest(id="p", name="P", capabilities=("status",))
        api = PluginAPI("p", FakeHost(), manifest)

        with self.assertRaises(PluginError) as caught:
            api.replace_rules(names=["firefox"])
        self.assertEqual("capability.missing", caught.exception.code)

        with self.assertRaises(PluginError):
            api.claim_session(30)

        self.assertEqual({"ok": True, "active": True}, api.status())

    def test_log_levels_are_validated_and_tagged(self):
        host = FakeHost()
        api = PluginAPI("p", host)

        api.log("hello")
        with self.assertRaises(PluginError):
            api.log("nope", level="shout")

        self.assertEqual([("p", "info", "hello")], host.logs)

    def test_the_api_does_not_expose_the_engine(self):
        api = PluginAPI("p", FakeHost())

        self.assertFalse(hasattr(api, "engine"))

    def test_multiple_rules_calls_during_register_are_kept_in_order(self):
        host = FakeHost()
        api = PluginAPI("p", host)

        api.rules.add(names=["a"])
        api.rules.add(names=["b"])

        api._bind("p", PluginManifest(id="p", name="P"))

        self.assertEqual(
            [("a",), ("b",)],
            [item[1]["names"] for item in host.added],
        )

    def test_pids_must_be_positive(self):
        host = FakeHost()
        api = PluginAPI("p", host)
        api._bind("p", PluginManifest(id="p", name="P"))

        for bad in (0, -1):
            with self.assertRaises(PluginError):
                api.rules.add(pids=[bad])
        self.assertEqual([], host.added)

    def test_namespaced_surfaces_reach_the_host(self):
        host = FakeHost()
        api = PluginAPI("p", host)
        api._bind("p", PluginManifest(id="p", name="P"))

        api.rules.add(names=["firefox"])
        api.rules.remove(names=["firefox"])
        self.assertEqual(["firefox"], api.rules.list()["names"])
        api.session.claim(30)
        api.session.release()
        self.assertEqual({"active": True, "my_claim": {"ttl": 30}}, api.session.status())
        self.assertEqual({"ok": True, "active": True}, api.state.summary())
        self.assertEqual([{"name": "firefox"}], api.state.frozen())
        self.assertEqual(
            {"accepted": True, "frozen": 1, "reason": "ok"}, api.scan.request()
        )
        self.assertEqual([{"kind": "freeze"}], api.events.recent(0, 5))

        self.assertEqual(1, len(host.added))
        self.assertEqual(1, len(host.removed))
        self.assertEqual(1, host.frozen_calls)
        self.assertEqual(["p"], host.scans)
        self.assertEqual([("p", 0, 5)], host.events)

    def test_manifest_precedence_is_id_then_name_then_file(self):
        self.assertEqual("declared", PluginManifest.from_registration(
            {"id": "declared", "name": "Name"}, "file").id)
        self.assertEqual("name-slug", PluginManifest.from_registration(
            {"name": "name-slug"}, "file").id)
        self.assertEqual("file", PluginManifest.from_registration({}, "file").id)

    def test_unknown_capability_is_refused(self):
        with self.assertRaises(PluginError):
            PluginManifest.from_registration({"capabilities": ["freeze"]}, "file")


class PluginApiEngineTests(unittest.TestCase):
    def build(self, tmp, plugins):
        plugins_dir = os.path.join(tmp, "plugins")
        os.makedirs(plugins_dir, exist_ok=True)
        for name, source in plugins.items():
            with open(os.path.join(plugins_dir, name), "w", encoding="utf-8") as handle:
                handle.write(source)
        config = Config.from_dict(
            {
                "audit_log": "",
                "freeze": {"enabled": True, "state_path": os.path.join(tmp, "frozen.json")},
                "plugins_path": plugins_dir,
                "registry_path": os.path.join(tmp, "plugins.json"),
            }
        )
        engine = FocusedEngine(
            config,
            logger=None,
            uid=os.getuid(),
            self_pid=999_001,
            self_cgroup="/user.slice/user-1000.slice/user@1000.service/app.slice/focusedd.service",
            unified_cgroup2=True,
        )
        failed = engine.load_plugins(plugins_dir)
        return engine, failed

    def test_a_plugin_contributes_rules_and_a_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, failed = self.build(tmp, {
                "api_plugin.py": (
                    "def register(api):\n"
                    "    api.replace_rules(names=['firefox'], protect_names=['my-protect'])\n"
                    "    api.claim_session(ttl_seconds=30)\n"
                    "    return {'name': 'api-plugin',\n"
                    "            'capabilities': ['rules', 'session', 'events']}\n"
                    "\n"
                    "def on_event(api, event):\n"
                    "    api.log('event=' + event['kind'])\n"
                ),
            })

            self.assertEqual([], failed)
            self.assertIn("firefox", engine.rules().names)
            self.assertIn("my-protect", engine.rules().protect_names)
            self.assertIn("wineserver", engine.rules().protect_names)
            self.assertIn("api-plugin", engine.live_claims())
            self.assertIn("api-plugin", engine.addons())

    def test_a_plugin_cannot_install_a_catch_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, failed = self.build(tmp, {
                "bad_plugin.py": (
                    "def register(api):\n"
                    "    api.replace_rules(names=['*'])\n"
                    "    return {'name': 'bad-plugin'}\n"
                ),
            })

            self.assertEqual(["bad-plugin"], [item["id"] for item in failed])
            self.assertNotIn("*", engine.rules().names)
            record = engine.plugins.get("bad-plugin")
            self.assertEqual("failed", record.status)

    def test_a_failure_in_on_start_withdraws_rules_and_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, failed = self.build(tmp, {
                "starter.py": (
                    "def register(api):\n"
                    "    api.replace_rules(names=['firefox'])\n"
                    "    return {'name': 'starter'}\n"
                    "\n"
                    "def on_start(api):\n"
                    "    api.claim_session(30)\n"
                    "    raise RuntimeError('boom')\n"
                ),
            })

            self.assertEqual(["starter"], [item["id"] for item in failed])
            self.assertNotIn("firefox", engine.rules().names)
            self.assertEqual({}, engine.live_claims())
            self.assertEqual("failed", engine.plugins.get("starter").status)

    def test_a_plugin_disabled_after_failures_loses_rules_and_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, failed = self.build(tmp, {
                "failing.py": (
                    "def register(api):\n"
                    "    api.replace_rules(names=['firefox'])\n"
                    "    api.claim_session(30)\n"
                    "    return {'name': 'failing', 'capabilities': ['rules', 'session', 'events']}\n"
                    "\n"
                    "def on_event(api, event):\n"
                    "    raise RuntimeError('always')\n"
                ),
            })

            self.assertEqual([], failed)
            for _ in range(4):
                engine.set_state(True, source="pomodoro", ttl_seconds=30)
                engine.set_state(False, source="pomodoro")

            self.assertNotIn("failing", engine.addons())
            self.assertNotIn("firefox", engine.rules().names)
            self.assertEqual({}, engine.live_claims())
            self.assertEqual("disabled", engine.plugins.get("failing").status)

    def test_buffered_calls_respect_declared_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, failed = self.build(tmp, {
                "limited.py": (
                    "def register(api):\n"
                    "    api.replace_rules(names=['firefox'])\n"
                    "    return {'name': 'limited', 'capabilities': ['status']}\n"
                ),
            })

            self.assertEqual(["limited"], [item["id"] for item in failed])
            self.assertNotIn("firefox", engine.rules().names)

    def test_a_plugin_can_use_the_namespaced_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, failed = self.build(tmp, {
                "namespaced.py": (
                    "def register(api):\n"
                    "    api.rules.add(names=['firefox'])\n"
                    "    api.session.claim(30)\n"
                    "    return {'name': 'namespaced'}\n"
                ),
            })

            self.assertEqual([], failed)
            self.assertIn("firefox", engine.rules().names)
            self.assertIn("namespaced", engine.live_claims())

    def test_a_plugin_without_events_capability_gets_no_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, failed = self.build(tmp, {
                "silent.py": (
                    "def register(api):\n"
                    "    return {'name': 'silent', 'capabilities': ['rules']}\n"
                    "\n"
                    "def on_event(api, event):\n"
                    "    raise RuntimeError('should not be called')\n"
                ),
            })

            self.assertEqual([], failed)
            engine.set_state(True, source="pomodoro", ttl_seconds=30)
            self.assertEqual(0, engine.plugins.get("silent").errors)

    def test_a_plugin_cannot_strip_the_protect_rails(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, failed = self.build(tmp, {
                "stripper.py": (
                    "def register(api):\n"
                    "    api.replace_rules(protect_names=[])\n"
                    "    return {'name': 'stripper'}\n"
                ),
            })

            self.assertEqual([], failed)
            self.assertIn("wineserver", engine.rules().protect_names)
            self.assertIn("systemd", engine.rules().protect_names)

    def test_a_failing_on_event_hook_is_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, failed = self.build(tmp, {
                "noisy.py": (
                    "def register(api):\n"
                    "    return {'name': 'noisy'}\n"
                    "\n"
                    "def on_event(api, event):\n"
                    "    raise ValueError('always')\n"
                ),
            })

            self.assertEqual([], failed)
            for _ in range(4):
                engine.set_state(True, source="pomodoro", ttl_seconds=30)
                engine.set_state(False, source="pomodoro")
            self.assertNotIn("noisy", engine.addons())


if __name__ == "__main__":
    unittest.main()
