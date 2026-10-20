"""Focused configuration: the model, its validation and its persistence.

The file layout, the typed readers and the error type live next to it
(``config_io``, ``config_parse``, ``config_errors``); this module owns the
model and the `load` / `save` lifecycle.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config_errors import FocusedConfigError
from .config_io import (
    config_dir,
    data_dir,
    default_audit_path,
    default_config_path,
    load_protect_names,
    protect_names_path,
    read_json,
    write_json,
)
from .config_parse import (
    bool_field,
    enforcement_mode,
    float_field,
    int_field,
    int_list_field,
    object_field,
    session_mode,
    str_field,
    str_list_field,
)
from .core.protect import DEFAULT_PROTECT_NAMES
from .core.rules import RuleSet
from .domain.policy import ENFORCEMENT_MODES
from .domain.session_timer import SESSION_MODES

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
LOG_LEVELS = ("debug", "info", "warning", "error")

@dataclass
class HttpConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    #: The port ChillFocus's ``delegate.base_url`` points at by default.
    port: int = 8766
    token: str = ""



@dataclass
class EnforcementConfig:
    enabled: bool = True
    mode: str = "audit"
    max_per_scan: int = 20


@dataclass
class SessionConfig:
    """A session somebody started inside Focused itself (no game involved)."""

    #: countdown = a fixed block, pomodoro = work/break rounds, stopwatch = open-ended.
    mode: str = "countdown"
    default_minutes: int = 25
    pause_minutes: int = 10
    pomodoro_work_minutes: int = 25
    pomodoro_break_minutes: int = 5
    #: 0 means "keep going until told to stop".
    pomodoro_cycles: int = 4


@dataclass
class UrlConfig:
    """Published to the browser extension through ``GET /api/v1/focus``."""

    patterns: List[str] = field(default_factory=list)


@dataclass
class Config:
    http: HttpConfig = field(default_factory=HttpConfig)
    enforcement: EnforcementConfig = field(default_factory=EnforcementConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    urls: UrlConfig = field(default_factory=UrlConfig)
    scan_interval: float = 1.0
    #: In-process plugins: ``~/.config/focused/plugins/*.py`` by default.
    plugins_path: str = ""
    #: Where the plugin registry is written.  Deliberately *not* the same path as
    #: ``plugins_path``: one is a directory of modules, the other a JSON record.
    registry_path: str = ""
    log_level: str = "info"
    audit_log: str = ""
    rules: RuleSet = field(default_factory=RuleSet)
    warnings: List[str] = field(default_factory=list)
    path: Optional[str] = None

    # -- paths -------------------------------------------------------------

    @staticmethod
    def config_dir() -> str:
        return config_dir()

    @staticmethod
    def data_dir() -> str:
        return data_dir()

    @classmethod
    def default_path(cls) -> str:
        return default_config_path()

    @classmethod
    def default_audit_path(cls) -> str:
        return default_audit_path()

    @classmethod
    def protect_names_path(cls):
        return protect_names_path()

    @classmethod
    def defaults(cls) -> "Config":
        return cls(
            audit_log=cls.default_audit_path(),
            plugins_path=os.path.join(cls.config_dir(), "plugins"),
            registry_path=os.path.join(cls.data_dir(), "plugins.json"),
            enforcement=EnforcementConfig(),
            rules=RuleSet(protect_names=load_protect_names()),
        )

    # -- loading -----------------------------------------------------------

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Config":
        resolved = os.path.expanduser(path) if path else cls.default_path()
        base = cls.defaults()
        if not os.path.exists(resolved):
            base.path = resolved
            base.warnings.append(
                "no config file at %s; using built-in defaults (no blacklist yet)"
                % resolved
            )
            return base
        config = cls.from_dict(read_json(resolved), path=resolved)
        return config

    @classmethod
    def from_dict(cls, data: Dict[str, Any], path: Optional[str] = None) -> "Config":
        base = cls.defaults()
        http_raw = object_field(data, "http")
        enforcement_raw = object_field(data, "enforcement")
        legacy = data.get("freeze")
        legacy_warning = (
            "the freeze section is gone; its settings were ignored"
            if legacy is not None
            else ""
        )
        session_raw = object_field(data, "session")
        urls_raw = object_field(data, "urls")
        blacklist = object_field(data, "blacklist")
        protect = object_field(data, "protect")

        config = cls(
            http=HttpConfig(
                enabled=bool_field(http_raw, "enabled", True),
                host=str_field(http_raw, "host", base.http.host),
                port=int_field(http_raw, "port", base.http.port),
                token=str_field(http_raw, "token", ""),
            ),
            warnings=[legacy_warning] if legacy_warning else [],
            enforcement=EnforcementConfig(
                enabled=bool_field(enforcement_raw, "enabled", True),
                mode=enforcement_mode(enforcement_raw, "mode", "audit"),
                max_per_scan=int_field(enforcement_raw, "max_per_scan", 20),
            ),
            session=SessionConfig(
                mode=session_mode(session_raw, "mode", "countdown"),
                default_minutes=int_field(session_raw, "default_minutes", 25),
                pause_minutes=int_field(session_raw, "pause_minutes", 10),
                pomodoro_work_minutes=int_field(session_raw, "pomodoro_work_minutes", 25),
                pomodoro_break_minutes=int_field(session_raw, "pomodoro_break_minutes", 5),
                pomodoro_cycles=int_field(session_raw, "pomodoro_cycles", 4),
            ),
            urls=UrlConfig(patterns=str_list_field(urls_raw, "patterns")),
            scan_interval=float_field(data, "scan_interval", base.scan_interval),
            plugins_path=str_field(
                data, "plugins_path", os.path.join(cls.config_dir(), "plugins")
            ),
            registry_path=str_field(
                data, "registry_path", os.path.join(cls.data_dir(), "plugins.json")
            ),
            log_level=str_field(data, "log_level", base.log_level),
            audit_log=str_field(data, "audit_log", base.audit_log),
            path=path,
        )

        protect_names = set(base.rules.protect_names)
        protect_names |= {name.lower() for name in str_list_field(protect, "names")}
        config.rules = RuleSet.from_dict(
            {
                "names": str_list_field(blacklist, "names"),
                "cmdline_substrings": str_list_field(blacklist, "cmdline_substrings"),
                "pids": int_list_field(blacklist, "pids"),
                "pid_guards": {},
                "protect_names": sorted(protect_names),
                "protect_cmdline_substrings": str_list_field(
                    protect, "cmdline_substrings"
                ),
            }
        )

        config._validate()
        return config

    def _validate(self) -> None:
        if not (1 <= self.http.port <= 65535):
            raise FocusedConfigError("http.port must be between 1 and 65535")
        if self.http.host not in LOOPBACK_HOSTS:
            raise FocusedConfigError(
                "http.host must be a loopback address %s (got %r): this API freezes "
                "processes and must not be exposed to the network."
                % (sorted(LOOPBACK_HOSTS), self.http.host)
            )
        if not math.isfinite(self.scan_interval) or not 0.1 <= self.scan_interval <= 3600.0:
            raise FocusedConfigError(
                "scan_interval must be a finite number in [0.1, 3600]"
            )
        if self.enforcement.mode not in ENFORCEMENT_MODES:
            raise FocusedConfigError(
                "enforcement.mode must be one of: %s" % ", ".join(ENFORCEMENT_MODES)
            )
        if self.enforcement.max_per_scan < 1:
            raise FocusedConfigError("enforcement.max_per_scan must be >= 1")
        if self.session.default_minutes < 1:
            raise FocusedConfigError("session.default_minutes must be >= 1")
        problems = self.rules.problems()
        if problems:
            raise FocusedConfigError(
                "unsafe blacklist: " + "; ".join(str(problem) for problem in problems)
            )

    # -- saving ------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "http": {
                "enabled": self.http.enabled,
                "host": self.http.host,
                "port": self.http.port,
                "token": self.http.token,
            },
            "scan_interval": self.scan_interval,
            "plugins_path": self.plugins_path,
            "registry_path": self.registry_path,
            "log_level": self.log_level,
            "audit_log": self.audit_log,
            "enforcement": {
                "enabled": self.enforcement.enabled,
                "mode": self.enforcement.mode,
                "max_per_scan": self.enforcement.max_per_scan,
            },
            "session": {
                "mode": self.session.mode,
                "default_minutes": self.session.default_minutes,
                "pause_minutes": self.session.pause_minutes,
                "pomodoro_work_minutes": self.session.pomodoro_work_minutes,
                "pomodoro_break_minutes": self.session.pomodoro_break_minutes,
                "pomodoro_cycles": self.session.pomodoro_cycles,
            },
            "urls": {"patterns": list(self.urls.patterns)},
            "blacklist": {
                "names": sorted(self.rules.names),
                "cmdline_substrings": list(self.rules.cmdline_substrings),
                "pids": sorted(self.rules.pids),
            },
            "protect": {
                "names": sorted(self.rules.protect_names - DEFAULT_PROTECT_NAMES),
                "cmdline_substrings": list(self.rules.protect_cmdline_substrings),
            },
        }

    def save(self, path: Optional[str] = None) -> str:
        target = os.path.expanduser(path) if path else (self.path or self.default_path())
        self.path = write_json(target, self.to_dict())
        return self.path
