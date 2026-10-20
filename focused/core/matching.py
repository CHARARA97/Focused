"""Precompiled name/cmdline matching for a rule set.

Why this module exists
----------------------
``RuleSet`` is immutable, so the expensive half of matching can be done **once
per rule set** instead of **once per process**:

* 450 of the 453 built-in protect names are exact strings.  A ``set`` lookup is
  O(1); ``fnmatchcase`` compiles/uses a regex per call.
* only a handful of patterns contain ``*``/``?``/``[`` and need a regex.
* the kernel truncates a task name to 15 characters, so a long exact rule has a
  special "15-character prefix" case that can be indexed instead of scanned.

The matcher preserves the original semantics exactly:

1. patterns are considered in sorted order;
2. the *first* matching pattern wins (so ``Decision.rule`` is stable);
3. exact rules longer than ``COMM_MAX_LEN`` also match a 15-character candidate
   they start with;
4. wildcard rules do **not** get that truncation fallback;
5. cmdline substrings are considered in their tuple order.

The module is pure: no I/O, no clock.  ``RuleMatcher`` objects are cached by
:func:`matcher_for`, keyed by the rule fields that affect matching.
"""

from __future__ import annotations

import re
from fnmatch import translate
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Pattern, Sequence, Tuple

from .procfs import COMM_MAX_LEN, ProcessInfo

_WILDCARD_CHARS = frozenset("*?[")


def has_wildcard(pattern: str) -> bool:
    """Whether ``pattern`` needs glob semantics rather than an exact lookup."""
    return not _WILDCARD_CHARS.isdisjoint(pattern)


class _PatternSet:
    """One namespace of patterns (protect names or blacklist names).

    ``order`` is the index in the sorted pattern list; it is what keeps
    "first match wins" identical to the original linear scan.
    """

    __slots__ = ("_exact", "_truncated", "_wildcards")

    def __init__(self, patterns: Iterable[str]) -> None:
        exact: Dict[str, int] = {}
        truncated: Dict[str, List[Tuple[str, int]]] = {}
        wildcards: List[Tuple[Pattern[str], int, str]] = []

        for order, text in enumerate(sorted(patterns)):
            if not text:
                continue
            if has_wildcard(text):
                wildcards.append((re.compile(translate(text)), order, text))
                continue
            exact[text] = order
            if len(text) > COMM_MAX_LEN:
                # A 15-character comm can be the kernel-truncated form of this
                # rule; index it by the prefix so the lookup stays O(1).
                truncated.setdefault(text[:COMM_MAX_LEN], []).append((text, order))

        for entries in truncated.values():
            entries.sort(key=lambda item: item[1])

        self._exact = exact
        self._truncated = truncated
        self._wildcards = tuple(wildcards)

    def empty(self) -> bool:
        return not self._exact and not self._wildcards

    @lru_cache(maxsize=4096)
    def match(self, candidates: Tuple[str, ...]) -> Optional[str]:
        """The sorted-first pattern matching any candidate, or ``None``."""
        best_order = -1
        best_text: Optional[str] = None

        for candidate in candidates:
            lowered = candidate.lower()
            if not lowered:
                continue

            order = self._exact.get(lowered)
            if order is not None and (best_text is None or order < best_order):
                best_order, best_text = order, lowered

            if len(lowered) == COMM_MAX_LEN:
                for text, truncated_order in self._truncated.get(lowered, ()):
                    if best_text is None or truncated_order < best_order:
                        best_order, best_text = truncated_order, text

            for regex, wildcard_order, text in self._wildcards:
                if regex.match(lowered) and (best_text is None or wildcard_order < best_order):
                    best_order, best_text = wildcard_order, text

        return best_text


class RuleMatcher:
    """Name/cmdline matching for one rule set, with exact names pre-indexed."""

    __slots__ = ("_names", "_protect_names", "_cmdline", "_protect_cmdline")

    def __init__(
        self,
        names: Iterable[str],
        protect_names: Iterable[str],
        cmdline_substrings: Sequence[str],
        protect_cmdline_substrings: Sequence[str],
    ) -> None:
        self._names = _PatternSet(names)
        self._protect_names = _PatternSet(protect_names)
        self._cmdline = tuple(cmdline_substrings)
        self._protect_cmdline = tuple(protect_cmdline_substrings)

    # -- names -------------------------------------------------------------

    def protect_name(self, proc: ProcessInfo) -> Optional[str]:
        """The protect-name pattern that matches ``proc``, if any."""
        return self._protect_names.match(proc.name_candidates())

    def blacklist_name(self, proc: ProcessInfo) -> Optional[str]:
        """The blacklist-name pattern that matches ``proc``, if any."""
        return self._names.match(proc.name_candidates())

    # -- cmdline -----------------------------------------------------------

    def protect_cmdline(self, proc: ProcessInfo) -> Optional[str]:
        """The protect cmdline substring that matches ``proc``, if any."""
        return _first_substring(self._protect_cmdline, proc.cmdline_text.lower())

    def blacklist_cmdline(self, proc: ProcessInfo) -> Optional[str]:
        """The blacklist cmdline substring that matches ``proc``, if any."""
        return _first_substring(self._cmdline, proc.cmdline_text.lower())

    # -- combined ----------------------------------------------------------

    def protect_reason(self, proc: ProcessInfo) -> Optional[str]:
        """The rule string for a protect match, or ``None``.

        The prefix is part of the decision's ``rule`` field, so it stays here
        rather than being re-created by every caller.
        """
        name = self.protect_name(proc)
        if name is not None:
            return "protect.name:" + name
        substring = self.protect_cmdline(proc)
        if substring is not None:
            return "protect.cmdline:" + substring
        return None

    def protects_names(self, candidates: Tuple[str, ...]) -> bool:
        return self._protect_names.match(candidates) is not None


def _first_substring(substrings: Sequence[str], text: str) -> Optional[str]:
    for substring in substrings:
        if substring and substring in text:
            return substring
    return None


def _key(rules) -> Tuple:
    """Hashable identity of the fields that affect matching."""
    return (
        frozenset(rules.names),
        frozenset(rules.protect_names),
        tuple(rules.cmdline_substrings),
        tuple(rules.protect_cmdline_substrings),
    )


@lru_cache(maxsize=32)
def _matcher_for_key(key: Tuple) -> RuleMatcher:
    names, protect_names, cmdline, protect_cmdline = key
    return RuleMatcher(names, protect_names, cmdline, protect_cmdline)


def matcher_for(rules) -> RuleMatcher:
    """A cached matcher for ``rules``.

    Rule sets change rarely (config edit / plugin push), so a small LRU keeps
    the indices warm without leaking memory.
    """
    return _matcher_for_key(_key(rules))
