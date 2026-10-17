"""Read-only state snapshots for a plugin.

The snapshot is intentionally small: what is active, what is frozen, what the
plugin itself claimed, and whether enforcement is healthy.  It never exposes an
engine handle, tokens, or the ability to mutate anything.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .. import __version__


class StateService:
    def __init__(self, engine: Any, rules) -> None:
        self._engine = engine
        self._rules = rules

    def summary(self, source: str) -> Dict[str, Any]:
        claims = self._engine.claims()
        return {
            "ok": True,
            "version": __version__,
            "active": self._engine.active(),
            "my_claim": claims.get(source),
            "claims": claims,
            "frozen_count": self._engine.frozen_count(),
            "health": self._engine.health(),
            "scan_ms": self._engine.scan_ms(),
        }

    def frozen(self) -> List[Dict[str, Any]]:
        return self._engine.frozen_list()

    def rules(self, source: str) -> Dict[str, Any]:
        return self._rules.list(source)
