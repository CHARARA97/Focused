import unittest

from focused.core.killer import Killer
from focused.domain.reasons import KillResult
from focused.domain.targets import ProcessFacts, Selection

UID = 1000


class FakeReader:
    def __init__(self, starttimes=None):
        self.starttimes = dict(starttimes or {})
        self.reads = []

    def starttime(self, pid):
        self.reads.append(int(pid))
        return self.starttimes.get(int(pid))


class FakeSender:
    def __init__(self, failures=None):
        self.sent = []
        self.failures = dict(failures or {})

    def send(self, pid, signum):
        error = self.failures.get(int(pid))
        if error is not None:
            raise error
        self.sent.append(int(pid))


def selection(pids=(1000,), starttimes=None):
    root = pids[0]
    return Selection(
        facts=ProcessFacts(pid=root, uid=UID, name="firefox", is_zombie=False, is_kernel_thread=False),
        pids=tuple(pids),
        starttimes=dict(starttimes or {pid: 1 for pid in pids}),
        rule="blacklist.name:firefox",
    )


def killer(reader, sender):
    return Killer(sender=sender, reader=reader)


class SingleKillTests(unittest.TestCase):
    def test_a_matching_process_is_sent_sigkill(self):
        sender = FakeSender()
        result = killer(FakeReader({1000: 7}), sender).kill_if_same(1000, 7)
        self.assertEqual(result, KillResult.SENT)
        self.assertEqual(sender.sent, [1000])

    def test_a_gone_process_is_not_signalled(self):
        sender = FakeSender()
        result = killer(FakeReader({}), sender).kill_if_same(1000, 7)
        self.assertEqual(result, KillResult.GONE)
        self.assertEqual(sender.sent, [])

    def test_a_reused_pid_is_not_signalled(self):
        sender = FakeSender()
        result = killer(FakeReader({1000: 99}), sender).kill_if_same(1000, 7)
        self.assertEqual(result, KillResult.REUSED)
        self.assertEqual(sender.sent, [])

    def test_a_vanished_process_between_check_and_send_is_gone(self):
        sender = FakeSender({1000: ProcessLookupError()})
        result = killer(FakeReader({1000: 7}), sender).kill_if_same(1000, 7)
        self.assertEqual(result, KillResult.GONE)

    def test_a_refused_permission_is_denied(self):
        sender = FakeSender({1000: PermissionError()})
        result = killer(FakeReader({1000: 7}), sender).kill_if_same(1000, 7)
        self.assertEqual(result, KillResult.DENIED)

    def test_the_starttime_is_checked_before_every_signal(self):
        reader = FakeReader({1000: 7})
        killer(reader, FakeSender()).kill_if_same(1000, 7)
        self.assertEqual(reader.reads, [1000])


class SubtreeKillTests(unittest.TestCase):
    def test_the_root_is_signalled_first(self):
        sender = FakeSender()
        target = selection(pids=(1000, 1001, 1002))
        killer(FakeReader({1000: 1, 1001: 1, 1002: 1}), sender).kill_subtree(target)
        self.assertEqual(sender.sent, [1000, 1001, 1002])

    def test_every_signal_is_reported(self):
        target = selection(pids=(1000, 1001))
        results = killer(FakeReader({1000: 1, 1001: 1}), FakeSender()).kill_subtree(target)
        self.assertEqual(list(results), [1000, 1001])
        self.assertEqual(set(results.values()), {KillResult.SENT})

    def test_a_reused_root_aborts_the_whole_tree(self):
        sender = FakeSender()
        target = selection(pids=(1000, 1001, 1002))
        results = killer(FakeReader({1000: 99, 1001: 1, 1002: 1}), sender).kill_subtree(target)
        self.assertEqual(results, {1000: KillResult.REUSED})
        self.assertEqual(sender.sent, [])

    def test_a_denied_root_aborts_the_whole_tree(self):
        sender = FakeSender({1000: PermissionError()})
        target = selection(pids=(1000, 1001))
        results = killer(FakeReader({1000: 1, 1001: 1}), sender).kill_subtree(target)
        self.assertEqual(results, {1000: KillResult.DENIED})
        self.assertEqual(sender.sent, [])

    def test_a_gone_root_does_not_abort_the_children(self):
        sender = FakeSender()
        target = selection(pids=(1000, 1001))
        results = killer(FakeReader({1001: 1}), sender).kill_subtree(target)
        self.assertEqual(results[1000], KillResult.GONE)
        self.assertEqual(sender.sent, [1001])

    def test_a_reused_child_does_not_abort_the_tree(self):
        sender = FakeSender()
        target = selection(pids=(1000, 1001, 1002))
        results = killer(FakeReader({1000: 1, 1001: 99, 1002: 1}), sender).kill_subtree(target)
        self.assertEqual(results[1001], KillResult.REUSED)
        self.assertEqual(sender.sent, [1000, 1002])

    def test_a_pid_without_a_starttime_is_never_signalled(self):
        sender = FakeSender()
        target = selection(pids=(1000, 1001), starttimes={1000: 1})
        results = killer(FakeReader({1000: 1, 1001: 1}), sender).kill_subtree(target)
        self.assertEqual(results[1001], KillResult.UNVERIFIED)
        self.assertNotIn(1001, sender.sent)

    def test_an_unverified_root_aborts_the_whole_tree(self):
        sender = FakeSender()
        target = selection(pids=(1000, 1001), starttimes={1001: 1})
        results = killer(FakeReader({1000: 1, 1001: 1}), sender).kill_subtree(target)
        self.assertEqual(results, {1000: KillResult.UNVERIFIED})
        self.assertEqual(sender.sent, [])


if __name__ == "__main__":
    unittest.main()


class RealSignalTests(unittest.TestCase):
    def test_a_real_process_is_killed_when_the_starttime_matches(self):
        import os
        import subprocess
        import time

        from focused.core.killer import OsSignalSender
        from focused.core.procfs import ProcFS

        procfs = ProcFS()
        child = subprocess.Popen(["sleep", "30"])
        try:
            expected = procfs.starttime(child.pid)
            self.assertIsNotNone(expected)
            result = Killer(sender=OsSignalSender(), reader=procfs).kill_if_same(
                child.pid, expected
            )
            self.assertEqual(KillResult.SENT, result)
            child.wait(timeout=5)
            self.assertIsNone(procfs.starttime(child.pid))
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

    def test_a_real_process_is_left_alone_when_the_starttime_differs(self):
        import subprocess

        from focused.core.killer import OsSignalSender
        from focused.core.procfs import ProcFS

        procfs = ProcFS()
        child = subprocess.Popen(["sleep", "30"])
        try:
            expected = procfs.starttime(child.pid)
            result = Killer(sender=OsSignalSender(), reader=procfs).kill_if_same(
                child.pid, expected + 1
            )
            self.assertEqual(KillResult.REUSED, result)
            self.assertIsNone(child.poll())
        finally:
            child.kill()
            child.wait(timeout=5)
