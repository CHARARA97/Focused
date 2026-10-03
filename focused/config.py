"""Focused configuration: rules, freeze settings, the browser list, the session.

Focused is a standalone application, so it keeps its own files under
``~/.config/focused`` and ``~/.local/share/focused`` and never reads ChillFocus's
config.  The rule engine underneath lives in ``focused.core`` (procfs, cgroup
rules, the freezer, the rule evaluator): one copy of the dangerous half, covered
by the engine's own tests.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core.protect import DEFAULT_PROTECT_NAMES
from .core.rules import RuleSet

APP_NAME = "focused"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
LOG_LEVELS = ("debug", "info", "warning", "error")


class FocusedConfigError(Exception):
    """Raised when Focused's own configuration cannot be trusted."""


@dataclass
class HttpConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    #: The port ChillFocus's ``delegate.base_url`` points at by default.
    port: int = 8766
    token: str = ""


@dataclass
class FreezeConfig:
    enabled: bool = True
    max_per_scan: int = 20
    confirm_timeout_ms: int = 1500
    use_units: bool = True
    state_path: str = ""


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
    freeze: FreezeConfig = field(default_factory=FreezeConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    urls: UrlConfig = field(default_factory=UrlConfig)
    scan_interval: float = 1.0
    dry_run: bool = False
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
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config"
        )
        return os.path.join(base, APP_NAME)

    @staticmethod
    def data_dir() -> str:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share"
        )
        return os.path.join(base, APP_NAME)

    @classmethod
    def default_path(cls) -> str:
        override = os.environ.get("FOCUSED_CONFIG")
        if override:
            return os.path.expanduser(override)
        return os.path.join(cls.config_dir(), "config.json")

    @classmethod
    def default_audit_path(cls) -> str:
        return os.path.join(cls.data_dir(), "audit.jsonl")

    @classmethod
    def protect_names_path(cls) -> Path:
        return Path(cls.config_dir()) / "protect_names.txt"

    @classmethod
    def defaults(cls) -> "Config":
        return cls(
            audit_log=cls.default_audit_path(),
            plugins_path=os.path.join(cls.config_dir(), "plugins"),
            registry_path=os.path.join(cls.data_dir(), "plugins.json"),
            freeze=FreezeConfig(
                state_path=os.path.join(cls.data_dir(), "frozen.json")
            ),
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
        try:
            with open(resolved, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise FocusedConfigError("%s is not valid JSON: %s" % (resolved, exc)) from exc
        if not isinstance(data, dict):
            raise FocusedConfigError("%s must contain a JSON object" % resolved)
        config = cls.from_dict(data, path=resolved)
        return config

    @classmethod
    def from_dict(cls, data: Dict[str, Any], path: Optional[str] = None) -> "Config":
        base = cls.defaults()
        http_raw = _obj(data, "http")
        freeze_raw = _obj(data, "freeze")
        session_raw = _obj(data, "session")
        urls_raw = _obj(data, "urls")
        blacklist = _obj(data, "blacklist")
        protect = _obj(data, "protect")

        config = cls(
            http=HttpConfig(
                enabled=_bool(http_raw, "enabled", True),
                host=_str(http_raw, "host", base.http.host),
                port=_int(http_raw, "port", base.http.port),
                token=_str(http_raw, "token", ""),
            ),
            freeze=FreezeConfig(
                enabled=_bool(freeze_raw, "enabled", True),
                max_per_scan=_int(freeze_raw, "max_per_scan", 20),
                confirm_timeout_ms=_int(freeze_raw, "confirm_timeout_ms", 1500),
                use_units=_bool(freeze_raw, "use_units", True),
                state_path=_str(
                    freeze_raw,
                    "state_path",
                    os.path.join(cls.data_dir(), "frozen.json"),
                ),
            ),
            session=SessionConfig(
                mode=_session_mode(session_raw, "mode", "countdown"),
                default_minutes=_int(session_raw, "default_minutes", 25),
                pause_minutes=_int(session_raw, "pause_minutes", 10),
                pomodoro_work_minutes=_int(session_raw, "pomodoro_work_minutes", 25),
                pomodoro_break_minutes=_int(session_raw, "pomodoro_break_minutes", 5),
                pomodoro_cycles=_int(session_raw, "pomodoro_cycles", 4),
            ),
            urls=UrlConfig(patterns=_str_list(urls_raw, "patterns")),
            scan_interval=_float(data, "scan_interval", base.scan_interval),
            plugins_path=_str(
                data, "plugins_path", os.path.join(cls.config_dir(), "plugins")
            ),
            registry_path=_str(
                data, "registry_path", os.path.join(cls.data_dir(), "plugins.json")
            ),
            dry_run=_bool(data, "dry_run", False),
            log_level=_str(data, "log_level", base.log_level),
            audit_log=_str(data, "audit_log", base.audit_log),
            warnings=[],
            path=path,
        )

        protect_names = set(base.rules.protect_names)
        protect_names |= {name.lower() for name in _str_list(protect, "names")}
        config.rules = RuleSet.from_dict(
            {
                "names": _str_list(blacklist, "names"),
                "cmdline_substrings": _str_list(blacklist, "cmdline_substrings"),
                "pids": _int_list(blacklist, "pids"),
                "pid_guards": {},
                "protect_names": sorted(protect_names),
                "protect_cmdline_substrings": _str_list(
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
        if self.scan_interval < 0.1:
            raise FocusedConfigError("scan_interval must be >= 0.1")
        if self.freeze.max_per_scan < 1:
            raise FocusedConfigError("freeze.max_per_scan must be >= 1")
        if self.freeze.confirm_timeout_ms < 100:
            raise FocusedConfigError("freeze.confirm_timeout_ms must be >= 100")
        if not self.freeze.state_path:
            raise FocusedConfigError(
                "freeze.state_path must not be empty: without it a crash could leave "
                "applications frozen forever"
            )
        if self.session.default_minutes < 1:
            raise FocusedConfigError("session.default_minutes must be >= 1")

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
            "dry_run": self.dry_run,
            "plugins_path": self.plugins_path,
            "registry_path": self.registry_path,
            "log_level": self.log_level,
            "audit_log": self.audit_log,
            "freeze": {
                "enabled": self.freeze.enabled,
                "max_per_scan": self.freeze.max_per_scan,
                "confirm_timeout_ms": self.freeze.confirm_timeout_ms,
                "use_units": self.freeze.use_units,
                "state_path": self.freeze.state_path,
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
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, target)
        self.path = target
        return target


def load_protect_names() -> frozenset:
    """Built-in rails plus whatever the user added to ``protect_names.txt``."""
    names = {name.lower() for name in DEFAULT_PROTECT_NAMES}
    path = Config.protect_names_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                entry = line.strip()
                if entry and not entry.startswith("#"):
                    names.add(entry.lower())
    except OSError:
        pass
    return frozenset(names)


# ---------------------------------------------------------------------------
# typed readers
# ---------------------------------------------------------------------------


def _obj(data: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise FocusedConfigError("%s must be an object" % key)
    return value


#: The three session shapes the app offers.
SESSION_MODES = ("countdown", "pomodoro", "stopwatch")


def _session_mode(data: Dict[str, Any], key: str, default: str) -> str:
    """A session mode that we actually implement, or the default."""
    value = data.get(key)
    if isinstance(value, str) and value.strip().lower() in SESSION_MODES:
        return value.strip().lower()
    return default


def _bool(data: Dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if isinstance(value, bool):
        return value
    raise FocusedConfigError("%s.%s must be true or false" % ("config", key))


def _int(data: Dict[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise FocusedConfigError("%s must be an integer" % key)
    return value


def _float(data: Dict[str, Any], key: str, default: float) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FocusedConfigError("%s must be a number" % key)
    return float(value)


def _str(data: Dict[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise FocusedConfigError("%s must be a string" % key)
    return value


def _str_list(data: Dict[str, Any], key: str) -> List[str]:
    value = data.get(key) or []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise FocusedConfigError("%s must be an array of strings" % key)
    return [item.strip() for item in value if item.strip()]


def _int_list(data: Dict[str, Any], key: str) -> List[int]:
    value = data.get(key) or []
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in value
    ):
        raise FocusedConfigError("%s must be an array of integers" % key)
    return list(value)
