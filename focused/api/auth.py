"""Request authentication.

One gate for every route: application token everywhere; a plugin token only for
its own plugin path, plus read-only ``/focus`` and ``/events`` when the plugin
has the matching capability.
"""

from __future__ import annotations

import hmac

from ..plugin_api import CAP_EVENTS_READ, CAP_STATE_READ
from .http import API_PREFIX, plugin_route


class RequestAuth:
    def __init__(self, engine, app_token: str, supplied: str) -> None:
        self._engine = engine
        self._app_token = app_token or ""
        self._supplied = supplied or ""

    def authorised(self) -> bool:
        if not self._app_token:
            return True
        return hmac.compare_digest(self._supplied, self._app_token)

    def plugin_authorised(self, plugin_id: str) -> bool:
        if self.authorised():
            return True
        return self._engine.plugins.verify(plugin_id, self._supplied)

    def authorised_for(self, path: str) -> bool:
        if self.authorised():
            return True
        if path.startswith(API_PREFIX + "/plugins/"):
            plugin_id, _tail = plugin_route(path)
            return bool(plugin_id) and self.plugin_authorised(plugin_id)
        if path in (API_PREFIX + "/focus", API_PREFIX + "/events"):
            record = self._engine.plugins.find_by_token(self._supplied)
            if record is None:
                return False
            required = CAP_EVENTS_READ if path.endswith("/events") else CAP_STATE_READ
            return required in self._engine.plugins.capabilities_for(record.id)
        return False
