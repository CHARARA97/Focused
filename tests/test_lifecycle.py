import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

from focused.config import Config
from focused.core.audit import AuditLog
from focused.core.procfs import ProcFS
from focused.engine import FocusedEngine

SELF_PID = 999_001


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.procfs = ProcFS()
        self.audit_path = os.path.join(self.dir, "audit.jsonl")

    def probe(self, name="zzprobe"):
        path = os.path.join(self.dir, name)
        shutil.copy("/bin/sleep", path)
        proc = subprocess.Popen([path, "30"])
        self.addCleanup(self.stop, proc)
        return proc

    def stop(self, proc):
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)

    def engine(self, mode="kill", names=("zzprobe",), enabled=True):
        config = Config.from_dict(
            {
                "audit_log": self.audit_path,
                "registry_path": os.path.join(self.dir, "plugins.json"),
                "enforcement": {"enabled": enabled, "mode": mode},
                "blacklist": {"names": list(names)},
            }
        )
        engine = FocusedEngine(
            config, logger=None, uid=os.getuid(), self_pid=SELF_PID
        )
        engine.audit = AuditLog(self.audit_path, logger=None)
        return engine

    def records(self, kind=None):
        if not os.path.exists(self.audit_path):
            return []
        with open(self.audit_path, "r", encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
        return [line for line in lines if kind is None or line["kind"] == kind]

    def test_kill_mode_kills_the_matched_process(self):
        child = self.probe()
        engine = self.engine(mode="kill")
        engine.start_session(5)

        result = engine.scan_once()
        child.wait(timeout=5)

        self.assertEqual(1, result.killed)
        self.assertIsNotNone(child.poll())

    def test_audit_mode_records_without_killing(self):
        child = self.probe()
        engine = self.engine(mode="audit")
        engine.start_session(5)

        result = engine.scan_once()

        self.assertEqual(0, result.killed)
        self.assertIsNone(child.poll())
        kills = self.records("enforce.would-kill")
        self.assertEqual(1, len(kills))
        self.assertEqual("zzprobe", kills[0]["name"])
        self.assertIn("starttime", kills[0])

    def test_no_session_means_no_signal(self):
        child = self.probe()
        engine = self.engine(mode="kill")

        result = engine.scan_once()

        self.assertEqual("no-session", result.gate)
        self.assertIsNone(child.poll())
        self.assertEqual([], self.records("enforce.kill"))

    def test_a_paused_session_means_no_signal(self):
        child = self.probe()
        engine = self.engine(mode="kill")
        engine.start_session(5)
        engine.pause(300)

        result = engine.scan_once()

        self.assertEqual("paused", result.gate)
        self.assertIsNone(child.poll())

    def test_disabling_enforcement_means_no_signal(self):
        child = self.probe()
        engine = self.engine(mode="kill", enabled=False)
        engine.start_session(5)

        result = engine.scan_once()

        self.assertEqual("disabled", result.gate)
        self.assertIsNone(child.poll())

    def test_the_parent_of_a_target_is_not_killed(self):
        probe = os.path.join(self.dir, "zzprobe")
        shutil.copy("/bin/sleep", probe)
        parent = subprocess.Popen(["/bin/sh", "-c", "%s 30 & wait" % probe])
        self.addCleanup(self.stop, parent)

        child_pids = self._wait_for_a_child(parent.pid)
        self.assertTrue(child_pids, "the shell never started the probe")

        engine = self.engine(mode="kill")
        engine.start_session(5)
        result = engine.scan_once()

        self.assertEqual(1, result.killed)
        self.assertIsNone(parent.poll(), "the parent must survive its child")
        self.assertTrue(
            all(self._wait_dead(pid) for pid in child_pids),
            "every child of the target must be gone",
        )

    def _wait_for_a_child(self, parent_pid):
        deadline = self._now() + 3.0
        while self._now() < deadline:
            if self.procfs.supports_live_children():
                children = [
                    pid
                    for pid in self.procfs.child_pids(parent_pid)
                    if self._comm(pid) == "zzprobe"
                ]
            else:
                children = [
                    proc.pid
                    for proc in self.procfs.snapshot()
                    if proc.ppid == parent_pid
                ]
            if children:
                return children
            self._sleep(0.02)
        return []

    def _comm(self, pid):
        try:
            with open("/proc/%d/comm" % pid, "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return ""

    def _wait_dead(self, pid, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._alive(pid):
                return True
            time.sleep(0.02)
        return not self._alive(pid)

    def _alive(self, pid):
        info = self.procfs.read(pid)
        return info is not None and info.state not in ("Z", "X", "x")

    @staticmethod
    def _now():
        return time.time()

    @staticmethod
    def _sleep(seconds):
        time.sleep(seconds)


if __name__ == "__main__":
    unittest.main()
