from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import FrozenSet, Optional

from .matching import matcher_for
from .procfs import COMM_MAX_LEN, ProcessInfo
from .rules import RuleSet

_WILDCARD_CHARS = frozenset("*?[")

ACTION_PROTECT = "protect"
ACTION_MATCH = "match"
ACTION_SKIP = "skip"


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
    return matcher_for(rules).protect_reason(proc)


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

    matcher = matcher_for(rules)

    # --- user/default protect rules ----------------------------------------
    protected = matcher.protect_reason(proc)
    if protected is not None:
        return Decision(ACTION_PROTECT, protected, "matched protect list")

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
    name = matcher.blacklist_name(proc)
    if name is not None:
        return Decision(
            ACTION_MATCH, "blacklist.name:" + name, "matched blacklist name"
        )

    substring = matcher.blacklist_cmdline(proc)
    if substring is not None:
        return Decision(
            ACTION_MATCH,
            "blacklist.cmdline:" + substring,
            "matched blacklist cmdline substring",
        )

    if proc.pid in rules.pids:
        guard = rules.pid_guards.get(proc.pid)
        rule = "blacklist.pid:" + str(proc.pid)
        if guard is None:
            # A PID without a start-time guard cannot be distinguished from a
            # reused PID.  Refuse rather than risk stopping a stranger.
            return Decision(
                ACTION_SKIP, rule, "pid rule has no reuse guard: refusing to kill"
            )
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
