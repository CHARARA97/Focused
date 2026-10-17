"""Freeze/thaw orchestration.

The Freezer adapter still sends every signal; this service owns the policy around
it: plan the target, run the safety gate, refuse/skip with a reason, keep the
handled cache, thaw stale records and recover at startup.  It is the only place
that calls ``Freezer.freeze``/``thaw`` for a scan.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from ..core import cgroupfs
from ..core.freezer import Frozen, Target
from ..core.procfs import ProcessInfo
from ..core.rules import ACTION_PROTECT, Decision, EvalContext, evaluate
from ..diagnostics import log_event
from ..domain.safety import check_safety, choose_mode

HANDLED_TTL_SECONDS = 30.0


class FreezeService:
    def __init__(
        self,
        *,
        procfs: Any,
        freezer: Any,
        rules: Any,
        clock: Callable[[], float],
        uid: int,
        self_pid: int,
        self_cgroup: Optional[str],
        unified_cgroup2: bool,
        config: Any,
        health: Any,
        stats: Dict[str, Any],
        recent: Any,
        audit: Callable[[Dict[str, Any]], None],
        log: Callable[[str, str], None],
        logger: Any,
    ) -> None:
        self._procfs = procfs
        self._freezer = freezer
        self._rules = rules
        self._clock = clock
        self._uid = uid
        self._self_pid = self_pid
        self._self_cgroup = self_cgroup
        self._unified_cgroup2 = unified_cgroup2
        self._config = config
        self._health = health
        self._stats = stats
        self._recent = recent
        self._audit = audit
        self._log = log
        self._logger = logger
        self._handled: Dict[int, Tuple[Optional[int], float]] = {}

    # -- records -----------------------------------------------------------

    def frozen(self) -> List[Frozen]:
        return self._freezer.entries()

    def recorded_pids(self) -> List[int]:
        return self._freezer.recorded_pids()

    def stopped_pids(self) -> List[int]:
        return self._freezer.stopped_pids()

    def frozen_count(self) -> int:
        return self._freezer.frozen_count()

    def thaw_all(self, reason: str = "manual") -> int:
        self._freezer.ensure_loaded()
        entries = self._freezer.entries()
        count = self._freezer.thaw_all(reason)
        if entries:
            self._stats["thawed"] += count
            self._stats["thaw_failed"] += max(0, len(entries) - count)
            self._audit({"kind": "thaw", "count": count, "reason": reason})
            log_event(self._logger, "info", "thaw.ok", count=count, reason=reason)
        # A target that has just been resumed must be eligible for the next
        # scan immediately; a 30s "already handled" entry would silently skip it.
        self._handled.clear()
        return count

    def recover_frozen(self) -> int:
        entries = self._freezer.load()
        if not entries:
            return 0
        log_event(self._logger, "warning", "recovery.start", count=len(entries))
        settled = self.thaw_all("startup-recovery")
        remaining = self._freezer.entries()
        for entry in remaining:
            self._health.record_recovery_failure(entry.name or str(entry.pid))
        if remaining:
            log_event(
                self._logger,
                "error",
                "recovery.incomplete",
                count=len(remaining),
                names=", ".join(sorted(entry.name or str(entry.pid) for entry in remaining)),
            )
        return settled

    def close(self) -> int:
        return self.thaw_all("shutdown")

    def thaw_stale(self) -> int:
        """Resume targets that no longer match the current rules."""
        count = 0
        for entry in self._freezer.entries():
            proc = self._procfs.read(entry.pid)
            if proc is None:
                if self._freezer.thaw(entry, "process-gone"):
                    self._forget(entry)
                    count += 1
                continue
            decision = evaluate(proc, self._rules.merged(), self.eval_context())
            if decision.action == ACTION_PROTECT or not decision.should_kill:
                if self._freezer.thaw(entry, "no-longer-matches"):
                    self._forget(entry)
                    count += 1
        if count:
            self._stats["thawed"] += count
            self._log("info", "resumed %d target(s) that no longer match" % count)
        return count

    # -- target helpers ----------------------------------------------------

    def eval_context(self) -> EvalContext:
        return EvalContext(
            uid=self._uid,
            self_pid=self._self_pid,
            self_ppid=os.getppid(),
            # -1 can never match a real process group, so Focused never claims to
            # protect a group it does not own -- each target is judged on its own.
            self_pgid=-1,
            hard_protect_pids=frozenset({self._self_pid, 1, 0}),
            protect_own_process_group=False,
        )

    def is_handled(self, proc: ProcessInfo, now: float) -> bool:
        entry = self._handled.get(proc.pid)
        if entry is None:
            return False
        starttime, expiry = entry
        if now >= expiry:
            del self._handled[proc.pid]
            return False
        if starttime is not None and proc.starttime is not None and starttime != proc.starttime:
            del self._handled[proc.pid]
            return False
        return True

    def _forget(self, entry: Frozen) -> None:
        for pid in entry.pids or (entry.pid,):
            self._handled.pop(int(pid), None)

    def mark_handled(self, proc: ProcessInfo, now: float) -> None:
        self._handled[proc.pid] = (proc.starttime, now + HANDLED_TTL_SECONDS)
        if len(self._handled) > 4096:
            for pid in [p for p, (_, exp) in self._handled.items() if exp <= now]:
                del self._handled[pid]

    def freezable_pids(self, pids: Iterable[int]) -> List[int]:
        """The part of a process tree Focused may safely suspend."""
        out: List[int] = []
        for index, raw in enumerate(pids):
            pid = int(raw)
            if pid <= 1 or pid == self._self_pid:
                if index == 0:
                    return []
                continue
            out.append(pid)
        return out

    # -- one target --------------------------------------------------------

    def freeze_target(
        self,
        proc: ProcessInfo,
        parents: Dict[int, Optional[int]],
        starttimes: Dict[int, Optional[int]],
        decision: Decision,
        ctx: EvalContext,
        now: float,
        dry_run: bool,
    ) -> bool:
        """Plan, safety-check and execute one suspension."""
        rule = decision.rule
        if self._freezer.contains(proc.pid):
            # Already recorded.  Returning False avoids inflating frozen/recent
            # when a scan sees the same target after a restart or a race.
            self.mark_handled(proc, now)
            return False
        cgroup = self._procfs.cgroup(proc.pid)
        tree = cgroupfs.process_tree(proc.pid, parents) or (proc.pid,)
        plan = cgroupfs.plan_for(
            cgroup,
            self._self_cgroup,
            unified=self._unified_cgroup2,
            allowed_pids=set(tree),
        )
        mode = choose_mode(plan.plan, plan.reason, bool(self._config.freeze.use_units))

        pids = self.freezable_pids(tree)
        guards_known = all(starttimes.get(int(pid)) is not None for pid in pids)

        verdict = check_safety(
            proc,
            decision,
            ctx,
            plan_mode=mode,
            plan_reason=plan.reason,
            plan_pids=pids,
            guards_known=guards_known,
        )
        if not verdict.allow:
            self._stats["freeze_skipped"] += 1
            self.mark_handled(proc, now)
            self._audit(
                {
                    "kind": "freeze-refused",
                    "pid": proc.pid,
                    "name": proc.display_name(),
                    "reason": verdict.reason,
                    "rule": rule,
                    "mode": mode,
                    "plan": plan.reason,
                }
            )
            log_event(
                self._logger,
                "warning",
                "freeze.refuse",
                pid=proc.pid,
                name=proc.display_name(),
                reason=verdict.reason,
                rule=rule,
                mode=mode,
                plan=plan.reason,
            )
            return False

        if dry_run:
            self._audit(
                {
                    "kind": "dry-run",
                    "pid": proc.pid,
                    "name": proc.display_name(),
                    "mode": mode,
                    "rule": rule,
                }
            )
            log_event(
                self._logger,
                "info",
                "freeze.dry-run",
                pid=proc.pid,
                name=proc.display_name(),
                mode=mode,
                tree=len(pids),
                rule=rule,
            )
            self.mark_handled(proc, now)
            return False

        guard = {int(pid): starttimes.get(int(pid)) for pid in pids}
        entry = self._freezer.freeze(
            Target(
                pid=proc.pid,
                name=proc.display_name(),
                pids=tuple(pids),
                guard=guard,
                mode=mode,
                cgroup=cgroup or "",
                unit=plan.unit or "",
            ),
            reason=rule,
        )
        if entry is None:
            self._stats["freeze_skipped"] += 1
            self.mark_handled(proc, now)
            return False

        self._stats["frozen"] += 1
        self._recent.append(
            {
                "name": entry.name,
                "pid": entry.pid,
                "mode": entry.mode,
                "unit": entry.unit,
                "at": entry.since,
                "rule": rule,
            }
        )
        self._audit(
            {
                "kind": "freeze",
                "pid": entry.pid,
                "name": entry.name,
                "mode": entry.mode,
                "unit": entry.unit,
                "pids": list(entry.pids),
                "rule": rule,
            }
        )
        log_event(
            self._logger,
            "info",
            "freeze.ok",
            pid=entry.pid,
            name=entry.name,
            mode=entry.mode,
            unit=entry.unit or None,
            tree=len(entry.pids),
            rule=rule,
        )
        self.mark_handled(proc, now)
        return True
