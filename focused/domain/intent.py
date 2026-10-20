from __future__ import annotations

from dataclasses import dataclass

from .reasons import Gate


@dataclass(frozen=True)
class Intent:
    enforcing: bool
    reason: Gate

    @classmethod
    def decide(
        cls, *, enabled: bool, active: bool, paused: bool, on_break: bool
    ) -> "Intent":
        if not enabled:
            return cls(False, Gate.DISABLED)
        if paused:
            return cls(False, Gate.PAUSED)
        if active:
            return cls(True, Gate.WORK)
        return cls(False, Gate.BREAK if on_break else Gate.NO_SESSION)
