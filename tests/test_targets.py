import unittest

from focused.domain.targets import (
    ProcessFacts,
    Selection,
    ancestors,
    check_starttimes,
    subtree,
)

UID = 1000
SELF_PID = 900_001
SELF_PPID = 900_000


def facts(pid=1000, uid=UID, name="firefox", zombie=False, kernel=False):
    return ProcessFacts(
        pid=pid,
        uid=uid,
        name=name,
        is_zombie=zombie,
        is_kernel_thread=kernel,
    )


class SubtreeTests(unittest.TestCase):
    def test_a_leaf_is_its_own_subtree(self):
        self.assertEqual(subtree(1000, {1000: 1}), (1000,))

    def test_descendants_are_included(self):
        parents = {1000: 1, 1001: 1000, 1002: 1000, 1003: 1001}
        self.assertEqual(subtree(1000, parents), (1000, 1001, 1002, 1003))

    def test_the_parent_is_never_included(self):
        parents = {1000: 500, 500: 1}
        self.assertNotIn(500, subtree(1000, parents))

    def test_siblings_are_not_included(self):
        parents = {1000: 1, 1001: 1}
        self.assertEqual(subtree(1000, parents), (1000,))

    def test_children_are_visited_in_pid_order(self):
        parents = {1000: 1, 1003: 1000, 1001: 1000, 1002: 1000}
        self.assertEqual(subtree(1000, parents), (1000, 1001, 1002, 1003))

    def test_a_cycle_does_not_loop(self):
        parents = {1000: 1001, 1001: 1000}
        self.assertEqual(subtree(1000, parents), (1000, 1001))

    def test_a_deeper_cycle_does_not_duplicate(self):
        parents = {1000: 1, 1001: 1000, 1002: 1001, 1000: 1002}
        self.assertEqual(subtree(1000, parents), (1000, 1001, 1002))

    def test_unknown_root_still_returns_itself(self):
        self.assertEqual(subtree(4242, {}), (4242,))


class AncestorTests(unittest.TestCase):
    def test_the_chain_reaches_the_top(self):
        parents = {1000: 1001, 1001: 1002, 1002: 1}
        self.assertEqual(ancestors(1000, parents), frozenset({1001, 1002, 1}))

    def test_a_root_process_has_no_ancestors(self):
        self.assertEqual(ancestors(1, {}), frozenset())

    def test_a_missing_pid_has_no_ancestors(self):
        self.assertEqual(ancestors(4242, {1000: 1}), frozenset())

    def test_a_cycle_does_not_loop(self):
        parents = {1000: 1001, 1001: 1000}
        self.assertTrue(ancestors(1000, parents))


class StarttimeTests(unittest.TestCase):
    def test_every_pid_resolved(self):
        check = check_starttimes([1000, 1001], {1000: 11, 1001: 22})
        self.assertTrue(check.ok)
        self.assertEqual(dict(check.starttimes), {1000: 11, 1001: 22})
        self.assertEqual(check.missing, ())

    def test_a_missing_starttime_fails_the_whole_check(self):
        check = check_starttimes([1000, 1001], {1000: 11, 1001: None})
        self.assertFalse(check.ok)
        self.assertEqual(check.missing, (1001,))

    def test_an_absent_pid_is_missing(self):
        check = check_starttimes([1000], {})
        self.assertFalse(check.ok)
        self.assertEqual(check.missing, (1000,))

    def test_resolved_values_are_kept_even_when_incomplete(self):
        check = check_starttimes([1000, 1001], {1000: 11})
        self.assertEqual(dict(check.starttimes), {1000: 11})


class SelectionTests(unittest.TestCase):
    def test_root_and_name_come_from_the_facts(self):
        selection = Selection(facts(pid=7, name="steam"), (7,), {7: 1}, "blacklist.name:steam")
        self.assertEqual(selection.root, 7)
        self.assertEqual(selection.name, "steam")


if __name__ == "__main__":
    unittest.main()
