"""The local Arch package must point at the published wheel and user service."""

import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCH = ROOT / "packaging" / "arch"
PKGBUILD = ARCH / "PKGBUILD"


class ArchPackageTests(unittest.TestCase):
    def test_pkgver_matches_the_application_version(self):
        from focused import __version__

        text = PKGBUILD.read_text(encoding="utf-8")
        match = re.search(r"^pkgver=(.+)$", text, re.M)

        self.assertIsNotNone(match)
        self.assertEqual(__version__, match.group(1).strip())

    def test_source_is_the_release_wheel_with_a_checksum(self):
        text = PKGBUILD.read_text(encoding="utf-8")

        self.assertIn("https://github.com/CHARARA97/Focused/releases/download/v${pkgver}", text)
        self.assertIn("focusd-${pkgver}-py3-none-any.whl", text)
        match = re.search(r"^sha256sums=\('([0-9a-f]{64})'\)$", text, re.M)
        self.assertIsNotNone(match, "missing a real wheel checksum")

    def test_package_installs_cli_service_and_helpers(self):
        text = PKGBUILD.read_text(encoding="utf-8")

        self.assertIn('depends=(\'bash\' \'python\')', text)
        self.assertIn('makedepends=(\'python-installer\')', text)
        self.assertIn('python -m installer', text)
        self.assertIn('/usr/lib/systemd/user/focused.service', text)
        self.assertIn('/usr/bin/focused-doctor', text)

    def test_service_uses_the_installed_focused_command(self):
        text = (ROOT / "packaging" / "focused.service").read_text(encoding="utf-8")

        self.assertIn("ExecStart=/usr/bin/focused", text)
        self.assertNotIn("--thaw-all", text)
        self.assertIn("WantedBy=default.target", text)

    def test_install_message_tells_the_user_to_enable_the_user_service(self):
        text = (ARCH / "focused.install").read_text(encoding="utf-8")

        self.assertIn("systemctl --user enable --now focused", text)

    def test_pkgbuild_parses(self):
        if not os.path.exists("/usr/bin/bash"):
            self.skipTest("bash is not installed")
        result = subprocess.run(
            ["bash", "-n", str(PKGBUILD)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_arch_readme_is_present(self):
        self.assertTrue((ARCH / "README.md").exists())
        self.assertTrue((ARCH / ".gitignore").exists())


if __name__ == "__main__":
    unittest.main()
