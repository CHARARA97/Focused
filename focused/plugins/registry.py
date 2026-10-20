"""Plugin registry: records, tokens, capabilities and persistence."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..plugin_api import SAFE_CAPABILITIES
from .records import (
    KIND_HTTP,
    KIND_PYTHON,
    STATUS_DISABLED,
    STATUS_ENABLED,
    STATUS_FAILED,
    PluginRecord,
    slugify,
)
from .store import RegistryStore

class PluginRegistry:
    """Who is connected, and are they allowed to be."""

    def __init__(self, path: str, logger=None, clock=time.time) -> None:
        self.path = path
        self._logger = logger
        self._clock = clock
        self._lock = threading.RLock()
        self._plugins: Dict[str, PluginRecord] = {}
        self._store = RegistryStore(clock=clock, log=self._log)

    # -- persistence -------------------------------------------------------

    def load(self) -> List[PluginRecord]:
        with self._lock:
            self._plugins = {}
            for item in self._store.read(self.path):
                record = PluginRecord.from_dict(item)
                if record.id:
                    self._plugins[record.id] = record
            return list(self._plugins.values())

    def save(self) -> None:
        with self._lock:
            records = [self._plugins[key] for key in sorted(self._plugins)]
        self._store.write(self.path, records)

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


