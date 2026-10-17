"""Coalesced, rate-limited scan requests from plugins.

A plugin may ask for a scan ("I just changed my list, act now"), but it may not
run the scan loop directly: the host owns the loop, the scan lock and the safety
gate.  This service only decides *whether* a request is accepted and returns a
small report.
"""

from __future__ import annotations

import collections
import math
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..core.rules import ACTION_PROTECT, evaluate
from ..diagnostics import log_event


@dataclass(frozen=True)
class ScanReport:
    accepted: bool
    frozen: int
    reason: str = "ok"

    def to_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "frozen": self.frozen,
            "reason": self.reason,
        }


class ScanRequester:
    """One request per source per interval; the scan itself stays global."""

    def __init__(
        self,
        scan_once: Callable[[], int],
        clock: Callable[[], float],
        min_interval: float = 1.0,
    ) -> None:
        self._scan_once = scan_once
        self._clock = clock
        self._min_interval = max(0.0, float(min_interval))
        # A global cooldown, not per plugin: the scan itself is global, and one
        # plugin must not be able to multiply the work by registering more ids.
        self._last: Optional[float] = None

    def request(self, source: str) -> ScanReport:
        now = self._clock()
        if self._last is not None and now - self._last < self._min_interval:
            return ScanReport(False, 0, "rate-limited")
        self._last = now
        frozen = self._scan_once()
        return ScanReport(True, int(frozen or 0), "ok")


class ScanService:
    """Runs the scan loop and hands every candidate to ``FreezeService``.

    It owns the scan lock, the latency samples and the session housekeeping.  It
    does **not** send signals: ``FreezeService`` plans, safety-checks and calls
    the Freezer.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float],
        procfs: Any,
        freezer: Any,
        rules: Any,
        session: Any,
        freeze_service: Any,
        health: Any,
        config: Any,
        stats: Dict[str, Any],
        audit: Callable[[Dict[str, Any]], None],
        log: Callable[[str, str], None],
        logger: Any,
        dry_run: Callable[[], bool],
        freeze_enabled: Callable[[], bool],
    ) -> None:
        self._clock = clock
        self._procfs = procfs
        self._freezer = freezer
        self._rules = rules
        self._session = session
        self._freeze = freeze_service
        self._health = health
        self._config = config
        self._stats = stats
        self._audit = audit
        self._log = log
        self._logger = logger
        self._dry_run = dry_run
        self._freeze_enabled = freeze_enabled
        self._scan_lock = threading.Lock()
        self._last_scan_ms = 0.0
        self._scan_samples = collections.deque(maxlen=60)
        self._scan_samples_lock = threading.Lock()

    # -- public ------------------------------------------------------------

    def scan_once(self) -> int:
        """Run exactly one scan.  Returns how many targets were suspended."""
        if not self._scan_lock.acquire(blocking=False):
            return 0
        started = self._clock()
        try:
            result = self._scan_locked()
        except Exception as exc:  # a bad scan must never kill the daemon
            error = "%s: %s" % (type(exc).__name__, exc)
            self._health.record_scan_failure(error)
            self._log(
                "error",
                "scan failed: %s (event=scan.fail failures=%d)"
                % (error, self._health.scan_failures),
            )
            if self._health.scan_failures == self._health.degraded_after:
                log_event(
                    self._logger,
                    "error",
                    "scan.degraded",
                    failures=self._health.scan_failures,
                    error=error,
                )
            return 0
        else:
            self._health.record_scan_success()
            return result
        finally:
            elapsed_ms = (self._clock() - started) * 1000.0
            with self._scan_samples_lock:
                self._last_scan_ms = elapsed_ms
                self._scan_samples.append(elapsed_ms)
            self._scan_lock.release()

    def scan_ms(self) -> Dict[str, float]:
        with self._scan_samples_lock:
            last = self._last_scan_ms
            samples = sorted(self._scan_samples)
        if not samples:
            return {"last": round(last, 2), "p50": 0.0, "p95": 0.0, "samples": 0}
        index = max(0, min(len(samples) - 1, int(round(len(samples) * 0.95)) - 1))
        return {
            "last": round(last, 2),
            "p50": round(samples[len(samples) // 2], 2),
            "p95": round(samples[index], 2),
            "samples": len(samples),
        }

    def run_forever(self, stop_event: threading.Event) -> None:
        interval = self._config.scan_interval
        if not math.isfinite(interval) or interval < 0.1:
            self._log(
                "error",
                "invalid scan_interval %r; using 1.0s for this loop" % (interval,),
            )
            interval = 1.0
        self._log(
            "info",
            "scan loop started (interval=%.2fs, dry_run=%s)"
            % (interval, self._dry_run()),
        )
        while not stop_event.is_set():
            started = self._clock()
            self.scan_once()
            elapsed = self._clock() - started
            stop_event.wait(max(0.0, interval - elapsed))

    # -- one pass ----------------------------------------------------------

    def _scan_locked(self) -> int:
        active = self._session.active()
        paused = self._session.paused()
        self._housekeeping(active, paused)
        if not active or paused:
            return 0

        rules = self._rules.merged()
        dry_run = self._dry_run()
        freeze_enabled = self._freeze_enabled()

        now = self._clock()
        self._stats["scans"] += 1
        if not freeze_enabled:
            if len(self._freezer):
                self._freeze.thaw_all("freeze-disabled")
            return 0

        ctx = self._freeze.eval_context()
        procs = self._procfs.snapshot()
        parents = {p.pid: p.ppid for p in procs}
        starttimes = {p.pid: p.starttime for p in procs}

        frozen_now = 0
        for proc in procs:
            if self._freeze.is_handled(proc, now):
                continue
            decision = evaluate(proc, rules, ctx)
            if decision.action == ACTION_PROTECT:
                if decision.rule.startswith("protect."):
                    self._stats["protected"] += 1
                continue
            if not decision.should_kill:
                continue
            self._stats["matched"] += 1

            if len(self._freezer) >= int(self._config.freeze.max_per_scan):
                self._stats["freeze_skipped"] += 1
                continue

            if self._freeze.freeze_target(
                proc, parents, starttimes, decision, ctx, now, dry_run
            ):
                frozen_now += 1
        return frozen_now

    def _housekeeping(self, active: bool, paused: bool = False) -> None:
        """Resume when the session ends or a pass was handed out."""
        rising = self._session.note_active(active)

        now = self._clock()
        lapsed = self._session.expire(now)
        lease_lapsed = bool(lapsed)
        for source in lapsed:
            log_event(self._logger, "warning", "session.lease-expired", source=source)
            self._audit({"kind": "lease-expired", "source": source})

        transition = self._session.take_transition()
        self._freezer.ensure_loaded()
        if not len(self._freezer):
            return
        if not active:
            if lease_lapsed:
                self._freeze.thaw_all("lease-expired")
            elif transition == "break":
                self._freeze.thaw_all("break")
            else:
                self._freeze.thaw_all("session-ended")
        elif paused:
            self._freeze.thaw_all("paused")
        elif rising:
            self._freeze.thaw_all("session-start")

