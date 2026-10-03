"""Plugins: the registry of everything that connects to Focused.

Two kinds, one registry:

``http``
    Anything that speaks the loopback API: the game plugin, the browser
    extension, a shell script, somebody else's little helper.  A plugin gets an
    id and a token at registration time, then contributes rules and claims
    sessions under that id.

``python``
    Optional modules in ``~/.config/focused/plugins/*.py``, loaded into this
    process.  They get an engine handle and can therefore do things an HTTP
    plugin cannot (react to events inside a scan, contribute rules on the fly).
    They are fully trusted -- same process, same user -- which is exactly why the
    documentation tells people to write HTTP plugins unless they need this.

The registry is deliberately small: it records who is allowed to do what, and it
is persisted as JSON so a restart does not make every plugin register again.
"""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

KIND_HTTP = "http"
KIND_PYTHON = "python"

STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled"
STATUS_FAILED = "failed"


@dataclass
class PluginRecord:
    id: str
    name: str
    kind: str = KIND_HTTP
    version: str = ""
    token: str = ""
    status: str = STATUS_ENABLED
    webhook_url: str = ""
    registered_at: float = 0.0
    last_seen: float = 0.0
    rules_count: int = 0
    session_ttl: float = 0.0
    errors: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "version": self.version,
            "token": self.token,
            "status": self.status,
            "webhook_url": self.webhook_url,
            "registered_at": self.registered_at,
            "last_seen": self.last_seen,
            "rules_count": self.rules_count,
            "session_ttl": self.session_ttl,
            "errors": self.errors,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PluginRecord":
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or data.get("id") or ""),
            kind=str(data.get("kind") or KIND_HTTP),
            version=str(data.get("version") or ""),
            token=str(data.get("token") or ""),
            status=str(data.get("status") or STATUS_ENABLED),
            webhook_url=str(data.get("webhook_url") or ""),
            registered_at=float(data.get("registered_at") or 0.0),
            last_seen=float(data.get("last_seen") or 0.0),
            rules_count=int(data.get("rules_count") or 0),
            session_ttl=float(data.get("session_ttl") or 0.0),
            errors=int(data.get("errors") or 0),
        )


def slugify(name: str) -> str:
    """A stable, URL-safe id from a plugin's name."""
    keep = []
    for ch in (name or "").strip().lower():
        if ch.isalnum() or ch in ("-", "_", "."):
            keep.append(ch)
        elif ch in (" ", "/"):
            keep.append("-")
    slug = "".join(keep).strip("-.") or "plugin"
    return slug[:40]


class PluginRegistry:
    """Who is connected, and are they allowed to be."""

    def __init__(self, path: str, logger=None, clock=time.time) -> None:
        self.path = path
        self._logger = logger
        self._clock = clock
        self._lock = threading.RLock()
        self._plugins: Dict[str, PluginRecord] = {}

    # -- persistence -------------------------------------------------------

    def load(self) -> List[PluginRecord]:
        with self._lock:
            self._plugins = {}
            if not self.path or not os.path.exists(self.path):
                return []
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, ValueError) as exc:
                self._log("error", "plugin registry unreadable (%s): %s" % (self.path, exc))
                return []
            for item in (data.get("plugins") if isinstance(data, dict) else None) or ():
                if not isinstance(item, dict):
                    continue
                record = PluginRecord.from_dict(item)
                if record.id:
                    self._plugins[record.id] = record
            return list(self._plugins.values())

    def save(self) -> None:
        with self._lock:
            payload = {
                "version": 1,
                "updated": self._clock(),
                "plugins": [self._plugins[key].to_dict() for key in sorted(self._plugins)],
            }
        parent = os.path.dirname(self.path)
        try:
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
            os.replace(tmp, self.path)
        except OSError as exc:
            self._log("error", "cannot write plugin registry %s: %s" % (self.path, exc))

    # -- logging -----------------------------------------------------------

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            return
        getattr(self._logger, level, self._logger.info)(message)

    # -- registry ----------------------------------------------------------

    def register(
        self,
        name: str,
        version: str = "",
        kind: str = KIND_HTTP,
        webhook_url: str = "",
        plugin_id: Optional[str] = None,
    ) -> PluginRecord:
        """Register a plugin (or refresh an existing registration)."""
        with self._lock:
            pid = slugify(plugin_id or name)
            existing = self._plugins.get(pid)
            if existing is not None:
                existing.name = name or existing.name
                existing.version = version or existing.version
                existing.kind = kind or existing.kind
                if webhook_url:
                    existing.webhook_url = webhook_url
                existing.last_seen = self._clock()
                record = existing
            else:
                record = PluginRecord(
                    id=pid,
                    name=name or pid,
                    kind=kind,
                    version=version,
                    token=secrets.token_urlsafe(24),
                    registered_at=self._clock(),
                    last_seen=self._clock(),
                    webhook_url=webhook_url,
                )
                self._plugins[pid] = record
        self.save()
        self._log("info", "plugin registered: %s (%s)" % (record.id, record.kind))
        return record

    def adopt(self, record: PluginRecord) -> PluginRecord:
        """Insert a record as-is (used for python plugins, which need no token)."""
        with self._lock:
            self._plugins[record.id] = record
        self.save()
        return record

    def get(self, plugin_id: str) -> Optional[PluginRecord]:
        with self._lock:
            return self._plugins.get(plugin_id)

    def list(self) -> List[PluginRecord]:
        with self._lock:
            return [self._plugins[key] for key in sorted(self._plugins)]

    def remove(self, plugin_id: str) -> bool:
        with self._lock:
            removed = self._plugins.pop(plugin_id, None) is not None
        if removed:
            self.save()
        return removed

    def set_status(self, plugin_id: str, status: str) -> Optional[PluginRecord]:
        with self._lock:
            record = self._plugins.get(plugin_id)
            if record is None:
                return None
            record.status = status
        self.save()
        return record

    def touch(self, plugin_id: str) -> None:
        """Record that a plugin just spoke to us."""
        with self._lock:
            record = self._plugins.get(plugin_id)
            if record is not None:
                record.last_seen = self._clock()
                record.session_ttl = record.session_ttl  # kept for the UI's sake
        self.save()

    def note_rules(self, plugin_id: str, count: int) -> None:
        with self._lock:
            record = self._plugins.get(plugin_id)
            if record is not None:
                record.rules_count = count
                record.last_seen = self._clock()
        self.save()

    def note_session(self, plugin_id: str, ttl: float) -> None:
        with self._lock:
            record = self._plugins.get(plugin_id)
            if record is not None:
                record.session_ttl = ttl
                record.last_seen = self._clock()
        self.save()

    def note_error(self, plugin_id: str) -> None:
        with self._lock:
            record = self._plugins.get(plugin_id)
            if record is not None:
                record.errors += 1
        self.save()

    def verify(self, plugin_id: str, token: str) -> bool:
        """Whether a plugin id + token pair is valid and enabled."""
        record = self.get(plugin_id)
        if record is None or record.status != STATUS_ENABLED:
            return False
        if record.kind == KIND_PYTHON:
            return True  # in-process plugins were trusted at load time
        return bool(record.token) and secrets.compare_digest(record.token, token or "")


@dataclass
class AddonResult:
    loaded: List[str] = field(default_factory=list)
    failed: List[Dict[str, str]] = field(default_factory=list)


def load_addons(directory: str, engine: Any, registry: PluginRegistry, logger=None) -> AddonResult:
    """Load the in-process plugins from ``directory``.

    A plugin that fails to import, refuses to describe itself, or raises while
    registering is recorded as a failed plugin and skipped.  It never stops the
    application: the focus session is more important than an addon.
    """
    result = AddonResult()
    if not directory or not os.path.isdir(directory):
        return result

    def log(level: str, message: str) -> None:
        if logger is not None:
            getattr(logger, level, logger.info)(message)

    for name in sorted(os.listdir(directory)):
        if not name.endswith(".py") or name.startswith("_"):
            continue
        path = os.path.join(directory, name)
        module_name = "focused_plugin_" + slugify(name[:-3]).replace("-", "_").replace(".", "_")
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError("cannot load %s" % path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            describe: Optional[Callable] = getattr(module, "register", None)
            if describe is None:
                raise AttributeError("a plugin must define register(focused)")
            info = describe(engine) or {}
            if not isinstance(info, dict):
                raise TypeError("register(focused) must return a dict")
            record = registry.register(
                name=str(info.get("name") or name[:-3]),
                version=str(info.get("version") or ""),
                kind=KIND_PYTHON,
                # A declared id wins, then the declared name, then the file name:
                # the identity must be stable, because rules and claims hang off it.
                plugin_id=str(info.get("id") or info.get("name") or name[:-3]),
            )
            engine.bind_addon(record.id, module)
            result.loaded.append(record.id)
            log("info", "plugin loaded: %s (%s)" % (record.id, path))
        except Exception as exc:  # a broken addon must not take the app down
            failed_id = slugify(name[:-3])
            record = registry.register(name=failed_id, kind=KIND_PYTHON)
            registry.set_status(record.id, STATUS_FAILED)
            registry.note_error(record.id)
            result.failed.append({"id": record.id, "error": "%s: %s" % (type(exc).__name__, exc)})
            log("error", "plugin failed to load: %s (%s)" % (path, exc))
    return result
