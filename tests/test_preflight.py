import unittest

from focused.domain.preflight import Context, check
from focused.domain.reasons import DenyReason
from focused.domain.targets import ProcessFacts, Selection

UID = 1000
SELF_PID = 900_001
SELF_PPID = 900_000


def selection(pids=(1000,), uid=UID, name="firefox", rule="blacklist.name:firefox"):
    root = pids[0]
    return Selection(
        facts=ProcessFacts(pid=root, uid=uid, name=name, is_zombie=False, is_kernel_thread=False),
        pids=tuple(pids),
        starttimes={pid: 1 for pid in pids},
        rule=rule,
    )


def context(target=None, **overrides):
    target = target or selection()
    base = dict(
        self_pid=SELF_PID,
        self_ppid=SELF_PPID,
        self_uid=UID,
        hard_protect_pids=frozenset({0, 1, SELF_PID}),
        parents={SELF_PID: SELF_PPID, SELF_PPID: 1},
        uids={pid: UID for pid in target.pids},
        candidates={pid: (target.name,) for pid in target.pids},
        is_protected=lambda candidates: False,
    )
    base.update(overrides)
    return Context(**base)


def reason(target=None, **overrides):
    return check(target or selection(), context(target, **overrides)).deny_reason


class AllowedTests(unittest.TestCase):
    def test_a_plain_match_is_allowed(self):
        self.assertTrue(check(selection(), context()).allow)

    def test_a_tree_is_allowed(self):
        target = selection(pids=(1000, 1001, 1002))
        self.assertTrue(check(target, context(target)).allow)

    def test_an_allowed_verdict_has_no_reason(self):
        self.assertIsNone(check(selection(), context()).deny_reason)


class HardRailTests(unittest.TestCase):
    def test_an_unknown_daemon_uid_is_refused(self):
        self.assertEqual(reason(self_uid=None), DenyReason.UNKNOWN_DAEMON_UID)

    def test_init_is_refused(self):
        self.assertEqual(reason(selection(pids=(1,))), DenyReason.PID_LE_1)

    def test_the_daemon_itself_is_refused(self):
        self.assertEqual(reason(selection(pids=(SELF_PID,))), DenyReason.SELF)

    def test_the_daemon_parent_is_refused(self):
        self.assertEqual(reason(selection(pids=(SELF_PPID,))), DenyReason.SELF)

    def test_a_hard_protected_pid_is_refused(self):
        target = selection(pids=(4242,))
        self.assertEqual(
            reason(target, hard_protect_pids=frozenset({0, 1, SELF_PID, 4242})),
            DenyReason.SELF,
        )


class ProcessStateTests(unittest.TestCase):
    def test_a_zombie_is_refused(self):
        target = Selection(
            facts=ProcessFacts(1000, UID, "firefox", True, False),
            pids=(1000,),
            starttimes={1000: 1},
            rule="blacklist.name:firefox",
        )
        self.assertEqual(reason(target), DenyReason.ZOMBIE)

    def test_a_kernel_thread_is_refused(self):
        target = Selection(
            facts=ProcessFacts(1000, UID, "firefox", False, True),
            pids=(1000,),
            starttimes={1000: 1},
            rule="blacklist.name:firefox",
        )
        self.assertEqual(reason(target), DenyReason.KERNEL_THREAD)

    def test_an_unknown_uid_is_refused(self):
        self.assertEqual(reason(selection(uid=None)), DenyReason.UNKNOWN_UID)

    def test_a_foreign_uid_is_refused(self):
        self.assertEqual(reason(selection(uid=UID + 1)), DenyReason.FOREIGN_UID)


class AncestryTests(unittest.TestCase):
    def test_a_target_above_the_daemon_is_refused(self):
        target = selection(pids=(5000,))
        self.assertEqual(
            reason(target, parents={SELF_PID: 5000, 5000: 1}),
            DenyReason.TARGET_IS_ANCESTOR,
        )

    def test_the_daemon_parent_as_target_is_refused(self):
        target = selection(pids=(SELF_PPID,))
        self.assertEqual(reason(target), DenyReason.SELF)

    def test_the_daemon_appearing_in_the_tree_is_refused(self):
        target = selection(pids=(1000, SELF_PID))
        self.assertEqual(reason(target, parents={}), DenyReason.SELF_IN_TREE)

    def test_the_daemon_parent_appearing_in_the_tree_is_refused(self):
        target = selection(pids=(1000, SELF_PPID))
        self.assertEqual(reason(target, parents={}), DenyReason.SELF_IN_TREE)


class TreeTests(unittest.TestCase):
    def test_an_empty_tree_is_refused(self):
        target = Selection(
            facts=ProcessFacts(1000, UID, "firefox", False, False),
            pids=(),
            starttimes={},
            rule="blacklist.name:firefox",
        )
        self.assertEqual(reason(target), DenyReason.EMPTY_TREE)

    def test_a_foreign_uid_member_is_refused(self):
        target = selection(pids=(1000, 1001))
        self.assertEqual(
            reason(target, uids={1000: UID, 1001: UID + 1}),
            DenyReason.TREE_FOREIGN_UID,
        )

    def test_an_unknown_uid_member_is_refused(self):
        target = selection(pids=(1000, 1001))
        self.assertEqual(
            reason(target, uids={1000: UID}),
            DenyReason.TREE_FOREIGN_UID,
        )

    def test_a_protected_root_is_refused(self):
        target = selection(name="bash")
        self.assertEqual(
            reason(target, is_protected=lambda candidates: "bash" in candidates),
            DenyReason.PROTECTED,
        )

    def test_a_protected_member_is_refused(self):
        target = selection(pids=(1000, 1001))
        self.assertEqual(
            reason(
                target,
                candidates={1000: ("firefox",), 1001: ("bash",)},
                is_protected=lambda candidates: "bash" in candidates,
            ),
            DenyReason.TREE_PROTECTED,
        )


class OrderTests(unittest.TestCase):
    def test_the_root_rails_win_over_the_tree_scans(self):
        target = selection(pids=(1000, 1001))
        self.assertEqual(
            reason(
                target,
                uids={1000: UID, 1001: UID + 1},
                candidates={1000: ("bash",), 1001: ("bash",)},
                is_protected=lambda candidates: "bash" in candidates,
            ),
            DenyReason.PROTECTED,
        )

    def test_the_ancestor_rail_wins_over_the_tree_scans(self):
        target = selection(pids=(5000, 1001))
        self.assertEqual(
            reason(
                target,
                parents={SELF_PID: 5000, 5000: 1},
                uids={5000: UID, 1001: UID + 1},
            ),
            DenyReason.TARGET_IS_ANCESTOR,
        )


if __name__ == "__main__":
    unittest.main()
