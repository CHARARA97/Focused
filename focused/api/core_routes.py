"""Core POST route: config, URLs and the session-control family."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from .http import API_PREFIX, ApiError
from .session_routes import post_session

_SESSION_PATHS = {
    API_PREFIX + "/focus/state",
    API_PREFIX + "/focus/session",
    API_PREFIX + "/focus/pause",
    API_PREFIX + "/scan",
    API_PREFIX + "/thaw",
}


def post_core(engine, path: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    if path in _SESSION_PATHS:
        return post_session(engine, path, body)
    if path == API_PREFIX + "/config":
        try:
            return 200, engine.update_config(body)
        except ValueError as exc:
            raise ApiError(400, str(exc)) from exc
    if path == API_PREFIX + "/urls":
        return 200, {
            "ok": True,
            "url_patterns": engine.set_url_patterns(body.get("patterns") or []),
        }
    return 404, {"ok": False, "error": "no such endpoint: %s" % path}
