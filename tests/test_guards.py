import unittest

from focused.core.guards import is_gone, judge


class JudgeTests(unittest.TestCase):
    def test_a_live_matching_pid_is_accepted(self):
        self.assertIsNone(judge(4242, None, 5000))

    def test_a_live_pid_with_a_matching_guard_is_accepted(self):
        self.assertIsNone(judge(4242, 5000, 5000))

    def test_init_and_pid_zero_are_never_targets(self):
        self.assertEqual("rule.pid-invalid", judge(1, None, 10).code)
        self.assertEqual("rule.pid-invalid", judge(0, None, 10).code)
        self.assertEqual("rule.pid-invalid", judge(-5, None, 10).code)

    def test_a_vanished_pid_is_refused(self):
        problem = judge(4242, None, None)
        self.assertEqual("rule.pid-gone", problem.code)
        self.assertTrue(is_gone(problem))

    def test_a_reused_pid_is_refused(self):
        problem = judge(4242, 5000, 6000)
        self.assertEqual("rule.pid-reused", problem.code)
        self.assertFalse(is_gone(problem))

    def test_pid_validity_is_checked_before_the_starttime(self):
        self.assertEqual("rule.pid-invalid", judge(1, None, None).code)

    def test_a_missing_live_starttime_beats_a_provided_guard(self):
        self.assertEqual("rule.pid-gone", judge(4242, 5000, None).code)


if __name__ == "__main__":
    unittest.main()
