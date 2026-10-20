from __future__ import annotations

from typing import Any, Callable, Dict

from ..diagnostics import log_event
from ..domain.reasons import ActionEvent, EventKind

JOURNAL_LEVEL = {
    EventKind.GATE: "info",
    EventKind.DENY: "warning",
    EventKind.DENY_SUMMARY: "warning",
    EventKind.WOULD_KILL: "info",
    EventKind.KILL: "info",
    EventKind.KILL_SUMMARY: "info",
    EventKind.SIGNAL_FAILED: "error",
}


class Reporting:
    def __init__(
        self,
        *,
        audit_log: Callable[[], Any],
        emit: Callable[..., Any],
        logger: Any,
        health: Any,
        stats: Dict[str, int],
    ) -> None:
        self._audit_log = audit_log
        self._emit = emit
        self._logger = logger
        self._health = health
        self._stats = stats

    def write(self, event: ActionEvent) -> None:
        payload = event.fields()
        self.record(payload)
        log_event(
            self._logger,
            JOURNAL_LEVEL.get(event.kind, "info"),
            payload.get("kind") or "event",
            **{key: value for key, value in payload.items() if key != "kind"},
        )

    def record(self, payload: Dict[str, Any]) -> None:
        log = self._audit_log()
        if log is not None:
            log.write(payload)
        kind = payload.get("kind") or "event"
        fields = {key: value for key, value in payload.items() if key != "kind"}
        self._emit(kind, **fields)

    def journal(self, level: str, kind: str, **fields: Any) -> None:
        log_event(self._logger, level, kind, **fields)

    def log(self, level: str, message: str) -> None:
        if self._logger is None:
            return
        getattr(self._logger, level, self._logger.info)(message)

    def bump(self, key: str) -> None:
        self._stats[key] = int(self._stats.get(key) or 0) + 1

    def kill_failure(self, name: str) -> None:
        self._health.record_kill_failure(name)
        self.bump("kill_failed")

    def respawn(self, name: str, count: int, threshold: int) -> None:
        self._health.note_respawn(name, count, threshold)

    def scan_ok(self) -> None:
        self._health.record_scan_success()

    def scan_failed(self, exc: BaseException) -> None:
        error = "%s: %s" % (type(exc).__name__, exc)
        self._health.record_scan_failure(error)
        self.log(
            "error",
            "scan failed: %s (event=scan.fail failures=%d)"
            % (error, self._health.scan_failures),
        )
        if self._health.scan_failures == self._health.degraded_after:
            self.journal(
                "error",
                EventKind.SCAN_DEGRADED.value,
                failures=self._health.scan_failures,
                error=error,
            )
