from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class ProcessFacts:
    pid: int
    uid: Optional[int]
    name: str
    is_zombie: bool
    is_kernel_thread: bool


@dataclass(frozen=True)
class StarttimeCheck:
    ok: bool
    starttimes: Mapping[int, int]
    missing: Tuple[int, ...]


@dataclass(frozen=True)
class Selection:
    facts: ProcessFacts
    pids: Tuple[int, ...]
    starttimes: Mapping[int, int]
    rule: str

    @property
    def root(self) -> int:
        return self.facts.pid

    @property
    def name(self) -> str:
        return self.facts.name

    @property
    def starttime(self) -> Optional[int]:
        return self.starttimes.get(self.root)


def subtree(root: int, parents: Mapping[int, Optional[int]]) -> Tuple[int, ...]:
    children: Dict[int, List[int]] = {}
    for pid, ppid in parents.items():
        if ppid is None:
            continue
        children.setdefault(int(ppid), []).append(int(pid))
    for siblings in children.values():
        siblings.sort()

    order: List[int] = []
    seen = {int(root)}
    queue = [int(root)]
    while queue:
        pid = queue.pop(0)
        order.append(pid)
        for child in children.get(pid, ()):
            if child in seen:
                continue
            seen.add(child)
            queue.append(child)
    return tuple(order)


def ancestors(pid: int, parents: Mapping[int, Optional[int]]) -> FrozenSet[int]:
    chain = set()
    current = parents.get(int(pid))
    while current is not None and int(current) > 0 and int(current) not in chain:
        chain.add(int(current))
        current = parents.get(int(current))
    return frozenset(chain)


def check_starttimes(
    pids: Sequence[int], starttimes: Mapping[int, Optional[int]]
) -> StarttimeCheck:
    resolved: Dict[int, int] = {}
    missing: List[int] = []
    for pid in pids:
        value = starttimes.get(int(pid))
        if value is None:
            missing.append(int(pid))
        else:
            resolved[int(pid)] = int(value)
    return StarttimeCheck(ok=not missing, starttimes=resolved, missing=tuple(missing))
