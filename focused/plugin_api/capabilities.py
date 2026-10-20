"""Capability names, aliases and the declared ∩ granted model."""

from __future__ import annotations

from typing import Any, Tuple

from .errors import PluginError


CAP_RULES_READ = "rules.read"
CAP_RULES_WRITE = "rules.write"
CAP_STATE_READ = "state.read"
CAP_STATE_HISTORY = "state.history"
CAP_SESSION_CLAIM = "session.claim"
CAP_SCAN_REQUEST = "scan.request"
CAP_EVENTS_READ = "events.read"

#: Dangerous capabilities are defined but never granted by default.
CAP_PROCESS_CONTROL = "process.control"
CAP_FOCUS_OVERRIDE = "focus.override"

SAFE_CAPABILITIES: Tuple[str, ...] = (
    CAP_RULES_READ,
    CAP_RULES_WRITE,
    CAP_STATE_READ,
    CAP_STATE_HISTORY,
    CAP_SESSION_CLAIM,
    CAP_SCAN_REQUEST,
    CAP_EVENTS_READ,
)

DEFAULT_CAPABILITIES = SAFE_CAPABILITIES
ALL_CAPABILITIES = SAFE_CAPABILITIES + (CAP_PROCESS_CONTROL, CAP_FOCUS_OVERRIDE)

#: Old manifests used short names; accept them and expand to the new set.
_CAPABILITY_ALIASES = {
    "rules": (CAP_RULES_READ, CAP_RULES_WRITE),
    "session": (CAP_SESSION_CLAIM,),
    "status": (CAP_STATE_READ, CAP_STATE_HISTORY),
    "events": (CAP_EVENTS_READ,),
    "scan": (CAP_SCAN_REQUEST,),
}


def normalize_capabilities(raw: Any, *, strict: bool = True) -> Tuple[str, ...]:
    """Expand aliases, reject unknown names, and keep a stable order.

    ``strict=False`` is for *persisted* records: a capability that an older
    version understood must not make the whole registry unloadable.
    """
    if raw is None:
        return DEFAULT_CAPABILITIES
    if isinstance(raw, str):
        raw = (raw,)
    out = []
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        for expanded in _CAPABILITY_ALIASES.get(text, (text,)):
            if expanded not in ALL_CAPABILITIES:
                if not strict:
                    continue
                raise PluginError(
                    "manifest.capability",
                    "unknown capability: %s" % expanded,
                )
            if expanded not in out:
                out.append(expanded)
    return tuple(out)
