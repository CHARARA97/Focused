from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, Optional, Tuple

SESSION_MODES = ("countdown", "pomodoro", "stopwatch")

MAX_CATCH_UP_STEPS = 64


@dataclass(frozen=True)
class SessionState:
    mode: str
    started_at: float
    phase: str = "work"
    until: float = 0.0
    phase_started: float = 0.0
    phase_until: float = 0.0
    work_minutes: float = 0.0
    break_minutes: float = 0.0
    cycle: int = 0
    cycles: int = 0
    minutes: float = 0.0

    @property
    def is_working(self) -> bool:
        return self.mode != "pomodoro" or self.phase == "work"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "started_at": self.started_at,
            "phase": self.phase,
            "until": self.until,
            "phase_started": self.phase_started,
            "phase_until": self.phase_until,
            "work_minutes": self.work_minutes,
            "break_minutes": self.break_minutes,
            "cycle": self.cycle,
            "cycles": self.cycles,
            "minutes": self.minutes,
        }


@dataclass(frozen=True)
class Transition:
    state: Optional[SessionState]
    transition: Optional[str] = None
    audit: Tuple[Dict[str, Any], ...] = ()
    logs: Tuple[Tuple[str, str], ...] = ()
    counts_session: bool = False


class DefaultSessionConfig:
    mode = "countdown"
    default_minutes = 25.0
    pomodoro_work_minutes = 25.0
    pomodoro_break_minutes = 5.0
    pomodoro_cycles = 4


def _log(level: str, message: str) -> Tuple[str, str]:
    return (level, message)


def start(
    current: Optional[SessionState],
    config: Any,
    now: float,
    *,
    minutes: Optional[float] = None,
    mode: Optional[str] = None,
    work_minutes: Optional[float] = None,
    break_minutes: Optional[float] = None,
    cycles: Optional[int] = None,
) -> Transition:
    chosen = (mode or config.mode or "countdown").strip().lower()
    if chosen not in SESSION_MODES:
        raise ValueError("mode must be one of: %s" % ", ".join(SESSION_MODES))

    if (
        current is not None
        and chosen == current.mode
        and minutes is not None
        and chosen == "countdown"
    ):
        state = replace(current, until=current.until + max(1.0, float(minutes)) * 60.0)
        return Transition(
            state=state,
            audit=({"kind": "session-extended", "until": state.until},),
            logs=(_log("info", "session extended by %.1f minute(s)" % float(minutes)),),
            counts_session=True,
        )

    if chosen == "countdown":
        duration = max(
            1.0, float(minutes if minutes is not None else config.default_minutes)
        )
        state = SessionState(
            mode=chosen,
            started_at=now,
            minutes=duration,
            until=now + duration * 60.0,
        )
    elif chosen == "pomodoro":
        work = max(
            1.0,
            float(
                work_minutes if work_minutes is not None else config.pomodoro_work_minutes
            ),
        )
        rest = max(
            1.0,
            float(
                break_minutes
                if break_minutes is not None
                else config.pomodoro_break_minutes
            ),
        )
        rounds = max(
            0, int(cycles if cycles is not None else config.pomodoro_cycles)
        )
        state = SessionState(
            mode=chosen,
            started_at=now,
            phase_started=now,
            phase_until=now + work * 60.0,
            work_minutes=work,
            break_minutes=rest,
            cycle=1,
            cycles=rounds,
        )
    else:
        state = SessionState(mode=chosen, started_at=now)

    return Transition(
        state=state,
        transition="started",
        audit=({"kind": "session-started", **state.to_dict()},),
        logs=(_log("info", _describe_start(state)),),
        counts_session=True,
    )


def _describe_start(state: SessionState) -> str:
    if state.mode == "countdown":
        return "session started (countdown) %g min" % state.minutes
    if state.mode == "pomodoro":
        return "session started (pomodoro) %g/%g min x%s" % (
            state.work_minutes,
            state.break_minutes,
            state.cycles or "∞",
        )
    return "session started (stopwatch)"


def extend(state: Optional[SessionState], minutes: float) -> Transition:
    if state is None:
        return Transition(state=None)
    minutes = max(1.0, float(minutes))
    if state.mode == "stopwatch":
        return Transition(state=state)
    if state.mode == "pomodoro":
        updated = replace(state, phase_until=state.phase_until + minutes * 60.0)
    else:
        updated = replace(state, until=state.until + minutes * 60.0)
    return Transition(
        state=updated,
        audit=({"kind": "session-extended", "minutes": minutes},),
        logs=(_log("info", "session extended by %g minute(s)" % minutes),),
    )


def skip_break(state: Optional[SessionState], now: float) -> Transition:
    if state is None:
        return Transition(state=None)
    if state.mode != "pomodoro" or state.phase != "break":
        return Transition(state=state)
    updated = replace(
        state,
        phase="work",
        phase_started=now,
        phase_until=now + state.work_minutes * 60.0,
    )
    return Transition(
        state=updated,
        transition="work",
        audit=({"kind": "session-phase", "phase": "work", "reason": "break-skipped"},),
        logs=(_log("info", "break skipped; back to work"),),
    )


def stop() -> Transition:
    return Transition(state=None, transition="ended", audit=({"kind": "session-stopped"},))


def shift(state: Optional[SessionState], seconds: float) -> Optional[SessionState]:
    seconds = max(0.0, float(seconds))
    if state is None or not seconds:
        return state
    if state.mode == "pomodoro" and state.phase_until:
        return replace(state, phase_until=state.phase_until + seconds)
    if state.until:
        return replace(state, until=state.until + seconds)
    return state


def advance(state: Optional[SessionState], now: float) -> Transition:
    if state is None:
        return Transition(state=None)

    if state.mode == "countdown":
        if state.until and state.until <= now:
            return Transition(
                state=None,
                transition="ended",
                audit=({"kind": "session-ended", "reason": "completed"},),
                logs=(_log("info", "session finished"),),
            )
        return Transition(state=state)

    if state.mode != "pomodoro":
        return Transition(state=state)

    audits = []
    logs = []
    transition: Optional[str] = None
    current = state
    steps = 0
    while current.phase_until and current.phase_until <= now:
        steps += 1
        if steps > MAX_CATCH_UP_STEPS:
            current = replace(current, phase_until=now)
            break
        if current.phase == "work":
            if current.cycles and current.cycle >= current.cycles:
                return Transition(
                    state=None,
                    transition="ended",
                    audit=tuple(audits)
                    + ({"kind": "session-ended", "reason": "completed"},),
                    logs=tuple(logs)
                    + (
                        _log(
                            "info",
                            "pomodoro finished: %d round(s)" % current.cycle,
                        ),
                    ),
                )
            current = replace(
                current,
                phase="break",
                phase_started=now,
                phase_until=now + current.break_minutes * 60.0,
            )
            transition = "break"
            audits.append(
                {"kind": "session-phase", "phase": "break", "cycle": current.cycle}
            )
            logs.append(
                _log("info", "break for %g minute(s)" % current.break_minutes)
            )
        else:
            cycle = current.cycle + 1
            if current.cycles and cycle > current.cycles:
                return Transition(
                    state=None,
                    transition="ended",
                    audit=tuple(audits)
                    + ({"kind": "session-ended", "reason": "completed"},),
                    logs=tuple(logs)
                    + (
                        _log(
                            "info",
                            "pomodoro finished: %d round(s)" % (cycle - 1),
                        ),
                    ),
                )
            current = replace(
                current,
                cycle=cycle,
                phase="work",
                phase_started=now,
                phase_until=now + current.work_minutes * 60.0,
            )
            transition = "work"
            audits.append(
                {"kind": "session-phase", "phase": "work", "cycle": cycle}
            )
            logs.append(_log("info", "round %d started" % cycle))

    return Transition(
        state=current,
        transition=transition,
        audit=tuple(audits),
        logs=tuple(logs),
    )


def view(state: Optional[SessionState], now: float) -> Dict[str, Any]:
    if state is None:
        return {
            "active": False,
            "mode": "",
            "phase": "",
            "started_at": 0.0,
            "until": 0.0,
            "phase_ends_at": 0.0,
            "cycle": 0,
            "cycles": 0,
            "work_minutes": 0.0,
            "break_minutes": 0.0,
            "elapsed": 0.0,
            "remaining": 0.0,
        }
    deadline = state.phase_until or state.until or 0.0
    return {
        "active": state.is_working,
        "mode": state.mode,
        "phase": state.phase,
        "started_at": float(state.started_at),
        "until": float(state.until),
        "phase_ends_at": float(deadline),
        "cycle": int(state.cycle),
        "cycles": int(state.cycles),
        "work_minutes": float(state.work_minutes),
        "break_minutes": float(state.break_minutes),
        "elapsed": max(0.0, now - float(state.started_at)),
        "remaining": max(0.0, deadline - now) if deadline else 0.0,
    }
