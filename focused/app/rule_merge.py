from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping

from ..core.rules import RuleSet


@dataclass(frozen=True)
class Merged:
    rules: RuleSet
    urls: List[str]


def merge_values(current: Iterable[Any], added: Iterable[Any], convert) -> List[Any]:
    out = list(convert(current))
    for value in convert(added):
        if value not in out:
            out.append(value)
    return out


def subtract_values(current: Iterable[Any], removed: Iterable[Any], convert) -> List[Any]:
    drop = set(convert(removed))
    return [value for value in convert(current) if value not in drop]


def merge_sources(
    sources: Mapping[str, Dict[str, Any]], *, protect_names: Iterable[str]
) -> Merged:
    names = set()
    cmdline: List[str] = []
    pids = set()
    guards: Dict[int, int] = {}
    protected = {name.lower() for name in protect_names}
    protect_substrings: List[str] = []
    urls: List[str] = []

    for entry in sources.values():
        names |= set(entry.get("names") or ())
        for text in entry.get("cmdline_substrings") or ():
            if text not in cmdline:
                cmdline.append(text)
        pids |= set(entry.get("pids") or ())
        guards.update(entry.get("pid_guards") or {})
        protected |= set(entry.get("protect_names") or ())
        for text in entry.get("protect_cmdline_substrings") or ():
            if text not in protect_substrings:
                protect_substrings.append(text)
        for pattern in entry.get("url_patterns") or ():
            if pattern not in urls:
                urls.append(pattern)

    rules = RuleSet.from_dict(
        {
            "names": sorted(names),
            "cmdline_substrings": cmdline,
            "pids": sorted(pids),
            "pid_guards": {str(pid): guard for pid, guard in guards.items()},
            "protect_names": sorted(protected),
            "protect_cmdline_substrings": sorted(protect_substrings),
        }
    )
    return Merged(rules=rules, urls=urls)
