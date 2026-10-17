"""The final gate before a process is suspended.

``evaluate()`` decides whether a process *matches* a rule; ``plan_freeze()``
decides how it would be suspended.  This module answers the last question:

    "Given everything we know, are we allowed to suspend it at all?"

The rule is one-directional: **if we cannot prove it is safe, we refuse.**  A
missing UID, an unknown start time, a protective rule, a shared unit, an empty
process tree -- each of them is a refusal with a stable reason code.  Suspending
nothing is always better than suspending the wrong thing.

The gate repeats the hard rails on purpose.  It is the last line of defence, so
a future refactor of ``evaluate`` or ``plan_freeze`` must not be able to bypass
them silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..core.procfs import ProcessInfo
from ..core.rules import ACTION_MATCH, Decision, EvalContext


@dataclass(frozen=True)
class SafetyVerdict:
    allow: bool
    reason: str
    rule: str = ""


def _refuse(reason: str, rule: str = "") -> SafetyVerdict:
    return SafetyVerdict(False, reason, rule)


def choose_mode(plan_mode: str, plan_reason: str, use_units: bool) -> str:
    """Turn a cgroup plan into the execution mode, or ``"skip"``.

    Most ``PLAN_SKIP`` reasons are safe to degrade to a process-tree freeze:
    there is no cgroup, the cgroup is not v2, or the unit is not a unit.  Two
    reasons are different: a slice and the user manager must not be touched at
    all, not even through the process tree of one of their members.
    """
    if plan_mode == "skip" and plan_reason in ("slice", "session-manager"):
        return "skip"
    if plan_mode == "unit" and use_units:
        return "unit"
    return "pid"


def check_safety(
    proc: ProcessInfo,
    decision: Decision,
    ctx: EvalContext,
    *,
    plan_mode: str,
    plan_reason: str,
    plan_pids: Sequence[int],
    guards_known: bool,
) -> SafetyVerdict:
    """Return whether ``proc`` may be suspended under this plan.

    ``guards_known`` means every PID in ``plan_pids`` has a start time in the
    snapshot that produced the target.  Without it a PID could be reused between
    the snapshot and the signal, and we would stop a stranger.
    """

    # -- hard rails: repeated deliberately (see module docstring) ----------
    if proc.pid <= 1:
        return _refuse("hard:pid<=1")
    if proc.is_zombie:
        return _refuse("hard:zombie")
    if proc.is_kernel_thread:
        return _refuse("hard:kernel-thread")
    if (
        proc.pid == ctx.self_pid
        or proc.pid == ctx.self_ppid
        or proc.pid in ctx.hard_protect_pids
    ):
        return _refuse("hard:self")
    if ctx.uid is None:
        # Without knowing which user we are, foreign-uid cannot be checked.
        return _refuse("unknown-daemon-uid")
    if proc.uid is None:
        # Unknown ownership means we cannot prove it is ours.  Refuse.
        return _refuse("unknown-uid")
    if ctx.uid is not None and proc.uid != ctx.uid:
        return _refuse("hard:foreign-uid")
    if (
        ctx.protect_own_process_group
        and ctx.self_pgid is not None
        and proc.pgid == ctx.self_pgid
    ):
        return _refuse("hard:own-process-group")

    # -- the decision itself ----------------------------------------------
    if decision.action != ACTION_MATCH:
        return _refuse("decision:" + decision.action, decision.rule)
    if decision.rule.startswith("protect."):
        return _refuse("protected", decision.rule)

    # -- the plan ----------------------------------------------------------
    if plan_mode == "skip":
        return _refuse("plan:" + plan_reason)
    if plan_mode == "unit" and plan_reason != "dedicated-app-unit":
        # Only the one plan that has been justified may freeze a whole cgroup.
        return _refuse("unit-plan:" + plan_reason)
    if not plan_pids:
        return _refuse("empty-tree")
    if not guards_known:
        return _refuse("unknown-starttime")

    return SafetyVerdict(True, "ok", decision.rule)
