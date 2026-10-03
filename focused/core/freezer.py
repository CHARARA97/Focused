"""Suspending matched applications instead of terminating them.

The rules that shape this module come from ``docs/freeze-mode.md``, and every one
of them exists because the alternative is a user whose applications are stuck:

* **The state file is written first, and it is the source of truth for thawing.**
  A freeze that is not recorded cannot be undone after a crash, so the record is
  written before the caller is told the freeze succeeded.
* **Thawing checks the PID guard (start time).** A recycled PID must never receive
  ``SIGCONT`` on behalf of a process that is long gone.
* **Thawing is idempotent.** A target that already exited is a success; only a
  real failure (permission, an unreadable cgroup) keeps the entry around for the
  next attempt.
* **Only what the caller hands over is touched.** The rule engine has already
  decided the target is blacklisted and not protected, and ``cgroupfs`` has
  already decided whether the whole unit may be frozen.

Two modes:

``pid``
    ``SIGSTOP`` the matching process and its descendants. Works for any process
    of the same user, including one whose cgroup is shared with unrelated
    processes (a terminal scope, a compositor's own service).

``unit``
    Freeze the whole cgroup through the systemd cgroup freezer. This also catches
    processes the application starts *after* the freeze, which is what makes it
    the better mode for a browser. It needs the cgroup to belong to a dedicated
    application unit; otherwise ``cgroupfs.plan_for`` returns ``pid``.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import cgroupfs

MODE_PID = "pid"
MODE_UNIT = "unit"

STATE_VERSION = 1


@dataclass(frozen=True)
class Target:
    """One thing to suspend, already vetted by the rule engine and ``cgroupfs``."""

    pid: int
    name: str
    #: The process and its descendants, root first.
    pids: Tuple[int, ...] = ()
    #: PID -> start time observed in the snapshot that produced this target.
    guard: Mapping[int, Optional[int]] = field(default_factory=dict)
    mode: str = MODE_PID
    cgroup: str = ""
    unit: str = ""

    def guard_for(self, pid: int) -> Optional[int]:
        return self.guard.get(pid)


@dataclass
class Frozen:
    """A recorded suspension."""

    pid: int
    name: str
    mode: str
    since: float
    starttime: Optional[int] = None
    cgroup: str = ""
    unit: str = ""
    pids: Tuple[int, ...] = ()
    reason: str = ""
    #: start time recorded for **every** pid we stopped.  Children need this as
    #: much as the root does: a child's PID can be recycled while the root lives
    #: on, and resuming a stranger is exactly the mistake this prevents.
    guards: Dict[int, Optional[int]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pid": self.pid,
            "name": self.name,
            "mode": self.mode,
            "since": self.since,
            "starttime": self.starttime,
            "cgroup": self.cgroup,
            "unit": self.unit,
            "pids": list(self.pids),
            "reason": self.reason,
            "guards": {str(pid): value for pid, value in sorted(self.guards.items())},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Frozen":
        pids = tuple(int(pid) for pid in (data.get("pids") or ()))
        raw_guards = data.get("guards") or {}
        guards: Dict[int, Optional[int]] = {}
        if isinstance(raw_guards, Mapping):
            for key, value in raw_guards.items():
                try:
                    guards[int(key)] = value
                except (TypeError, ValueError):
                    continue
        pid = int(data.get("pid") or 0)
        starttime = data.get("starttime")
        if pid and pid not in guards:
            # Records written before per-pid guards existed still have to work.
            guards[pid] = starttime
        return cls(
            pid=pid,
            name=str(data.get("name") or ""),
            mode=str(data.get("mode") or MODE_PID),
            since=float(data.get("since") or 0.0),
            starttime=starttime,
            cgroup=str(data.get("cgroup") or ""),
            unit=str(data.get("unit") or ""),
            pids=pids,
            reason=str(data.get("reason") or ""),
            guards=guards,
        )


def run_systemctl(args: Sequence[str], timeout: float = 5.0) -> Tuple[int, str]:
    """Run ``systemctl --user <args>``; returns ``(returncode, output)``.

    127 means "could not run it at all", which the caller treats as a failure to
    freeze (never as a success).
    """
    try:
        finished = subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "%s: %s" % (type(exc).__name__, exc)
    return finished.returncode, (finished.stdout + finished.stderr).strip()


class Freezer:
    """Suspends and resumes targets, and remembers what it suspended."""

    def __init__(
        self,
        procfs,
        state_path: str,
        logger=None,
        signal_sender=None,
        unit_runner=None,
        clock=time.time,
        sleep=time.sleep,
        confirm_timeout_ms: int = 1500,
    ) -> None:
        self.procfs = procfs
        self.state_path = state_path
        self._logger = logger
        self._signal = signal_sender or os.kill
        self._systemctl = unit_runner or run_systemctl
        self._clock = clock
        self._sleep = sleep
        self._confirm_timeout = max(0.0, confirm_timeout_ms / 1000.0)
        self._entries: Dict[int, Frozen] = {}
        self._lock = threading.RLock()
        self._loaded = False

    # -- logging -----------------------------------------------------------

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            return
        getattr(self._logger, level, self._logger.info)(message)

    # -- the state file ----------------------------------------------------

    def load(self) -> List[Frozen]:
        """Read the state file.  Does not thaw anything."""
        with self._lock:
            self._loaded = True
            self._entries = {}
            if not self.state_path or not os.path.exists(self.state_path):
                return []
            try:
                with open(self.state_path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (OSError, ValueError) as exc:
                # An unreadable record means we cannot know what is suspended;
                # say so loudly rather than pretending the list is empty.
                self._log("error", "freeze state file unreadable (%s): %s" % (self.state_path, exc))
                return []
            items = raw.get("frozen") if isinstance(raw, dict) else None
            for item in items or ():
                try:
                    entry = Frozen.from_dict(item)
                except (TypeError, ValueError):
                    continue
                if entry.pid:
                    self._entries[entry.pid] = entry
            return list(self._entries.values())

    def ensure_loaded(self) -> None:
        """Read the state file once, if that has not happened yet.

        The scan loop calls this before it decides anything about leftovers: a
        record written by a previous run is only visible after a load, and a
        session must not start on top of one it cannot see.
        """
        with self._lock:
            if not self._loaded:
                self.load()

    def _save(self) -> None:
        if not self.state_path:
            return
        payload = {
            "version": STATE_VERSION,
            "updated": self._clock(),
            "frozen": [entry.to_dict() for entry in self._ordered()],
        }
        parent = os.path.dirname(self.state_path)
        try:
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
            os.replace(tmp, self.state_path)
        except OSError as exc:
            # Not fatal for the freeze itself, but the caller must know that the
            # crash-recovery guarantee is gone.
            self._log("error", "cannot write freeze state to %s: %s" % (self.state_path, exc))

    def _ordered(self) -> List[Frozen]:
        return [self._entries[pid] for pid in sorted(self._entries)]

    def entries(self) -> List[Frozen]:
        with self._lock:
            return list(self._ordered())

    def contains(self, pid: int) -> bool:
        with self._lock:
            return pid in self._entries

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    # -- freezing ----------------------------------------------------------

    def freeze(self, target: Target, reason: str = "") -> Optional[Frozen]:
        """Suspend a target.  Returns the record, or None when nothing was frozen."""
        with self._lock:
            if target.pid in self._entries:
                return self._entries[target.pid]

            if target.mode == MODE_UNIT and target.unit:
                entry = self._freeze_unit(target, reason)
            else:
                entry = self._freeze_pids(target, reason)

            if entry is not None:
                self._entries[entry.pid] = entry
                self._save()
            return entry

    def _freeze_pids(self, target: Target, reason: str) -> Optional[Frozen]:
        pids = list(target.pids or (target.pid,))
        if target.pid not in pids:
            pids.insert(0, target.pid)

        stopped: List[int] = []
        failures: List[str] = []
        for pid in pids:
            expected = target.guard_for(pid)
            current = self.procfs.starttime(pid) if hasattr(self.procfs, "starttime") else None
            if current is None:
                # Gone between the snapshot and now: nothing to suspend.
                if pid == target.pid:
                    self._log("info", "freeze skipped pid=%d (%s): already gone" % (pid, target.name))
                    return None
                continue
            if expected is not None and current != expected:
                self._log(
                    "warning",
                    "freeze skipped pid=%d (%s): pid was reused (starttime %s != %s)"
                    % (pid, target.name, current, expected),
                )
                continue
            try:
                self._signal(pid, signal.SIGSTOP)
            except ProcessLookupError:
                continue
            except OSError as exc:
                failures.append("%d: %s" % (pid, exc))
                continue
            stopped.append(pid)

        if not stopped:
            if failures:
                self._log("error", "freeze failed for %s: %s" % (target.name, "; ".join(failures)))
            return None

        self._log(
            "info",
            "frozen pid=%d (%s) mode=%s pids=%s" % (target.pid, target.name, MODE_PID, stopped),
        )
        return Frozen(
            pid=target.pid,
            name=target.name,
            mode=MODE_PID,
            since=self._clock(),
            starttime=target.guard_for(target.pid),
            cgroup=target.cgroup,
            unit=target.unit,
            pids=tuple(stopped),
            reason=reason,
            guards={pid: target.guard_for(pid) for pid in stopped},
        )

    def _freeze_unit(self, target: Target, reason: str) -> Optional[Frozen]:
        """Freeze a whole cgroup through systemd, then the cgroup file."""
        code, output = self._systemctl(["freeze", target.unit])
        if code != 0:
            self._log(
                "warning",
                "systemctl freeze %s failed (%s: %s); trying cgroup.freeze"
                % (target.unit, code, output or "no output"),
            )
            if not self._write_cgroup_freeze(target.cgroup, "1"):
                return None
        if not self._confirm_frozen(target.cgroup):
            self._log(
                "error",
                "freeze of %s did not reach frozen=1 within %.2fs; thawing it back"
                % (target.unit, self._confirm_timeout),
            )
            self.thaw_unit(target.unit, target.cgroup, reason="confirm-timeout")
            return None

        self._log(
            "info",
            "frozen pid=%d (%s) mode=%s unit=%s"
            % (target.pid, target.name, MODE_UNIT, target.unit),
        )
        return Frozen(
            pid=target.pid,
            name=target.name,
            mode=MODE_UNIT,
            since=self._clock(),
            starttime=target.guard_for(target.pid),
            cgroup=target.cgroup,
            unit=target.unit,
            pids=tuple(target.pids),
            reason=reason,
            guards={pid: target.guard_for(pid) for pid in target.pids},
        )

    def _write_cgroup_freeze(self, cgroup_path: str, value: str) -> bool:
        if not cgroup_path:
            return False
        path = cgroupfs.freeze_file(cgroup_path)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(value)
        except OSError as exc:
            self._log("error", "cannot write %s: %s" % (path, exc))
            return False
        return True

    def _confirm_frozen(self, cgroup_path: str) -> bool:
        """Wait for the kernel to report ``frozen 1``.

        Freezing is asynchronous, and a frozen task still reports ``S`` in
        ``/proc/<pid>/status`` -- this file is the only confirmation there is.
        """
        if not cgroup_path:
            return False
        deadline = self._clock() + self._confirm_timeout
        while True:
            try:
                state = self.procfs.cgroup_frozen(cgroup_path)
            except Exception:  # a reader that cannot answer is not a confirmation
                state = None
            if state is True:
                return True
            if self._clock() >= deadline:
                return False
            self._sleep(0.05)

    # -- thawing -----------------------------------------------------------

    def thaw_unit(self, unit: str, cgroup_path: str = "", reason: str = "") -> bool:
        """Unfreeze a cgroup.  Returns True when the freezer is off again."""
        ok = True
        if unit:
            code, output = self._systemctl(["thaw", unit])
            if code != 0:
                ok = False
                self._log(
                    "warning",
                    "systemctl thaw %s failed (%s: %s)" % (unit, code, output or "no output"),
                )
        if cgroup_path:
            if not self._write_cgroup_freeze(cgroup_path, "0") and not ok:
                # Both routes failed; report it so the entry is kept for retry.
                return False
        return ok

    def thaw(self, entry: Frozen, reason: str = "") -> bool:
        """Settle one recorded target and forget it.

        True means the record is settled: the target was resumed, or it is
        provably gone (``starttime`` unreadable, or the PID belongs to somebody
        else now).  Only a real failure -- a signal that could not be delivered,
        an unwritable cgroup -- returns False and keeps the record for the next
        attempt.
        """
        with self._lock:
            ok = self._thaw_entry(entry)
            if ok:
                self._entries.pop(entry.pid, None)
                self._save()
                self._log(
                    "info", "thawed pid=%d (%s) mode=%s reason=%s" % (entry.pid, entry.name, entry.mode, reason or entry.reason)
                )
            return ok

    def _thaw_entry(self, entry: Frozen) -> bool:
        if entry.mode == MODE_UNIT and (entry.unit or entry.cgroup):
            return self.thaw_unit(entry.unit, entry.cgroup)

        ok = True
        for pid in entry.pids or (entry.pid,):
            expected = entry.guards.get(pid)
            if expected is None and pid == entry.pid:
                expected = entry.starttime
            current = self.procfs.starttime(pid) if hasattr(self.procfs, "starttime") else None
            if current is None:
                continue  # already gone: nothing to resume
            if expected is not None and current != expected:
                # A different process owns this PID now, which also means the one
                # we suspended is gone -- there is nothing left to resume.  Signal
                # nothing (a stranger must never receive our SIGCONT) and log at
                # error level, because at this point the record is the only
                # evidence that a suspension ever happened.
                self._log(
                    "error",
                    "not resuming pid=%d (%s): pid now belongs to another process "
                    "(starttime %s != %s recorded); nothing to resume"
                    % (pid, entry.name, current, expected),
                )
                continue
            try:
                self._signal(pid, signal.SIGCONT)
            except ProcessLookupError:
                continue
            except OSError as exc:
                ok = False
                self._log("error", "cannot resume pid=%d: %s" % (pid, exc))
        return ok

    def thaw_all(self, reason: str = "") -> int:
        """Settle every recorded target.  Returns how many records were settled."""
        with self._lock:
            entries = self._ordered()
        count = 0
        for entry in entries:
            if self.thaw(entry, reason):
                count += 1
        return count

    def recover(self) -> int:
        """Startup recovery: load the state file and resume whatever is in it.

        This is what makes "the daemon died while your browser was frozen" a
        recoverable situation instead of a lost afternoon.
        """
        entries = self.load()
        if not entries:
            return 0
        self._log(
            "warning",
            "found %d frozen target(s) from a previous run; resuming them" % len(entries),
        )
        return self.thaw_all("startup-recovery")


# ---------------------------------------------------------------------------
# standalone helpers: these work without a daemon, which is what ``--thaw-all``
# and the systemd ``ExecStopPost=`` hook need
# ---------------------------------------------------------------------------


def list_from_state(state_path: str) -> List[Frozen]:
    """Every recorded suspension, without needing a live daemon."""
    freezer = Freezer(procfs=_NullProcFS(), state_path=state_path)
    return freezer.load()


def thaw_all_from_state(
    state_path: str, procfs=None, logger=None, unit_runner=None
) -> int:
    """Resume everything in the state file.  Idempotent and self-contained.

    Deliberately does not talk to the daemon: systemd runs this after the daemon
    is already gone, and the daemon may also be wedged.
    """
    from .procfs import ProcFS

    freezer = Freezer(
        procfs=procfs or ProcFS(),
        state_path=state_path,
        logger=logger,
        unit_runner=unit_runner,
    )
    return freezer.recover()


class _NullProcFS:
    """A /proc reader that knows nothing -- used only to parse a state file."""

    def starttime(self, pid: int) -> Optional[int]:
        return None

    def cgroup_frozen(self, cgroup_path: str) -> Optional[bool]:
        return None
