"""Plugin registry: records, tokens, capabilities and persistence."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..plugin_api import SAFE_CAPABILITIES, normalize_capabilities

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
    declared_capabilities: Tuple[str, ...] = SAFE_CAPABILITIES
    granted_capabilities: Tuple[str, ...] = SAFE_CAPABILITIES

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
            "declared_capabilities": list(self.declared_capabilities),
            "granted_capabilities": list(self.granted_capabilities),
        }

    def to_public_dict(self) -> Dict[str, Any]:
        """The record without its token; safe for list/status responses."""
        data = self.to_dict()
        data.pop("token", None)
        data["token_set"] = bool(self.token)
        return data

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
            declared_capabilities=normalize_capabilities(
                data.get("declared_capabilities") or SAFE_CAPABILITIES
            ),
            granted_capabilities=normalize_capabilities(
                data.get("granted_capabilities") or SAFE_CAPABILITIES
            ),
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
            # Plugin tokens are credentials; never leave them world-readable.
            os.chmod(tmp, 0o600)
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
        capabilities: Optional[Tuple[str, ...]] = None,
        granted: Optional[Tuple[str, ...]] = None,
    ) -> PluginRecord:
        """Register a plugin (or refresh an existing registration).

        ``capabilities`` is what the plugin *declared*; ``granted`` is what the
        host actually allows.  Dangerous capabilities are never granted by
        default, and a reload cannot silently escalate an existing record.
        """
        declared = tuple(capabilities or SAFE_CAPABILITIES)
        allowed = tuple(granted or SAFE_CAPABILITIES)
        with self._lock:
            pid = slugify(plugin_id or name)
            existing = self._plugins.get(pid)
            if existing is not None:
                existing.name = name or existing.name
                existing.version = version or existing.version
                existing.kind = kind or existing.kind
                if webhook_url:
                    existing.webhook_url = webhook_url
                existing.declared_capabilities = declared
                if granted is not None:
                    existing.granted_capabilities = allowed
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
                    declared_capabilities=declared,
                    granted_capabilities=allowed,
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
        """Record that a plugin just spoke to us (memory only).

        ``last_seen`` is a UI hint.  Persisting it on every lease renewal is what
        made a 2-second heartbeat rewrite ``plugins.json``; the transitions that
        matter (register, rules change, session claim) save explicitly.
        """
        with self._lock:
            record = self._plugins.get(plugin_id)
            if record is not None:
                record.last_seen = self._clock()

    def note_rules(self, plugin_id: str, count: int) -> None:
        changed = False
        with self._lock:
            record = self._plugins.get(plugin_id)
            if record is not None:
                changed = record.rules_count != count
                record.rules_count = count
                record.last_seen = self._clock()
        if changed:
            self.save()

    def note_session(self, plugin_id: str, ttl: float, persist: bool = True) -> None:
        """Update a plugin's lease hint; persist only when asked.

        ``set_state`` passes ``persist=False`` for a renewal: the claim did not
        change, so there is nothing worth writing to disk.
        """
        with self._lock:
            record = self._plugins.get(plugin_id)
            if record is None:
                return
            record.session_ttl = ttl
            record.last_seen = self._clock()
        if persist:
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

    def capabilities_for(self, plugin_id: str) -> frozenset:
        """The effective capabilities: declared by the plugin ∩ granted by host."""
        record = self.get(plugin_id)
        if record is None or record.status != STATUS_ENABLED:
            return frozenset()
        return frozenset(record.declared_capabilities) & frozenset(
            record.granted_capabilities
        )

    def grant(self, plugin_id: str, capabilities) -> Optional[PluginRecord]:
        """Grant capabilities (application-token action; not exposed to plugins)."""
        record = self.get(plugin_id)
        if record is None:
            return None
        merged = set(record.granted_capabilities) | {str(item) for item in capabilities}
        with self._lock:
            record.granted_capabilities = tuple(sorted(merged))
        self.save()
        return record

    def find_by_token(self, token: str) -> Optional[PluginRecord]:
        """The enabled HTTP plugin that owns ``token``, or ``None``."""
        supplied = token or ""
        if not supplied:
            return None
        with self._lock:
            records = list(self._plugins.values())
        for record in records:
            if record.status != STATUS_ENABLED or record.kind != KIND_HTTP:
                continue
            if record.token and secrets.compare_digest(record.token, supplied):
                return record
        return None

    def verify_any(self, token: str) -> bool:
        """Whether ``token`` belongs to any enabled HTTP plugin.

        Used for read-only endpoints (``/focus`` and ``/events``), so a plugin
        token can read the state it is allowed to see without being the
        application token.  Mutating endpoints stay application-only.
        """
        supplied = token or ""
        if not supplied:
            return False
        with self._lock:
            records = list(self._plugins.values())
        for record in records:
            if record.status != STATUS_ENABLED or record.kind != KIND_HTTP:
                continue
            if record.token and secrets.compare_digest(record.token, supplied):
                return True
        return False


