from __future__ import annotations

from typing import List, Optional, Set, Tuple

from ..ports import ProcessReader


def live_subtree(
    root: int,
    reader: ProcessReader,
    *,
    fallback: Optional[Tuple[int, ...]] = None,
    max_nodes: int = 512,
) -> Tuple[int, ...]:
    if not reader.supports_live_children():
        return tuple(fallback) if fallback else (int(root),)

    order: List[int] = []
    seen: Set[int] = set()
    queue: List[int] = [int(root)]
    while queue and len(order) < max_nodes:
        pid = queue.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        order.append(pid)
        for child in reader.child_pids(pid):
            if int(child) not in seen:
                queue.append(int(child))
    return tuple(order)
