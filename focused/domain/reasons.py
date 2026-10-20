from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Tuple


class Mode(str, Enum):
    AUDIT = "audit"
    KILL = "kill"


class Gate(str, Enum):
    WORK = "work"
    BUSY = "busy"
    NO_SESSION = "no-session"
    BREAK = "break"
    PAUSED = "paused"
    DISABLED = "disabled"


class DenyReason(str, Enum):
    UNKNOWN_DAEMON_UID = "unknown-daemon-uid"
    UNKNOWN_UID = "unknown-uid"
    PID_LE_1 = "hard:pid<=1"
    SELF = "hard:self"
    ZOMBIE = "hard:zombie"
    KERNEL_THREAD = "hard:kernel-thread"
    FOREIGN_UID = "hard:foreign-uid"
    PROTECTED = "protected"
    TARGET_IS_ANCESTOR = "target-is-ancestor"
    EMPTY_TREE = "empty-tree"
    SELF_IN_TREE = "self-in-tree"
    TREE_FOREIGN_UID = "tree-foreign-uid"
    TREE_PROTECTED = "tree-protected"
    UNKNOWN_STARTTIME = "unknown-starttime"


class KillResult(str, Enum):
    SENT = "sent"
    GONE = "gone"
    REUSED = "reused"
    DENIED = "denied"
    UNVERIFIED = "unverified"


class Outcome(str, Enum):
    KILLED = "killed"
    AUDIT = "audit"
    DENIED = "denied"


class EventKind(str, Enum):
    GATE = "enforce.gate"
    DENY = "enforce.deny"
    WOULD_KILL = "enforce.would-kill"
    KILL = "enforce.kill"
    KILL_SUMMARY = "enforce.kill.summary"
    DENY_SUMMARY = "enforce.deny.summary"
    SIGNAL_FAILED = "enforce.signal-failed"
    MODE = "enforce.mode"
    SCAN_FAIL = "scan.fail"
    SCAN_SLOW = "scan.slow"
    SCAN_DEGRADED = "scan.degraded"


@dataclass(frozen=True)
class ActionEvent:
    kind: EventKind
    at: float
    mono: float = 0.0
    scan: int = 0
    name: str = ""
    pid: int = 0
    starttime: int = 0
    pids: Tuple[int, ...] = ()
    tree: int = 0
    rule: str = ""
    reason: str = ""
    result: str = ""
    verdict: str = ""
    intent: str = ""
    policy: int = 0
    mode: str = ""
    count: int = 0
    duration_s: float = 0.0

    def fields(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "kind": self.kind.value,
            "ts": self.at,
            "mono": self.mono,
            "scan": self.scan,
        }
        optional = (
            "name",
            "pid",
            "starttime",
            "pids",
            "tree",
            "rule",
            "reason",
            "result",
            "verdict",
            "intent",
            "policy",
            "mode",
            "count",
            "duration_s",
        )
        for key in optional:
            value = getattr(self, key)
            if value in ("", 0, (), 0.0):
                continue
            payload[key] = list(value) if isinstance(value, tuple) else value
        return payload
