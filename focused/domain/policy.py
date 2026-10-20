from __future__ import annotations

from dataclasses import dataclass

from .reasons import Mode


@dataclass(frozen=True)
class Policy:
    mode: Mode
    max_per_scan: int
    version: int


ENFORCEMENT_MODES = tuple(mode.value for mode in Mode)
