"""Tests for the decision layer.

This is where the safety of the whole project is proven, so the cases are
deliberately adversarial: every way a rule could misfire has a test.
"""

from __future__ import annotations

import unittest

from focused.core.rules import (
    ACTION_PROTECT,
    ACTION_SKIP,
    EvalContext,
    RuleSet,
    evaluate,
    is_catch_all_pattern,
    name_matches,
    normalize_many,
)
from tests.helpers import SELF_PGID, SELF_PID, SELF_PPID, UID, make_proc


def ctx(**overrides) -> EvalContext:
    base = dict(
        uid=UID,
        self_pid=SELF_PID,
        self_ppid=SELF_PPID,
        self_pgid=SELF_PGID,
        # Disabled by default so unrelated cases stay focused; enabled in the
        # dedicated process-group tests below.
        protect_own_process_group=False,
    )
    base.update(overrides)
    return EvalContext(**base)


class NameMatchingTests(unittest.TestCase):
    def test_exact_comm_match(self):
        rules = RuleSet(names=frozenset({"firefox"}))
        self.assertTrue(evaluate(make_proc(comm="firefox"), rules, ctx()).should_kill)

    def test_match_is_case_insensitive(self):
        rules = RuleSet(names=frozenset({"firefox"}))
        decision = evaluate(make_proc(comm="FireFox"), rules, ctx())
        self.assertTrue(decision.should_kill)

    def test_glob_match(self):
        rules = RuleSet(names=frozenset({"chrom*"}))
        self.assertTrue(evaluate(make_proc(comm="chromium"), rules, ctx()).should_kill)

    def test_matches_exe_basename_when_comm_differs(self):
        rules = RuleSet(names=frozenset({"firefox"}))
        proc = make_proc(comm="python3", exe="/usr/lib/firefox/firefox")
        self.assertTrue(evaluate(proc, rules, ctx()).should_kill)

    def test_matches_argv0_basename(self):
        rules = RuleSet(names=frozenset({"distractor"}))
        proc = make_proc(
            comm="sh",
            cmdline=("/opt/tools/distractor", "--run"),
            exe="/usr/bin/dash",
        )
        self.assertTrue(evaluate(proc, rules, ctx()).should_kill)

    def test_kernel_comm_truncation_is_compensated(self):
        """comm is capped at 15 chars, so a 16-char rule must still match."""
        truncated = "chromium-browse"  # exactly 15 characters
        self.assertEqual(len(truncated), 15)
        rules = RuleSet(names=frozenset({"chromium-browser"}))
        decision = evaluate(make_proc(comm=truncated, exe=None), rules, ctx())
        self.assertTrue(
            decision.should_kill,
            "a plain rule longer than 15 chars must match its truncated comm",
        )

    def test_truncation_compensation_is_skipped_for_globs(self):
        """Glob + truncation cannot both be reasoned about; do not guess."""
        truncated = "chromium-browse"
        rules = RuleSet(names=frozenset({"chromium-browser*"}))
        decision = evaluate(make_proc(comm=truncated, exe=None), rules, ctx())
        self.assertFalse(decision.should_kill)

    def test_short_rule_does_not_match_a_longer_name(self):
        rules = RuleSet(names=frozenset({"chrome"}))
        self.assertFalse(name_matches("chrome", make_proc(comm="chromium")))


class CmdlineMatchingTests(unittest.TestCase):
    def test_substring_across_arguments(self):
        rules = RuleSet(cmdline_substrings=("--profile distractor",))
        proc = make_proc(
            comm="firefox",
            cmdline=("firefox", "--profile", "distractor"),
        )
        self.assertTrue(evaluate(proc, rules, ctx()).should_kill)

    def test_substring_is_case_insensitive(self):
        rules = RuleSet(cmdline_substrings=("distractor",))
        proc = make_proc(cmdline=("/opt/Distractor",))
        self.assertTrue(evaluate(proc, rules, ctx()).should_kill)

    def test_unrelated_cmdline_does_not_match(self):
        rules = RuleSet(cmdline_substrings=("distractor",))
        proc = make_proc(cmdline=("firefox", "--new-window"))
        self.assertFalse(evaluate(proc, rules, ctx()).should_kill)


class PidMatchingTests(unittest.TestCase):
    def test_pid_with_matching_guard(self):
        rules = RuleSet(pids=frozenset({4242}), pid_guards={4242: 777})
        proc = make_proc(pid=4242, starttime=777)
        self.assertTrue(evaluate(proc, rules, ctx()).should_kill)

    def test_pid_reuse_is_refused(self):
        """The classic PID-reuse trap: same PID, different process."""
        rules = RuleSet(pids=frozenset({4242}), pid_guards={4242: 777})
        proc = make_proc(pid=4242, starttime=999)
        decision = evaluate(proc, rules, ctx())
        self.assertFalse(decision.should_kill)
        self.assertIn("guard mismatch", decision.reason)

    def test_pid_without_guard_still_matches(self):
        rules = RuleSet(pids=frozenset({4242}))
        proc = make_proc(pid=4242, starttime=999)
        self.assertTrue(evaluate(proc, rules, ctx()).should_kill)

    def test_unknown_starttime_fails_closed(self):
        """Without a start time the guard cannot be verified, so refuse to kill."""
        rules = RuleSet(pids=frozenset({4242}), pid_guards={4242: 777})
        proc = make_proc(pid=4242, starttime=None)
        decision = evaluate(proc, rules, ctx())
        self.assertFalse(decision.should_kill)
        self.assertIn("cannot verify", decision.reason)


class HardRailTests(unittest.TestCase):
    def test_pid_one_is_protected(self):
        rules = RuleSet(names=frozenset({"*"}))
        decision = evaluate(make_proc(pid=1, comm="systemd"), rules, ctx())
        self.assertEqual(decision.action, ACTION_PROTECT)

    def test_kernel_thread_is_protected(self):
        rules = RuleSet(names=frozenset({"kworker"}))
        proc = make_proc(comm="kworker/0:1", cmdline=(), exe=None)
        decision = evaluate(proc, rules, ctx())
        self.assertEqual(decision.action, ACTION_PROTECT)
        self.assertEqual(decision.rule, "hard:kernel-thread")

    def test_zombie_is_skipped_not_protected(self):
        proc = make_proc(comm="firefox", cmdline=(), state="Z", exe=None)
        decision = evaluate(proc, RuleSet(names=frozenset({"firefox"})), ctx())
        self.assertEqual(decision.action, ACTION_SKIP)

    def test_daemon_itself_is_protected(self):
        rules = RuleSet(names=frozenset({"chillfocusd"}))
        proc = make_proc(pid=SELF_PID, comm="chillfocusd")
        self.assertEqual(evaluate(proc, rules, ctx()).action, ACTION_PROTECT)

    def test_parent_process_is_protected(self):
        """The parent is usually the launcher script that also owns the game."""
        rules = RuleSet(names=frozenset({"bash"}))
        proc = make_proc(pid=SELF_PPID, comm="bash")
        self.assertEqual(evaluate(proc, rules, ctx()).action, ACTION_PROTECT)

    def test_extra_hard_protect_pid(self):
        rules = RuleSet(names=frozenset({"chillwithyou"}))
        proc = make_proc(pid=5150, comm="Chill With You.exe")
        protected = ctx(hard_protect_pids=frozenset({5150}))
        self.assertEqual(evaluate(proc, rules, protected).action, ACTION_PROTECT)

    def test_foreign_uid_is_protected(self):
        rules = RuleSet(names=frozenset({"firefox"}))
        proc = make_proc(comm="firefox", uid=UID + 1)
        decision = evaluate(proc, rules, ctx())
        self.assertEqual(decision.action, ACTION_PROTECT)
        self.assertEqual(decision.rule, "hard:foreign-uid")

    def test_own_process_group_is_protected(self):
        rules = RuleSet(names=frozenset({"distractor"}))
        proc = make_proc(pid=7777, comm="distractor", pgid=SELF_PGID)
        decision = evaluate(proc, rules, ctx(protect_own_process_group=True))
        self.assertEqual(decision.action, ACTION_PROTECT)
        self.assertEqual(decision.rule, "hard:own-process-group")

    def test_other_process_group_is_not_protected(self):
        rules = RuleSet(names=frozenset({"distractor"}))
        proc = make_proc(pid=7777, comm="distractor", pgid=SELF_PGID + 5000)
        decision = evaluate(proc, rules, ctx(protect_own_process_group=True))
        self.assertTrue(decision.should_kill)


class ProtectReasonTests(unittest.TestCase):
    """The settings UI asks "would the protect list shield this?"."""

    def test_reports_the_matching_protect_name(self):
        from focused.core.rules import protect_reason

        rules = RuleSet(protect_names=frozenset({"wineserver"}))
        proc = make_proc(comm="wineserver", exe="/usr/bin/wineserver")
        self.assertTrue(protect_reason(proc, rules).startswith("protect.name:"))

    def test_reports_a_protect_cmdline_match(self):
        from focused.core.rules import protect_reason

        rules = RuleSet(protect_cmdline_substrings=("keep-me",))
        proc = make_proc(cmdline=("firefox", "keep-me"))
        self.assertTrue(protect_reason(proc, rules).startswith("protect.cmdline:"))

    def test_returns_none_for_an_unprotected_process(self):
        from focused.core.rules import protect_reason

        self.assertIsNone(protect_reason(make_proc(comm="firefox"), RuleSet()))


class PrecedenceTests(unittest.TestCase):
    def test_protect_beats_match(self):
        rules = RuleSet(
            names=frozenset({"firefox"}),
            protect_names=frozenset({"firefox"}),
        )
        decision = evaluate(make_proc(comm="firefox"), rules, ctx())
        self.assertEqual(decision.action, ACTION_PROTECT)
        self.assertTrue(decision.rule.startswith("protect."))

    def test_protect_wins_even_for_a_catch_all_blacklist(self):
        """The single most important property in this project.

        A user who writes ``*`` gets "nothing is killed" as long as a protect
        rule covers the process.
        """
        rules = RuleSet(
            names=frozenset({"*"}),
            protect_names=frozenset({"wineserver"}),
        )
        proc = make_proc(comm="wineserver", exe="/usr/bin/wineserver")
        self.assertEqual(evaluate(proc, rules, ctx()).action, ACTION_PROTECT)

    def test_protect_cmdline_beats_blacklist_name(self):
        rules = RuleSet(
            names=frozenset({"firefox"}),
            protect_cmdline_substrings=("keep-me",),
        )
        proc = make_proc(comm="firefox", cmdline=("firefox", "keep-me"))
        decision = evaluate(proc, rules, ctx())
        self.assertEqual(decision.action, ACTION_PROTECT)

    def test_empty_rules_skip_everything(self):
        decision = evaluate(make_proc(), RuleSet(), ctx())
        self.assertEqual(decision.action, ACTION_SKIP)


class RuleSetTests(unittest.TestCase):
    def test_roundtrip(self):
        original = RuleSet.from_dict(
            {
                "names": ["Firefox", "  discord  "],
                "cmdline_substrings": ["--profile distractor"],
                "pids": [42, "43"],
                "pid_guards": {"42": 100},
                "protect_names": ["wineserver"],
            }
        )
        again = RuleSet.from_dict(original.to_dict())
        self.assertEqual(original.names, again.names)
        self.assertEqual(original.pids, again.pids)
        self.assertEqual(original.pid_guards, again.pid_guards)
        self.assertEqual(original.cmdline_substrings, again.cmdline_substrings)

    def test_normalisation_and_deduplication(self):
        self.assertEqual(normalize_many([" A ", "a", "", "  ", "b"]), ("a", "b"))

    def test_shadowed_names(self):
        rules = RuleSet(
            names=frozenset({"wineserver", "firefox"}),
            protect_names=frozenset({"wineserver"}),
        )
        self.assertEqual(rules.shadowed_names(), ("wineserver",))

    def test_shadowed_names_detects_glob_overlap(self):
        rules = RuleSet(
            names=frozenset({"steam"}),
            protect_names=frozenset({"steam*"}),
        )
        self.assertEqual(rules.shadowed_names(), ("steam",))

    def test_pids_without_guard(self):
        rules = RuleSet(pids=frozenset({1, 2}), pid_guards={1: 99})
        self.assertEqual(rules.pids_without_guard(), (2,))

    def test_is_empty(self):
        self.assertTrue(RuleSet().is_empty)
        self.assertFalse(RuleSet(names=frozenset({"x"})).is_empty)

    def test_catch_all_detection(self):
        for pattern in ("*", "**", "*?", "?"):
            self.assertTrue(is_catch_all_pattern(pattern), pattern)
        for pattern in ("firefox", "chrom*", "steam*"):
            self.assertFalse(is_catch_all_pattern(pattern), pattern)


if __name__ == "__main__":
    unittest.main()
