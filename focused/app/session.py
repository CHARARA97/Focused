"""Owns every frontend session claim **and** the manual session state machine.

Two independent sources decide whether focus is active:

* a claim from a frontend (a lease, renewable, several may coexist);
* a session started inside Focused itself (countdown / pomodoro / stopwatch).

They are combined here so the engine only asks ``active()`` / ``session_state()``
and never manipulates the state directly.  Audit and logging stay callbacks: the
engine owns the single audit stream, the service owns the state.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import SESSION_MODES
from ..domain.claims import ClaimBook


class _DefaultSessionConfig:
    mode = "countdown"
    default_minutes = 25.0
    pomodoro_work_minutes = 25.0
    pomodoro_break_minutes = 5.0
    pomodoro_cycles = 4


class SessionService:
    def __init__(
        self,
        clock: Callable[[], float],
        *,
        session_config: Optional[Callable[[], Any]] = None,
        stats: Optional[Dict[str, Any]] = None,
        audit: Optional[Callable[[Dict[str, Any]], None]] = None,
        log: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._clock = clock
        self._session_config = session_config
        self._stats = stats if stats is not None else {}
        self._audit = audit or (lambda _fields: None)
        self._log = log or (lambda _level, _message: None)
        self._lock = threading.RLock()
        self._claims = ClaimBook()

        self._session: Optional[Dict[str, Any]] = None
        self._transition: Optional[str] = None
        self._pause_until = 0.0
        self._session_since = 0.0
        self._was_active = False

    # -- claims ------------------------------------------------------------

    def claim(self, source: str, now: float, ttl_seconds: float) -> bool:
        with self._lock:
            return self._claims.claim(source, now, ttl_seconds)

    def withdraw(self, source: str) -> bool:
        with self._lock:
            return self._claims.withdraw(source)

    def expire(self, now: float):
        with self._lock:
            return self._claims.expire(now)

    def live(self) -> Dict[str, float]:
        now = self._clock()
        with self._lock:
            return {
                source: claim.until
                for source, claim in self._claims.live(now).items()
            }

    def claims(self) -> Dict[str, Dict[str, float]]:
        with self._lock:
            return {
                source: {"until": claim.until, "ttl": claim.ttl}
                for source, claim in self._claims.all().items()
            }

    def delegated_active(self) -> bool:
        return bool(self.live())

    def delegated(self) -> bool:
        with self._lock:
            return bool(self._claims.all())

    def nearest_deadline(self) -> float:
        now = self._clock()
        with self._lock:
            return self._claims.nearest_deadline(now)

    def nearest_ttl(self) -> float:
        with self._lock:
            return self._claims.nearest_ttl()

    def describe(self) -> str:
        now = self._clock()
        with self._lock:
            return self._claims.describe(now)

    # -- manual session state ---------------------------------------------

    def manual_session_active(self) -> bool:
        self.advance()
        with self._lock:
            return self._session is not None

    def session(self) -> Optional[Dict[str, Any]]:
        self.advance()
        with self._lock:
            return dict(self._session) if self._session else None

    def active(self) -> bool:
        self.advance()
        with self._lock:
            session = self._session
            manual = bool(session) and session.get("phase", "work") == "work"
        return bool(manual or self.delegated_active())

    def pause_until(self) -> float:
        with self._lock:
            return self._pause_until

    def session_since(self) -> float:
        with self._lock:
            if self._session:
                return float(self._session.get("started_at") or 0.0)
            return self._session_since

    def paused(self) -> bool:
        with self._lock:
            return self._pause_until > self._clock()

    def note_active(self, active: bool) -> bool:
        """Record whether the combined session was active; return "rising"."""
        with self._lock:
            rising = active and not self._was_active
            self._was_active = active
            if rising:
                self._session_since = self._clock()
            return rising

    def take_transition(self) -> Optional[str]:
        with self._lock:
            transition, self._transition = self._transition, None
            return transition

    # -- manual session mutations -----------------------------------------

    def start_session(
        self,
        minutes: Optional[float] = None,
        mode: Optional[str] = None,
        work_minutes: Optional[float] = None,
        break_minutes: Optional[float] = None,
        cycles: Optional[int] = None,
    ) -> Dict[str, Any]:
        session_config = (
            self._session_config() if self._session_config is not None else _DefaultSessionConfig()
        )
        chosen = (mode or session_config.mode or "countdown").strip().lower()
        if chosen not in SESSION_MODES:
            raise ValueError("mode must be one of: %s" % ", ".join(SESSION_MODES))

        now = self._clock()
        audit: Dict[str, Any]
        message: str
        with self._lock:
            running = self._session

            # Asking for more time on a countdown is an extension, not a restart.
            if (
                running
                and chosen == running.get("mode")
                and minutes is not None
                and chosen == "countdown"
            ):
                running["until"] += max(1.0, float(minutes)) * 60.0
                self._stats["sessions"] = int(self._stats.get("sessions", 0)) + 1
                snapshot = dict(running)
                audit = {"kind": "session-extended", "until": snapshot["until"]}
                message = "session extended by %.1f minute(s)" % float(minutes)
            else:
                if chosen == "countdown":
                    duration = float(
                        minutes if minutes is not None else session_config.default_minutes
                    )
                    session = {
                        "mode": chosen,
                        "started_at": now,
                        "minutes": max(1.0, duration),
                        "phase": "work",
                        "until": now + max(1.0, duration) * 60.0,
                    }
                elif chosen == "pomodoro":
                    work = float(
                        work_minutes
                        if work_minutes is not None
                        else session_config.pomodoro_work_minutes
                    )
                    rest = float(
                        break_minutes
                        if break_minutes is not None
                        else session_config.pomodoro_break_minutes
                    )
                    rounds = int(
                        cycles
                        if cycles is not None
                        else session_config.pomodoro_cycles
                    )
                    session = {
                        "mode": chosen,
                        "started_at": now,
                        "phase": "work",
                        "phase_started": now,
                        "phase_until": now + max(1.0, work) * 60.0,
                        "work_minutes": max(1.0, work),
                        "break_minutes": max(1.0, rest),
                        "cycle": 1,
                        "cycles": max(0, rounds),
                        "until": 0.0,
                    }
                else:  # stopwatch
                    session = {
                        "mode": chosen,
                        "started_at": now,
                        "phase": "work",
                        "until": 0.0,
                    }

                self._session = session
                self._transition = "started"
                self._stats["sessions"] = int(self._stats.get("sessions", 0)) + 1
                snapshot = dict(session)
                audit = {"kind": "session-started", **snapshot}
                message = "session started (%s)" % snapshot["mode"] + (
                    " %g min" % snapshot.get("minutes", 0)
                    if snapshot["mode"] == "countdown"
                    else " %g/%g min x%s"
                    % (
                        snapshot.get("work_minutes", 0),
                        snapshot.get("break_minutes", 0),
                        snapshot.get("cycles") or "∞",
                    )
                    if snapshot["mode"] == "pomodoro"
                    else ""
                )
        self._audit(audit)
        self._log("info", message)
        return snapshot

    def extend_session(self, minutes: float) -> Optional[Dict[str, Any]]:
        minutes = max(1.0, float(minutes))
        self.advance()
        with self._lock:
            session = self._session
            if session is None:
                return None
            if session["mode"] == "stopwatch":
                return dict(session)
            if session["mode"] == "pomodoro":
                session["phase_until"] += minutes * 60.0
            else:
                session["until"] += minutes * 60.0
            snapshot = dict(session)
        self._audit({"kind": "session-extended", "minutes": minutes})
        self._log("info", "session extended by %g minute(s)" % minutes)
        return snapshot

    def skip_break(self) -> Optional[Dict[str, Any]]:
        self.advance()
        with self._lock:
            session = self._session
            if session is None or session["mode"] != "pomodoro" or session["phase"] != "break":
                return dict(session) if session else None
            now = self._clock()
            session["phase"] = "work"
            session["phase_started"] = now
            session["phase_until"] = now + session["work_minutes"] * 60.0
            self._transition = "work"
            snapshot = dict(session)
        self._audit({"kind": "session-phase", "phase": "work", "reason": "break-skipped"})
        self._log("info", "break skipped; back to work")
        return snapshot

    def stop_session(self) -> bool:
        self.advance()
        with self._lock:
            was = self._session is not None
            self._session = None
            self._transition = "ended"
        self._audit({"kind": "session-stopped"})
        return was

    def advance(self, now: Optional[float] = None) -> Optional[str]:
        """Move past due phase boundaries; idempotent and safe from read paths."""
        now = self._clock() if now is None else now
        actions: List[Tuple[Any, ...]] = []
        transition: Optional[str] = None
        with self._lock:
            session = self._session
            if session is None:
                return None

            if session["mode"] == "countdown":
                if session["until"] and session["until"] <= now:
                    self._session = None
                    self._transition = "ended"
                    actions.append(("audit", {"kind": "session-ended", "reason": "completed"}))
                    actions.append(("log", "info", "session finished"))
                    transition = "ended"
                else:
                    return None
            elif session["mode"] != "pomodoro":
                return None  # a stopwatch never ends by itself
            else:
                guards = 0
                while session.get("phase_until") and session["phase_until"] <= now:
                    guards += 1
                    if guards > 64:  # a long sleep must not loop forever
                        session["phase_until"] = now
                        break
                    if session["phase"] == "work":
                        if session["cycles"] and session["cycle"] >= session["cycles"]:
                            self._session = None
                            self._transition = "ended"
                            actions.append(
                                ("audit", {"kind": "session-ended", "reason": "completed"})
                            )
                            actions.append(
                                (
                                    "log",
                                    "info",
                                    "pomodoro finished: %d round(s)" % session["cycle"],
                                )
                            )
                            transition = "ended"
                            break
                        session["phase"] = "break"
                        session["phase_started"] = now
                        session["phase_until"] = now + session["break_minutes"] * 60.0
                        transition = "break"
                        actions.append(
                            (
                                "audit",
                                {"kind": "session-phase", "phase": "break", "cycle": session["cycle"]},
                            )
                        )
                        actions.append(
                            ("log", "info", "break for %g minute(s)" % session["break_minutes"])
                        )
                    else:
                        session["cycle"] += 1
                        if session["cycles"] and session["cycle"] > session["cycles"]:
                            self._session = None
                            self._transition = "ended"
                            actions.append(
                                ("audit", {"kind": "session-ended", "reason": "completed"})
                            )
                            actions.append(
                                (
                                    "log",
                                    "info",
                                    "pomodoro finished: %d round(s)" % (session["cycle"] - 1),
                                )
                            )
                            transition = "ended"
                            break
                        session["phase"] = "work"
                        session["phase_started"] = now
                        session["phase_until"] = now + session["work_minutes"] * 60.0
                        transition = "work"
                        actions.append(
                            (
                                "audit",
                                {"kind": "session-phase", "phase": "work", "cycle": session["cycle"]},
                            )
                        )
                        actions.append(("log", "info", "round %d started" % session["cycle"]))
                if transition:
                    self._transition = transition

        for action in actions:
            if action[0] == "audit":
                self._audit(action[1])
            else:
                self._log(action[1], action[2])
        return transition

    def session_state(self) -> Dict[str, Any]:
        self.advance()
        now = self._clock()
        with self._lock:
            session = dict(self._session) if self._session else None
        if session is None:
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
        mode = session["mode"]
        deadline = session.get("phase_until") or session.get("until") or 0.0
        return {
            "active": mode != "pomodoro" or session.get("phase") == "work",
            "mode": mode,
            "phase": session.get("phase", "work"),
            "started_at": float(session.get("started_at") or 0.0),
            "until": float(session.get("until") or 0.0),
            "phase_ends_at": float(deadline),
            "cycle": int(session.get("cycle") or 0),
            "cycles": int(session.get("cycles") or 0),
            "work_minutes": float(session.get("work_minutes") or 0.0),
            "break_minutes": float(session.get("break_minutes") or 0.0),
            "elapsed": max(0.0, now - float(session.get("started_at") or now)),
            "remaining": max(0.0, deadline - now) if deadline else 0.0,
        }

    def pause(self, seconds: float) -> float:
        seconds = max(0.0, float(seconds))
        self.advance()
        with self._lock:
            now = self._clock()
            self._pause_until = now + seconds if seconds else 0.0
            until = self._pause_until
            session = self._session
            if session is not None and seconds:
                if session["mode"] == "pomodoro" and session.get("phase_until"):
                    session["phase_until"] += seconds
                elif session.get("until"):
                    session["until"] += seconds
        self._audit({"kind": "pause", "seconds": seconds})
        return until
