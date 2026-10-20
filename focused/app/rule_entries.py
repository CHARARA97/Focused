from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional

from ..core import guards
from ..core.rules import RuleProblem, RuleRejected, RuleSet

MAX_RULES_PER_SOURCE = 5000

LogFn = Callable[[str, str], None]


def urls_of(values: Any) -> List[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        raise RuleRejected([RuleProblem("rules.type", "url_patterns must be a sequence")])
    try:
        items = iter(values)
    except TypeError as exc:
        raise RuleRejected(
            [RuleProblem("rules.type", "url_patterns must be a sequence")]
        ) from exc
    out: List[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def entry_for(incoming: RuleSet, guards_by_pid: Dict[int, int], urls: Iterable[str]) -> Dict[str, Any]:
    return {
        "names": sorted(incoming.names),
        "cmdline_substrings": list(incoming.cmdline_substrings),
        "pids": sorted(incoming.pids),
        "pid_guards": dict(guards_by_pid),
        "protect_names": sorted(incoming.protect_names),
        "protect_cmdline_substrings": list(incoming.protect_cmdline_substrings),
        "url_patterns": [str(item) for item in urls if str(item).strip()],
    }


def guards_for(
    procfs: Any,
    log: Optional[LogFn],
    incoming: RuleSet,
    existing: Dict[int, Any],
    *,
    strict: bool,
    source: str,
    problems: Optional[List[RuleProblem]] = None,
) -> Dict[int, int]:
    problems = problems if problems is not None else []
    resolved: Dict[int, int] = {}
    for pid in sorted(incoming.pids):
        current = procfs.starttime(pid)
        problem = guards.judge(pid, existing.get(pid), current)
        if problem is not None:
            problems.append(problem)
            if guards.is_gone(problem) and not strict and log is not None:
                log("warning", "config pid %s is gone; the rule will fail closed" % pid)
            continue
        resolved[pid] = current
    return resolved


def prepare(
    payload: Dict[str, Any],
    current: Dict[str, Any],
    *,
    procfs: Any,
    log: Optional[LogFn],
    source: str,
) -> Dict[str, Any]:
    payload = payload or {}
    for key in (
        "names",
        "cmdline_substrings",
        "pids",
        "protect_names",
        "protect_cmdline_substrings",
    ):
        if isinstance(payload.get(key), (str, bytes)):
            raise RuleRejected(
                [RuleProblem("rules.type", "%s must be a sequence" % key)]
            )
    incoming = RuleSet.from_dict(payload)
    problems = list(incoming.problems())
    total = len(incoming.names) + len(incoming.cmdline_substrings) + len(incoming.pids)
    if total > MAX_RULES_PER_SOURCE:
        problems.append(
            RuleProblem(
                "rules.too-many",
                "a source may contain at most %d rules" % MAX_RULES_PER_SOURCE,
            )
        )

    current_guards = {
        int(pid): guard for pid, guard in (current.get("pid_guards") or {}).items()
    }
    kept: Dict[int, int] = {}
    for pid in sorted(incoming.pids):
        if pid <= 1:
            problems.append(
                RuleProblem("rule.pid-invalid", "pid 0/1 is never a target", str(pid))
            )
            continue
        if pid not in current_guards:
            continue
        provided = incoming.pid_guards.get(pid)
        if provided is not None and provided != current_guards[pid]:
            problems.append(
                RuleProblem(
                    "rule.pid-guard",
                    "guard conflicts with the existing source entry",
                    str(pid),
                )
            )
            continue
        kept[pid] = current_guards[pid]

    new_pids = frozenset(pid for pid in incoming.pids if pid not in current_guards)
    if new_pids:
        new_rule = RuleSet(
            pids=new_pids,
            pid_guards={
                pid: incoming.pid_guards[pid]
                for pid in new_pids
                if pid in incoming.pid_guards
            },
        )
        kept.update(
            guards_for(
                procfs,
                log,
                new_rule,
                dict(new_rule.pid_guards),
                strict=True,
                source=source,
                problems=problems,
            )
        )
    if problems:
        raise RuleRejected(problems)
    return entry_for(incoming, kept, urls_of(payload.get("url_patterns")))
