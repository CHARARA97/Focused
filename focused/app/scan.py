from __future__ import annotations

import collections
import math
import threading
from dataclasses import replace
from typing import Any, Callable, Dict, Optional

from ..core.matching import matcher_for
from ..core.decision import EvalContext
from .reporting import Reporting
from .timing import Timeline
from ..domain.identity import Identity
from ..domain.intent import Intent
from ..domain.policy import Policy
from ..domain.reasons import EventKind, Gate, Mode
from ..ports import ProcessReader
from .enforcer import Enforcer
from ..domain.tick import Tick
from .targeting import Targeting
from .tick import ScanResult, Snapshot, run_tick

SLOW_SCAN_MS = 100.0


class ScanRequestReport:
    __slots__ = ("accepted", "killed", "reason")

    def __init__(self, accepted: bool, killed: int, reason: str = "ok") -> None:
        self.accepted = accepted
        self.killed = killed
        self.reason = reason

    def to_dict(self) -> dict:
        return {"accepted": self.accepted, "killed": self.killed, "reason": self.reason}


class ScanRequester:
    def __init__(
        self,
        scan_once: Callable[[], ScanResult],
        clock: Callable[[], float],
        min_interval: float = 1.0,
    ) -> None:
        self._scan_once = scan_once
        self._clock = clock
        self._min_interval = max(0.0, float(min_interval))
        self._last: Optional[float] = None

    def request(self, source: str) -> ScanRequestReport:
        now = self._clock()
        if self._last is not None and now - self._last < self._min_interval:
            return ScanRequestReport(False, 0, "rate-limited")
        self._last = now
        result = self._scan_once()
        return ScanRequestReport(True, int(result.killed or 0), "ok")


class ScanService:
    def __init__(
        self,
        *,
        timeline: Timeline,
        procfs: ProcessReader,
        rules: Any,
        session: Any,
        enforcer: Enforcer,
        config: Any,
        reporting: Reporting,
        identity: Identity,
    ) -> None:
        self._timeline = timeline
        self._procfs = procfs
        self._rules = rules
        self._session = session
        self._enforcer = enforcer
        self._config = config
        self._reporting = reporting
        self._identity = identity
        self._scan_lock = threading.Lock()
        self._last_scan_ms = 0.0
        self._scan_samples = collections.deque(maxlen=60)
        self._scan_samples_lock = threading.Lock()
        self._last_gate: Optional[Gate] = None
        self._last_result = ScanResult()
        self._scan_seq = 0

    def scan_once(self) -> ScanResult:
        if not self._scan_lock.acquire(blocking=False):
            return ScanResult(gate=Gate.BUSY.value, mode=self._mode().value)
        self._scan_seq += 1
        started = self._timeline.wall()
        try:
            result = self._scan_locked()
            self._reporting.scan_ok()
        except Exception as exc:
            self._reporting.scan_failed(exc)
            result = ScanResult(
                mode=self._mode().value,
                error="%s: %s" % (type(exc).__name__, exc),
            )
        finally:
            elapsed_ms = (self._timeline.wall() - started) * 1000.0
            with self._scan_samples_lock:
                self._last_scan_ms = elapsed_ms
                self._scan_samples.append(elapsed_ms)
            self._scan_lock.release()

        self._last_result = replace(
            result, duration_ms=round(elapsed_ms, 2), scan=self._scan_seq
        )
        if elapsed_ms > SLOW_SCAN_MS:
            self._reporting.journal(
                "warning",
                EventKind.SCAN_SLOW.value,
                scan=self._scan_seq,
                duration_ms=round(elapsed_ms, 2),
            )
        return self._last_result

    def last_result(self) -> ScanResult:
        return self._last_result

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
            self._reporting.log(
                "error",
                "invalid scan_interval %r; using 1.0s for this loop" % (interval,),
            )
            interval = 1.0
        self._reporting.log(
            "info",
            "scan loop started (interval=%.2fs, mode=%s)"
            % (interval, self._mode().value),
        )
        while not stop_event.is_set():
            started = self._timeline.wall()
            self.scan_once()
            elapsed = self._timeline.wall() - started
            stop_event.wait(max(0.0, interval - elapsed))

    def _mode(self) -> Mode:
        return Mode(self._config.enforcement.mode)

    def _scan_locked(self) -> ScanResult:
        active = self._session.active()
        paused = self._session.paused()
        self._session.note_active(active)
        for source in self._session.expire(self._timeline.wall()):
            self._reporting.journal("warning", "session.lease-expired", source=source)
            self._reporting.record({"kind": "lease-expired", "source": source})
        transition = self._session.take_transition()

        intent = Intent.decide(
            enabled=bool(self._config.enforcement.enabled),
            active=active,
            paused=paused,
            on_break=transition == "break",
        )
        tick = Tick(
            scan=self._scan_seq,
            mono=self._timeline.monotonic(),
            intent=intent,
            policy=Policy(
                mode=self._mode(),
                max_per_scan=int(self._config.enforcement.max_per_scan),
                version=self._rules.version(),
            ),
        )
        self._enforcer.sweep(tick)

        if not intent.enforcing:
            return self._gated(tick)

        self._note_running(tick)
        self._reporting.bump("scans")
        rules = self._rules.merged()
        return run_tick(
            tick,
            self._snapshot(),
            rules=rules,
            evaluation=self._evaluation_context(),
            targeting=Targeting(
                procfs=self._procfs,
                matcher=matcher_for(rules),
                identity=self._identity,
            ),
            enforcer=self._enforcer,
        )

    def _snapshot(self) -> Snapshot:
        procs = self._procfs.snapshot()
        rows = {proc.pid: proc for proc in procs}
        parents = {proc.pid: proc.ppid for proc in procs}
        return Snapshot(rows=rows, parents=parents)

    def _gated(self, tick: Tick) -> ScanResult:
        if self._last_gate != tick.intent.reason:
            self._last_gate = tick.intent.reason
            self._reporting.journal(
                "info",
                EventKind.GATE.value,
                reason=tick.intent.reason.value,
                mode=tick.policy.mode.value,
            )
        return ScanResult(
            gate=tick.intent.reason.value,
            mode=tick.policy.mode.value,
            scan=tick.scan,
        )

    def _note_running(self, tick: Tick) -> None:
        if self._last_gate != Gate.WORK:
            self._last_gate = Gate.WORK
            self._reporting.journal(
                "info",
                EventKind.GATE.value,
                reason=Gate.WORK.value,
                mode=tick.policy.mode.value,
            )

    def _evaluation_context(self) -> EvalContext:
        return EvalContext(
            uid=self._identity.uid,
            self_pid=self._identity.pid,
            self_ppid=self._identity.ppid,
            self_pgid=-1,
            hard_protect_pids=self._identity.hard_protect_pids,
            protect_own_process_group=False,
        )
