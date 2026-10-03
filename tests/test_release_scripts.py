"""Shape checks for this repository's own scripts.

They are the first thing a user runs and a syntax error in one is a wall of bash
noise instead of an install, so they get checked rather than trusted.
"""

import os
import shutil
import subprocess
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class ScriptSyntaxTests(unittest.TestCase):
    def test_every_shell_script_parses(self):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not installed")

        failures = []
        for name in sorted(os.listdir(SCRIPTS)):
            if not name.endswith(".sh"):
                continue
            result = subprocess.run(
                [bash, "-n", os.path.join(SCRIPTS, name)], capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                failures.append("%s: %s" % (name, result.stderr.strip()))

        self.assertEqual([], failures)


class InstallerTests(unittest.TestCase):
    def test_the_installer_and_its_helpers_exist(self):
        for name in ("install.sh", "launch-focused.sh", "doctor-focused.sh", "run-tests.sh"):
            path = os.path.join(SCRIPTS, name)
            self.assertTrue(os.path.exists(path), name)
            self.assertTrue(os.access(path, os.X_OK), "%s is not executable" % name)

    def test_the_installer_never_needs_root(self):
        script = read(os.path.join(SCRIPTS, "install.sh"))

        self.assertNotIn("sudo ", script)
        self.assertIn("systemctl --user", script)

    def test_the_installer_works_standalone(self):
        # The README documents `curl .../install.sh | sh`.  Piped, there is no source
        # tree next to the script, so the installer has to fetch the release instead.
        script = read(os.path.join(SCRIPTS, "install.sh"))

        self.assertIn("latest_wheel", script)
        self.assertIn("python3 -m zipfile -e", script)
        self.assertIn("no source tree next to this script", script)

    def test_the_installer_keeps_an_existing_config(self):
        # Overwriting ~/.config/focused/config.json would silently re-arm a blacklist
        # the user had emptied, or the other way round.
        script = read(os.path.join(SCRIPTS, "install.sh"))

        self.assertIn("kept existing config", script)

    def test_the_service_unit_can_resume_after_a_crash(self):
        # systemd runs ExecStopPost even when the main process was killed: that is
        # what makes a suspended application unable to outlive Focused.
        unit = read(os.path.join(REPO, "packaging", "focused.service"))

        self.assertIn("ExecStopPost=", unit)
        self.assertIn("--thaw-all", unit)


class AurTests(unittest.TestCase):
    def test_both_pkgbuilds_exist_and_look_like_pkgbuilds(self):
        for name in ("focused", "focused-git"):
            text = read(os.path.join(REPO, "packaging", "aur", name, "PKGBUILD"))

            self.assertIn("pkgname=%s" % name, text)
            self.assertIn("license=('MIT')", text)
            self.assertIn("sha256sums=", text)

    def test_the_git_package_conflicts_with_the_release_one(self):
        text = read(os.path.join(REPO, "packaging", "aur", "focused-git", "PKGBUILD"))

        self.assertIn("conflicts=('focused')", text)
        self.assertIn("provides=('focused')", text)

    def test_the_release_package_installs_the_service_but_does_not_enable_it(self):
        text = read(os.path.join(REPO, "packaging", "aur", "focused", "PKGBUILD"))

        self.assertIn("/usr/lib/systemd/user/focused.service", text)
        self.assertNotIn("systemctl enable", text.split("package()")[1])
