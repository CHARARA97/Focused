import unittest

from focused.app.enforcer import ActionOutcome, Tick
from focused.app.targeting import Candidate
from focused.app.tick import Snapshot, run_tick
from focused.core.decision import EvalContext
from focused.core.rules import RuleSet
from focused.domain.intent import Intent
from focused.domain.policy import Policy
from focused.domain.preflight import Context as PreflightContext
from focused.domain.reasons import Gate, Mode, Outcome
from focused.domain.targets import ProcessFacts, Selection
from tests.helpers import UID, make_proc

SELF_PID = 999_001
SELF_PPID = 999_000


def tick(mode=Mode.KILL, intent=Gate.WORK, cap=20, scan=1, mono=10.0, version=3):
    return Tick(
        scan=scan,
        mono=mono,
        intent=Intent(enforcing=intent is Gate.WORK, reason=intent),
        policy=Policy(mode=mode, max_per_scan=cap, version=version),
    )


def evaluation():
    return EvalContext(
        uid=UID,
        self_pid=SELF_PID,
        self_ppid=SELF_PPID,
        self_pgid=-1,
        hard_protect_pids=frozenset({0, 1, SELF_PID}),
        protect_own_process_group=False,
    )


def candidate(proc, rule, *, uid=UID, allow=True):
    selection = Selection(
        facts=ProcessFacts(
            pid=proc.pid,
            uid=proc.uid,
            name=proc.display_name(),
            is_zombie=proc.is_zombie,
            is_kernel_thread=proc.is_kernel_thread,
        ),
        pids=(proc.pid,),
        starttimes={proc.pid: proc.starttime},
        rule=rule,
    )
    context = PreflightContext(
        self_pid=SELF_PID,
        self_ppid=SELF_PPID,
        self_uid=uid if allow else None,
        hard_protect_pids=frozenset({0, 1, SELF_PID}),
        parents={},
        uids={proc.pid: proc.uid},
        candidates={proc.pid: proc.name_candidates()},
        is_protected=lambda names: False,
    )
    return Candidate(selection=selection, context=context)


class FakeTargeting:
    def __init__(self, allow=True):
        self.calls = []
        self.allow = allow

    def resolve(self, proc, rule, *, parents, rows):
        self.calls.append(proc.pid)
        return candidate(proc, rule, allow=self.allow)


class FakeEnforcer:
    def __init__(self, outcome=Outcome.KILLED):
        self.denies = []
        self.enforces = []
        self.outcome = outcome

    def deny(self, selection, verdict, tick):
        self.denies.append(selection)

    def enforce(self, selection, *, tick):
        self.enforces.append(selection)
        return ActionOutcome(self.outcome)


def snapshot(*procs):
    rows = {proc.pid: proc for proc in procs}
    parents = {proc.pid: proc.ppid for proc in procs}
    return Snapshot(rows=rows, parents=parents)


def rules(names=("firefox",), protect=()):
    return RuleSet(names=set(names), protect_names=set(protect))


def run(t, snap, *, targeting=None, enforcer=None, rule_set=None):
    return run_tick(
        t,
        snap,
        rules=rule_set or rules(),
        evaluation=evaluation(),
        targeting=targeting or FakeTargeting(),
        enforcer=enforcer or FakeEnforcer(),
    )


class GatedTests(unittest.TestCase):
    def test_a_non_enforcing_intent_runs_nothing(self):
        targeting, enforcer = FakeTargeting(), FakeEnforcer()
        result = run(
            tick(intent=Gate.NO_SESSION),
            snapshot(make_proc()),
            targeting=targeting,
            enforcer=enforcer,
        )

        self.assertEqual("no-session", result.gate)
        self.assertEqual([], targeting.calls)
        self.assertEqual([], enforcer.enforces)

    def test_the_gate_reason_reaches_the_report(self):
        result = run(tick(intent=Gate.PAUSED), snapshot(make_proc()))
        self.assertEqual("paused", result.gate)

    def test_an_enforcing_intent_has_no_gate(self):
        result = run(tick(), snapshot(make_proc()))
        self.assertEqual("", result.gate)


class MatchTests(unittest.TestCase):
    def test_a_match_is_enforced(self):
        enforcer = FakeEnforcer()
        result = run(tick(), snapshot(make_proc()), enforcer=enforcer)

        self.assertEqual(1, result.killed)
        self.assertEqual(1, len(enforcer.enforces))

    def test_a_non_match_is_ignored(self):
        enforcer = FakeEnforcer()
        result = run(
            tick(),
            snapshot(make_proc(comm="steam", cmdline=("steam",), exe="/usr/bin/steam")),
            enforcer=enforcer,
        )

        self.assertEqual(0, result.killed)
        self.assertEqual([], enforcer.enforces)

    def test_a_protected_name_is_counted_not_enforced(self):
        enforcer = FakeEnforcer()
        result = run(
            tick(),
            snapshot(make_proc(comm="bash")),
            rule_set=rules(names={"bash"}, protect={"bash"}),
            enforcer=enforcer,
        )

        self.assertEqual({"protected": 1}, dict(result.by_reason))
        self.assertEqual([], enforcer.enforces)

    def test_a_denied_target_is_counted_and_reported(self):
        enforcer = FakeEnforcer()
        result = run(
            tick(),
            snapshot(make_proc()),
            targeting=FakeTargeting(allow=False),
            enforcer=enforcer,
        )

        self.assertEqual(1, result.denied)
        self.assertEqual(1, len(enforcer.denies))
        self.assertIn("unknown-daemon-uid", result.by_reason)

    def test_audit_mode_does_not_count_a_kill(self):
        enforcer = FakeEnforcer(outcome=Outcome.AUDIT)
        result = run(tick(mode=Mode.AUDIT), snapshot(make_proc()), enforcer=enforcer)

        self.assertEqual(0, result.killed)
        self.assertEqual(1, len(enforcer.enforces))


class LimitTests(unittest.TestCase):
    def test_the_scan_cap_is_applied(self):
        procs = [make_proc(pid=1000), make_proc(pid=1001)]
        enforcer = FakeEnforcer()
        result = run(tick(cap=1), snapshot(*procs), enforcer=enforcer)

        self.assertEqual(1, result.killed)
        self.assertEqual(1, result.throttled)
        self.assertEqual(1, len(enforcer.enforces))

    def test_a_tree_already_killed_is_not_matched_twice(self):
        procs = [make_proc(pid=1000), make_proc(pid=1001)]

        class WideningTargeting(FakeTargeting):
            def resolve(self, proc, rule, *, parents, rows):
                pids = (proc.pid, 1001)
                base = candidate(proc, rule)
                return Candidate(
                    selection=Selection(
                        facts=base.selection.facts,
                        pids=pids,
                        starttimes={pid: rows[pid].starttime for pid in pids},
                        rule=rule,
                    ),
                    context=PreflightContext(
                        self_pid=SELF_PID,
                        self_ppid=SELF_PPID,
                        self_uid=UID,
                        hard_protect_pids=frozenset({0, 1, SELF_PID}),
                        parents={},
                        uids={pid: UID for pid in pids},
                        candidates={pid: rows[pid].name_candidates() for pid in pids},
                        is_protected=lambda names: False,
                    ),
                )

        enforcer = FakeEnforcer()
        result = run(
            tick(), snapshot(*procs), targeting=WideningTargeting(), enforcer=enforcer
        )

        self.assertEqual(1, result.killed)
        self.assertEqual(1, len(enforcer.enforces))
        self.assertEqual(1, result.by_reason["seen"])

    def test_a_gone_target_is_counted(self):
        class GoneTargeting:
            def resolve(self, proc, rule, *, parents, rows):
                return None

        result = run(tick(), snapshot(make_proc()), targeting=GoneTargeting())
        self.assertEqual({"gone": 1}, dict(result.by_reason))


class ReportTests(unittest.TestCase):
    def test_the_scan_id_reaches_the_report(self):
        result = run(tick(scan=42), snapshot(make_proc()))
        self.assertEqual(42, result.scan)

    def test_the_mode_reaches_the_report(self):
        result = run(tick(mode=Mode.AUDIT), snapshot(make_proc()))
        self.assertEqual("audit", result.mode)


if __name__ == "__main__":
    unittest.main()
