from __future__ import annotations

import os
import signal
from typing import Any, Dict, Optional

from ..domain.reasons import KillResult
from ..domain.targets import Selection
from ..ports import ProcessReader, SignalSender


class OsSignalSender:
    def send(self, pid: int, signum: int) -> None:
        os.kill(int(pid), int(signum))


class Killer:
    def __init__(
        self, *, sender: SignalSender, reader: ProcessReader, logger: Any = None
    ) -> None:
        self._sender = sender
        self._reader = reader
        self._logger = logger

    def kill_if_same(self, pid: int, expected_starttime: int) -> KillResult:
        current = self._reader.starttime(pid)
        if current is None:
            return KillResult.GONE
        if current != expected_starttime:
            return KillResult.REUSED
        try:
            self._sender.send(pid, signal.SIGKILL)
        except ProcessLookupError:
            return KillResult.GONE
        except PermissionError:
            return KillResult.DENIED
        return KillResult.SENT

    def kill_subtree(self, selection: Selection) -> Dict[int, KillResult]:
        results: Dict[int, KillResult] = {}
        for pid in selection.pids:
            expected: Optional[int] = selection.starttimes.get(int(pid))
            if expected is None:
                results[int(pid)] = KillResult.UNVERIFIED
                if int(pid) == selection.root:
                    break
                continue
            result = self.kill_if_same(int(pid), int(expected))
            results[int(pid)] = result
            if int(pid) == selection.root and result in (
                KillResult.REUSED,
                KillResult.DENIED,
            ):
                break
        return results
