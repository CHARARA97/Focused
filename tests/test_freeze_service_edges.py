"""FreezeService failure paths: a failed thaw must not look successful."""

import os
import tempfile
import time
import unittest
from unittest import mock

from focused.config import Config
from focused.core.freezer import Frozen, MODE_PID
from focused.engine import FocusedEngine


class FreezeServiceEdgeTests(unittest.TestCase):
    def test_thaw_stale_does_not_forget_when_thaw_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config.from_dict(
                {
                    "audit_log": "",
                    "freeze": {"enabled": True, "state_path": os.path.join(tmp, "frozen.json")},
                }
            )
            engine = FocusedEngine(
                config,
                logger=None,
                uid=os.getuid(),
                self_pid=999_001,
                self_cgroup="/user.slice/x",
                unified_cgroup2=True,
            )
            service = engine._freeze_service
            pid = os.getpid()
            entry = Frozen(
                pid=pid,
                name="self-test",
                mode=MODE_PID,
                since=0.0,
                pids=(pid,),
                guards={pid: None},
            )
            service._freezer._entries[pid] = entry
            service._handled[pid] = (None, time.time() + 30)

            with mock.patch.object(service._freezer, "thaw", return_value=False):
                count = service.thaw_stale()

            self.assertEqual(0, count)
            self.assertIn(pid, service._freezer._entries)
            self.assertIn(pid, service._handled)


if __name__ == "__main__":
    unittest.main()
