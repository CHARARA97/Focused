"""The only door between a plugin and the application.

Both the in-process PluginAPI and the HTTP plugin endpoints call this gateway.
Authorization, rate limiting, ownership and audit live here once; the services
behind it (rules, session, state, scan) have no concept of a plugin.

Effective capability = declared by the plugin ∩ granted by the host.  A plugin
cannot grant itself anything, and the HTTP registration path only ever grants
the safe set.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..plugin_api import (
    CAP_EVENTS_READ,
    CAP_RULES_READ,
    CAP_RULES_WRITE,
    CAP_SCAN_REQUEST,
    CAP_SESSION_CLAIM,
    CAP_STATE_HISTORY,
    CAP_STATE_READ,
    PluginError,
)
from ..plugins import STATUS_ENABLED


class PluginGateway:
    def __init__(self, engine: Any, rules: Any, session: Any, state: Any, scan: Any) -> None:
        self._engine = engine
        self._rules = rules
        self._session = session
        self._state = state
        self._scan = scan

    # -- authorization -----------------------------------------------------

    def _record(self, plugin_id: str):
        record = self._engine.plugins.get(plugin_id)
        if record is None or record.status != STATUS_ENABLED:
            raise PluginError("plugin.disabled", "plugin %s is not enabled" % plugin_id)
        return record

    def _require(self, plugin_id: str, capability: str):
        record = self._record(plugin_id)
        if capability not in self._engine.plugins.capabilities_for(plugin_id):
            raise PluginError(
                "capability.missing",
                "plugin %s has not been granted %s" % (plugin_id, capability),
            )
        return record

    # -- host methods used by PluginAPI / loader --------------------------

    def bind_addon(self, plugin_id: str, module: Any, api: Any) -> None:
        self._engine.bind_addon(plugin_id, module, api)

    def clear_plugin(self, plugin_id: str) -> None:
        # Cleanup must work even while a record is being marked failed.
        self._engine.clear_plugin(plugin_id)

    def log_plugin(self, plugin_id: str, level: str, message: str) -> None:
        self._engine.log_plugin(plugin_id, level, message)

    # -- rules -------------------------------------------------------------

    def _audit_rules(self, plugin_id: str, action: str, payload: Dict[str, Any]) -> None:
        self._engine.record_plugin_action(
            plugin_id,
            "rules." + action,
            names=len(payload.get("names") or ()),
            cmdline=len(payload.get("cmdline_substrings") or ()),
            pids=len(payload.get("pids") or ()),
        )

    def replace_plugin_rules(self, plugin_id: str, payload: Dict[str, Any]):
        self._require(plugin_id, CAP_RULES_WRITE)
        rules = self._rules.replace(plugin_id, payload)
        self._audit_rules(plugin_id, "replace", payload)
        return rules

    def add_plugin_rules(self, plugin_id: str, payload: Dict[str, Any]):
        self._require(plugin_id, CAP_RULES_WRITE)
        rules = self._rules.add(plugin_id, **self._rule_kwargs(payload))
        self._audit_rules(plugin_id, "add", payload)
        return rules

    def remove_plugin_rules(self, plugin_id: str, payload: Dict[str, Any]):
        self._require(plugin_id, CAP_RULES_WRITE)
        rules = self._rules.remove(plugin_id, **self._rule_kwargs(payload))
        self._audit_rules(plugin_id, "remove", payload)
        return rules

    def list_plugin_rules(self, plugin_id: str) -> Dict[str, Any]:
        self._require(plugin_id, CAP_RULES_READ)
        return self._rules.list(plugin_id)

    @staticmethod
    def _rule_kwargs(payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "names": payload.get("names") or (),
            "cmdline_substrings": payload.get("cmdline_substrings") or (),
            "pids": payload.get("pids") or (),
            "protect_names": payload.get("protect_names") or (),
            "protect_cmdline_substrings": payload.get("protect_cmdline_substrings") or (),
        }

    # -- session -----------------------------------------------------------

    def claim_plugin_session(self, plugin_id: str, ttl_seconds: float) -> bool:
        self._require(plugin_id, CAP_SESSION_CLAIM)
        try:
            ttl = float(ttl_seconds)
        except (TypeError, ValueError) as exc:
            raise PluginError("session.ttl", "ttl_seconds must be a number") from exc
        if not 5 <= ttl <= 3600:
            raise PluginError("session.ttl", "ttl_seconds must be between 5 and 3600")
        return self._engine.set_state(True, source=plugin_id, ttl_seconds=ttl)

    def release_plugin_session(self, plugin_id: str) -> bool:
        self._require(plugin_id, CAP_SESSION_CLAIM)
        return self._engine.set_state(False, source=plugin_id)

    def plugin_session_status(self, plugin_id: str) -> Dict[str, Any]:
        self._require(plugin_id, CAP_STATE_READ)
        claims = self._engine.claims()
        return {
            "active": self._engine.active(),
            "my_claim": claims.get(plugin_id),
            "claims": claims,
        }

    # -- state -------------------------------------------------------------

    def plugin_status_snapshot(self, plugin_id: Optional[str] = None) -> Dict[str, Any]:
        if plugin_id is not None:
            self._require(plugin_id, CAP_STATE_READ)
        return self._state.summary(plugin_id or "")

    def plugin_history_snapshot(self, plugin_id: Optional[str] = None) -> list:
        if plugin_id is not None:
            self._require(plugin_id, CAP_STATE_HISTORY)
        return self._state.history()

    # -- scan / events -----------------------------------------------------

    def request_plugin_scan(self, plugin_id: str):
        self._require(plugin_id, CAP_SCAN_REQUEST)
        report = self._scan.request(plugin_id)
        self._engine.record_plugin_action(
            plugin_id,
            "scan.request",
            accepted=bool(report.accepted),
            killed=int(report.killed),
            reason=report.reason,
        )
        return report

    def recent_events(
        self, plugin_id: Optional[str] = None, since: int = 0, limit: int = 100
    ) -> list:
        if plugin_id is not None:
            self._require(plugin_id, CAP_EVENTS_READ)
        return self._engine.events_since(since, limit)
