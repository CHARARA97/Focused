"""Helpers the engine tests share: synthetic /proc entries and fakes.

The engine lives in ``focused.core``; these helpers build the /proc trees and
clocks its tests need, without touching the real system.
"""

import os
import tempfile
from typing import Optional, Sequence

from focused.core.procfs import ProcessInfo


UID = 1000
SELF_PID = 900_001
SELF_PPID = 900_000
SELF_PGID = 900_000


def make_proc(
    pid: int = 1000,
    comm: str = "firefox",
    cmdline: Optional[Sequence[str]] = None,
    uid: int = UID,
    ppid: int = 1,
    pgid: int = 1000,
    starttime: int = 4242,
    state: str = "S",
    exe: Optional[str] = "/usr/lib/firefox/firefox",
) -> ProcessInfo:
    if cmdline is None:
        cmdline = (exe or comm,)
    return ProcessInfo(
        pid=pid,
        comm=comm,
        cmdline=tuple(cmdline),
        uid=uid,
        ppid=ppid,
        pgid=pgid,
        starttime=starttime,
        state=state,
        exe=exe,
    )


def build_fake_proc(
    root: str,
    pid: int,
    comm: str = "firefox",
    cmdline: Sequence[str] = ("firefox",),
    uid: int = UID,
    ppid: int = 1,
    pgid: int = 1000,
    starttime: int = 4242,
    state: str = "S",
    exe: Optional[str] = None,
    write_comm_file: bool = True,
) -> str:
    """Materialise one ``/proc/<pid>`` directory under ``root``.

    ``/proc/<pid>/stat`` is written field-accurately so the parser is exercised
    against the real layout, including the awkward ``pid (comm)`` prefix.
    """
    directory = os.path.join(root, str(pid))
    os.makedirs(directory, exist_ok=True)

    if write_comm_file:
        with open(os.path.join(directory, "comm"), "w", encoding="utf-8") as handle:
            handle.write(comm + "\n")

    with open(os.path.join(directory, "cmdline"), "wb") as handle:
        if cmdline:
            handle.write(b"\x00".join(arg.encode("utf-8") for arg in cmdline) + b"\x00")

    with open(os.path.join(directory, "status"), "w", encoding="utf-8") as handle:
        handle.write("Name:\t%s\n" % comm)
        handle.write("Uid:\t%d\t%d\t%d\t%d\n" % (uid, uid, uid, uid))

    # fields 3..22 of the stat line: state ppid pgrp session tty_nr tpgid flags
    # minflt cminflt majflt cmajflt utime stime cutime cstime priority nice
    # num_threads itrealvalue starttime
    tail = [
        state,
        str(ppid),
        str(pgid),
        str(pgid),
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "20",
        "0",
        "1",
        "0",
        str(starttime),
    ]
    with open(os.path.join(directory, "stat"), "w", encoding="utf-8") as handle:
        handle.write("%d (%s) %s\n" % (pid, comm, " ".join(tail)))

    if exe:
        link = os.path.join(directory, "exe")
        try:
            os.symlink(exe, link)
        except (OSError, NotImplementedError):
            pass

    return directory


class FakeProcFS:
    """A procfs whose liveness can be scripted, for Killer tests."""

    def __init__(
        self,
        starttimes: Optional[Dict[int, int]] = None,
        states: Optional[Dict[int, str]] = None,
        dies_on: Optional[Iterable[int]] = None,
    ) -> None:
        self.starttimes: Dict[int, int] = dict(starttimes or {})
        self.states: Dict[int, str] = dict(states or {})
        self.dies_on = set(dies_on if dies_on is not None else self.starttimes.keys())
        self.probes: list = []

    # -- procfs surface used by Killer -------------------------------------

    def probe(self, pid: int):
        self.probes.append(pid)
        if pid not in self.starttimes:
            return None
        return {
            "state": self.states.get(pid, "S"),
            "starttime": self.starttimes[pid],
            "ppid": 1,
            "pgid": pid,
        }

    def starttime(self, pid: int):
        if pid not in self.starttimes:
            return None
        return self.starttimes[pid]

    def is_alive(self, pid: int, starttime: Optional[int] = None) -> bool:
        if pid not in self.starttimes:
            return False
        if self.states.get(pid, "S") in ("Z", "X", "x"):
            return False
        if starttime is not None and self.starttimes[pid] != starttime:
            return False
        return True

    def snapshot(self):
        return []

    def kill(self, pid: int) -> None:
        self.starttimes.pop(pid, None)


class FakeClock:
    """Monotonic clock whose value only moves when someone sleeps."""

    def __init__(self, step: float = 0.5, start: float = 0.0) -> None:
        self.value = start
        self.step = step

    def __call__(self) -> float:
        return self.value

    def sleep(self, _seconds: float) -> None:
        self.value += self.step
