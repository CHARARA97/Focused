from __future__ import annotations

from typing import Any, Dict


class ConfigView:
    def __init__(self, config: Any, rules: Any, session: Any) -> None:
        self._config = config
        self._rules = rules
        self._session = session

    def to_dict(self) -> Dict[str, Any]:
        config = self._config
        source = self._rules.sources().get("config") or {}
        return {
            "ok": True,
            "path": config.path or "",
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
            "enforcement": {
                "enabled": config.enforcement.enabled,
                "mode": config.enforcement.mode,
                "max_per_scan": config.enforcement.max_per_scan,
            },
            "plugins_path": getattr(config, "plugins_path", ""),
            "protected_count": len(self._rules.merged().protect_names),
        }
