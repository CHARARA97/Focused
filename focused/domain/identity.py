from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Optional


@dataclass(frozen=True)
class Identity:
    pid: int
    ppid: int
    uid: Optional[int]

    @property
    def hard_protect_pids(self) -> FrozenSet[int]:
        return frozenset({self.pid, 0, 1})
