"""The doctor reads the same Health fields as the dashboard."""

import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "doctor-focused.sh"


class DoctorHealthTests(unittest.TestCase):
    def test_the_doctor_queries_status_health(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("/api/v1/status", text)
        self.assertIn("health", text)
        self.assertIn("scan_failures", text)
        self.assertIn("degraded", text)

    def test_the_script_still_parses(self):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not installed")
        result = subprocess.run(
            [bash, "-n", str(SCRIPT)], capture_output=True, text=True, timeout=10
        )
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
