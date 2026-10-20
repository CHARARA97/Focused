from __future__ import annotations

import collections
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, MutableMapping, Optional, Set

from ..core.decision import ACTION_PROTECT, EvalContext, evaluate
from ..domain.preflight import check as preflight_check
from ..domain.reasons import Outcome
from ..domain.tick import Tick


@dataclass(frozen=True)
class Snapshot:
    rows: MutableMapping[int, Any]
    parents: Mapping[int, Optional[int]]


@dataclass(frozen=True)
class ScanResult:
    gate: str = ""
    mode: str = "audit"
    scan: int = 0
    killed: int = 0
    denied: int = 0
    throttled: int = 0
    by_reason: Mapping[str, int] = field(default_factory=dict)
    duration_ms: float = 0.0
    error: str = ""

    def to_wire(self) -> Dict[str, Any]:
        names = sorted(self.by_reason)
        return {
            "ok": True,
            "scan": self.scan,
            "gate": self.gate,
            "mode": self.mode,
            "killed": self.killed,
            "denied": self.denied,
            "throttled": self.throttled,
            "by_reason": {name: int(self.by_reason[name]) for name in names},
            "by_reason_names": names,
            "by_reason_counts": [int(self.by_reason[name]) for name in names],
            "duration_ms": round(self.duration_ms, 2),
            "error": self.error,
        }


def run_tick(
    tick: Tick,
    snapshot: Snapshot,
    *,
    rules: Any,
    evaluation: EvalContext,
    targeting: Any,
    enforcer: Any,
) -> ScanResult:
    if not tick.intent.enforcing:
        return ScanResult(
            gate=tick.intent.reason.value,
            mode=tick.policy.mode.value,
            scan=tick.scan,
        )

    by_reason: Dict[str, int] = collections.Counter()
    killed = denied = throttled = 0
    seen: Set[int] = set()

    for proc in snapshot.rows.values():
        decision = evaluate(proc, rules, evaluation)
        if decision.action == ACTION_PROTECT:
            if decision.rule.startswith("protect."):
                by_reason["protected"] += 1
            continue
        if not decision.should_kill:
            continue
        if proc.pid in seen:
            by_reason["seen"] += 1
            continue
        if killed >= tick.policy.max_per_scan:
            throttled += 1
            by_reason["throttled"] += 1
            continue

        candidate = targeting.resolve(
            proc, decision.rule, parents=snapshot.parents, rows=snapshot.rows
        )
        if candidate is None:
            by_reason["gone"] += 1
            continue

        verdict = preflight_check(candidate.selection, candidate.context)
        if not verdict.allow:
            denied += 1
            by_reason[verdict.deny_reason.value] += 1
            enforcer.deny(candidate.selection, verdict, tick)
            continue

        if enforcer.enforce(candidate.selection, tick=tick).kind is Outcome.KILLED:
            killed += 1
            seen.update(candidate.selection.pids)

    return ScanResult(
        gate="",
        mode=tick.policy.mode.value,
        scan=tick.scan,
        killed=killed,
        denied=denied,
        throttled=throttled,
        by_reason=dict(by_reason),
    )
