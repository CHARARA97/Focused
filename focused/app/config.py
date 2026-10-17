"""Partial configuration updates.

Only the fields the UI may safely change are accepted; port/host/state path need
a restart and are reported as warnings.  The service mutates the persisted
``Config`` and keeps the runtime rule/session/freeze state in sync through
callbacks, so engine.update_config stays a thin delegate.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Callable, Dict, List, Optional

from ..config import SESSION_MODES
from ..core import urlfilter
from ..core.rules import RuleSet


def _config_blacklist(config: Any) -> Dict[str, Any]:
    return {
        "names": set(config.rules.names),
        "cmdline_substrings": list(config.rules.cmdline_substrings),
        "pids": set(config.rules.pids),
    }


def _config_protect(config: Any) -> set:
    return set(config.rules.protect_names)


class ConfigService:
    def __init__(
        self,
        config: Any,
        rules: Any,
        *,
        dry_run: Callable[[], bool],
        set_dry_run: Callable[[bool], bool],
        set_freeze_enabled: Callable[[bool], None],
        thaw_all: Callable[[str], int],
        apply_rules: Callable[..., Any],
        sources: Callable[[], Dict[str, Any]],
        set_url_patterns: Callable[..., List[str]],
        config_dict: Callable[[], Dict[str, Any]],
        emit: Callable[..., Any],
        set_http_token: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._config = config
        self._rules = rules
        self._dry_run = dry_run
        self._set_dry_run = set_dry_run
        self._set_freeze_enabled = set_freeze_enabled
        self._thaw_all = thaw_all
        self._apply_rules = apply_rules
        self._sources = sources
        self._set_url_patterns = set_url_patterns
        self._config_dict = config_dict
        self._emit = emit
        self._set_http_token = set_http_token

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        patch = patch or {}
        warnings: List[str] = []
        candidate = copy.deepcopy(self._config)
        prepared_rules = None

        # 1. Apply everything to a candidate Config.  Nothing runtime-visible
        # changes until every validation has passed.
        if "dry_run" in patch:
            candidate.dry_run = bool(patch["dry_run"])
        if "scan_interval" in patch:
            value = float(patch["scan_interval"])
            if not math.isfinite(value) or not 0.1 <= value <= 3600.0:
                raise ValueError("scan_interval must be a finite number in [0.1, 3600]")
            candidate.scan_interval = value
        if isinstance(patch.get("session"), dict):
            session = patch["session"]
            if "mode" in session:
                mode = str(session["mode"]).strip().lower()
                if mode not in SESSION_MODES:
                    raise ValueError("mode must be one of: %s" % ", ".join(SESSION_MODES))
                candidate.session.mode = mode
            if "default_minutes" in session:
                candidate.session.default_minutes = max(1, int(session["default_minutes"]))
            if "pause_minutes" in session:
                candidate.session.pause_minutes = max(1, int(session["pause_minutes"]))
            if "pomodoro_work_minutes" in session:
                candidate.session.pomodoro_work_minutes = max(
                    1, int(session["pomodoro_work_minutes"])
                )
            if "pomodoro_break_minutes" in session:
                candidate.session.pomodoro_break_minutes = max(
                    1, int(session["pomodoro_break_minutes"])
                )
            if "pomodoro_cycles" in session:
                candidate.session.pomodoro_cycles = max(0, int(session["pomodoro_cycles"]))
        if isinstance(patch.get("http"), dict):
            http = patch["http"]
            if "token" in http:
                candidate.http.token = str(http["token"] or "")
            for forbidden in ("port", "host", "enabled"):
                if forbidden in http:
                    warnings.append("http.%s needs a restart" % forbidden)
        if isinstance(patch.get("freeze"), dict):
            freeze = patch["freeze"]
            if "enabled" in freeze:
                candidate.freeze.enabled = bool(freeze["enabled"])
            if "max_per_scan" in freeze:
                candidate.freeze.max_per_scan = max(1, int(freeze["max_per_scan"]))
            for forbidden in ("state_path", "use_units", "confirm_timeout_ms"):
                if forbidden in freeze:
                    warnings.append("freeze.%s needs a restart" % forbidden)
        if isinstance(patch.get("urls"), dict) and "patterns" in patch["urls"]:
            patterns = patch["urls"]["patterns"]
            if isinstance(patterns, (str, bytes)):
                raise ValueError("urls.patterns must be a sequence")
            candidate.urls.patterns = urlfilter.sanitize(patterns)

        if isinstance(patch.get("blacklist"), dict) or isinstance(patch.get("protect"), dict):
            blacklist = patch.get("blacklist") or {}
            protect = patch.get("protect") or {}
            entry = {
                "names": blacklist.get("names", sorted(_config_blacklist(candidate)["names"])),
                "cmdline_substrings": blacklist.get(
                    "cmdline_substrings", _config_blacklist(candidate)["cmdline_substrings"]
                ),
                "pids": blacklist.get("pids", sorted(_config_blacklist(candidate)["pids"])),
                "protect_names": protect.get("names", sorted(_config_protect(candidate))),
                "protect_cmdline_substrings": protect.get(
                    "cmdline_substrings", list(candidate.rules.protect_cmdline_substrings)
                ),
            }
            # Pure validation: dead/reused PIDs raise here, before any commit.
            prepared_rules = self._rules.preview("config", entry)

        # 2. Commit.  Rules first: if a PID vanished between preview and apply,
        # it is still before any Config field has changed.
        if prepared_rules is not None:
            self._apply_rules(prepared_rules, source="config")
            candidate.rules = RuleSet.from_dict(self._sources()["config"])

        self._config.__dict__.update(candidate.__dict__)
        self._set_dry_run(candidate.dry_run)
        self._set_freeze_enabled(candidate.freeze.enabled)
        if not candidate.freeze.enabled:
            self._thaw_all("freeze-disabled")
        if self._set_http_token is not None:
            self._set_http_token(candidate.http.token)
        self._set_url_patterns(candidate.urls.patterns, source="config")

        # 3. Persist.  A save failure is a warning, not a rollback: the running
        # process has already committed the same values.
        if candidate.path:
            try:
                candidate.save(candidate.path)
            except OSError as exc:
                warnings.append("could not write %s: %s" % (candidate.path, exc))
        else:
            warnings.append("no config path: changes apply until Focused restarts")

        payload = self._config_dict()
        payload["warnings"] = warnings
        self._emit("config", detail="configuration updated")
        return payload
