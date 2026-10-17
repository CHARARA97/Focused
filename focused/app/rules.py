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

MAX_RULES_PER_SOURCE = 5000

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


def _urls(values: Any) -> List[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        raise RuleRejected([RuleProblem("rules.type", "url_patterns must be a sequence")])
    try:
        items = iter(values)
    except TypeError as exc:
        raise RuleRejected(
            [RuleProblem("rules.type", "url_patterns must be a sequence")]
        ) from exc
    out: List[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _merge(current: Iterable[Any], added: Iterable[Any], convert) -> List[Any]:
    out = list(convert(current))
    for value in convert(added):
        if value not in out:
            out.append(value)
    return out


def _subtract(current: Iterable[Any], removed: Iterable[Any], convert) -> List[Any]:
    drop = set(convert(removed))
    return [value for value in convert(current) if value not in drop]


def _entry(incoming: RuleSet, guards: Dict[int, int], urls: Iterable[str]) -> Dict[str, Any]:
    return {
        "names": sorted(incoming.names),
        "cmdline_substrings": list(incoming.cmdline_substrings),
        "pids": sorted(incoming.pids),
        "pid_guards": dict(guards),
        "protect_names": sorted(incoming.protect_names),
        "protect_cmdline_substrings": list(incoming.protect_cmdline_substrings),
        "url_patterns": [str(item) for item in urls if str(item).strip()],
    }


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
        self._sources["config"] = _entry(
            initial_rules,
            self._guards_for(
                initial_rules,
                dict(initial_rules.pid_guards),
                strict=False,
                source="config",
            ),
            url_patterns,
        )
        self._merged = self._recompute_locked()
        self._url_patterns = urlfilter.sanitize(list(url_patterns))

    # -- reads -------------------------------------------------------------

    def merged(self) -> RuleSet:
        with self._lock:
            return self._merged

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
        payload = payload or {}
        for key in (
            "names",
            "cmdline_substrings",
            "pids",
            "protect_names",
            "protect_cmdline_substrings",
        ):
            if isinstance(payload.get(key), (str, bytes)):
                raise RuleRejected(
                    [RuleProblem("rules.type", "%s must be a sequence" % key)]
                )
        incoming = RuleSet.from_dict(payload)
        problems = list(incoming.problems())
        total = (
            len(incoming.names)
            + len(incoming.cmdline_substrings)
            + len(incoming.pids)
        )
        if total > MAX_RULES_PER_SOURCE:
            problems.append(
                RuleProblem(
                    "rules.too-many",
                    "a source may contain at most %d rules" % MAX_RULES_PER_SOURCE,
                )
            )

        with self._lock:
            current = dict(self._sources.get(source) or {})
        current_guards = {
            int(pid): guard for pid, guard in (current.get("pid_guards") or {}).items()
        }
        guards: Dict[int, int] = {}
        for pid in sorted(incoming.pids):
            if pid <= 1:
                problems.append(
                    RuleProblem("rule.pid-invalid", "pid 0/1 is never a target", str(pid))
                )
                continue
            if pid in current_guards:
                # The guard was valid when the entry was added.  A process may
                # have exited since; re-reading /proc here would either fail the
                # whole edit or, worse, adopt a reused PID.  Keep the old guard:
                # evaluate() fails closed if the process is gone or reused.
                provided = incoming.pid_guards.get(pid)
                if provided is not None and provided != current_guards[pid]:
                    problems.append(
                        RuleProblem(
                            "rule.pid-guard",
                            "guard conflicts with the existing source entry",
                            str(pid),
                        )
                    )
                    continue
                guards[pid] = current_guards[pid]

        new_pids = frozenset(pid for pid in incoming.pids if pid not in current_guards)
        if new_pids:
            new_rule = RuleSet(
                pids=new_pids,
                pid_guards={
                    pid: incoming.pid_guards[pid]
                    for pid in new_pids
                    if pid in incoming.pid_guards
                },
            )
            guards.update(
                self._guards_for(
                    new_rule,
                    dict(new_rule.pid_guards),
                    strict=True,
                    source=source,
                    problems=problems,
                )
            )
        if problems:
            raise RuleRejected(problems)

        urls = _urls(payload.get("url_patterns"))
        return _entry(incoming, guards, urls)

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
            "names": _merge(current.get("names"), names, _strings),
            "cmdline_substrings": _merge(
                current.get("cmdline_substrings"), cmdline_substrings, _strings
            ),
            "pids": _merge(current.get("pids"), pids, _pids),
            "pid_guards": dict(current.get("pid_guards") or ()),
            "protect_names": _merge(
                current.get("protect_names"), protect_names, _strings
            ),
            "protect_cmdline_substrings": _merge(
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
        remaining = _subtract(current.get("pids"), pids, _pids)
        remaining_set = set(remaining)
        guards = {
            int(pid): guard
            for pid, guard in (current.get("pid_guards") or {}).items()
            if int(pid) in remaining_set
        }
        payload = {
            "names": _subtract(current.get("names"), names, _strings),
            "cmdline_substrings": _subtract(
                current.get("cmdline_substrings"), cmdline_substrings, _strings
            ),
            "pids": remaining,
            "pid_guards": guards,
            "protect_names": _subtract(
                current.get("protect_names"), protect_names, _strings
            ),
            "protect_cmdline_substrings": _subtract(
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
            entry["url_patterns"] = _urls(patterns)
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

    def _guards_for(
        self,
        incoming: RuleSet,
        existing: Dict[int, Any],
        *,
        strict: bool,
        source: str,
        problems: Optional[List[RuleProblem]] = None,
    ) -> Dict[int, int]:
        problems = problems if problems is not None else []
        guards: Dict[int, int] = {}
        for pid in sorted(incoming.pids):
            if pid <= 1:
                problems.append(
                    RuleProblem("rule.pid-invalid", "pid 0/1 is never a target", str(pid))
                )
                continue
            current = self._procfs.starttime(pid)
            if current is None:
                problems.append(
                    RuleProblem("rule.pid-gone", "pid is gone or unreadable", str(pid))
                )
                if not strict and self._log is not None:
                    self._log(
                        "warning",
                        "config pid %s is gone; the rule will fail closed" % pid,
                    )
                continue
            provided = existing.get(pid)
            if provided is not None and provided != current:
                problems.append(
                    RuleProblem(
                        "rule.pid-reused",
                        "starttime does not match the live process",
                        str(pid),
                    )
                )
                continue
            guards[pid] = current
        return guards

    def _recompute_locked(self) -> RuleSet:
        names = set()
        cmdline: List[str] = []
        pids = set()
        guards: Dict[int, int] = {}
        protect_names = {name.lower() for name in load_protect_names()}
        protect_substrings: List[str] = []
        urls: List[str] = []

        for entry in self._sources.values():
            names |= set(entry.get("names") or ())
            for text in entry.get("cmdline_substrings") or ():
                if text not in cmdline:
                    cmdline.append(text)
            pids |= set(entry.get("pids") or ())
            guards.update(entry.get("pid_guards") or {})
            protect_names |= set(entry.get("protect_names") or ())
            for text in entry.get("protect_cmdline_substrings") or ():
                if text not in protect_substrings:
                    protect_substrings.append(text)
            for pattern in entry.get("url_patterns") or ():
                if pattern not in urls:
                    urls.append(pattern)

        merged = RuleSet.from_dict(
            {
                "names": sorted(names),
                "cmdline_substrings": cmdline,
                "pids": sorted(pids),
                "pid_guards": {str(pid): guard for pid, guard in guards.items()},
                "protect_names": sorted(protect_names),
                "protect_cmdline_substrings": sorted(protect_substrings),
            }
        )
        self._merged = merged
        self._url_patterns = urlfilter.sanitize(urls)
        return merged
