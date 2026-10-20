"""The CLI documents the audit switch and the single-scan mode."""

import os
import subprocess
import sys
import unittest


class OnceHelpTests(unittest.TestCase):
    def test_audit_and_once_are_documented(self):
        focusedd = os.path.join(os.path.dirname(os.path.dirname(__file__)), "focusedd.py")
        result = subprocess.run(
            [sys.executable, focusedd, "--help"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("usage: focused", result.stdout)
        self.assertIn("--audit", result.stdout)
        self.assertIn("--once", result.stdout)
        self.assertNotIn("--thaw-all", result.stdout)

    def test_version_uses_the_focused_command_name(self):
        focusedd = os.path.join(os.path.dirname(os.path.dirname(__file__)), "focusedd.py")
        result = subprocess.run(
            [sys.executable, focusedd, "--version"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(result.stdout.startswith("focused "), result.stdout)


if __name__ == "__main__":
    unittest.main()
