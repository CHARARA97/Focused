"""Health: the single answer to "is enforcement actually working?"."""

import unittest

from focused.app.health import Health


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

    def test_disabled_enforcement_is_a_problem(self):
        health = Health(enforcement_enabled=False)

        self.assertIn("enforcement-disabled", health.problems)

    def test_a_respawn_storm_is_reported_once_per_name(self):
        health = Health()
        health.note_respawn("firefox", 11, 10)
        health.note_respawn("firefox", 12, 10)
        health.note_respawn("steam", 11, 10)

        self.assertEqual(("firefox", "steam"), health.respawn_storm)
        self.assertIn("respawn-storm", health.problems)

    def test_a_kill_failure_is_a_problem(self):
        health = Health()
        health.record_kill_failure("firefox")

        self.assertEqual(1, health.kill_failures)
        self.assertIn("kill-failures", health.problems)

    def test_to_dict_is_json_shaped(self):
        payload = Health(scan_failures=1, last_scan_error="boom").to_dict()

        self.assertEqual(1, payload["scan_failures"])
        self.assertEqual("boom", payload["last_scan_error"])
        self.assertFalse(payload["degraded"])
        self.assertIsInstance(payload["problems"], list)


if __name__ == "__main__":
    unittest.main()
