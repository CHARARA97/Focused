from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class Health:
    scan_failures: int = 0
    last_scan_error: str = ""
    degraded_after: int = 3
    kill_failures: int = 0
    respawn_storm: Tuple[str, ...] = ()
    enforcement_enabled: bool = True

    @property
    def degraded(self) -> bool:
        return self.scan_failures >= max(1, self.degraded_after)

    @property
    def problems(self) -> Tuple[str, ...]:
        found: List[str] = []
        if self.degraded:
            found.append("scan-degraded")
        if not self.enforcement_enabled:
            found.append("enforcement-disabled")
        if self.kill_failures:
            found.append("kill-failures")
        if self.respawn_storm:
            found.append("respawn-storm")
        return tuple(found)

    def record_scan_failure(self, error: str) -> None:
        self.scan_failures += 1
        self.last_scan_error = error

    def record_scan_success(self) -> None:
        self.scan_failures = 0
        self.last_scan_error = ""

    def record_kill_failure(self, name: str) -> None:
        self.kill_failures += 1

    def note_respawn(self, name: str, count: int, threshold: int) -> None:
        if not name or count <= threshold or name in self.respawn_storm:
            return
        self.respawn_storm = self.respawn_storm + (name,)

    def to_dict(self) -> dict:
        return {
            "degraded": self.degraded,
            "scan_failures": self.scan_failures,
            "last_scan_error": self.last_scan_error,
            "enforcement_enabled": self.enforcement_enabled,
            "kill_failures": self.kill_failures,
            "respawn_storm": list(self.respawn_storm),
            "problems": list(self.problems),
        }
