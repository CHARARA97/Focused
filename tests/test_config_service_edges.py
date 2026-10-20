"""ConfigService edge cases: failures must not half-apply."""

import os
import tempfile
import unittest

from focused.config import Config, FocusedConfigError
from focused.engine import FocusedEngine


def make_engine(tmp):
    config = Config.from_dict(
        {
            "audit_log": "",
            "enforcement": {"enabled": True, "mode": "audit"},
        }
    )
    return FocusedEngine(
        config,
        logger=None,
        uid=os.getuid(),
        self_pid=999_001,
    )


class ConfigServiceEdgeTests(unittest.TestCase):
    def test_a_rejected_blacklist_does_not_change_the_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(tmp)
            engine.config.enforcement.mode = "kill"

            with self.assertRaises(ValueError):
                engine.update_config(
                    {"enforcement": {"mode": "audit"}, "blacklist": {"pids": [999_999_999]}}
                )

            self.assertEqual("kill", engine.config.enforcement.mode)

    def test_switching_to_kill_needs_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(tmp)

            with self.assertRaises(ValueError):
                engine.update_config({"enforcement": {"mode": "kill"}})

            payload = engine.update_config(
                {"enforcement": {"mode": "kill"}, "confirm": "kill"}
            )

            self.assertEqual("kill", payload["enforcement"]["mode"])

    def test_scan_interval_must_be_finite(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(tmp)
            before = engine.config.scan_interval

            for value in (float("nan"), float("inf"), -1.0, 0.0):
                with self.assertRaises(ValueError):
                    engine.update_config({"scan_interval": value})
                self.assertEqual(before, engine.config.scan_interval)

    def test_config_file_rejects_non_finite_scan_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FocusedConfigError):
                Config.from_dict(
                    {
                        "audit_log": "",
                        "scan_interval": float("inf"),
                        "enforcement": {"enabled": True, "mode": "audit"},
                    }
                )

    def test_url_patterns_must_be_a_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(tmp)

            with self.assertRaises(ValueError):
                engine.update_config({"urls": {"patterns": "ab"}})


if __name__ == "__main__":
    unittest.main()
