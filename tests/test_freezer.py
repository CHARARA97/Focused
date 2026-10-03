"""The freezer: recording, guarding, persisting and resuming suspensions.

Everything here uses fakes, so no real process is ever stopped.  The tests that
stop a real process live in ``test_freeze_live.py``.
"""

import json
import os
import signal
import tempfile
import unittest
from unittest import mock

from focused.core import cgroupfs
from focused.core.freezer import (
    MODE_PID,
    MODE_UNIT,
    Freezer,
    Frozen,
    Target,
    list_from_state,
    thaw_all_from_state,
)

APP = "/user.slice/user-1000.slice/user@1000.service/app.slice/app-firefox-7.scope"


class FakeProcFS:
    """Start times and cgroup freeze state, scripted by the test."""

    def __init__(self, starttimes=None, frozen=None):
        self.starttimes = dict(starttimes or {})
        self.frozen = frozen
        self.frozen_reads = []

    def starttime(self, pid):
        return self.starttimes.get(pid)

    def cgroup_frozen(self, cgroup_path):
        self.frozen_reads.append(cgroup_path)
        return self.frozen


class Sink:
    """Records signals; optionally fails for chosen PIDs."""

    def __init__(self, deny=()):
        self.calls = []
        self.deny = set(deny)

    def __call__(self, pid, sig):
        if pid in self.deny:
            raise PermissionError(1, "Operation not permitted")
        self.calls.append((pid, sig))

    def stop_calls(self):
        return [pid for pid, sig in self.calls if sig == signal.SIGSTOP]

    def cont_calls(self):
        return [pid for pid, sig in self.calls if sig == signal.SIGCONT]


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        self.now += 0.1
        return self.now


def target_for(pid=4242, pids=None, starttimes=None, mode=MODE_PID, unit="", cgroup=""):
    """Build a target whose guard matches the start times the procfs reports.

    Passing a different map is what the "pid was reused" tests do on purpose.
    """
    pids = tuple(pids or (pid,))
    if pid not in pids:
        pids = (pid,) + pids
    starttimes = dict(starttimes or {})
    guard = {p: starttimes.get(p, 1000) for p in pids}
    return Target(
        pid=pid,
        name="firefox",
        pids=pids,
        guard=guard,
        mode=mode,
        cgroup=cgroup,
        unit=unit,
    )


class FreezerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_path = os.path.join(self._tmp.name, "frozen.json")

    def freezer(self, procfs=None, sink=None, **kwargs):
        return Freezer(
            procfs=procfs or FakeProcFS({4242: 1000}),
            state_path=self.state_path,
            logger=None,
            signal_sender=sink or Sink(),
            clock=Clock(),
            sleep=lambda _seconds: None,
            **kwargs,
        )

    def read_state(self):
        with open(self.state_path, "r", encoding="utf-8") as handle:
            return json.load(handle)


class FreezePidTests(FreezerTestCase):
    def test_a_target_and_its_children_are_stopped_root_first(self):
        sink = Sink()
        starttimes = {4242: 1000, 4243: 1001, 4244: 1002}
        procfs = FakeProcFS(starttimes)
        freezer = self.freezer(procfs, sink)

        entry = freezer.freeze(target_for(pids=(4242, 4243, 4244), starttimes=starttimes))

        self.assertIsNotNone(entry)
        self.assertEqual([4242, 4243, 4244], sink.stop_calls())
        self.assertEqual(MODE_PID, entry.mode)
        self.assertEqual((4242, 4243, 4244), entry.pids)

    def test_the_record_is_on_disk_before_the_caller_knows(self):
        freezer = self.freezer(FakeProcFS({4242: 1000}))

        freezer.freeze(target_for())

        state = self.read_state()
        self.assertEqual(1, len(state["frozen"]))
        self.assertEqual(4242, state["frozen"][0]["pid"])
        self.assertEqual("firefox", state["frozen"][0]["name"])

    def test_a_process_that_already_vanished_is_not_frozen(self):
        # starttime() returning None means "gone between snapshot and signal".
        freezer = self.freezer(FakeProcFS({}))

        self.assertIsNone(freezer.freeze(target_for()))
        self.assertEqual(0, len(freezer))
        self.assertFalse(os.path.exists(self.state_path))

    def test_a_reused_pid_is_skipped(self):
        sink = Sink()
        # The snapshot said 1001 for 4243; /proc now says something else.
        procfs = FakeProcFS({4242: 1000, 4243: 9999})
        freezer = self.freezer(procfs, sink)

        entry = freezer.freeze(
            target_for(pids=(4242, 4243), starttimes={4242: 1000, 4243: 1001})
        )

        self.assertEqual([4242], sink.stop_calls())
        self.assertEqual((4242,), entry.pids)

    def test_freezing_twice_is_a_no_op(self):
        sink = Sink()
        freezer = self.freezer(FakeProcFS({4242: 1000}), sink)

        first = freezer.freeze(target_for())
        second = freezer.freeze(target_for())

        self.assertIs(first, second)
        self.assertEqual([4242], sink.stop_calls())
        self.assertEqual(1, len(freezer))

    def test_a_permission_error_is_reported_and_not_recorded(self):
        sink = Sink(deny={4242})
        freezer = self.freezer(FakeProcFS({4242: 1000}), sink)

        self.assertIsNone(freezer.freeze(target_for()))
        self.assertEqual(0, len(freezer))

    def test_a_child_that_vanishes_does_not_lose_the_root(self):
        sink = Sink()
        procfs = FakeProcFS({4242: 1000})  # 4243 already gone
        freezer = self.freezer(procfs, sink)

        entry = freezer.freeze(
            target_for(pids=(4242, 4243), starttimes={4242: 1000, 4243: 1001})
        )

        self.assertEqual([4242], sink.stop_calls())
        self.assertEqual((4242,), entry.pids)


class ThawTests(FreezerTestCase):
    def test_thawing_resumes_every_recorded_pid(self):
        sink = Sink()
        starttimes = {4242: 1000, 4243: 1001}
        freezer = self.freezer(FakeProcFS(starttimes), sink)
        freezer.freeze(target_for(pids=(4242, 4243), starttimes=starttimes))
        sink.calls.clear()

        self.assertTrue(freezer.thaw(freezer.entries()[0], "session-end"))

        self.assertEqual([4242, 4243], sink.cont_calls())
        self.assertEqual(0, len(freezer))
        self.assertEqual([], self.read_state()["frozen"])

    def test_a_gone_process_is_a_successful_thaw(self):
        procfs = FakeProcFS({4242: 1000})
        freezer = self.freezer(procfs)
        freezer.freeze(target_for())

        procfs.starttimes.clear()  # it exited while frozen
        self.assertTrue(freezer.thaw(freezer.entries()[0]))

        self.assertEqual(0, len(freezer))

    def test_a_reused_pid_is_never_resumed(self):
        # The dangerous case: the frozen process died and its PID now belongs to
        # somebody else.  Resuming would signal a stranger.
        sink = Sink()
        starttimes = {4242: 1000, 4243: 1001}
        procfs = FakeProcFS(starttimes)
        freezer = self.freezer(procfs, sink)
        freezer.freeze(target_for(pids=(4242, 4243), starttimes=starttimes))
        sink.calls.clear()
        procfs.starttimes[4243] = 7777  # reused

        freezer.thaw(freezer.entries()[0])

        self.assertEqual([4242], sink.cont_calls())

    def test_a_failing_thaw_keeps_the_record_for_the_next_attempt(self):
        sink = Sink()
        procfs = FakeProcFS({4242: 1000})
        freezer = self.freezer(procfs, sink)
        freezer.freeze(target_for())
        sink.deny = {4242}

        self.assertFalse(freezer.thaw(freezer.entries()[0]))

        self.assertEqual(1, len(freezer))
        self.assertEqual(1, len(self.read_state()["frozen"]))

    def test_thaw_all_reports_how_many_were_resumed(self):
        sink = Sink()
        starttimes = {4242: 1000, 4243: 1001}
        freezer = self.freezer(FakeProcFS(starttimes), sink)
        freezer.freeze(target_for(pid=4242, pids=(4242,), starttimes=starttimes))
        freezer.freeze(target_for(pid=4243, pids=(4243,), starttimes=starttimes))

        self.assertEqual(2, freezer.thaw_all("enforcement-off"))
        self.assertEqual(0, len(freezer))

    def test_thawing_an_empty_freezer_is_fine(self):
        freezer = self.freezer()

        self.assertEqual(0, freezer.thaw_all("session-start"))


class PersistenceTests(FreezerTestCase):
    def test_load_reads_what_a_previous_run_wrote(self):
        freezer = self.freezer(FakeProcFS({4242: 1000}))
        freezer.freeze(target_for())

        fresh = Freezer(procfs=FakeProcFS({4242: 1000}), state_path=self.state_path)
        entries = fresh.load()

        self.assertEqual(1, len(entries))
        self.assertEqual(4242, entries[0].pid)
        self.assertEqual("firefox", entries[0].name)

    def test_recover_resumes_leftovers(self):
        sink = Sink()
        freezer = self.freezer(FakeProcFS({4242: 1000}), sink)
        freezer.freeze(target_for())
        sink.calls.clear()

        fresh = Freezer(
            procfs=FakeProcFS({4242: 1000}),
            state_path=self.state_path,
            signal_sender=sink,
        )
        self.assertEqual(1, fresh.recover())
        self.assertEqual([4242], sink.cont_calls())
        self.assertEqual([], self.read_state()["frozen"])

    def test_a_corrupt_state_file_is_reported_not_crashed(self):
        with open(self.state_path, "w", encoding="utf-8") as handle:
            handle.write("{not json")

        freezer = self.freezer()

        self.assertEqual([], freezer.load())

    def test_unknown_fields_are_ignored_when_loading(self):
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump({"version": 99, "frozen": [{"pid": 4242, "name": "x", "extra": 1}]}, handle)

        entries = self.freezer().load()

        self.assertEqual([4242], [entry.pid for entry in entries])

    def test_the_standalone_helpers_need_no_daemon(self):
        freezer = self.freezer(FakeProcFS({4242: 1000}))
        freezer.freeze(target_for())

        self.assertEqual([4242], [entry.pid for entry in list_from_state(self.state_path)])

        sink = Sink()
        count = thaw_all_from_state(
            self.state_path, procfs=FakeProcFS({4242: 1000}), unit_runner=None, logger=None
        )
        self.assertEqual(1, count)
        self.assertEqual([], list_from_state(self.state_path))
        del sink


class UnitModeTests(FreezerTestCase):
    def setUp(self):
        super().setUp()
        self._cgroup_root = tempfile.TemporaryDirectory()
        self.addCleanup(self._cgroup_root.cleanup)
        patcher = mock.patch.object(cgroupfs, "CGROUP_ROOT", self._cgroup_root.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.makedirs(os.path.join(self._cgroup_root.name, APP.strip("/")), exist_ok=True)

    def test_a_dedicated_unit_is_frozen_through_systemd(self):
        calls = []

        def runner(args, timeout=5.0):
            calls.append(list(args))
            return 0, ""

        freezer = self.freezer(FakeProcFS({4242: 1000}, frozen=True), unit_runner=runner)

        entry = freezer.freeze(target_for(mode=MODE_UNIT, unit="app-firefox-7.scope", cgroup=APP))

        self.assertEqual([["freeze", "app-firefox-7.scope"]], calls)
        self.assertEqual(MODE_UNIT, entry.mode)
        self.assertEqual("app-firefox-7.scope", entry.unit)

    def test_a_systemd_failure_falls_back_to_the_cgroup_file(self):
        def runner(args, timeout=5.0):
            return 1, "Failed to freeze unit"

        freezer = self.freezer(FakeProcFS({4242: 1000}, frozen=True), unit_runner=runner)

        entry = freezer.freeze(target_for(mode=MODE_UNIT, unit="u.scope", cgroup=APP))

        self.assertIsNotNone(entry)
        freeze_file = cgroupfs.freeze_file(APP)
        with open(freeze_file, "r", encoding="utf-8") as handle:
            self.assertEqual("1", handle.read())

    def test_an_unconfirmed_freeze_is_rolled_back(self):
        # The kernel never reported frozen=1: do not claim a suspension that did
        # not happen, and make sure the unit is not left half-frozen.
        calls = []

        def runner(args, timeout=5.0):
            calls.append(list(args))
            return 0, ""

        freezer = self.freezer(FakeProcFS({4242: 1000}, frozen=False), unit_runner=runner)

        self.assertIsNone(freezer.freeze(target_for(mode=MODE_UNIT, unit="u.scope", cgroup=APP)))
        self.assertIn(["thaw", "u.scope"], calls)
        self.assertEqual(0, len(freezer))

    def test_thawing_a_unit_uses_systemd_and_the_cgroup_file(self):
        calls = []

        def runner(args, timeout=5.0):
            calls.append(list(args))
            return 0, ""

        freezer = self.freezer(FakeProcFS({4242: 1000}, frozen=False), unit_runner=runner)
        entry = Frozen(pid=4242, name="firefox", mode=MODE_UNIT, since=1.0,
                       unit="app-firefox-7.scope", cgroup=APP, pids=(4242,))
        freezer._entries[4242] = entry

        self.assertTrue(freezer.thaw(entry, "session-end"))

        self.assertIn(["thaw", "app-firefox-7.scope"], calls)
        with open(cgroupfs.freeze_file(APP), "r", encoding="utf-8") as handle:
            self.assertEqual("0", handle.read())

    def test_both_routes_failing_keeps_the_record(self):
        def runner(args, timeout=5.0):
            return 1, "no bus"

        freezer = self.freezer(FakeProcFS({4242: 1000}), unit_runner=runner)
        entry = Frozen(pid=99, name="firefox", mode=MODE_UNIT, since=1.0,
                       unit="app-elsewhere.scope", cgroup="/does/not/exist")
        freezer._entries[99] = entry

        self.assertFalse(freezer.thaw(entry))
        self.assertEqual(1, len(freezer))

    def test_a_unit_entry_with_nothing_to_act_on_is_dropped(self):
        freezer = self.freezer(FakeProcFS({4242: 1000}))
        entry = Frozen(pid=99, name="firefox", mode=MODE_UNIT, since=1.0)
        freezer._entries[99] = entry

        self.assertTrue(freezer.thaw(entry))
        self.assertEqual(0, len(freezer))


if __name__ == "__main__":
    unittest.main()
