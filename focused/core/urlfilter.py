"""URL patterns: which addresses are "the blacklist" for the browser side.

The same rule is implemented in the Chrome extension (``focused-extension/src/
logic.js``), because the extension has to keep working while the daemon is
unreachable.  Both sides must agree, so the semantics are deliberately tiny and
are spelled out here:

* matching is **case-insensitive**;
* a pattern containing ``*`` is a glob over the whole URL, where ``*`` matches
  any characters -- including ``/`` and ``:``;
* a pattern without ``*`` is a **substring** match, which is what people type
  (``reddit.com``);
* an empty pattern list matches nothing.

``docs/focused-protocol.md`` documents this as part of the contract between
ChillFocus, the Focused application and the browser extension.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Pattern

_CACHE: dict = {}


def _compile(pattern: str) -> Optional[Pattern[str]]:
    raw = (pattern or "").strip()
    if not raw:
        return None
    cached = _CACHE.get(raw)
    if cached is not None:
        return cached
    if "*" in raw:
        regex = "^" + ".*".join(re.escape(part) for part in raw.split("*")) + "$"
    else:
        regex = re.escape(raw)
    compiled = re.compile(regex, re.IGNORECASE)
    if len(_CACHE) > 512:
        _CACHE.clear()
    _CACHE[raw] = compiled
    return compiled


def matches_pattern(url: str, pattern: str) -> bool:
    """Whether one URL matches one pattern."""
    if not url:
        return False
    compiled = _compile(pattern)
    if compiled is None:
        return False
    return compiled.search(url) is not None


def matches_any(url: str, patterns: Iterable[str]) -> bool:
    """Whether a URL matches any pattern in the list."""
    for pattern in patterns or ():
        if matches_pattern(url, pattern):
            return True
    return False


def sanitize(patterns: Iterable[str]) -> List[str]:
    """Trim, drop blanks and duplicates, keeping the user's order."""
    seen = set()
    out: List[str] = []
    for raw in patterns or ():
        text = (raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out
