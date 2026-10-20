"""Engine-level tests: configuration, sessions, rules and PID guards.

Nothing here signals a process; the end-to-end kill path lives in
``test_lifecycle.py``, and the per-target decisions in ``test_enforcer.py``.
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from focused.config import Config, FocusedConfigError
from focused.engine import FocusedEngine


class Burner:
    """A CPU-burning process with a marker name, in its own session."""

    def __init__(self, marker):
        self.dir = tempfile.mkdtemp(prefix="focused-test-")
        self.path = os.path.join(self.dir, marker)
        shutil.copy(shutil.which("yes") or "/usr/bin/yes", self.path)
        self.marker = marker
        self.proc = subprocess.Popen(
            [self.path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.pid = self.proc.pid

    def stop(self):
        try:
            os.kill(self.pid, signal.SIGCONT)
        except OSError:
            pass
        if self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        shutil.rmtree(self.dir, ignore_errors=True)


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.burner = Burner("cf-focused-live")
        self.addCleanup(self.burner.stop)
        time.sleep(0.3)

    def engine(self, blacklist=None, **kwargs):
        config = Config.from_dict(
            {
                "audit_log": "",
                "blacklist": {"names": blacklist if blacklist is not None else [self.burner.marker]},
                "enforcement": {"enabled": True, "mode": "audit"},
            }
        )
        return FocusedEngine(
            config,
            logger=None,
            uid=os.getuid(),
            self_pid=999_001,
            **kwargs,
        )


class SessionTests(EngineTestCase):

    def test_nothing_happens_while_no_session_runs(self):
        engine = self.engine()

        result = engine.scan_once()

        self.assertEqual("no-session", result.gate)
        self.assertEqual(0, result.killed)
        self.assertIsNone(self.burner.proc.poll())

    def test_a_delegated_state_is_enough(self):
        engine = self.engine()

        engine.set_state(True, source="chillfocus")
        engine.scan_once()

        self.assertTrue(engine.active())
        self.assertTrue(engine.focus_state()["delegated"])
        self.assertFalse(engine.focus_state()["manual_session"])


    def test_a_lease_expires_and_ends_the_session(self):
        engine = self.engine()

        engine.set_state(True, source="chillfocused", ttl_seconds=30)

        self.assertTrue(engine.active())
        self.assertTrue(engine.delegated())
        self.assertAlmostEqual(30.0, engine.status()["delegated_ttl"], delta=0.1)

        # Renewing keeps it alive...
        engine.set_state(True, source="chillfocused", ttl_seconds=30)
        self.assertTrue(engine.active())

        # ...and letting it lapse ends the session, even with nothing suspended.
        engine._clock = lambda: time.time() + 60
        self.assertFalse(engine.active())
        engine.scan_once()
        self.assertFalse(engine.delegated())
        self.assertEqual(0.0, engine.status()["delegated_ttl"])


    def test_a_session_without_a_lease_never_expires(self):
        # A manual session (or a frontend that did not ask for a lease) is ended
        # explicitly, not by a timer.
        engine = self.engine()
        engine.set_state(True, source="script")

        self.assertTrue(engine.active())
        self.assertEqual(0.0, engine.status()["delegated_ttl"])

    def test_a_countdown_session_ends_by_itself(self):
        engine = self.engine(blacklist=[])
        engine.start_session(25)

        self.assertTrue(engine.active())
        self.assertEqual("countdown", engine.session_state()["mode"])

        engine._clock = lambda: time.time() + 25 * 60 + 1
        engine.scan_once()

        self.assertFalse(engine.active())
        self.assertIsNone(engine.session())
        self.assertEqual(0, engine.session_state()["remaining"])


    def test_a_finite_pomodoro_stops_after_its_last_round(self):
        engine = self.engine(blacklist=[])
        engine.start_session(mode="pomodoro", work_minutes=25, break_minutes=5, cycles=1)

        engine._clock = lambda: time.time() + 25 * 60 + 1
        engine.scan_once()

        self.assertIsNone(engine.session(), "one round means one round")
        self.assertFalse(engine.active())

    def test_a_stopwatch_runs_until_it_is_stopped(self):
        engine = self.engine(blacklist=[])
        engine.start_session(mode="stopwatch")

        engine._clock = lambda: time.time() + 3 * 3600
        engine.scan_once()

        self.assertTrue(engine.active(), "a stopwatch has no end")
        self.assertAlmostEqual(3 * 3600, engine.session_state()["elapsed"], delta=5)
        self.assertTrue(engine.stop_session())
        self.assertFalse(engine.active())

    def test_a_pass_does_not_eat_the_session(self):
        engine = self.engine(blacklist=[])
        engine.start_session(25)
        before = engine.session_state()["remaining"]

        engine.pause(600)

        after = engine.session_state()["remaining"]
        self.assertAlmostEqual(before + 600, after, delta=1)

    def test_extending_adds_time_to_what_is_running(self):
        engine = self.engine(blacklist=[])
        engine.start_session(25)
        before = engine.session_state()["remaining"]

        engine.extend_session(5)

        self.assertAlmostEqual(before + 300, engine.session_state()["remaining"], delta=1)

    def test_a_break_can_be_skipped(self):
        engine = self.engine(blacklist=[])
        engine.start_session(mode="pomodoro", work_minutes=1, break_minutes=10, cycles=3)

        engine._clock = lambda: time.time() + 61
        engine.scan_once()
        self.assertEqual("break", engine.session_state()["phase"])

        engine.skip_break()

        self.assertEqual("work", engine.session_state()["phase"])
        self.assertTrue(engine.active())


class RuleTests(EngineTestCase):
    def test_api_rules_replace_the_blacklist(self):
        engine = self.engine(blacklist=[])

        engine.apply_rules({"names": [self.burner.marker], "cmdline_substrings": [], "pids": []})

        self.assertIn(self.burner.marker, engine.rules().names)

    def test_the_built_in_rails_can_never_be_stripped(self):
        engine = self.engine(blacklist=[])
        before = set(engine.rules().protect_names)

        engine.apply_rules({"names": [], "protect_names": ["my-shell"]}, source="plugin-x")
        self.assertIn("my-shell", engine.rules().protect_names)

        # A source withdrawing its own additions is fine...
        engine.apply_rules({"names": [], "protect_names": []}, source="plugin-x")
        self.assertNotIn("my-shell", engine.rules().protect_names)

        # ...but the built-in rails are still there, whatever anybody pushes.
        after = set(engine.rules().protect_names)
        self.assertTrue(before <= after, before - after)

    def test_several_plugins_coexist_and_do_not_overwrite_each_other(self):
        engine = self.engine(blacklist=[])

        engine.apply_rules({"names": ["alpha"], "url_patterns": ["a.example"]}, source="one")
        engine.apply_rules({"names": ["beta"], "url_patterns": ["b.example"]}, source="two")

        self.assertEqual({"alpha", "beta"}, set(engine.rules().names))
        self.assertEqual(["a.example", "b.example"], engine.url_patterns())

        engine.apply_rules({"names": []}, source="one")

        self.assertEqual({"beta"}, set(engine.rules().names))
        self.assertEqual(["b.example"], engine.url_patterns())

    def test_any_live_claim_keeps_the_session_open(self):
        engine = self.engine(blacklist=[])

        engine.set_state(True, source="chillfocused", ttl_seconds=30)
        engine.set_state(True, source="pomodoro", ttl_seconds=30)
        engine.set_state(False, source="chillfocused")

        self.assertTrue(engine.active(), "the other plugin still claims a session")

        engine.set_state(False, source="pomodoro")
        self.assertFalse(engine.active())


class InstallLayoutTests(unittest.TestCase):
    """The CLI has to run from the layout ``scripts/install-focused.sh`` creates."""

    def test_the_installed_layout_finds_the_shared_engine(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            app = os.path.join(tmp, "app")
            os.makedirs(app)
            shutil.copy(os.path.join(repo, "focusedd.py"), os.path.join(app, "focusedd.py"))
            shutil.copytree(os.path.join(repo, "focused"), os.path.join(app, "focused"))
            config_path = os.path.join(tmp, "config.json")

            result = subprocess.run(
                [sys.executable, os.path.join(app, "focusedd.py"),
                 "--config", config_path, "--write-default-config", config_path],
                capture_output=True, text=True, timeout=60,
                # No PYTHONPATH: the bootstrap itself must find the library.
                env={"PATH": os.environ.get("PATH", ""), "HOME": tmp},
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(os.path.exists(config_path))


class ConfigTests(unittest.TestCase):
    def test_defaults_are_safe_and_complete(self):
        config = Config.defaults()

        self.assertEqual(8766, config.http.port)
        self.assertEqual("127.0.0.1", config.http.host)
        self.assertTrue(config.enforcement.enabled)
        self.assertEqual("audit", config.enforcement.mode)
        self.assertGreaterEqual(config.enforcement.max_per_scan, 1)
        self.assertIn("wineserver", config.rules.protect_names)

    def test_only_a_loopback_host_is_accepted(self):
        with self.assertRaises(FocusedConfigError):
            Config.from_dict({"http": {"host": "0.0.0.0"}})

    def test_a_bad_port_is_refused(self):
        with self.assertRaises(FocusedConfigError):
            Config.from_dict({"http": {"port": 0}})

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(FocusedConfigError):
            Config.from_dict({"enforcement": {"mode": "terminate"}})

    def test_a_zero_scan_cap_is_refused(self):
        with self.assertRaises(FocusedConfigError):
            Config.from_dict({"enforcement": {"max_per_scan": 0}})

    def test_wrong_types_are_refused_rather_than_coerced(self):
        with self.assertRaises(FocusedConfigError):
            Config.from_dict({"enforcement": {"enabled": "yes"}})
        with self.assertRaises(FocusedConfigError):
            Config.from_dict({"blacklist": {"names": "firefox"}})

    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            config = Config.from_dict(
                {"blacklist": {"names": ["firefox"]}, "urls": {"patterns": ["reddit.com"]}}
            )
            config.save(path)

            loaded = Config.load(path)

            self.assertEqual({"firefox"}, set(loaded.rules.names))
            self.assertEqual(["reddit.com"], loaded.urls.patterns)


class PidRuleTests(EngineTestCase):
    def test_apply_rules_fills_the_starttime_guard(self):
        engine = self.engine()

        engine.apply_rules({"pids": [self.burner.pid]}, source="plugin")

        entry = engine.sources()["plugin"]
        expected = engine.procfs.starttime(self.burner.pid)
        self.assertEqual([self.burner.pid], entry["pids"])
        self.assertEqual(expected, entry["pid_guards"][self.burner.pid])

    def test_a_gone_pid_is_rejected(self):
        engine = self.engine()

        with self.assertRaises(ValueError):
            engine.apply_rules({"pids": [999_999_999]}, source="plugin")

    def test_a_mismatched_guard_is_rejected(self):
        engine = self.engine()

        with self.assertRaises(ValueError):
            engine.apply_rules(
                {"pids": [self.burner.pid], "pid_guards": {self.burner.pid: 1}},
                source="plugin",
            )

    def test_pid_zero_or_one_is_rejected(self):
        engine = self.engine()

        with self.assertRaises(ValueError):
            engine.apply_rules({"pids": [1]}, source="plugin")


class ScanTimingTests(EngineTestCase):
    def test_status_reports_scan_latency(self):
        engine = self.engine()

        engine.scan_once()
        summary = engine.status()["scan_ms"]

        self.assertGreaterEqual(summary["last"], 0.0)
        self.assertEqual(1, summary["samples"])


class _CollectingAudit:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)
        return True

    def close(self):
        pass


class _CountingLog:
    def __init__(self):
        self.lines = []

    def info(self, message):
        self.lines.append(message)

    def warning(self, message):
        pass

    def error(self, message):
        pass

    def debug(self, message):
        pass


class ConfigPersistenceTests(unittest.TestCase):
    def make_engine(self, tmp, freezer=None):
        path = os.path.join(tmp, "config.json")
        config = Config.from_dict(
            {
                "audit_log": "",
                "enforcement": {"enabled": True, "mode": "audit"},
            },
            path=path,
        )
        engine = FocusedEngine(
            config,
            logger=None,
            uid=os.getuid(),
            self_pid=999_001,
        )
        return engine, path

    def test_a_blacklist_patch_is_written_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, path = self.make_engine(tmp)

            engine.update_config({"blacklist": {"names": ["firefox"]}})

            loaded = Config.load(path)
            self.assertEqual({"firefox"}, set(loaded.rules.names))

    def test_a_partial_patch_keeps_the_previous_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, path = self.make_engine(tmp)

            engine.update_config({"blacklist": {"names": ["firefox"]}})
            engine.update_config({"blacklist": {"cmdline_substrings": ["--profile"]}})

            loaded = Config.load(path)
            self.assertEqual({"firefox"}, set(loaded.rules.names))
            self.assertEqual(("--profile",), loaded.rules.cmdline_substrings)

    def test_a_catch_all_blacklist_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, path = self.make_engine(tmp)

            with self.assertRaises(ValueError):
                engine.update_config({"blacklist": {"names": ["*"]}})

            self.assertEqual([], sorted(engine.rules().names))
            loaded = Config.load(path)
            self.assertEqual([], sorted(loaded.rules.names))


class HeartbeatIdempotencyTests(unittest.TestCase):
    def make_engine(self, tmp):
        path = os.path.join(tmp, "config.json")
        config = Config.from_dict(
            {
                "audit_log": "",
                "enforcement": {"enabled": True, "mode": "audit"},
            },
            path=path,
        )
        return FocusedEngine(
            config,
            logger=None,
            uid=os.getuid(),
            self_pid=999_001,
        )

    def test_renewing_a_claim_is_not_an_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(tmp)
            engine.audit = _CollectingAudit()
            engine.logger = _CountingLog()

            for _ in range(5):
                engine.set_state(True, source="game-timer", ttl_seconds=30)

            self.assertEqual(1, len(engine.audit.records))
            self.assertEqual(1, len(engine.logger.lines))

            engine.set_state(False, source="game-timer")
            engine.set_state(False, source="game-timer")

            # The first withdrawal is a transition; the second is a no-op.
            self.assertEqual(2, len(engine.audit.records))
            self.assertEqual(2, len(engine.logger.lines))

    def test_a_renewal_does_not_rewrite_the_plugin_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(tmp)
            record = engine.plugins.register(name="probe")
            saves = []
            engine.plugins.save = lambda: saves.append(1)

            for _ in range(5):
                engine.set_state(True, source=record.id, ttl_seconds=30)

            # First claim persists; renewals only refresh last_seen in memory.
            self.assertEqual(1, len(saves))


if __name__ == "__main__":
    unittest.main()
