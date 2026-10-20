from __future__ import annotations

from typing import Optional, Protocol, Sequence, Tuple

from .domain.reasons import ActionEvent


class Clock(Protocol):
    def now(self) -> float:
        ...


class ProcessRow(Protocol):
    pid: int
    ppid: Optional[int]
    uid: Optional[int]
    comm: str
    state: str
    starttime: Optional[int]

    def display_name(self) -> str:
        ...


class ProcessReader(Protocol):
    def snapshot(self) -> Sequence[ProcessRow]:
        ...

    def starttime(self, pid: int) -> Optional[int]:
        ...

    def supports_live_children(self) -> bool:
        ...

    def child_pids(self, pid: int) -> Tuple[int, ...]:
        ...


class SignalSender(Protocol):
    def send(self, pid: int, signum: int) -> None:
        ...


class Recorder(Protocol):
    def write(self, event: ActionEvent) -> None:
        ...
