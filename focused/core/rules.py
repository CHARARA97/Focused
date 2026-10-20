"""The rule set: what to match, what to protect, and what must be rejected."""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple



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
    blacklist rule would be catastrophic, so config/API validation rejects it.
    """
    return bool(pattern) and all(ch in _GLOB_CHARS for ch in pattern)


@dataclass(frozen=True)
class RuleProblem:
    """One reason a rule set must not be accepted."""

    code: str
    detail: str
    pattern: str = ""

    def __str__(self) -> str:
        suffix = " (%s)" % self.pattern if self.pattern else ""
        return "%s: %s%s" % (self.code, self.detail, suffix)


class RuleRejected(ValueError):
    """Raised when a rule set would be unsafe to apply.

    The API boundary translates this to ``400``; the configuration loader turns
    it into ``FocusedConfigError``.  Keeping it a value-based exception means
    callers can list every problem instead of fixing them one at a time.
    """

    def __init__(self, problems: Sequence[RuleProblem]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(str(problem) for problem in self.problems))


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

    def problems(self) -> Tuple[RuleProblem, ...]:
        """Unsafe patterns in this rule set, in a stable order.

        This is deliberately a value-returning method rather than a raise: the
        config loader, the HTTP API and the plugin registry all need to show the
        same list of problems, and tests can assert on it without catching.
        """
        found: List[RuleProblem] = []
        for pattern in sorted(self.names):
            if is_catch_all_pattern(pattern):
                found.append(
                    RuleProblem(
                        "rule.catch-all",
                        "a blacklist pattern made only of glob syntax matches every process",
                        pattern,
                    )
                )
        for substring in self.cmdline_substrings:
            if len(substring) < 2:
                found.append(
                    RuleProblem(
                        "rule.cmdline-short",
                        "a one-character cmdline substring is too broad; use at least two characters",
                        substring,
                    )
                )
        return tuple(found)

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


def _as_sequence(value: object) -> Sequence[object]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(value)
    return (value,)


def _as_str_sequence(value: object) -> Tuple[str, ...]:
    return tuple(item for item in _as_sequence(value) if isinstance(item, str))
