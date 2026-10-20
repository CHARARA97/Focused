from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from ..core.killer import Killer
from .history import DenyBook, Episode, EpisodeBook
from ..domain.preflight import Verdict
from ..domain.tick import Tick
from ..domain.reasons import (
    ActionEvent,
    DenyReason,
    EventKind,
    KillResult,
    Mode,
    Outcome,
)
from ..domain.targets import Selection
from ..ports import Clock, Recorder

RESPAWN_STORM_THRESHOLD = 10


@dataclass(frozen=True)
class ActionOutcome:
    kind: Outcome
    deny_reason: Optional[DenyReason] = None
    results: Dict[int, KillResult] = field(default_factory=dict)


class Enforcer:
    def __init__(
        self,
        *,
        killer: Killer,
        reporting: Recorder,
        clock: Clock,
        history: EpisodeBook,
        denies: DenyBook,
    ) -> None:
        self._killer = killer
        self._reporting = reporting
        self._clock = clock
        self._history = history
        self._denies = denies

    def deny(self, selection: Selection, verdict: Verdict, tick: Tick) -> ActionOutcome:
        reason = verdict.deny_reason.value if verdict.deny_reason else "unknown"
        self._bump("denied")
        if self._denies.note(reason, selection.name, tick.mono):
            self._write(
                self._event(
                    EventKind.DENY,
                    selection,
                    tick,
                    reason=reason,
                    verdict="deny",
                )
            )
        return ActionOutcome(Outcome.DENIED, deny_reason=verdict.deny_reason)

    def enforce(self, selection: Selection, *, tick: Tick) -> ActionOutcome:
        if tick.policy.mode is not Mode.KILL:
            self._bump("would_kill")
            self._write(
                self._event(EventKind.WOULD_KILL, selection, tick, verdict="allow")
            )
            return ActionOutcome(Outcome.AUDIT)

        results = self._killer.kill_subtree(selection)
        self._bump("killed")
        self._report_failures(selection, results, tick)
        self._open_episode(selection, tick)
        return ActionOutcome(Outcome.KILLED, results=results)

    def sweep(self, tick: Tick) -> None:
        for episode in self._history.sweep(tick.mono):
            if episode.count <= 1:
                continue
            self._write(
                ActionEvent(
                    kind=EventKind.KILL_SUMMARY,
                    at=self._clock(),
                    mono=tick.mono,
                    scan=tick.scan,
                    name=episode.name,
                    rule=episode.rule,
                    count=episode.count,
                    duration_s=round(episode.duration_s, 3),
                )
            )
        for window in self._denies.sweep(tick.mono):
            if window.count <= 1:
                continue
            self._write(
                ActionEvent(
                    kind=EventKind.DENY_SUMMARY,
                    at=self._clock(),
                    mono=tick.mono,
                    scan=tick.scan,
                    name=window.name,
                    reason=window.reason,
                    count=window.count,
                    duration_s=round(window.duration_s, 3),
                )
            )

    def episodes(self) -> Tuple[Episode, ...]:
        return self._history.current()

    def _event(
        self, kind: EventKind, selection: Selection, tick: Tick, **extra: Any
    ) -> ActionEvent:
        return ActionEvent(
            kind=kind,
            at=self._clock(),
            mono=tick.mono,
            scan=tick.scan,
            name=selection.name,
            pid=selection.root,
            starttime=int(selection.starttime or 0),
            pids=selection.pids,
            tree=len(selection.pids),
            rule=selection.rule,
            intent=tick.intent.reason.value,
            policy=tick.policy.version,
            mode=tick.policy.mode.value,
            **extra,
        )

    def _report_failures(
        self, selection: Selection, results: Dict[int, KillResult], tick: Tick
    ) -> None:
        for pid, result in results.items():
            if result not in (KillResult.DENIED, KillResult.UNVERIFIED):
                continue
            self._reporting.kill_failure(selection.name)
            self._write(
                ActionEvent(
                    kind=EventKind.SIGNAL_FAILED,
                    at=self._clock(),
                    mono=tick.mono,
                    scan=tick.scan,
                    name=selection.name,
                    pid=int(pid),
                    starttime=int(selection.starttimes.get(int(pid), 0)),
                    rule=selection.rule,
                    reason=result.value,
                    result=result.value,
                    intent=tick.intent.reason.value,
                    policy=tick.policy.version,
                    mode=tick.policy.mode.value,
                )
            )

    def _open_episode(self, selection: Selection, tick: Tick) -> None:
        first = self._history.note(
            selection.name, selection.rule, selection.root, tick.mono
        )
        if first:
            self._write(self._event(EventKind.KILL, selection, tick, verdict="allow"))
        self._reporting.respawn(
            selection.name,
            self._history.count_of(selection.name),
            RESPAWN_STORM_THRESHOLD,
        )

    def _bump(self, key: str) -> None:
        self._reporting.bump(key)

    def _write(self, event: ActionEvent) -> None:
        self._reporting.write(event)
