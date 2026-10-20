from __future__ import annotations

from typing import Optional

from .rules import RuleProblem


def judge(
    pid: int, provided: Optional[int], current: Optional[int]
) -> Optional[RuleProblem]:
    if pid <= 1:
        return RuleProblem("rule.pid-invalid", "pid 0/1 is never a target", str(pid))
    if current is None:
        return RuleProblem("rule.pid-gone", "pid is gone or unreadable", str(pid))
    if provided is not None and provided != current:
        return RuleProblem(
            "rule.pid-reused",
            "starttime does not match the live process",
            str(pid),
        )
    return None


def is_gone(problem: Optional[RuleProblem]) -> bool:
    return problem is not None and problem.code == "rule.pid-gone"
