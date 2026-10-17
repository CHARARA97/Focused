"""Plugin lifecycle: load, bind, fail, clear, disable, remove.

The registry (``plugins.py``) is persistence and token/record validation; this
service is the runtime side.  It owns the loaded modules and never lets a failed
plugin keep its rules or its lease.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from ..diagnostics import log_event
from ..plugins import (
    STATUS_DISABLED,
    STATUS_ENABLED,
    PluginRecord,
    PluginRegistry,
    load_addons,
)


class PluginRuntime:
    def __init__(
        self,
        *,
        registry: PluginRegistry,
        gateway: Any,
        logger: Any,
        clock: Callable[[], float],
        record_event: Callable[..., Dict[str, Any]],
        emit: Callable[..., Dict[str, Any]],
        clear_source: Callable[[str], None],
        set_state: Callable[[bool, str], bool],
    ) -> None:
        self._registry = registry
        self._gateway = gateway
        self._logger = logger
        self._clock = clock
        self._record_event = record_event
        self._emit = emit
        self._clear_source = clear_source
        self._set_state = set_state
        self._lock = threading.RLock()
        self._addons: Dict[str, Any] = {}

    # -- loaded modules ----------------------------------------------------

    def subscribers(self):
        with self._lock:
            return [
                (plugin_id, module, api)
                for plugin_id, (module, api) in self._addons.items()
            ]

    def bind(self, plugin_id: str, module: Any, api: Any) -> None:
        with self._lock:
            self._addons[plugin_id] = (module, api)

    def stop(self, plugin_id: str) -> None:
        """Give a plugin one chance to clean up, then forget it."""
        with self._lock:
            handle = self._addons.pop(plugin_id, None)
        if handle is None:
            return
        module, api = handle
        callback = getattr(module, "on_stop", None)
        if callback is None:
            return
        try:
            callback(api)
        except Exception as exc:
            log_event(
                self._logger,
                "warning",
                "plugin.stop.fail",
                source=plugin_id,
                error="%s: %s" % (type(exc).__name__, exc),
            )

    def addons(self) -> List[str]:
        with self._lock:
            return sorted(self._addons)

    # -- lifecycle ---------------------------------------------------------

    def load(self, directory: str) -> List[Dict[str, str]]:
        self._registry.load()
        result = load_addons(directory, self._gateway, self._registry, self._logger)
        for plugin_id in result.loaded:
            self._emit("plugin", source=plugin_id, detail="plugin loaded")
        for failure in result.failed:
            self._emit(
                "error",
                source=failure["id"],
                detail="plugin failed: " + failure["error"],
            )
        return result.failed

    def fail(self, plugin_id: str, where: str, exc: BaseException) -> None:
        """Record a failure; five in a row disable and clear the plugin."""
        self._registry.note_error(plugin_id)
        record = self._registry.get(plugin_id)
        errors = record.errors if record is not None else 1
        self._record_event(
            "error",
            source=plugin_id,
            detail="plugin %s failed: %s: %s" % (where, type(exc).__name__, exc),
        )
        if errors >= 5:
            self.clear(plugin_id)
            self._registry.set_status(plugin_id, STATUS_DISABLED)
            self._record_event(
                "plugin",
                source=plugin_id,
                detail="disabled after %d failures" % errors,
            )
            log_event(
                self._logger,
                "error",
                "plugin.disabled",
                source=plugin_id,
                failures=errors,
            )

    def clear(self, plugin_id: str) -> None:
        self.stop(plugin_id)
        self._clear_source(plugin_id)
        self._set_state(False, plugin_id)

    def set_status(self, plugin_id: str, enabled: bool) -> Optional[PluginRecord]:
        status = STATUS_ENABLED if enabled else STATUS_DISABLED
        record = self._registry.set_status(plugin_id, status)
        if record is None:
            return None
        if not enabled:
            self.clear(plugin_id)
        self._emit("plugin", source=plugin_id, detail=status)
        return record

    def remove(self, plugin_id: str) -> bool:
        self.clear(plugin_id)
        removed = self._registry.remove(plugin_id)
        if removed:
            self._emit("plugin", source=plugin_id, detail="removed")
        return removed
