"""Health: the one place that answers "is enforcement actually working?".

A tool that freezes processes must never look healthy while it is doing nothing.
Every failure that can stop enforcement is recorded here, and the status/UI/
doctor all read the same object instead of grepping logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class Health:
    """Mutable health record owned by the engine.

    Reads are simple attribute accesses; the engine's scan thread is the only
    writer, and the values are scalars/a tuple, so a lock here would add noise
    without buying anything.
    """

    scan_failures: int = 0
    last_scan_error: str = ""
    degraded_after: int = 3
    recovery_failures: Tuple[str, ...] = ()
    freeze_enabled: bool = True

    @property
    def degraded(self) -> bool:
        return self.scan_failures >= max(1, self.degraded_after)

    @property
    def problems(self) -> Tuple[str, ...]:
        found: List[str] = []
        if self.degraded:
            found.append("scan-degraded")
        if not self.freeze_enabled:
            found.append("freeze-disabled")
        if self.recovery_failures:
            found.append("recovery-incomplete")
        return tuple(found)

    def record_scan_failure(self, error: str) -> None:
        self.scan_failures += 1
        self.last_scan_error = error

    def record_scan_success(self) -> None:
        self.scan_failures = 0
        self.last_scan_error = ""

    def record_recovery_failure(self, name: str) -> None:
        if name and name not in self.recovery_failures:
            self.recovery_failures = self.recovery_failures + (name,)

    def to_dict(self) -> dict:
        return {
            "degraded": self.degraded,
            "scan_failures": self.scan_failures,
            "last_scan_error": self.last_scan_error,
            "freeze_enabled": self.freeze_enabled,
            "recovery_failures": list(self.recovery_failures),
            "problems": list(self.problems),
        }
