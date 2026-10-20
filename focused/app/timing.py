from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Timeline:
    wall: Callable[[], float]
    monotonic: Callable[[], float]

    @classmethod
    def real(cls) -> "Timeline":
        return cls(wall=time.time, monotonic=time.monotonic)
