"""The installed command is ``focused``, not a Python invocation."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-focused.sh"


class InstallerContractTests(unittest.TestCase):
    def test_the_installer_uses_focused_and_the_split_source_layout(self):
        text = SCRIPT.read_text(encoding="utf-8")

        self.assertIn('"$BIN_DIR/focused"', text)
        self.assertIn('ln -sf "$BIN_DIR/focused" "$BIN_DIR/focusedd"', text)
        self.assertIn('cp -r "$ROOT/focused" "$APP_DIR/focused"', text)
        self.assertIn('cp -f "$ROOT/focusedd.py"', text)
        self.assertNotIn('$ROOT/focused/focused', text)
        self.assertTrue(os.access(SCRIPT, os.X_OK), "install-focused.sh is not executable")

    def test_a_local_install_creates_the_focused_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env.update({
                "HOME": os.path.join(tmp, "home"),
                "XDG_CONFIG_HOME": os.path.join(tmp, "config"),
                "FOCUSED_APP_DIR": os.path.join(tmp, "app"),
                "FOCUSED_BIN_DIR": os.path.join(tmp, "bin"),
            })
            result = subprocess.run(
                [str(SCRIPT), "--no-service"],
                capture_output=True, text=True, timeout=120, env=env,
            )
            self.assertEqual(0, result.returncode, result.stderr)

            launcher = os.path.join(tmp, "bin", "focused")
            alias = os.path.join(tmp, "bin", "focusedd")
            self.assertTrue(os.path.exists(launcher))
            self.assertTrue(os.path.islink(alias))
            self.assertEqual(launcher, os.path.realpath(alias))

            version = subprocess.run(
                [launcher, "--version"],
                capture_output=True, text=True, timeout=30, env=env,
            )
            self.assertEqual(0, version.returncode, version.stderr)
            self.assertTrue(version.stdout.startswith("focused "), version.stdout)

            help_result = subprocess.run(
                [launcher, "--help"],
                capture_output=True, text=True, timeout=30, env=env,
            )
            self.assertEqual(0, help_result.returncode, help_result.stderr)
            self.assertIn("usage: focused", help_result.stdout)


if __name__ == "__main__":
    unittest.main()
