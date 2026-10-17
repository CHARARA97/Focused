"""--once defaults to thawing; --keep-frozen is the explicit opt-out."""

import os
import subprocess
import sys
import unittest


class OnceHelpTests(unittest.TestCase):
    def test_keep_frozen_is_documented(self):
        focusedd = os.path.join(os.path.dirname(os.path.dirname(__file__)), "focusedd.py")
        result = subprocess.run(
            [sys.executable, focusedd, "--help"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("usage: focused", result.stdout)
        self.assertIn("--keep-frozen", result.stdout)
        self.assertIn("thaw what it froze", result.stdout)

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
