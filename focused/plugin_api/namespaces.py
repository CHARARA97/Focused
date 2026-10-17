"""The small namespaced surfaces exposed as ``api.rules`` / ``api.session`` ….

They delegate to ``PluginAPI``; keeping them here lets the core API stay focused
on identity, buffering and authorization.
"""

from __future__ import annotations

from typing import Any, Dict

from .capabilities import (
    CAP_EVENTS_READ,
    CAP_RULES_READ,
    CAP_SCAN_REQUEST,
    CAP_SESSION_CLAIM,
    CAP_STATE_FROZEN,
    CAP_STATE_READ,
)


class _RulesApi:
    def __init__(self, api: "PluginAPI") -> None:
        self._api = api

    def replace(self, **payload) -> None:
        self._api._rules_request("replace", payload)

    def add(self, **payload) -> None:
        self._api._rules_request("add", payload)

    def remove(self, **payload) -> None:
        self._api._rules_request("remove", payload)

    def list(self) -> Dict[str, Any]:
        self._api._require(CAP_RULES_READ)
        return self._api._host.list_plugin_rules(self._api.id)


class _SessionApi:
    def __init__(self, api: "PluginAPI") -> None:
        self._api = api

    def claim(self, ttl_seconds: float = 30) -> None:
        self._api._session_request("claim", ttl_seconds)

    def release(self) -> None:
        self._api._session_request("release", None)

    def status(self) -> Dict[str, Any]:
        self._api._require(CAP_STATE_READ)
        return self._api._host.plugin_session_status(self._api.id)


class _StateApi:
    def __init__(self, api: "PluginAPI") -> None:
        self._api = api

    def summary(self) -> Dict[str, Any]:
        self._api._require(CAP_STATE_READ)
        return self._api._api_status()

    def frozen(self) -> list:
        self._api._require(CAP_STATE_FROZEN)
        return self._api._host.plugin_frozen_snapshot(self._api.id)

    def rules(self) -> Dict[str, Any]:
        return self._api.rules.list()


class _ScanApi:
    def __init__(self, api: "PluginAPI") -> None:
        self._api = api

    def request(self) -> Dict[str, Any]:
        self._api._require(CAP_SCAN_REQUEST)
        report = self._api._host.request_plugin_scan(self._api.id)
        return report.to_dict() if hasattr(report, "to_dict") else dict(report)


class _EventsApi:
    def __init__(self, api: "PluginAPI") -> None:
        self._api = api

    def recent(self, since: int = 0, limit: int = 100) -> list:
        self._api._require(CAP_EVENTS_READ)
        return self._api._host.recent_events(self._api.id, since, limit)
