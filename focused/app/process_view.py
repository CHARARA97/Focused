from __future__ import annotations

import os
from typing import Any, Dict, List

from ..core.decision import ACTION_PROTECT, EvalContext, evaluate
from .icons import ProcessIcons


class ProcessView:
    def __init__(
        self,
        *,
        procfs: Any,
        rules: Any,
        uid: Any,
        self_pid: int,
        icons: ProcessIcons = None,
    ) -> None:
        self._procfs = procfs
        self._rules = rules
        self._uid = uid
        self._self_pid = self_pid
        self._icons = icons or ProcessIcons()

    def list(self, limit: int = 400) -> List[Dict[str, Any]]:
        counts: Dict[str, int] = {}
        representatives: Dict[str, object] = {}
        shielded: Dict[str, str] = {}
        rules = self._rules.merged()
        context = self._context()
        for proc in self._procfs.snapshot():
            if proc.is_kernel_thread or proc.is_zombie:
                continue
            if self._uid is not None and proc.uid is not None and proc.uid != self._uid:
                continue
            name = proc.comm or proc.exe_basename
            if not name:
                continue
            counts[name] = counts.get(name, 0) + 1
            representatives.setdefault(name, proc)
            if name not in shielded:
                decision = evaluate(proc, rules, context)
                if decision.action == ACTION_PROTECT:
                    shielded[name] = decision.rule
        items = [
            {
                "name": name,
                "count": counts[name],
                "protected": name in shielded,
                "protect_rule": shielded.get(name, ""),
                "exe": getattr(representatives.get(name), "exe", "") or "",
            }
            for name in sorted(counts)
        ]
        return items[: max(1, int(limit or 400))]

    def arrays(self, limit: int = 400) -> Dict[str, Any]:
        items = self.list(limit)
        return {
            "names": [item["name"] for item in items],
            "counts": [int(item["count"]) for item in items],
            "protected_flags": [bool(item["protected"]) for item in items],
            "protect_rules": [item["protect_rule"] for item in items],
            "icons": [
                self._icons.icon_for(item["name"], item.get("exe", "")) for item in items
            ],
        }

    def _context(self) -> EvalContext:
        return EvalContext(
            uid=self._uid,
            self_pid=self._self_pid,
            self_ppid=os.getppid(),
            self_pgid=-1,
            hard_protect_pids=frozenset({self._self_pid, 1, 0}),
            protect_own_process_group=False,
        )
