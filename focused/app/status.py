"""Read-only snapshots: status, focus state, config view and process picker.

These are formatting/query use cases, not state owners.  Moving them out keeps
the engine from becoming the place where every response shape is assembled.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from ..core.rules import ACTION_PROTECT, evaluate


class StatusService:
    def __init__(
        self,
        *,
        clock: Callable[[], float],
        rules: Any,
        session: Any,
        freeze: Any,
        scan: Any,
        registry: Any,
        health: Any,
        stats: Dict[str, Any],
        recent: Any,
        config: Any,
        procfs: Any,
        uid: Any,
        self_pid: int,
        self_cgroup: Any,
        unified_cgroup2: bool,
        dry_run: Callable[[], bool],
        freeze_enabled: Callable[[], bool],
        event_seq: Callable[[], int],
        addons: Callable[[], List[str]],
        version: str,
    ) -> None:
        self._clock = clock
        self._rules = rules
        self._session = session
        self._freeze = freeze
        self._scan = scan
        self._registry = registry
        self._health = health
        self._stats = stats
        self._recent = recent
        self._config = config
        self._procfs = procfs
        self._uid = uid
        self._self_pid = self_pid
        self._self_cgroup = self_cgroup
        self._unified_cgroup2 = unified_cgroup2
        self._dry_run = dry_run
        self._freeze_enabled = freeze_enabled
        self._event_seq = event_seq
        self._addons = addons
        self._version = version

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
        entries = self._freeze.frozen()
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
            "dry_run": self._dry_run(),
            **self.session_payload_fields(),
            "frozen_count": self._freeze.frozen_count(),
            "frozen_recorded_count": len(self._freeze.recorded_pids()),
            "degraded": self._health.degraded,
            "scan_failures": self._health.scan_failures,
            "last_scan_error": self._health.last_scan_error,
            "frozen_names": [entry.name for entry in entries],
            "frozen_units": [entry.unit for entry in entries if entry.unit],
            "frozen_total": int(self._stats.get("frozen") or 0),
            "recent_names": [item.get("name", "") for item in list(self._recent)],
            "protect_count": len(self._rules.merged().protect_names),
            "pid": int(self._self_pid or 0),
            "version": self._version,
        }

    def frozen_list(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": entry.name,
                "pid": entry.pid,
                "mode": entry.mode,
                "unit": entry.unit,
                "cgroup": entry.cgroup,
                "since": entry.since,
                "reason": entry.reason,
                "pids": list(entry.pids),
            }
            for entry in self._freeze.frozen()
        ]

    # -- full status -------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        rules = self._rules.merged()
        claims = self._session.claims()
        return {
            "ok": True,
            "version": self._version,
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
            "dry_run": self._dry_run(),
            "freeze_enabled": self._freeze_enabled(),
            "unified_cgroup2": self._unified_cgroup2,
            "self_cgroup": self._self_cgroup or "",
            "state_path": self._config.freeze.state_path,
            "blacklist": {
                "names": sorted(rules.names),
                "cmdline_substrings": list(rules.cmdline_substrings),
                "pids": sorted(rules.pids),
            },
            "protect_count": len(rules.protect_names),
            "url_patterns": self._rules.url_patterns(),
            "frozen_count": self._freeze.frozen_count(),
            "frozen_recorded_count": len(self._freeze.recorded_pids()),
            "degraded": self._health.degraded,
            "scan_failures": self._health.scan_failures,
            "last_scan_error": self._health.last_scan_error,
            "frozen_names": [entry.name for entry in self._freeze.frozen()],
            "frozen": self.frozen_list(),
            "recent": list(self._recent),
            "scan_ms": self._scan.scan_ms(),
            "health": self._health.to_dict(),
            "stats": dict(self._stats),
            "claims": claims,
            "sources": {
                source: len(entry.get("names") or ())
                + len(entry.get("cmdline_substrings") or ())
                for source, entry in self._rules.sources().items()
            },
            "plugins": self.plugin_payloads(),
            "addons": self._addons(),
            "event_seq": self._event_seq(),
            "plugins_path": getattr(self._config, "plugins_path", ""),
        }

    def plugin_payloads(self) -> List[Dict[str, Any]]:
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

    # -- config view -------------------------------------------------------

    def config_dict(self) -> Dict[str, Any]:
        config = self._config
        source = self._rules.sources().get("config") or {}
        return {
            "ok": True,
            "path": config.path or "",
            "dry_run": self._dry_run(),
            "scan_interval": config.scan_interval,
            "blacklist": {
                "names": sorted(source.get("names") or ()),
                "cmdline_substrings": list(source.get("cmdline_substrings") or ()),
                "pids": sorted(source.get("pids") or ()),
            },
            "protect": {
                "names": sorted(source.get("protect_names") or ()),
                "cmdline_substrings": list(source.get("protect_cmdline_substrings") or ()),
            },
            "urls": {"patterns": self._rules.url_patterns()},
            "session": {
                "mode": config.session.mode,
                "default_minutes": config.session.default_minutes,
                "pause_minutes": config.session.pause_minutes,
                "pomodoro_work_minutes": config.session.pomodoro_work_minutes,
                "pomodoro_break_minutes": config.session.pomodoro_break_minutes,
                "pomodoro_cycles": config.session.pomodoro_cycles,
            },
            "session_state": self._session.session_state(),
            "http": {
                "enabled": config.http.enabled,
                "host": config.http.host,
                "port": config.http.port,
                "token_set": bool(config.http.token),
            },
            "freeze": {
                "enabled": config.freeze.enabled,
                "max_per_scan": config.freeze.max_per_scan,
                "use_units": config.freeze.use_units,
                "state_path": config.freeze.state_path,
            },
            "plugins_path": getattr(config, "plugins_path", ""),
            "protected_count": len(self._rules.merged().protect_names),
        }

    # -- process picker ----------------------------------------------------

    def visible_processes(self, limit: int = 400) -> List[Dict[str, Any]]:
        counts: Dict[str, int] = {}
        shielded: Dict[str, str] = {}
        rules = self._rules.merged()
        context = self._freeze.eval_context()
        for proc in self._procfs.snapshot():
            if proc.is_kernel_thread or proc.is_zombie:
                continue
            if self._uid is not None and proc.uid is not None and proc.uid != self._uid:
                continue
            name = proc.comm or proc.exe_basename
            if not name:
                continue
            counts[name] = counts.get(name, 0) + 1
            if name not in shielded:
                decision = evaluate(proc, rules, context)
                if decision.action == ACTION_PROTECT:
                    shielded[name] = decision.rule
        items = [
            {
                "name": name,
                "count": counts[name],
                "protected": name in shielded,
                "protect_rule": shielded.get(name, ""),
            }
            for name in sorted(counts)
        ]
        return items[: max(1, int(limit or 400))]

    def visible_process_arrays(self, limit: int = 400) -> Dict[str, Any]:
        items = self.visible_processes(limit)
        return {
            "names": [item["name"] for item in items],
            "counts": [int(item["count"]) for item in items],
            "protected_flags": [bool(item["protected"]) for item in items],
            "protect_rules": [item["protect_rule"] for item in items],
        }
