"""Shape validation for plugin rule inputs."""

from __future__ import annotations

from typing import Any, Iterable, Tuple

from .errors import PluginError


def strings(values: Any, label: str) -> Tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        raise PluginError(
            "rules.type", "%s must be a sequence of strings, not a single string" % label
        )
    try:
        items: Iterable[Any] = iter(values)
    except TypeError as exc:
        raise PluginError("rules.type", "%s must be a sequence of strings" % label) from exc
    out = []
    for item in items:
        if not isinstance(item, str):
            raise PluginError("rules.type", "%s must contain only strings" % label)
        text = item.strip()
        if text:
            out.append(text)
    return tuple(out)


def pids(values: Any) -> Tuple[int, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        raise PluginError("rules.type", "pids must be a sequence of integers")
    try:
        items = iter(values)
    except TypeError as exc:
        raise PluginError("rules.type", "pids must be a sequence of integers") from exc
    out = []
    for item in items:
        if isinstance(item, bool):
            raise PluginError("rules.type", "pids must be integers, not booleans")
        try:
            pid = int(item)
        except (TypeError, ValueError) as exc:
            raise PluginError("rules.type", "pids must be integers") from exc
        if pid <= 0:
            raise PluginError("rules.pid", "pids must be positive process ids")
        if pid not in out:
            out.append(pid)
    return tuple(out)
