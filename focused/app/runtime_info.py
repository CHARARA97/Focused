from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List


@dataclass(frozen=True)
class RuntimeInfo:
    pid: int
    version: str
    addons: Callable[[], List[str]]
    event_seq: Callable[[], int]
