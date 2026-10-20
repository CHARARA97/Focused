from __future__ import annotations

from typing import Any, Callable, Dict, List


class PluginView:
    def __init__(
        self, *, registry: Any, session: Any, clock: Callable[[], float]
    ) -> None:
        self._registry = registry
        self._session = session
        self._clock = clock

    def payloads(self) -> List[Dict[str, Any]]:
        now = self._clock()
        claims = self._session.claims()
        live = self._session.live()
        out: List[Dict[str, Any]] = []
        for record in self._registry.list():
            payload = record.to_public_dict()
            payload.update(
                {
                    "enabled": record.status == "enabled",
                    "last_seen_ago": round(max(0.0, now - record.last_seen), 1)
                    if record.last_seen
                    else None,
                    "session_claim": claims.get(record.id),
                    "session_live": record.id in live,
                }
            )
            out.append(payload)
        return out
