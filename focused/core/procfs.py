"""Read-only access to ``/proc``.

Everything here is deliberately defensive: ``/proc`` is a live filesystem and
processes disappear between ``readdir`` and ``read``.  A failure to read one
process must never abort a scan, so every accessor returns ``None`` (or an
empty container) instead of raising.

The ``root`` parameter exists so tests can point the reader at a synthetic
``/proc`` tree built in a temporary directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import cgroupfs

#: Linux stores task names in a 16-byte field: 15 visible characters plus NUL.
#: Anything longer is silently truncated by the kernel, which is the single most
#: common source of bugs in process-blacklist tools.
COMM_MAX_LEN = 15

#: Process states that mean "already dead".  A zombie still has a /proc entry
#: (until its parent reaps it) but is not a running process any more.
DEAD_STATES = frozenset({"Z", "X", "x"})


@dataclass(frozen=True)
class ProcessInfo:
    """An immutable snapshot of one process."""

    pid: int
    comm: str = ""
    cmdline: Tuple[str, ...] = ()
    uid: Optional[int] = None
    ppid: Optional[int] = None
    pgid: Optional[int] = None
    starttime: Optional[int] = None
    state: str = ""
    exe: Optional[str] = None

    # -- derived views -----------------------------------------------------

    @property
    def argv0(self) -> str:
        return self.cmdline[0] if self.cmdline else ""

    @property
    def exe_basename(self) -> str:
        return os.path.basename(self.exe) if self.exe else ""

    @property
    def argv0_basename(self) -> str:
        argv0 = self.argv0
        return os.path.basename(argv0) if argv0 else ""

    @property
    def cmdline_text(self) -> str:
        """Arguments joined with spaces.

        Note this loses argument boundaries: a substring rule of ``"a b"`` can
        match ``["a", "b"]`` as well as ``["a b"]``.  That is intentional --
        substring rules are a coarse escape hatch for poorly named processes.
        """
        return " ".join(self.cmdline)

    @property
    def is_kernel_thread(self) -> bool:
        """Kernel threads have no cmdline at all and cannot be signalled."""
        return not self.cmdline and self.state not in DEAD_STATES

    @property
    def is_zombie(self) -> bool:
        return self.state in DEAD_STATES

    def name_candidates(self) -> Tuple[str, ...]:
        """Every name this process could reasonably be known by.

        Order is stable and duplicates are removed.  A rule matching *any*
        candidate counts as a match.
        """
        out: List[str] = []
        for value in (self.comm, self.exe_basename, self.argv0_basename):
            if value and value not in out:
                out.append(value)
        return tuple(out)

    def display_name(self) -> str:
        return self.comm or self.exe_basename or self.argv0_basename or str(self.pid)


class ProcFS:
    """A thin, race-tolerant reader over a procfs mount."""

    def __init__(self, root: str = "/proc") -> None:
        self.root = str(root)

    # -- low level ---------------------------------------------------------

    def _path(self, pid: int, *parts: str) -> str:
        return os.path.join(self.root, str(pid), *parts)

    @staticmethod
    def _read_bytes(path: str) -> Optional[bytes]:
        try:
            with open(path, "rb") as handle:
                return handle.read()
        except OSError:
            # ProcessLookupError, FileNotFoundError, PermissionError, EIO...
            # All of them mean "this process is not readable right now".
            return None

    @staticmethod
    def _readlink(path: str) -> Optional[str]:
        try:
            target = os.readlink(path)
        except OSError:
            return None
        deleted_suffix = " (deleted)"
        if target.endswith(deleted_suffix):
            target = target[: -len(deleted_suffix)]
        return target or None

    def _read_stat(self, pid: int) -> Optional[Dict[str, object]]:
        """Parse ``/proc/<pid>/stat``.

        ``comm`` sits in parentheses and may itself contain spaces *and*
        parentheses, so the only correct strategy is to split on the **last**
        ``)`` and treat what follows as the numeric fields.
        """
        raw = self._read_bytes(self._path(pid, "stat"))
        if raw is None:
            return None
        text = raw.decode("utf-8", "replace")
        close = text.rfind(")")
        if close < 0:
            return None
        comm_raw = text[text.find("(") + 1 : close] if text.find("(") >= 0 else ""
        fields = text[close + 1 :].split()

        def field(number: int) -> Optional[str]:
            # ``fields[0]`` is field 3 of the stat line (the state character).
            index = number - 3
            if 0 <= index < len(fields):
                return fields[index]
            return None

        def as_int(number: int) -> Optional[int]:
            value = field(number)
            try:
                return int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None

        return {
            "state": field(3) or "",
            "ppid": as_int(4),
            "pgid": as_int(5),
            "starttime": as_int(22),
            "comm_raw": comm_raw,
        }

    def _read_comm(self, pid: int) -> Optional[str]:
        raw = self._read_bytes(self._path(pid, "comm"))
        if raw is None:
            return None
        # Only the trailing newline is removed: leading spaces are legal in a
        # task name and must not be stripped.
        return raw.decode("utf-8", "replace").rstrip("\n")

    def _read_cmdline(self, pid: int) -> Optional[Tuple[str, ...]]:
        raw = self._read_bytes(self._path(pid, "cmdline"))
        if raw is None:
            return None
        if not raw:
            return ()
        parts = raw.split(b"\x00")
        if parts and parts[-1] == b"":
            parts.pop()
        return tuple(part.decode("utf-8", "replace") for part in parts)

    def _read_text(self, path: str) -> Optional[str]:
        raw = self._read_bytes(path)
        if raw is None:
            return None
        return raw.decode("utf-8", "replace")

    def cgroup(self, pid: int) -> Optional[str]:
        """The cgroup path a process lives in, or None when it is unreadable.

        Freeze mode needs this to decide whether a process may be suspended as a
        whole unit or only as a process tree; see ``cgroupfs``.
        """
        return cgroupfs.parse_proc_cgroup(self._read_text(self._path(pid, "cgroup")) or "")

    def cgroup_frozen(self, cgroup_path: str) -> Optional[bool]:
        """Whether a cgroup currently reports ``frozen 1``; None if unreadable.

        This is the only reliable confirmation: a cgroup-frozen task still shows
        ``S`` in ``/proc/<pid>/status``.
        """
        text = self._read_text(cgroupfs.events_file(cgroup_path))
        if text is None:
            return None
        return cgroupfs.is_frozen(text)

    def _read_uid(self, pid: int) -> Optional[int]:
        raw = self._read_bytes(self._path(pid, "status"))
        if raw is None:
            return None
        for line in raw.decode("utf-8", "replace").splitlines():
            if line.startswith("Uid:"):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        return int(parts[1])
                    except ValueError:
                        return None
                return None
        return None

    # -- public API --------------------------------------------------------

    def pids(self) -> List[int]:
        try:
            names = os.listdir(self.root)
        except OSError:
            return []
        out: List[int] = []
        for name in names:
            if name.isdigit():
                try:
                    out.append(int(name))
                except ValueError:
                    continue
        out.sort()
        return out

    def probe(self, pid: int) -> Optional[Dict[str, object]]:
        """Cheap identity probe: state / ppid / pgid / starttime."""
        return self._read_stat(pid)

    def starttime(self, pid: int) -> Optional[int]:
        fields = self._read_stat(pid)
        if fields is None:
            return None
        value = fields.get("starttime")
        return value if isinstance(value, int) else None

    def is_alive(self, pid: int, starttime: Optional[int] = None) -> bool:
        """Is *this specific* process still running?

        Passing ``starttime`` makes the check immune to PID reuse: if the PID
        now belongs to a different process, the original one is gone.
        """
        fields = self._read_stat(pid)
        if fields is None:
            return False
        if fields.get("state") in DEAD_STATES:
            return False
        if starttime is not None:
            current = fields.get("starttime")
            if isinstance(current, int) and current != starttime:
                return False
        return True

    def read(self, pid: int) -> Optional[ProcessInfo]:
        fields = self._read_stat(pid)
        if fields is None:
            return None
        comm = self._read_comm(pid)
        if comm is None:
            comm = str(fields.get("comm_raw") or "")
        cmdline = self._read_cmdline(pid)
        return ProcessInfo(
            pid=pid,
            comm=comm or "",
            cmdline=cmdline if cmdline is not None else (),
            uid=self._read_uid(pid),
            ppid=fields.get("ppid") if isinstance(fields.get("ppid"), int) else None,
            pgid=fields.get("pgid") if isinstance(fields.get("pgid"), int) else None,
            starttime=(
                fields.get("starttime")
                if isinstance(fields.get("starttime"), int)
                else None
            ),
            state=str(fields.get("state") or ""),
            exe=self._readlink(self._path(pid, "exe")),
        )

    def snapshot(self) -> List[ProcessInfo]:
        """Read every process once.  Vanished processes are skipped."""
        out: List[ProcessInfo] = []
        for pid in self.pids():
            info = self.read(pid)
            if info is not None:
                out.append(info)
        return out
