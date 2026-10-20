from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

from ..domain import session_timer as machine
from ..domain.session_timer import DefaultSessionConfig, SessionState, Transition


class ManualSession:
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
        self._state: Optional[SessionState] = None
        self._transition: Optional[str] = None

    @property
    def state(self) -> Optional[SessionState]:
        with self._lock:
            return self._state

    def exists(self) -> bool:
        self.advance()
        with self._lock:
            return self._state is not None

    def is_working(self) -> bool:
        self.advance()
        with self._lock:
            state = self._state
            return bool(state) and state.is_working

    def started_at(self) -> Optional[float]:
        with self._lock:
            state = self._state
            return float(state.started_at) if state else None

    def snapshot(self) -> Optional[Dict[str, Any]]:
        self.advance()
        with self._lock:
            state = self._state
            return state.to_dict() if state else None

    def take_transition(self) -> Optional[str]:
        with self._lock:
            transition, self._transition = self._transition, None
            return transition

    def shift(self, seconds: float) -> None:
        with self._lock:
            self._state = machine.shift(self._state, seconds)

    def start_session(
        self,
        minutes: Optional[float] = None,
        mode: Optional[str] = None,
        work_minutes: Optional[float] = None,
        break_minutes: Optional[float] = None,
        cycles: Optional[int] = None,
    ) -> Dict[str, Any]:
        config = self._config()
        now = self._clock()
        result = self._run(
            lambda state: machine.start(
                state,
                config,
                now,
                minutes=minutes,
                mode=mode,
                work_minutes=work_minutes,
                break_minutes=break_minutes,
                cycles=cycles,
            )
        )
        return result.state.to_dict() if result.state else {}

    def extend_session(self, minutes: float) -> Optional[Dict[str, Any]]:
        self.advance()
        result = self._run(lambda state: machine.extend(state, minutes))
        return result.state.to_dict() if result.state else None

    def skip_break(self) -> Optional[Dict[str, Any]]:
        self.advance()
        result = self._run(lambda state: machine.skip_break(state, self._clock()))
        return result.state.to_dict() if result.state else None

    def stop_session(self) -> bool:
        self.advance()
        with self._lock:
            was = self._state is not None
        self._run(lambda state: machine.stop())
        return was

    def advance(self, now: Optional[float] = None) -> Optional[str]:
        moment = self._clock() if now is None else now
        return self._run(lambda state: machine.advance(state, moment)).transition

    def state_view(self) -> Dict[str, Any]:
        self.advance()
        with self._lock:
            state = self._state
        return machine.view(state, self._clock())

    def _config(self) -> Any:
        if self._session_config is None:
            return DefaultSessionConfig()
        return self._session_config()

    def _run(self, change: Callable[[Optional[SessionState]], Transition]) -> Transition:
        with self._lock:
            result = change(self._state)
            self._state = result.state
            if result.transition is not None:
                self._transition = result.transition
            if result.counts_session:
                self._stats["sessions"] = int(self._stats.get("sessions", 0)) + 1
        for record in result.audit:
            self._audit(record)
        for level, message in result.logs:
            self._log(level, message)
        return result
