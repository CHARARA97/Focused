"""Focused's own tests: configuration, the session engine, and the protocol.

The engine is exercised with a real process (a copy of ``yes`` under a marker
name, in its own session), the same trick the ChillFocus daemon tests use: a
synthetic process may prove the decision, but only a real one proves the freeze.
"""

import json
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


def ticks(pid):
    try:
        with open("/proc/%d/stat" % pid, "r", encoding="utf-8") as handle:
            rest = handle.read().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return None
    return int(rest[11]) + int(rest[12])


def state(pid):
    try:
        with open("/proc/%d/stat" % pid, "r", encoding="utf-8") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return None


def starttime(pid):
    try:
        with open("/proc/%d/stat" % pid, "r", encoding="utf-8") as handle:
            return int(handle.read().rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


def wait_for_state(pid, wanted, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if state(pid) == wanted:
            return True
        time.sleep(0.02)
    return False


def wait_until_running(pid, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if state(pid) not in (None, "T"):
            return True
        time.sleep(0.02)
    return False


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
        self.state_path = os.path.join(self._tmp.name, "frozen.json")
        self.burner = Burner("cf-focused-live")
        self.addCleanup(self.burner.stop)
        time.sleep(0.3)

    def engine(self, blacklist=None, dry_run=False, **kwargs):
        config = Config.from_dict(
            {
                "audit_log": "",
                "dry_run": dry_run,
                "blacklist": {"names": blacklist if blacklist is not None else [self.burner.marker]},
                "freeze": {"enabled": True, "state_path": self.state_path},
            }
        )
        return FocusedEngine(
            config,
            logger=None,
            uid=os.getuid(),
            self_pid=999_001,
            # A cgroup of our own, so the "do not freeze what contains us" rule
            # does not mask the process-tree path under test.
            self_cgroup="/user.slice/user-1000.slice/user@1000.service/app.slice/focusedd.service",
            unified_cgroup2=True,
            **kwargs,
        )


class SessionTests(EngineTestCase):
    def test_a_session_suspends_a_real_target_and_resuming_ends_it(self):
        engine = self.engine()

        engine.start_session(1)
        frozen = engine.scan_once()

        self.assertEqual(1, frozen)
        self.assertTrue(wait_for_state(self.burner.pid, "T"))
        before = ticks(self.burner.pid)
        time.sleep(0.5)
        self.assertEqual(before, ticks(self.burner.pid), "a suspended process must not use CPU")
        self.assertEqual(1, len(engine.frozen()))
        self.assertTrue(os.path.exists(self.state_path))

        engine.stop_session()
        engine.scan_once()

        self.assertTrue(wait_until_running(self.burner.pid))
        self.assertEqual(0, len(engine.frozen()))
        with open(self.state_path, "r", encoding="utf-8") as handle:
            self.assertEqual([], json.load(handle)["frozen"])

    def test_nothing_happens_while_no_session_runs(self):
        engine = self.engine()

        self.assertEqual(0, engine.scan_once())
        self.assertNotEqual("T", state(self.burner.pid))
        self.assertEqual(0, len(engine.frozen()))

    def test_a_delegated_state_is_enough(self):
        engine = self.engine()

        engine.set_state(True, source="chillfocus")
        engine.scan_once()

        self.assertTrue(wait_for_state(self.burner.pid, "T"))
        self.assertTrue(engine.focus_state()["delegated"])
        self.assertFalse(engine.focus_state()["manual_session"])

    def test_a_temporary_pass_resumes_and_holds_off(self):
        engine = self.engine()
        engine.start_session(5)
        engine.scan_once()
        self.assertTrue(wait_for_state(self.burner.pid, "T"))

        engine.pause(300)
        engine.scan_once()  # resumes because a pass was handed out

        self.assertTrue(wait_until_running(self.burner.pid))
        self.assertEqual(0, len(engine.frozen()))

        # While the pass lasts, a scan must not freeze it again.
        self.assertEqual(0, engine.scan_once())
        self.assertNotEqual("T", state(self.burner.pid))

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

    def test_a_lapsed_lease_resumes_what_it_had_frozen(self):
        engine = self.engine()
        engine.set_state(True, source="chillfocused", ttl_seconds=30)
        engine.scan_once()
        self.assertTrue(wait_for_state(self.burner.pid, "T"))

        # The frontend died: the lease lapses and the next scan resumes everything.
        engine._clock = lambda: time.time() + 60
        engine.scan_once()

        self.assertTrue(wait_until_running(self.burner.pid))
        self.assertEqual(0, len(engine.frozen()))

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

    def test_a_pomodoro_resumes_everything_during_its_breaks(self):
        engine = self.engine(blacklist=[])
        engine.apply_rules({"names": [self.burner.marker]}, source="plugin-x")
        engine.start_session(mode="pomodoro", work_minutes=25, break_minutes=5, cycles=2)
        engine.scan_once()
        self.assertTrue(wait_for_state(self.burner.pid, "T"))

        # Break time: the applications have to come back. That is the point of one.
        engine._clock = lambda: time.time() + 25 * 60 + 1
        engine.scan_once()
        state = engine.session_state()
        self.assertEqual("break", state["phase"])
        self.assertFalse(engine.active(), "a break must not be active")
        self.assertTrue(wait_until_running(self.burner.pid))

        # Next round: back to work, and the target is suspended again.
        engine._clock = lambda: time.time() + 30 * 60 + 1
        engine.scan_once()
        state = engine.session_state()
        self.assertEqual("work", state["phase"])
        self.assertEqual(2, state["cycle"])
        self.assertTrue(engine.active())

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

    def test_dry_run_touches_nothing(self):
        engine = self.engine(dry_run=True)
        engine.start_session(5)

        self.assertEqual(0, engine.scan_once())
        self.assertNotEqual("T", state(self.burner.pid))
        self.assertEqual(0, len(engine.frozen()))
        self.assertFalse(os.path.exists(self.state_path))


class RuleTests(EngineTestCase):
    def test_api_rules_replace_the_blacklist(self):
        engine = self.engine(blacklist=[])

        engine.apply_rules({"names": [self.burner.marker], "cmdline_substrings": [], "pids": []})
        engine.start_session(5)
        engine.scan_once()

        self.assertTrue(wait_for_state(self.burner.pid, "T"))

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

    def test_a_target_that_stops_matching_is_resumed(self):
        # The rules come from a plugin-like source, so withdrawing *that* source is
        # what stops the match -- the configuration file is a different source and
        # is not touched.
        engine = self.engine(blacklist=[])
        engine.apply_rules({"names": [self.burner.marker]}, source="plugin-x")
        engine.start_session(5)
        engine.scan_once()
        self.assertTrue(wait_for_state(self.burner.pid, "T"))

        engine.apply_rules({"names": []}, source="plugin-x")
        engine.scan_once()

        self.assertTrue(wait_until_running(self.burner.pid))

    def test_the_engine_never_suspends_itself(self):
        engine = self.engine()

        self.assertEqual([], engine._freezable_pids((999_001, 999_002)))
        self.assertEqual([], engine._freezable_pids((1,)))
        self.assertEqual([4242], engine._freezable_pids((4242, 999_001)))


class RecoveryTests(EngineTestCase):
    def test_startup_recovery_resumes_what_a_crash_left(self):
        os.kill(self.burner.pid, signal.SIGSTOP)
        self.assertTrue(wait_for_state(self.burner.pid, "T"))
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "version": 1,
                    "frozen": [
                        {
                            "pid": self.burner.pid,
                            "name": self.burner.marker,
                            "mode": "pid",
                            "since": time.time(),
                            "starttime": starttime(self.burner.pid),
                            "pids": [self.burner.pid],
                            "guards": {str(self.burner.pid): starttime(self.burner.pid)},
                        }
                    ],
                },
                handle,
            )

        engine = self.engine()

        self.assertEqual(1, engine.recover_frozen())
        self.assertTrue(wait_until_running(self.burner.pid))

    def test_a_reused_pid_is_never_resumed(self):
        os.kill(self.burner.pid, signal.SIGSTOP)
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "version": 1,
                    "frozen": [
                        {
                            "pid": self.burner.pid,
                            "name": "stale",
                            "mode": "pid",
                            "since": time.time(),
                            "starttime": (starttime(self.burner.pid) or 0) - 1,
                            "pids": [self.burner.pid],
                            "guards": {str(self.burner.pid): (starttime(self.burner.pid) or 0) - 1},
                        }
                    ],
                },
                handle,
            )

        engine = self.engine()

        self.assertEqual(1, engine.recover_frozen())  # the record is settled...
        self.assertEqual("T", state(self.burner.pid))  # ...but nobody was signalled


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
        self.assertTrue(config.freeze.enabled)
        self.assertTrue(config.freeze.state_path.endswith("frozen.json"))
        self.assertIn("wineserver", config.rules.protect_names)

    def test_only_a_loopback_host_is_accepted(self):
        with self.assertRaises(FocusedConfigError):
            Config.from_dict({"http": {"host": "0.0.0.0"}})

    def test_a_bad_port_is_refused(self):
        with self.assertRaises(FocusedConfigError):
            Config.from_dict({"http": {"port": 0}})

    def test_an_empty_state_path_is_refused(self):
        with self.assertRaises(FocusedConfigError):
            Config.from_dict({"freeze": {"state_path": ""}})

    def test_wrong_types_are_refused_rather_than_coerced(self):
        with self.assertRaises(FocusedConfigError):
            Config.from_dict({"freeze": {"enabled": "yes"}})
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


if __name__ == "__main__":
    unittest.main()
