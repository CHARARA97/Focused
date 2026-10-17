"""HTTP adapter constants and DTO helpers.

The handler should read like a router: these are the small, testable pieces it
uses for paths, query/body fields and plugin payloads.
"""

from __future__ import annotations

import time
from typing import Any, Dict
from urllib.parse import urlparse

from ..plugins import STATUS_ENABLED

API_PREFIX = "/api/v1"
MAX_BODY_BYTES = 256 * 1024


def is_http_url(value: str) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def plugin_route(path: str):
    """Split ``/api/v1/plugins/{id}[/action]`` into (id, action)."""
    rest = path[len(API_PREFIX + "/plugins/") :].strip("/")
    if not rest:
        return "", ""
    parts = rest.split("/", 1)
    return parts[0], (parts[1] if len(parts) > 1 else "")


def plugin_payload(engine, record) -> Dict[str, Any]:
    claims = engine.claims()
    live = engine.live_claims()
    now = time.time()
    return {
        **record.to_public_dict(),
        "enabled": record.status == STATUS_ENABLED,
        "last_seen_ago": round(max(0.0, now - record.last_seen), 1)
        if record.last_seen
        else None,
        "session_claim": claims.get(record.id),
        "session_live": record.id in live,
    }


def plugin_payloads(engine) -> list:
    return [plugin_payload(engine, record) for record in engine.plugins.list()]


def int_param(query: Dict[str, list], key: str, default: int) -> int:
    values = query.get(key)
    if not values:
        return default
    try:
        return int(values[0])
    except (TypeError, ValueError):
        return default


def number_field(body: Dict[str, Any], key: str) -> float:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApiError(400, "%s must be a number" % key)
    return float(value)


def bool_field(body: Dict[str, Any], key: str) -> bool:
    value = body.get(key)
    if not isinstance(value, bool):
        raise ApiError(400, "field '%s' must be true or false" % key)
    return value
