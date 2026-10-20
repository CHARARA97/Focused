"""Read-only snapshots: status, focus state, config view and process picker.

These are formatting/query use cases, not state owners.  Moving them out keeps
the engine from becoming the place where every response shape is assembled.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from .process_view import ProcessView
from .runtime_info import RuntimeInfo


class StatusService:
    def __init__(
        self,
        *,
        rules: Any,
        session: Any,
        enforcer: Any,
        scan: Any,
        health: Any,
        stats: Dict[str, Any],
        config: Any,
        runtime: RuntimeInfo,
        processes: ProcessView,
        plugins: Any,
    ) -> None:
        self._rules = rules
        self._session = session
        self._enforcer = enforcer
        self._scan = scan
        self._health = health
        self._stats = stats
        self._config = config
        self._runtime = runtime
        self._processes = processes
        self._plugins = plugins

    # -- flat frontend view ------------------------------------------------

    def session_payload_fields(self) -> Dict[str, Any]:
        state = self._session.session_state()
        return {
            "session_mode": state["mode"],
            "session_phase": state["phase"],
            "session_started_at": state["started_at"],
            "session_until": state["until"],
            "session_phase_ends_at": state["phase_ends_at"],
            "session_cycle": state["cycle"],
            "session_cycles": state["cycles"],
            "session_work_minutes": state["work_minutes"],
            "session_break_minutes": state["break_minutes"],
            "session_elapsed": state["elapsed"],
            "session_remaining": state["remaining"],
        }

    def focus_state(self) -> Dict[str, Any]:
        recent = self.recent_list()
        return {
            "ok": True,
            "active": self._session.active(),
            "since": self._session.session_since(),
            "pause_until": self._session.pause_until(),
            "paused": self._session.paused(),
            "url_patterns": self._rules.url_patterns(),
            "manual_session": self._session.manual_session_active(),
            "delegated": self._session.delegated(),
            "delegated_active": self._session.delegated_active(),
            "delegated_until": self._session.nearest_deadline(),
            "delegated_ttl": self._session.nearest_ttl(),
            "delegated_source": self._session.describe(),
            "enforcement_mode": self._config.enforcement.mode,
            "enforcement_enabled": bool(self._config.enforcement.enabled),
            **self.session_payload_fields(),
            "killed_total": int(self._stats.get("killed") or 0),
            "degraded": self._health.degraded,
            "scan_failures": self._health.scan_failures,
            "last_scan_error": self._health.last_scan_error,
            "recent_names": [item["name"] for item in recent],
            "recent_counts": [int(item["count"]) for item in recent],
            "recent_last_at": [float(item["last_at"]) for item in recent],
            "recent_rules": [str(item["rule"]) for item in recent],
            "protect_count": len(self._rules.merged().protect_names),
            "pid": int(self._runtime.pid or 0),
            "version": self._runtime.version,
        }

    def recent_list(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": episode.name,
                "rule": episode.rule,
                "count": episode.count,
                "roots": episode.roots,
                "first_at": episode.first_at,
                "last_at": episode.last_at,
                "duration_s": episode.duration_s,
            }
            for episode in self._enforcer.episodes()
        ]

    # -- full status -------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        rules = self._rules.merged()
        claims = self._session.claims()
        return {
            "ok": True,
            "version": self._runtime.version,
            "active": self._session.active(),
            "manual_session": self._session.manual_session_active(),
            "delegated": self._session.delegated(),
            "delegated_active": self._session.delegated_active(),
            "delegated_until": self._session.nearest_deadline(),
            "delegated_ttl": self._session.nearest_ttl(),
            "delegated_source": self._session.describe(),
            "session_since": self._session.session_since(),
            "session_state": self._session.session_state(),
            **self.session_payload_fields(),
            "pause_until": self._session.pause_until(),
            "enforcement": {
                "enabled": bool(self._config.enforcement.enabled),
                "mode": self._config.enforcement.mode,
                "max_per_scan": int(self._config.enforcement.max_per_scan),
            },
            "blacklist": {
                "names": sorted(rules.names),
                "cmdline_substrings": list(rules.cmdline_substrings),
                "pids": sorted(rules.pids),
            },
            "protect_count": len(rules.protect_names),
            "url_patterns": self._rules.url_patterns(),
            "killed_total": int(self._stats.get("killed") or 0),
            "degraded": self._health.degraded,
            "scan_failures": self._health.scan_failures,
            "last_scan_error": self._health.last_scan_error,
            "recent": self.recent_list(),
            "scan_report": self._scan.last_result().to_wire(),
            "scan_ms": self._scan.scan_ms(),
            "health": self._health.to_dict(),
            "stats": dict(self._stats),
            "claims": claims,
            "sources": {
                source: len(entry.get("names") or ())
                + len(entry.get("cmdline_substrings") or ())
                for source, entry in self._rules.sources().items()
            },
            "plugins": self._plugins.payloads(),
            "addons": self._runtime.addons(),
            "event_seq": self._runtime.event_seq(),
            "plugins_path": getattr(self._config, "plugins_path", ""),
        }

    # -- process picker ----------------------------------------------------

    def visible_processes(self, limit: int = 400) -> List[Dict[str, Any]]:
        return self._processes.list(limit)

    def visible_process_arrays(self, limit: int = 400) -> Dict[str, Any]:
        return self._processes.arrays(limit)
