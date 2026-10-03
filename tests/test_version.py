"""One version, in two files that must agree.

The package version is read by people and by package managers (AUR's pkgver, a
wheel's filename).  When they disagree, the bug report says "0.1.0" and the wheel
says something else.
"""

import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import focused  # noqa: E402  (the package is importable from the repository root)


def project_version() -> str:
    with open(os.path.join(REPO, "pyproject.toml"), encoding="utf-8") as handle:
        text = handle.read()
    match = re.search(r'^version = "([^"]+)"', text, re.M)
    assert match, "no version in pyproject.toml"
    return match.group(1)


class VersionTests(unittest.TestCase):
    def test_the_package_and_the_project_file_agree(self):
        self.assertEqual(project_version(), focused.__version__)

    def test_the_aur_pkgver_matches(self):
        with open(os.path.join(REPO, "packaging", "aur", "focused", "PKGBUILD"), encoding="utf-8") as handle:
            text = handle.read()

        match = re.search(r"^pkgver=(.+)$", text, re.M)
        self.assertEqual(project_version(), match.group(1).strip())


if __name__ == "__main__":
    unittest.main()
