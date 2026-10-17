"""The last gate: if we cannot prove it is safe, refuse."""

import unittest

from focused.core.rules import ACTION_MATCH, ACTION_PROTECT, ACTION_SKIP, Decision, EvalContext
from focused.domain.safety import check_safety, choose_mode
from tests.helpers import SELF_PID, SELF_PPID, UID, make_proc


def ctx(**overrides) -> EvalContext:
    base = dict(
        uid=UID,
        self_pid=SELF_PID,
        self_ppid=SELF_PPID,
        self_pgid=900_000,
        hard_protect_pids=frozenset({1, 0, SELF_PID}),
        protect_own_process_group=False,
    )
    base.update(overrides)
    return EvalContext(**base)


def match(rule="blacklist.name:firefox") -> Decision:
    return Decision(ACTION_MATCH, rule, "test")


def verdict(proc=None, decision=None, context=None, **overrides):
    kwargs = dict(
        plan_mode="pid",
        plan_reason="shared-unit",
        plan_pids=(1000,),
        guards_known=True,
    )
    kwargs.update(overrides)
    return check_safety(
        proc or make_proc(),
        decision or match(),
        context or ctx(),
        **kwargs,
    )


class SafetyGateTests(unittest.TestCase):
    def test_a_normal_match_is_allowed(self):
        result = verdict()

        self.assertTrue(result.allow)
        self.assertEqual("ok", result.reason)
        self.assertEqual("blacklist.name:firefox", result.rule)

    def test_hard_rails_are_repeated_here(self):
        self.assertFalse(verdict(make_proc(pid=1)).allow)
        self.assertFalse(verdict(make_proc(state="Z")).allow)
        self.assertFalse(verdict(make_proc(cmdline=(), exe=None)).allow)
        self.assertFalse(verdict(make_proc(pid=SELF_PID)).allow)

    def test_unknown_uid_is_a_refusal(self):
        result = verdict(make_proc(uid=None))

        self.assertFalse(result.allow)
        self.assertEqual("unknown-uid", result.reason)

    def test_unknown_daemon_uid_is_a_refusal(self):
        result = verdict(context=ctx(uid=None))

        self.assertFalse(result.allow)
        self.assertEqual("unknown-daemon-uid", result.reason)

    def test_foreign_uid_is_a_refusal(self):
        result = verdict(make_proc(uid=2000))

        self.assertFalse(result.allow)
        self.assertEqual("hard:foreign-uid", result.reason)

    def test_own_process_group_is_a_refusal_when_enabled(self):
        result = verdict(
            make_proc(pgid=900_000),
            context=ctx(protect_own_process_group=True, self_pgid=900_000),
        )

        self.assertFalse(result.allow)
        self.assertEqual("hard:own-process-group", result.reason)

    def test_a_non_match_is_a_refusal(self):
        result = verdict(decision=Decision(ACTION_SKIP))

        self.assertFalse(result.allow)
        self.assertEqual("decision:skip", result.reason)

    def test_a_protect_rule_is_a_refusal(self):
        result = verdict(decision=Decision(ACTION_PROTECT, "protect.name:firefox"))

        self.assertFalse(result.allow)
        self.assertEqual("decision:protect", result.reason)

    def test_a_skipped_plan_is_a_refusal(self):
        result = verdict(plan_mode="skip", plan_reason="slice")

        self.assertFalse(result.allow)
        self.assertEqual("plan:slice", result.reason)

    def test_a_unit_plan_needs_the_one_justified_reason(self):
        result = verdict(plan_mode="unit", plan_reason="shared-unit")

        self.assertFalse(result.allow)
        self.assertEqual("unit-plan:shared-unit", result.reason)

    def test_an_empty_tree_is_a_refusal(self):
        result = verdict(plan_pids=())

        self.assertFalse(result.allow)
        self.assertEqual("empty-tree", result.reason)

    def test_an_unknown_start_time_is_a_refusal(self):
        result = verdict(guards_known=False)

        self.assertFalse(result.allow)
        self.assertEqual("unknown-starttime", result.reason)


class ModeChoiceTests(unittest.TestCase):
    def test_a_slice_or_session_manager_is_never_touched(self):
        self.assertEqual("skip", choose_mode("skip", "slice", True))
        self.assertEqual("skip", choose_mode("skip", "session-manager", False))

    def test_safe_skip_reasons_degrade_to_the_process_tree(self):
        self.assertEqual("pid", choose_mode("skip", "unknown-cgroup", True))
        self.assertEqual("pid", choose_mode("skip", "not-cgroup-v2", True))
        self.assertEqual("pid", choose_mode("skip", "not-a-unit", True))

    def test_unit_mode_requires_units_to_be_enabled(self):
        self.assertEqual("unit", choose_mode("unit", "dedicated-app-unit", True))
        self.assertEqual("pid", choose_mode("unit", "dedicated-app-unit", False))

    def test_shared_units_use_pid_mode(self):
        self.assertEqual("pid", choose_mode("pid", "shared-unit", True))


if __name__ == "__main__":
    unittest.main()
