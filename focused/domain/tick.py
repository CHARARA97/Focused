from __future__ import annotations

from dataclasses import dataclass

from .intent import Intent
from .policy import Policy


@dataclass(frozen=True)
class Tick:
    scan: int
    mono: float
    intent: Intent
    policy: Policy
