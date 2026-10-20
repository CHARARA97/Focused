"""Owns the rule sources and the merged rule set.

This used to live in ``FocusedEngine``.  Moving it here gives the rule lifecycle
one owner:

  * each plugin/source has one entry;
  * the config source is just another entry;
  * every mutation is validated, normalised and merged here;
  * after a mutation the caller is notified (the engine uses that to thaw
    targets that no longer match).

The service is thread-safe on its own; the callback runs **outside** the lock so
the engine can take its own locks without a lock-order inversion.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Iterable, List, Optional

from ..config import load_protect_names
from ..core import urlfilter
from ..core.rules import RuleProblem, RuleRejected, RuleSet
from .rule_entries import entry_for, guards_for, prepare, urls_of
from .rule_merge import merge_sources, merge_values, subtract_values


RuleChanged = Callable[[str, Optional[Dict[str, Any]], RuleSet], None]
LogFn = Callable[[str, str], None]


def _strings(values: Any) -> List[str]:
    if isinstance(values, (str, bytes)):
        raise RuleRejected([RuleProblem("rules.type", "expected a sequence of strings")])
    out: List[str] = []
    for value in values or ():
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
    return out


def _pids(values: Any) -> List[int]:
    if isinstance(values, (str, bytes)):
        raise RuleRejected([RuleProblem("rules.type", "expected a sequence of integers")])
    out: List[int] = []
    for value in values or ():
        if isinstance(value, bool):
            raise RuleRejected([RuleProblem("rules.type", "pids must be integers")])
        try:
            pid = int(value)
        except (TypeError, ValueError):
            raise RuleRejected([RuleProblem("rules.type", "pids must be integers")])
        if pid > 0 and pid not in out:
            out.append(pid)
    return out





class RuleSourceService:
    """The single owner of per-source rules and the merged set."""

    def __init__(
        self,
        procfs: Any,
        initial_rules: RuleSet,
        url_patterns: Iterable[str] = (),
        *,
        on_changed: Optional[RuleChanged] = None,
        log: Optional[LogFn] = None,
    ) -> None:
        self._procfs = procfs
        self._lock = threading.RLock()
        self._on_changed = on_changed
        self._log = log
        self._sources: Dict[str, Dict[str, Any]] = {}
        # The config file is just another source.  Fill guards for live PIDs; a
        # vanished config PID is kept but will fail closed when evaluated.
        self._sources["config"] = entry_for(
            initial_rules,
            guards_for(
                self._procfs,
                self._log,
                initial_rules,
                dict(initial_rules.pid_guards),
                strict=False,
                source="config",
            ),
            url_patterns,
        )
        self._version = 0
        self._merged = self._publish(self._recompute_locked())
        self._url_patterns = urlfilter.sanitize(list(url_patterns))

    # -- reads -------------------------------------------------------------

    def merged(self) -> RuleSet:
        with self._lock:
            return self._merged

    def version(self) -> int:
        with self._lock:
            return self._version

    def _publish(self, merged: RuleSet) -> RuleSet:
        self._merged = merged
        self._version += 1
        return merged

    def sources(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {name: dict(entry) for name, entry in self._sources.items()}

    def list(self, source: str) -> Dict[str, Any]:
        with self._lock:
            entry = dict(self._sources.get(source) or {})
        return {
            "names": list(entry.get("names") or ()),
            "cmdline_substrings": list(entry.get("cmdline_substrings") or ()),
            "pids": list(entry.get("pids") or ()),
            "protect_names": list(entry.get("protect_names") or ()),
            "protect_cmdline_substrings": list(
                entry.get("protect_cmdline_substrings") or ()
            ),
            "url_patterns": list(entry.get("url_patterns") or ()),
        }

    def source_rules(self, source: str) -> RuleSet:
        """The source's contribution as a RuleSet (not the merged set)."""
        with self._lock:
            return RuleSet.from_dict(self._sources.get(source) or {})

    def url_patterns(self) -> List[str]:
        with self._lock:
            return list(self._url_patterns)

    # -- writes ------------------------------------------------------------

    def replace(self, source: str, payload: Dict[str, Any]) -> RuleSet:
        return self._store(source, self._prepare(source, payload))

    def preview(self, source: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and normalise without touching the source or notifying.

        Used by ConfigService to make an update transactional: a rejected PID
        must not leave earlier config fields half-applied.
        """
        return self._prepare(source, payload)

    def _prepare(self, source: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return prepare(
            payload,
            self._current(source),
            procfs=self._procfs,
            log=self._log,
            source=source,
        )

    def add(
        self,
        source: str,
        *,
        names: Iterable[Any] = (),
        cmdline_substrings: Iterable[Any] = (),
        pids: Iterable[Any] = (),
        protect_names: Iterable[Any] = (),
        protect_cmdline_substrings: Iterable[Any] = (),
    ) -> RuleSet:
        current = self._current(source)
        payload = {
            "names": merge_values(current.get("names"), names, _strings),
            "cmdline_substrings": merge_values(
                current.get("cmdline_substrings"), cmdline_substrings, _strings
            ),
            "pids": merge_values(current.get("pids"), pids, _pids),
            "pid_guards": dict(current.get("pid_guards") or ()),
            "protect_names": merge_values(
                current.get("protect_names"), protect_names, _strings
            ),
            "protect_cmdline_substrings": merge_values(
                current.get("protect_cmdline_substrings"),
                protect_cmdline_substrings,
                _strings,
            ),
            "url_patterns": list(current.get("url_patterns") or ()),
        }
        return self.replace(source, payload)

    def remove(
        self,
        source: str,
        *,
        names: Iterable[Any] = (),
        cmdline_substrings: Iterable[Any] = (),
        pids: Iterable[Any] = (),
        protect_names: Iterable[Any] = (),
        protect_cmdline_substrings: Iterable[Any] = (),
    ) -> RuleSet:
        current = self._current(source)
        remaining = subtract_values(current.get("pids"), pids, _pids)
        remaining_set = set(remaining)
        guards = {
            int(pid): guard
            for pid, guard in (current.get("pid_guards") or {}).items()
            if int(pid) in remaining_set
        }
        payload = {
            "names": subtract_values(current.get("names"), names, _strings),
            "cmdline_substrings": subtract_values(
                current.get("cmdline_substrings"), cmdline_substrings, _strings
            ),
            "pids": remaining,
            "pid_guards": guards,
            "protect_names": subtract_values(
                current.get("protect_names"), protect_names, _strings
            ),
            "protect_cmdline_substrings": subtract_values(
                current.get("protect_cmdline_substrings"),
                protect_cmdline_substrings,
                _strings,
            ),
            "url_patterns": list(current.get("url_patterns") or ()),
        }
        return self.replace(source, payload)

    def clear(self, source: str) -> RuleSet:
        with self._lock:
            self._sources.pop(source, None)
            merged = self._recompute_locked()
        self._notify(source, None, merged)
        return merged

    def set_url_patterns(self, patterns: Iterable[str], source: str = "config") -> List[str]:
        with self._lock:
            entry = dict(self._sources.get(source) or {})
            entry["url_patterns"] = urls_of(patterns)
            self._sources[source] = entry
            self._recompute_locked()
            return list(self._url_patterns)

    # -- internals ---------------------------------------------------------

    def _current(self, source: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._sources.get(source) or {})

    def _store(self, source: str, entry: Dict[str, Any]) -> RuleSet:
        with self._lock:
            self._sources[source] = entry
            merged = self._recompute_locked()
        self._notify(source, entry, merged)
        return merged

    def _notify(
        self, source: str, entry: Optional[Dict[str, Any]], merged: RuleSet
    ) -> None:
        if self._on_changed is None:
            return
        self._on_changed(source, entry, merged)

    def _recompute_locked(self) -> RuleSet:
        merged = merge_sources(self._sources, protect_names=load_protect_names())
        self._publish(merged.rules)
        self._url_patterns = urlfilter.sanitize(merged.urls)
        return merged.rules
