"""The precompiled matcher must be indistinguishable from the old linear scan.

This is the safety net for the performance rewrite: every decision below is
compared against a reference implementation that uses the original
``name_matches`` / ``cmdline_matches`` loops.  If the matcher ever changes which
pattern wins, which process is protected, or the ``Decision.rule`` string, the
test fails before the faster code can reach a scan.
"""

import unittest

from focused.core.matching import matcher_for
from focused.core.procfs import ProcessInfo
from focused.core.rules import RuleSet
from focused.core.decision import (
    ACTION_MATCH,
    ACTION_PROTECT,
    ACTION_SKIP,
    Decision,
    EvalContext,
    cmdline_matches,
    evaluate,
    name_matches,
)


def proc(
    pid=4242,
    comm="firefox",
    cmdline=("firefox",),
    exe="/usr/bin/firefox",
    uid=1000,
    ppid=10,
    pgid=20,
    starttime=100,
    state="S",
):
    return ProcessInfo(
        pid=pid,
        comm=comm,
        cmdline=tuple(cmdline),
        uid=uid,
        ppid=ppid,
        pgid=pgid,
        starttime=starttime,
        state=state,
        exe=exe,
    )


def ctx(**kwargs):
    base = dict(
        uid=1000,
        self_pid=9001,
        self_ppid=9000,
        self_pgid=-1,
        hard_protect_pids=frozenset({1, 0, 9001}),
        protect_own_process_group=False,
    )
    base.update(kwargs)
    return EvalContext(**base)


def reference_protect_reason(target, rules):
    for pattern in sorted(rules.protect_names):
        if name_matches(pattern, target):
            return "protect.name:" + pattern
    for substring in rules.protect_cmdline_substrings:
        if cmdline_matches(substring, target):
            return "protect.cmdline:" + substring
    return None


def reference_blacklist_reason(target, rules):
    for pattern in sorted(rules.names):
        if name_matches(pattern, target):
            return "blacklist.name:" + pattern
    for substring in rules.cmdline_substrings:
        if cmdline_matches(substring, target):
            return "blacklist.cmdline:" + substring
    return None


def reference_evaluate(target, rules, context):
    """A copy of the original linear implementation, kept only for comparison."""
    if target.pid <= 1:
        return Decision(ACTION_PROTECT, "hard:pid<=1", "init/kernel is never a target")
    if target.is_zombie:
        return Decision(ACTION_SKIP, "hard:zombie", "process has already exited")
    if target.is_kernel_thread:
        return Decision(ACTION_PROTECT, "hard:kernel-thread", "kernel threads cannot be signalled")
    if target.pid == context.self_pid or target.pid == context.self_ppid or target.pid in context.hard_protect_pids:
        return Decision(ACTION_PROTECT, "hard:self", "the daemon and its parent must survive")
    if context.uid is not None and target.uid is not None and target.uid != context.uid:
        return Decision(ACTION_PROTECT, "hard:foreign-uid", "process belongs to another user")

    protected = reference_protect_reason(target, rules)
    if protected is not None:
        return Decision(ACTION_PROTECT, protected, "matched protect list")

    if (
        context.protect_own_process_group
        and context.self_pgid is not None
        and target.pgid is not None
        and target.pgid == context.self_pgid
    ):
        return Decision(
            ACTION_PROTECT,
            "hard:own-process-group",
            "same process group as the daemon (typically the game launch chain)",
        )

    blacklist = reference_blacklist_reason(target, rules)
    if blacklist is not None:
        action = ACTION_PROTECT if blacklist.startswith("protect.") else ACTION_MATCH
        return Decision(action, blacklist, "matched blacklist")

    if target.pid in rules.pids:
        guard = rules.pid_guards.get(target.pid)
        rule = "blacklist.pid:" + str(target.pid)
        if guard is None:
            return Decision(ACTION_SKIP, rule, "pid rule has no reuse guard: refusing to kill")
        if target.starttime is None:
            return Decision(ACTION_SKIP, rule, "starttime unavailable")
        if target.starttime == guard:
            return Decision(ACTION_MATCH, rule, "matched blacklist pid (guard verified)")
        return Decision(ACTION_SKIP, rule, "pid-reuse guard mismatch")

    return Decision(ACTION_SKIP)


def rules(**kwargs):
    base = dict(
        names=frozenset(),
        cmdline_substrings=(),
        pids=frozenset(),
        pid_guards={},
        protect_names=frozenset(),
        protect_cmdline_substrings=(),
    )
    base.update(kwargs)
    return RuleSet(**base)


class MatcherEquivalenceTests(unittest.TestCase):
    def cases(self):
        firefox = proc()
        bash = proc(pid=4243, comm="bash", cmdline=("/bin/bash", "-l"), exe="/usr/bin/bash")
        chromium = proc(
            pid=4244,
            comm="chromium-browse",
            cmdline=("weird",),
            exe=None,
        )
        exe_only = proc(
            pid=4245,
            comm="my-launcher",
            cmdline=("my-launcher",),
            exe="/opt/Firefox/firefox",
        )
        foreign = proc(pid=4246, uid=2000)
        self_proc = proc(pid=9001)
        kernel = proc(pid=4247, comm="kworker/0:1", cmdline=(), exe=None, state="S")
        zombie = proc(pid=4248, comm="firefox", cmdline=(), exe=None, state="Z")
        wineserver = proc(
            pid=4249, comm="wineserver", cmdline=("wineserver",), exe="/usr/bin/wineserver"
        )
        profiled = proc(pid=4250, cmdline=("firefox", "--profile", "default"))
        unknown_start = proc(pid=4251, starttime=None)
        return [
            firefox,
            bash,
            chromium,
            exe_only,
            foreign,
            self_proc,
            kernel,
            zombie,
            wineserver,
            profiled,
            unknown_start,
        ]

    def rule_sets(self):
        return [
            rules(),
            rules(names=frozenset({"firefox"})),
            rules(names=frozenset({"fire*"})),
            rules(names=frozenset({"*"})),
            rules(names=frozenset({"*", "firefox"})),
            rules(protect_names=frozenset({"wineserver"})),
            rules(protect_names=frozenset({"*server", "wineserver"})),
            rules(names=frozenset({"chromium-browser"})),
            rules(names=frozenset({"firefox"}), protect_names=frozenset({"firefox"})),
            rules(cmdline_substrings=("--profile", "profile")),
            rules(protect_cmdline_substrings=("launcher",)),
            rules(names=frozenset({"firefox"}), pids=frozenset({4242}), pid_guards={4242: 100}),
            rules(names=frozenset({"firefox"}), pids=frozenset({4242}), pid_guards={}),
            rules(names=frozenset({"firefox"}), pids=frozenset({4242}), pid_guards={4242: 999}),
            rules(pids=frozenset({4242}), pid_guards={}),
            rules(pids=frozenset({4242}), pid_guards={4242: 100}),
            rules(pids=frozenset({4242}), pid_guards={4242: 999}),
            rules(pids=frozenset({4251}), pid_guards={4251: 100}),
        ]

    def test_protect_reason_matches_reference(self):
        for rule_set in self.rule_sets():
            for target in self.cases():
                with self.subTest(rules=rule_set, pid=target.pid):
                    self.assertEqual(
                        reference_protect_reason(target, rule_set),
                        matcher_for(rule_set).protect_reason(target),
                    )

    def test_blacklist_reason_matches_reference(self):
        for rule_set in self.rule_sets():
            for target in self.cases():
                matcher = matcher_for(rule_set)
                name = matcher.blacklist_name(target)
                matched = "blacklist.name:" + name if name else None
                if matched is None:
                    substring = matcher.blacklist_cmdline(target)
                    matched = "blacklist.cmdline:" + substring if substring else None
                with self.subTest(rules=rule_set, pid=target.pid):
                    self.assertEqual(
                        reference_blacklist_reason(target, rule_set), matched
                    )

    def test_full_decision_matches_reference(self):
        context = ctx()
        for rule_set in self.rule_sets():
            for target in self.cases():
                with self.subTest(rules=rule_set, pid=target.pid):
                    expected = reference_evaluate(target, rule_set, context)
                    actual = evaluate(target, rule_set, context)
                    self.assertEqual(expected.action, actual.action)
                    self.assertEqual(expected.rule, actual.rule)


class MatcherSemanticsTests(unittest.TestCase):
    def test_first_pattern_in_sorted_order_wins(self):
        rule_set = rules(names=frozenset({"*", "firefox"}))
        decision = evaluate(proc(), rule_set, ctx())
        self.assertEqual("blacklist.name:*", decision.rule)

    def test_protect_wins_over_blacklist(self):
        rule_set = rules(names=frozenset({"*"}), protect_names=frozenset({"firefox"}))
        decision = evaluate(proc(), rule_set, ctx())
        self.assertEqual(ACTION_PROTECT, decision.action)
        self.assertEqual("protect.name:firefox", decision.rule)

    def test_long_exact_rule_matches_truncated_comm(self):
        rule_set = rules(names=frozenset({"chromium-browser"}))
        decision = evaluate(
            proc(pid=4244, comm="chromium-browse", cmdline=("weird",), exe=None),
            rule_set,
            ctx(),
        )
        self.assertEqual(ACTION_MATCH, decision.action)
        self.assertEqual("blacklist.name:chromium-browser", decision.rule)

    def test_wildcard_does_not_need_a_literal_prefix(self):
        rule_set = rules(names=frozenset({"*fox"}))
        self.assertEqual(ACTION_MATCH, evaluate(proc(), rule_set, ctx()).action)

    def test_cmdline_substrings_keep_their_order(self):
        rule_set = rules(cmdline_substrings=("--profile", "profile"))
        target = proc(cmdline=("firefox", "--profile", "default"))
        self.assertEqual(
            "blacklist.cmdline:--profile",
            evaluate(target, rule_set, ctx()).rule,
        )

    def test_exe_basename_is_a_name_candidate(self):
        rule_set = rules(names=frozenset({"firefox"}))
        target = proc(comm="my-launcher", cmdline=("my-launcher",), exe="/opt/Firefox/firefox")
        self.assertEqual(ACTION_MATCH, evaluate(target, rule_set, ctx()).action)

    def test_matcher_is_cached_by_rule_content(self):
        first = matcher_for(rules(names=frozenset({"firefox"})))
        second = matcher_for(rules(names=frozenset({"firefox"})))
        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
