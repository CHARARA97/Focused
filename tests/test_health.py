"""Health: the single answer to "is enforcement actually working?"."""

import unittest

from focused.domain.health import Health


class HealthTests(unittest.TestCase):
    def test_degraded_only_after_the_threshold(self):
        health = Health(degraded_after=3)

        health.record_scan_failure("boom")
        health.record_scan_failure("boom")
        self.assertFalse(health.degraded)

        health.record_scan_failure("boom")
        self.assertTrue(health.degraded)
        self.assertIn("scan-degraded", health.problems)

    def test_a_success_resets_the_failure_streak(self):
        health = Health(degraded_after=2)
        health.record_scan_failure("boom")
        health.record_scan_failure("boom")

        health.record_scan_success()

        self.assertFalse(health.degraded)
        self.assertEqual("", health.last_scan_error)

    def test_disabled_freeze_is_a_problem(self):
        health = Health(freeze_enabled=False)

        self.assertIn("freeze-disabled", health.problems)

    def test_recovery_failures_are_unique(self):
        health = Health()
        health.record_recovery_failure("firefox")
        health.record_recovery_failure("firefox")
        health.record_recovery_failure("steam")

        self.assertEqual(("firefox", "steam"), health.recovery_failures)
        self.assertIn("recovery-incomplete", health.problems)

    def test_to_dict_is_json_shaped(self):
        payload = Health(scan_failures=1, last_scan_error="boom").to_dict()

        self.assertEqual(1, payload["scan_failures"])
        self.assertEqual("boom", payload["last_scan_error"])
        self.assertFalse(payload["degraded"])
        self.assertIsInstance(payload["problems"], list)


if __name__ == "__main__":
    unittest.main()
