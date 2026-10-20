from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, FrozenSet, Mapping, Optional, Tuple

from .reasons import DenyReason
from .targets import Selection, ancestors


@dataclass(frozen=True)
class Context:
    self_pid: int
    self_ppid: int
    self_uid: Optional[int]
    hard_protect_pids: FrozenSet[int]
    parents: Mapping[int, Optional[int]]
    uids: Mapping[int, Optional[int]]
    candidates: Mapping[int, Tuple[str, ...]]
    is_protected: Callable[[Tuple[str, ...]], bool]


@dataclass(frozen=True)
class Verdict:
    allow: bool
    deny_reason: Optional[DenyReason] = None


ALLOWED = Verdict(True)


def deny(reason: DenyReason) -> Verdict:
    return Verdict(False, reason)


def check(selection: Selection, context: Context) -> Verdict:
    facts = selection.facts

    if context.self_uid is None:
        return deny(DenyReason.UNKNOWN_DAEMON_UID)
    if facts.pid <= 1:
        return deny(DenyReason.PID_LE_1)
    if facts.pid == context.self_pid or facts.pid == context.self_ppid:
        return deny(DenyReason.SELF)
    if facts.pid in context.hard_protect_pids:
        return deny(DenyReason.SELF)
    if facts.is_zombie:
        return deny(DenyReason.ZOMBIE)
    if facts.is_kernel_thread:
        return deny(DenyReason.KERNEL_THREAD)
    if facts.uid is None:
        return deny(DenyReason.UNKNOWN_UID)
    if facts.uid != context.self_uid:
        return deny(DenyReason.FOREIGN_UID)
    if facts.pid in ancestors(context.self_pid, context.parents):
        return deny(DenyReason.TARGET_IS_ANCESTOR)
    if context.is_protected(context.candidates.get(facts.pid, ())):
        return deny(DenyReason.PROTECTED)
    if not selection.pids:
        return deny(DenyReason.EMPTY_TREE)
    if any(pid not in selection.starttimes for pid in selection.pids):
        return deny(DenyReason.UNKNOWN_STARTTIME)
    if context.self_pid in selection.pids or context.self_ppid in selection.pids:
        return deny(DenyReason.SELF_IN_TREE)

    for pid in selection.pids:
        if context.uids.get(pid) != context.self_uid:
            return deny(DenyReason.TREE_FOREIGN_UID)

    for pid in selection.pids:
        if context.is_protected(context.candidates.get(pid, ())):
            return deny(DenyReason.TREE_PROTECTED)

    return ALLOWED
