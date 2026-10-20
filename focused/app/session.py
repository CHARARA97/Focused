"""Frontend claims, the pause window, and the manual session they combine with."""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

from .claims import ClaimBook
from .manual_session import ManualSession


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
        self._stats = stats if stats is not None else {}
        self._audit = audit or (lambda _fields: None)
        self._log = log or (lambda _level, _message: None)
        self._lock = threading.RLock()
        self._claims = ClaimBook()
        self._manual = ManualSession(
            clock,
            session_config=session_config,
            stats=self._stats,
            audit=self._audit,
            log=self._log,
        )
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

    # -- what the orchestrator asks ----------------------------------------

    def manual_session_active(self) -> bool:
        return self._manual.exists()

    def session(self) -> Optional[Dict[str, Any]]:
        return self._manual.snapshot()

    def active(self) -> bool:
        return bool(self._manual.is_working() or self.delegated_active())

    def pause_until(self) -> float:
        with self._lock:
            return self._pause_until

    def session_since(self) -> float:
        started = self._manual.started_at()
        if started is not None:
            return started
        with self._lock:
            return self._session_since

    def paused(self) -> bool:
        with self._lock:
            return self._pause_until > self._clock()

    def note_active(self, active: bool) -> bool:
        with self._lock:
            rising = active and not self._was_active
            self._was_active = active
            if rising:
                self._session_since = self._clock()
            return rising

    def take_transition(self) -> Optional[str]:
        return self._manual.take_transition()

    # -- manual session mutations ------------------------------------------

    def start_session(
        self,
        minutes: Optional[float] = None,
        mode: Optional[str] = None,
        work_minutes: Optional[float] = None,
        break_minutes: Optional[float] = None,
        cycles: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self._manual.start_session(
            minutes=minutes,
            mode=mode,
            work_minutes=work_minutes,
            break_minutes=break_minutes,
            cycles=cycles,
        )

    def extend_session(self, minutes: float) -> Optional[Dict[str, Any]]:
        return self._manual.extend_session(minutes)

    def skip_break(self) -> Optional[Dict[str, Any]]:
        return self._manual.skip_break()

    def stop_session(self) -> bool:
        return self._manual.stop_session()

    def advance(self, now: Optional[float] = None) -> Optional[str]:
        return self._manual.advance(now)

    def session_state(self) -> Dict[str, Any]:
        return self._manual.state_view()

    def pause(self, seconds: float) -> float:
        seconds = max(0.0, float(seconds))
        self._manual.advance()
        with self._lock:
            now = self._clock()
            self._pause_until = now + seconds if seconds else 0.0
            until = self._pause_until
        self._manual.shift(seconds)
        self._audit({"kind": "pause", "seconds": seconds})
        return until
