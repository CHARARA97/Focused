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
            "killed_total": self._engine.killed_total(),
            "scan": self._engine.scan_report(),
            "health": self._engine.health(),
            "scan_ms": self._engine.scan_ms(),
        }

    def history(self) -> List[Dict[str, Any]]:
        return self._engine.recent_list()

    def rules(self, source: str) -> Dict[str, Any]:
        return self._rules.list(source)
