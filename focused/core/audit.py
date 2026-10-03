"""Append-only JSONL audit trail.

Every decision that reaches the kill path is written here, including dry runs.
A tool that terminates other people's processes owes the user a receipt.

Audit failures never break enforcement: if the log cannot be written the error
is reported once through the logger and auditing is disabled for the session.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict


class AuditLog:
    def __init__(
        self,
        path: str,
        enabled: bool = True,
        logger=None,
        clock=time.time,
    ) -> None:
        self.path = os.path.expanduser(path) if path else ""
        self.enabled = bool(enabled and self.path)
        self._logger = logger
        self._clock = clock
        self._lock = threading.Lock()

    def _log(self, level: str, message: str) -> None:
        if self._logger is not None:
            getattr(self._logger, level, self._logger.info)(message)

    def write(self, record: Dict[str, Any]) -> bool:
        """Append one record.  Returns True when it reached the disk."""
        if not self.enabled:
            return False
        payload = dict(record)
        payload.setdefault("ts", self._clock())
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._lock:
            try:
                parent = os.path.dirname(self.path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                # 0600: the audit trail names every application the user runs.
                fd = os.open(
                    self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
                )
                try:
                    os.write(fd, (line + "\n").encode("utf-8"))
                finally:
                    os.close(fd)
                return True
            except OSError as exc:
                self._log(
                    "warning",
                    "audit log disabled: cannot write %s (%s)" % (self.path, exc),
                )
                self.enabled = False
                return False

    def close(self) -> None:
        self.enabled = False
