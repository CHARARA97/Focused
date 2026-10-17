"""Credential-bearing JSON files are created 0600, not 0644."""

import os
import stat
import tempfile
import unittest

from focused.config import Config
from focused.core.freezer import Freezer
from focused.core.procfs import ProcFS
from focused.plugins import PluginRegistry


class FilePermissionTests(unittest.TestCase):
    def test_config_json_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            config = Config.from_dict(
                {
                    "audit_log": "",
                    "http": {"token": "secret"},
                    "freeze": {"state_path": os.path.join(tmp, "frozen.json")},
                }
            )
            config.save(path)

            self.assertEqual(0o600, stat.S_IMODE(os.stat(path).st_mode))

    def test_frozen_state_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "frozen.json")
            freezer = Freezer(procfs=ProcFS(), state_path=path, logger=None)
            freezer._save()

            self.assertEqual(0o600, stat.S_IMODE(os.stat(path).st_mode))

    def test_plugins_json_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "plugins.json")
            registry = PluginRegistry(path)
            registry.register(name="p", plugin_id="p")
            registry.save()

            self.assertEqual(0o600, stat.S_IMODE(os.stat(path).st_mode))


if __name__ == "__main__":
    unittest.main()
