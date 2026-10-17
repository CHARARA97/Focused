"""Transport-level checks for the loopback HTTP API.

A token protects the API, but an open install (no token) still must not be
reachable from a random web page or a DNS-rebinding name.  Browsers can send
simple requests without a preflight, so Host/Origin are checked here, before
routing, and JSON bodies must declare ``application/json``.
"""

from __future__ import annotations

from typing import Mapping, Optional
from urllib.parse import urlsplit

from .http import ApiError

_LOOPBACK_NAMES = {"127.0.0.1", "::1", "localhost"}


def _hostname(value: str) -> Optional[str]:
    if not value:
        return None
    try:
        parsed = urlsplit("//" + value)
        host = parsed.hostname
    except ValueError:
        return None
    return host.rstrip(".").lower() if host else None


def is_loopback_host(value: str) -> bool:
    return _hostname(value) in _LOOPBACK_NAMES


def is_loopback_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return (parsed.hostname or "").rstrip(".").lower() in _LOOPBACK_NAMES


def validate_transport(headers: Mapping[str, str]) -> None:
    host = headers.get("Host", "") or ""
    if not host:
        raise ApiError(400, "Host header is required")
    if not is_loopback_host(host):
        raise ApiError(403, "Host must be loopback")
    origin = headers.get("Origin", "") or ""
    if origin and not is_loopback_origin(origin):
        raise ApiError(403, "Origin must be loopback")


def require_json_content_type(headers: Mapping[str, str]) -> None:
    content_type = (headers.get("Content-Type", "") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ApiError(415, "Content-Type must be application/json")
