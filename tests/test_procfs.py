"""Tests for /proc parsing, run against a synthetic procfs tree."""

from __future__ import annotations

import os
import tempfile
import unittest

from focused.core.procfs import COMM_MAX_LEN, ProcFS
from tests.helpers import build_fake_proc


class ProcFSTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.fs = ProcFS(root=self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_reads_every_field(self):
        build_fake_proc(
            self.root,
            1234,
            comm="firefox",
            cmdline=("firefox", "--new-window"),
            uid=1000,
            ppid=900,
            pgid=901,
            starttime=55555,
            state="S",
            exe="/usr/lib/firefox/firefox",
        )
        info = self.fs.read(1234)
        self.assertIsNotNone(info)
        self.assertEqual(info.pid, 1234)
        self.assertEqual(info.comm, "firefox")
        self.assertEqual(info.cmdline, ("firefox", "--new-window"))
        self.assertEqual(info.uid, 1000)
        self.assertEqual(info.ppid, 900)
        self.assertEqual(info.pgid, 901)
        self.assertEqual(info.starttime, 55555)
        self.assertEqual(info.state, "S")
        self.assertEqual(info.exe, "/usr/lib/firefox/firefox")
        self.assertEqual(info.exe_basename, "firefox")
        self.assertEqual(info.cmdline_text, "firefox --new-window")

    def test_comm_with_spaces_and_parentheses(self):
        """The stat line must be split on the *last* ')'."""
        build_fake_proc(
            self.root,
            42,
            comm="my (weird) name",
            cmdline=("weird",),
            starttime=31337,
            exe=None,
        )
        info = self.fs.read(42)
        self.assertEqual(info.comm, "my (weird) name")
        self.assertEqual(info.starttime, 31337)
        self.assertEqual(info.state, "S")

    def test_comm_file_missing_falls_back_to_stat(self):
        build_fake_proc(self.root, 7, comm="fallback", write_comm_file=False, exe=None)
        info = self.fs.read(7)
        self.assertEqual(info.comm, "fallback")

    def test_kernel_thread_has_empty_cmdline(self):
        build_fake_proc(self.root, 9, comm="kworker/0:1", cmdline=(), state="S")
        info = self.fs.read(9)
        self.assertEqual(info.cmdline, ())
        self.assertTrue(info.is_kernel_thread)
        self.assertFalse(info.is_zombie)

    def test_zombie_is_not_a_kernel_thread(self):
        build_fake_proc(self.root, 11, comm="firefox", cmdline=(), state="Z")
        info = self.fs.read(11)
        self.assertTrue(info.is_zombie)
        self.assertFalse(info.is_kernel_thread)

    def test_deleted_executable_suffix_is_stripped(self):
        directory = build_fake_proc(self.root, 13, comm="firefox", exe=None)
        os.symlink("/usr/bin/firefox (deleted)", os.path.join(directory, "exe"))
        info = self.fs.read(13)
        self.assertEqual(info.exe, "/usr/bin/firefox")

    def test_vanished_process_returns_none(self):
        self.assertIsNone(self.fs.read(999999))
        self.assertIsNone(self.fs.starttime(999999))
        self.assertFalse(self.fs.is_alive(999999))

    def test_pids_are_filtered_and_sorted(self):
        build_fake_proc(self.root, 300)
        build_fake_proc(self.root, 100)
        os.makedirs(os.path.join(self.root, "not-a-pid"))
        os.makedirs(os.path.join(self.root, "sys"), exist_ok=True)
        self.assertEqual(self.fs.pids(), [100, 300])

    def test_is_alive_respects_starttime(self):
        build_fake_proc(self.root, 21, comm="firefox", starttime=1000, exe=None)
        self.assertTrue(self.fs.is_alive(21, 1000))
        self.assertFalse(self.fs.is_alive(21, 2000))
        self.assertTrue(self.fs.is_alive(21))

    def test_is_alive_is_false_for_zombie(self):
        build_fake_proc(self.root, 23, comm="firefox", state="Z", exe=None)
        self.assertFalse(self.fs.is_alive(23, 4242))

    def test_snapshot_covers_every_pid(self):
        build_fake_proc(self.root, 1, comm="systemd", cmdline=("/sbin/init",), exe=None)
        build_fake_proc(self.root, 2, comm="firefox", exe=None)
        snapshot = self.fs.snapshot()
        self.assertEqual(sorted(p.pid for p in snapshot), [1, 2])

    def test_name_candidates_prefers_distinct_values(self):
        build_fake_proc(
            self.root,
            31,
            comm="python3",
            cmdline=("/usr/lib/firefox/firefox",),
            exe="/usr/lib/firefox/firefox",
        )
        info = self.fs.read(31)
        candidates = info.name_candidates()
        self.assertEqual(candidates[0], "python3")
        self.assertIn("firefox", candidates)
        # no duplicates even though exe and argv0 agree
        self.assertEqual(len(candidates), len(set(candidates)))

    def test_comm_max_len_constant_matches_linux(self):
        self.assertEqual(COMM_MAX_LEN, 15)

    def test_missing_root_is_tolerated(self):
        fs = ProcFS(root=os.path.join(self.root, "does-not-exist"))
        self.assertEqual(fs.pids(), [])
        self.assertEqual(fs.snapshot(), [])

    def _write_children(self, pid, by_tid):
        for tid, text in by_tid.items():
            directory = os.path.join(self.root, str(pid), "task", str(tid))
            os.makedirs(directory, exist_ok=True)
            with open(os.path.join(directory, "children"), "w", encoding="utf-8") as handle:
                handle.write(text)

    def test_child_pids_is_empty_without_a_task_directory(self):
        self.assertEqual(self.fs.child_pids(1000), ())

    def test_child_pids_reads_one_thread(self):
        self._write_children(1000, {1000: "1001 1002\n"})
        self.assertEqual(self.fs.child_pids(1000), (1001, 1002))

    def test_child_pids_unions_every_thread(self):
        self._write_children(1000, {1000: "1001", 1002: "1003"})
        self.assertEqual(self.fs.child_pids(1000), (1001, 1003))

    def test_child_pids_ignores_garbage_tokens(self):
        self._write_children(1000, {1000: "1001 nonsense 1002\n"})
        self.assertEqual(self.fs.child_pids(1000), (1001, 1002))

    def test_an_empty_children_file_means_no_children(self):
        self._write_children(1000, {1000: ""})
        self.assertEqual(self.fs.child_pids(1000), ())

    def test_live_children_are_unsupported_by_default(self):
        self.assertFalse(self.fs.supports_live_children())

    def test_live_children_are_detected_when_present(self):
        directory = os.path.join(self.root, "self", "task", "1")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "children"), "w", encoding="utf-8") as handle:
            handle.write("")
        self.assertTrue(self.fs.supports_live_children())


if __name__ == "__main__":
    unittest.main()
