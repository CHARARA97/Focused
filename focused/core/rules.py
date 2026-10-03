"""The decision layer: *is this process a target?*

This module is the only place in the project that decides whether something
should die.  It is intentionally pure -- no I/O, no clock, no globals -- so the
entire safety surface can be exhaustively unit tested.

Evaluation is **fail-closed**: protective rules are checked before blacklist
rules, and the first match wins.  A user who writes ``names = ["*"]`` by mistake
gets "nothing is ever killed", not "the machine is killed".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

from .procfs import COMM_MAX_LEN, ProcessInfo

ACTION_PROTECT = "protect"
ACTION_MATCH = "match"
ACTION_SKIP = "skip"

_WILDCARD_CHARS = frozenset("*?[")
_GLOB_CHARS = frozenset("*?[]")


def normalize(value: str) -> str:
    """Rules and candidate names are compared lower-cased and trimmed."""
    return value.strip().lower()


def normalize_many(values: Iterable[str]) -> Tuple[str, ...]:
    out: List[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        norm = normalize(value)
        if norm and norm not in out:
            out.append(norm)
    return tuple(out)


def is_catch_all_pattern(pattern: str) -> bool:
    """True for patterns made only of glob syntax (``*``, ``*?``, ``**`` ...).

    Such a pattern matches every process on the system; accepting it as a
    blacklist rule would be catastrophic, so config validation rejects it.
    """
    return bool(pattern) and all(ch in _GLOB_CHARS for ch in pattern)


@dataclass(frozen=True)
class RuleSet:
    """A complete, immutable rule set.

    ``names``/``protect_names`` are lower-cased glob patterns.
    ``cmdline_substrings`` are lower-cased plain substrings.
    ``pid_guards`` maps a PID to the ``starttime`` observed when the rule was
    created; it is what makes PID rules safe against PID reuse.
    """

    names: FrozenSet[str] = frozenset()
    cmdline_substrings: Tuple[str, ...] = ()
    pids: FrozenSet[int] = frozenset()
    pid_guards: Mapping[int, int] = field(default_factory=dict)
    protect_names: FrozenSet[str] = frozenset()
    protect_cmdline_substrings: Tuple[str, ...] = ()

    # -- construction ------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, object]]) -> "RuleSet":
        data = data or {}
        guards: Dict[int, int] = {}
        raw_guards = data.get("pid_guards") or {}
        if isinstance(raw_guards, Mapping):
            for key, value in raw_guards.items():
                try:
                    guards[int(key)] = int(value)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
        pids: List[int] = []
        for value in _as_sequence(data.get("pids")):
            try:
                pids.append(int(value))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
        return cls(
            names=frozenset(normalize_many(_as_str_sequence(data.get("names")))),
            cmdline_substrings=normalize_many(
                _as_str_sequence(data.get("cmdline_substrings"))
            ),
            pids=frozenset(pids),
            pid_guards=guards,
            protect_names=frozenset(
                normalize_many(_as_str_sequence(data.get("protect_names")))
            ),
            protect_cmdline_substrings=normalize_many(
                _as_str_sequence(data.get("protect_cmdline_substrings"))
            ),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "names": sorted(self.names),
            "cmdline_substrings": list(self.cmdline_substrings),
            "pids": sorted(self.pids),
            "pid_guards": {str(k): v for k, v in sorted(self.pid_guards.items())},
            "protect_names": sorted(self.protect_names),
            "protect_cmdline_substrings": list(self.protect_cmdline_substrings),
        }

    # -- introspection -----------------------------------------------------

    @property
    def is_empty(self) -> bool:
        return not (self.names or self.cmdline_substrings or self.pids)

    def shadowed_names(self) -> Tuple[str, ...]:
        """Blacklist names that a protect rule will always win over.

        Silently inert rules are dangerous: the user believes they are filtering
        something.  Config validation surfaces this list as a warning.
        """
        out = []
        for name in sorted(self.names):
            for protect in sorted(self.protect_names):
                if name == protect or fnmatchcase(name, protect) or fnmatchcase(protect, name):
                    out.append(name)
                    break
        return tuple(out)

    def pids_without_guard(self) -> Tuple[int, ...]:
        return tuple(sorted(pid for pid in self.pids if pid not in self.pid_guards))


@dataclass(frozen=True)
class EvalContext:
    """Everything the decision needs to know about *us*."""

    uid: Optional[int]
    self_pid: int
    self_ppid: int
    self_pgid: Optional[int] = None
    hard_protect_pids: FrozenSet[int] = frozenset()
    protect_own_process_group: bool = True


@dataclass(frozen=True)
class Decision:
    action: str
    rule: str = ""
    reason: str = ""

    @property
    def is_protected(self) -> bool:
        return self.action == ACTION_PROTECT

    @property
    def should_kill(self) -> bool:
        return self.action == ACTION_MATCH


# ---------------------------------------------------------------------------
# matching primitives
# ---------------------------------------------------------------------------


def name_matches(pattern: str, proc: ProcessInfo) -> bool:
    """Match a lower-cased glob ``pattern`` against every name candidate.

    Two subtleties are handled here:

    * the kernel truncates ``comm`` to :data:`COMM_MAX_LEN` characters, so a
      plain (non-glob) rule longer than that also matches a 15-character
      candidate it starts with -- ``chromium-browser`` must match comm
      ``chromium-browse``;
    * glob patterns cannot be reconciled with truncation, so that fallback is
      deliberately skipped when the rule contains wildcards.
    """
    plain = _WILDCARD_CHARS.isdisjoint(pattern)
    for candidate in proc.name_candidates():
        lowered = candidate.lower()
        if fnmatchcase(lowered, pattern):
            return True
        if (
            plain
            and len(lowered) == COMM_MAX_LEN
            and len(pattern) > COMM_MAX_LEN
            and pattern.startswith(lowered)
        ):
            return True
    return False


def cmdline_matches(substring: str, proc: ProcessInfo) -> bool:
    if not substring:
        return False
    return substring in proc.cmdline_text.lower()


# ---------------------------------------------------------------------------
# the decision
# ---------------------------------------------------------------------------


def protect_reason(proc: ProcessInfo, rules: RuleSet) -> Optional[str]:
    """Return the protect rule matching ``proc``, or None.

    Only the configurable protect list is consulted here, not the hard rails --
    this answers "would the user's protect list shield this?", which is what the
    settings UI needs to tell the user why an entry cannot be blocked.
    """
    for pattern in sorted(rules.protect_names):
        if name_matches(pattern, proc):
            return "protect.name:" + pattern
    for substring in rules.protect_cmdline_substrings:
        if cmdline_matches(substring, proc):
            return "protect.cmdline:" + substring
    return None


def evaluate(proc: ProcessInfo, rules: RuleSet, ctx: EvalContext) -> Decision:
    """Decide what to do with ``proc``.  First match wins; protect beats match."""

    # --- hard rails: not configurable, ever ---------------------------------
    if proc.pid <= 1:
        return Decision(ACTION_PROTECT, "hard:pid<=1", "init/kernel is never a target")

    if proc.is_zombie:
        return Decision(ACTION_SKIP, "hard:zombie", "process has already exited")

    if proc.is_kernel_thread:
        return Decision(
            ACTION_PROTECT, "hard:kernel-thread", "kernel threads cannot be signalled"
        )

    if proc.pid == ctx.self_pid or proc.pid == ctx.self_ppid or proc.pid in ctx.hard_protect_pids:
        return Decision(
            ACTION_PROTECT, "hard:self", "the daemon and its parent must survive"
        )

    if ctx.uid is not None and proc.uid is not None and proc.uid != ctx.uid:
        return Decision(
            ACTION_PROTECT, "hard:foreign-uid", "process belongs to another user"
        )

    # --- user/default protect rules ----------------------------------------
    for pattern in sorted(rules.protect_names):
        if name_matches(pattern, proc):
            return Decision(
                ACTION_PROTECT, "protect.name:" + pattern, "matched protect list"
            )

    for substring in rules.protect_cmdline_substrings:
        if cmdline_matches(substring, proc):
            return Decision(
                ACTION_PROTECT, "protect.cmdline:" + substring, "matched protect list"
            )

    if (
        ctx.protect_own_process_group
        and ctx.self_pgid is not None
        and proc.pgid is not None
        and proc.pgid == ctx.self_pgid
    ):
        return Decision(
            ACTION_PROTECT,
            "hard:own-process-group",
            "same process group as the daemon (typically the game launch chain)",
        )

    # --- blacklist ---------------------------------------------------------
    for pattern in sorted(rules.names):
        if name_matches(pattern, proc):
            return Decision(
                ACTION_MATCH, "blacklist.name:" + pattern, "matched blacklist name"
            )

    for substring in rules.cmdline_substrings:
        if cmdline_matches(substring, proc):
            return Decision(
                ACTION_MATCH,
                "blacklist.cmdline:" + substring,
                "matched blacklist cmdline substring",
            )

    if proc.pid in rules.pids:
        guard = rules.pid_guards.get(proc.pid)
        rule = "blacklist.pid:" + str(proc.pid)
        if guard is None:
            return Decision(ACTION_MATCH, rule, "matched blacklist pid (no reuse guard)")
        if proc.starttime is None:
            # Fail closed: without a start time the guard cannot be verified, so
            # we cannot prove this PID is still the process the rule meant.
            return Decision(
                ACTION_SKIP, rule, "starttime unavailable: cannot verify the pid reuse guard"
            )
        if proc.starttime == guard:
            return Decision(ACTION_MATCH, rule, "matched blacklist pid (guard verified)")
        return Decision(
            ACTION_SKIP, rule, "pid-reuse guard mismatch: this is a different process"
        )

    return Decision(ACTION_SKIP)


# ---------------------------------------------------------------------------
# helpers used by config validation
# ---------------------------------------------------------------------------


def _as_sequence(value: object) -> Sequence[object]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(value)
    return (value,)


def _as_str_sequence(value: object) -> Tuple[str, ...]:
    return tuple(item for item in _as_sequence(value) if isinstance(item, str))
