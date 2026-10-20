import unittest

from focused.domain.intent import Intent
from focused.domain.reasons import Gate


def decide(**kwargs):
    base = dict(enabled=True, active=True, paused=False, on_break=False)
    base.update(kwargs)
    return Intent.decide(**base)


class IntentTests(unittest.TestCase):
    def test_work_is_the_only_enforcing_state(self):
        self.assertTrue(decide().enforcing)
        self.assertEqual(Gate.WORK, decide().reason)

    def test_a_disabled_feature_never_enforces(self):
        intent = decide(enabled=False)
        self.assertFalse(intent.enforcing)
        self.assertEqual(Gate.DISABLED, intent.reason)

    def test_a_pause_blocks_enforcement_even_while_active(self):
        intent = decide(paused=True)
        self.assertFalse(intent.enforcing)
        self.assertEqual(Gate.PAUSED, intent.reason)

    def test_a_break_is_not_enforcing(self):
        intent = decide(active=False, on_break=True)
        self.assertFalse(intent.enforcing)
        self.assertEqual(Gate.BREAK, intent.reason)

    def test_no_session_is_not_enforcing(self):
        intent = decide(active=False, on_break=False)
        self.assertFalse(intent.enforcing)
        self.assertEqual(Gate.NO_SESSION, intent.reason)

    def test_disabled_wins_over_every_other_state(self):
        intent = decide(enabled=False, active=False, paused=True, on_break=True)
        self.assertEqual(Gate.DISABLED, intent.reason)

    def test_paused_wins_over_a_break(self):
        intent = decide(active=False, paused=True, on_break=True)
        self.assertEqual(Gate.PAUSED, intent.reason)

    def test_the_matrix_is_exhaustive(self):
        for enabled in (True, False):
            for active in (True, False):
                for paused in (True, False):
                    for on_break in (True, False):
                        intent = decide(
                            enabled=enabled,
                            active=active,
                            paused=paused,
                            on_break=on_break,
                        )
                        expected = enabled and active and not paused
                        self.assertEqual(expected, intent.enforcing, intent)


if __name__ == "__main__":
    unittest.main()
