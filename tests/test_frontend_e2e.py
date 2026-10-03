"""End to end: a frontend talks to a real Focused, and a real process is suspended.

This is the integration the whole design exists for.  Nothing is faked except the
game itself: the test plays the part of ChillFocused by speaking the exact request
sequence the plugin sends, and watches a real CPU burner stop and start:

    frontend  --rules-->  Focused  -->  SIGSTOP
    frontend  --heartbeat(lease)-->  Focused   ...and when the heartbeats stop,
                                                 Focused resumes everything by itself.

The lease case is the one worth having: it is what protects the user from a plugin
that crashed, was killed, or was itself frozen mid-session.
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FOCUSED_DIR = os.path.dirname(HERE)
FOCUSEDD = os.path.join(FOCUSED_DIR, "focusedd.py")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def state(pid):
    """One-letter process state from /proc, or None once it is gone."""
    try:
        with open("/proc/%d/stat" % pid, "r", encoding="utf-8") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return None


def log_text(path) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return "(no log)"


def wait_for(predicate, timeout=15.0, interval=0.2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class Child:
    """A background process we can talk to and clean up."""

    def __init__(self, argv, log_path, env=None):
        self.log = open(log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            argv,
            stdout=self.log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        self.log.close()

    def kill(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)
            self.proc.wait(timeout=10)


class FrontendEndToEndTests(unittest.TestCase):
    """The game plugin's request sequence, against a real Focused."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.env = dict(os.environ)
        self.port = free_port()
        self.marker = "cf-e2e-live"
        self.burner_path = os.path.join(self.dir, self.marker)
        shutil.copy(shutil.which("yes") or "/usr/bin/yes", self.burner_path)

        self.state_path = os.path.join(self.dir, "frozen.json")
        config_path = os.path.join(self.dir, "focused.json")
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "http": {"enabled": True, "host": "127.0.0.1", "port": self.port},
                    "scan_interval": 0.3,
                    "audit_log": os.path.join(self.dir, "audit.jsonl"),
                    "plugins_path": os.path.join(self.dir, "plugins"),
                    "registry_path": os.path.join(self.dir, "plugins.json"),
                    "blacklist": {"names": []},
                    "freeze": {"enabled": True, "state_path": self.state_path},
                },
                handle,
            )
        self.config_path = config_path

        self.focused = Child(
            [sys.executable, FOCUSEDD, "--config", config_path],
            os.path.join(self.dir, "focused.log"),
            env=self.env,
        )
        self.addCleanup(self.focused.stop)
        self.assertTrue(
            wait_for(self._reachable),
            "focusedd did not come up: " + log_text(os.path.join(self.dir, "focused.log")),
        )

        self.burner = subprocess.Popen(
            [self.burner_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.addCleanup(self._cleanup_burner)
        time.sleep(0.4)

    # -- helpers -----------------------------------------------------------

    def _cleanup_burner(self):
        try:
            os.kill(self.burner.pid, signal.SIGCONT)
        except OSError:
            pass
        if self.burner.poll() is None:
            self.burner.kill()
            self.burner.wait(timeout=5)

    def _call(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            "http://127.0.0.1:%d/api/v1%s" % (self.port, path),
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read().decode("utf-8"))
            exc.close()
            return exc.code, payload

    def _reachable(self) -> bool:
        try:
            self._call("GET", "/focus")
            return True
        except Exception:
            return False

    def push_rules(self, names, source="builtin"):
        """Exactly what FocusSettings.ToRulePayload() sends."""
        body = {
            "names": names,
            "cmdline_substrings": [],
            "pids": [],
            "protect_names": ["niri"],
            "protect_cmdline_substrings": [],
        }
        path = "/focus/rules" if source == "builtin" else "/plugins/%s/rules" % source
        return self._call("PUT", path, body)

    def heartbeat(self, active=True, lease=30):
        """Exactly what FocusedClient.ReportSessionAsync() sends."""
        return self._call(
            "POST", "/focus/state",
            {"active": active, "dry_run": False, "source": "game-timer", "ttl_seconds": lease},
        )

    # -- the tests ---------------------------------------------------------

    def test_the_plugin_rules_reach_the_process(self):
        status, payload = self.push_rules([self.marker])

        self.assertEqual(200, status)
        self.assertIn(self.marker, payload["applied"]["names"])

        # It lands in the merged rule set, and the source it came from is recorded.
        merged = self._call("GET", "/status")[1]
        self.assertIn(self.marker, merged["blacklist"]["names"])
        self.assertIn("builtin", merged["sources"])

    def test_a_session_suspends_a_real_process(self):
        self.push_rules([self.marker])
        self.heartbeat(active=True, lease=30)

        self.assertTrue(
            wait_for(lambda: state(self.burner.pid) == "T"),
            "Focused never suspended the target",
        )
        frozen = self._call("GET", "/focus/frozen")[1]
        self.assertEqual(1, frozen["count"])
        self.assertEqual(self.marker, frozen["frozen"][0]["name"])
        self.assertTrue(os.path.exists(self.state_path))

        # And the process really is not being scheduled: its CPU time stops.
        with open("/proc/%d/stat" % self.burner.pid, "r", encoding="utf-8") as handle:
            before = int(handle.read().rsplit(")", 1)[1].split()[11])
        time.sleep(0.6)
        with open("/proc/%d/stat" % self.burner.pid, "r", encoding="utf-8") as handle:
            after = int(handle.read().rsplit(")", 1)[1].split()[11])
        self.assertEqual(before, after, "a suspended process must not accumulate CPU time")

    def test_ending_the_session_resumes_everything(self):
        self.push_rules([self.marker])
        self.heartbeat(active=True, lease=30)
        self.assertTrue(wait_for(lambda: state(self.burner.pid) == "T"))

        self.heartbeat(active=False)

        self.assertTrue(
            wait_for(lambda: state(self.burner.pid) in ("S", "R")),
            "the target stayed suspended after the session ended",
        )
        self.assertEqual(0, self._call("GET", "/focus/frozen")[1]["count"])

    def test_a_plugin_that_stops_heartbeating_is_cleaned_up_after(self):
        """The crash case: nobody says the session ended, so the lease has to."""
        self.push_rules([self.marker])
        self.heartbeat(active=True, lease=3)
        self.assertTrue(wait_for(lambda: state(self.burner.pid) == "T"))

        # No more heartbeats: this is what a plugin that died looks like.
        self.assertTrue(
            wait_for(lambda: state(self.burner.pid) in ("S", "R"), timeout=30.0),
            "the lease lapsed but the target stayed suspended",
        )
        self.assertFalse(self._call("GET", "/focus")[1]["active"])

    def test_a_killed_focused_can_still_be_recovered(self):
        """The systemd ExecStopPost path: Focused is gone, the record is not."""
        self.push_rules([self.marker])
        self.heartbeat(active=True, lease=120)
        self.assertTrue(wait_for(lambda: state(self.burner.pid) == "T"))

        self.focused.kill()
        self.assertEqual("T", state(self.burner.pid), "nothing should resume by itself yet")

        result = subprocess.run(
            [sys.executable, FOCUSEDD, "--config", self.config_path, "--thaw-all"],
            capture_output=True, text=True, timeout=30, env=self.env,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(1, json.loads(result.stdout)["thawed"])
        self.assertTrue(wait_for(lambda: state(self.burner.pid) in ("S", "R")))

    def test_frozen_lists_what_is_suspended_without_the_app_running(self):
        self.push_rules([self.marker])
        self.heartbeat(active=True, lease=120)
        self.assertTrue(wait_for(lambda: state(self.burner.pid) == "T"))

        result = subprocess.run(
            [sys.executable, FOCUSEDD, "--config", self.config_path, "--frozen"],
            capture_output=True, text=True, timeout=30, env=self.env,
        )

        payload = json.loads(result.stdout)
        self.assertEqual(1, payload["count"])
        self.assertEqual(self.burner.pid, payload["frozen"][0]["pid"])

    def test_two_frontends_do_not_disturb_each_other(self):
        """What a second little plugin must be able to rely on."""
        status, registration = self._call("POST", "/plugins/register", {"name": "pomodoro"})
        self.assertEqual(200, status)
        token = registration["token"]

        self.push_rules([self.marker])
        request = urllib.request.Request(
            "http://127.0.0.1:%d/api/v1/plugins/pomodoro/rules" % self.port,
            data=json.dumps({"names": ["other-app"]}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Focused-Token": token},
            method="PUT",
        )
        urllib.request.urlopen(request, timeout=5).close()

        merged = self._call("GET", "/status")[1]
        self.assertEqual({self.marker, "other-app"}, set(merged["blacklist"]["names"]))
        self.assertEqual({"builtin", "pomodoro", "config"}, set(merged["sources"]))

        # Withdrawing one leaves the other alone.
        self.push_rules([])
        merged = self._call("GET", "/status")[1]
        self.assertEqual({"other-app"}, set(merged["blacklist"]["names"]))


if __name__ == "__main__":
    unittest.main()
