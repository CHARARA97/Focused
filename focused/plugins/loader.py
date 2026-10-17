"""In-process plugin loader.

A broken addon is recorded and skipped; it never takes the daemon down.  The
loader only knows the ``PluginAPI`` host contract, not the engine.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..diagnostics import log_event
from ..plugin_api import PluginAPI, PluginManifest, SAFE_CAPABILITIES
from .registry import KIND_PYTHON, STATUS_FAILED, PluginRegistry, slugify

@dataclass
class AddonResult:
    loaded: List[str] = field(default_factory=list)
    failed: List[Dict[str, str]] = field(default_factory=list)


def load_addons(directory: str, host: Any, registry: PluginRegistry, logger=None) -> AddonResult:
    """Load the in-process plugins from ``directory``.

    A plugin that fails to import, refuses to describe itself, or raises while
    registering is recorded as a failed plugin and skipped.  It never stops the
    application: the focus session is more important than an addon.

    ``host`` provides the methods :class:`PluginAPI` calls
    (``replace_plugin_rules``, ``claim_plugin_session``,
    ``release_plugin_session``, ``plugin_status_snapshot``, ``log_plugin`` and
    ``bind_addon``).  The engine implements them; tests can supply a fake.
    """
    result = AddonResult()
    if not directory or not os.path.isdir(directory):
        return result

    for name in sorted(os.listdir(directory)):
        if not name.endswith(".py") or name.startswith("_"):
            continue
        path = os.path.join(directory, name)
        provisional_id = slugify(name[:-3])
        module_name = "focused_plugin_" + slugify(name[:-3]).replace("-", "_").replace(".", "_")
        record = None
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError("cannot load %s" % path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            describe: Optional[Callable] = getattr(module, "register", None)
            if describe is None:
                raise AttributeError("a plugin must define register(api)")
            api = PluginAPI(provisional_id, host)
            info = describe(api)
            manifest = PluginManifest.from_registration(info, provisional_id)
            record = registry.register(
                name=manifest.name,
                version=manifest.version,
                kind=KIND_PYTHON,
                # A declared id wins, then the declared name, then the file name:
                # the identity must be stable, because rules and claims hang off it.
                plugin_id=manifest.id,
                capabilities=manifest.capabilities,
                granted=SAFE_CAPABILITIES,
            )
            api._bind(record.id, manifest)
            start = getattr(module, "on_start", None)
            if start is not None:
                start(api)
            host.bind_addon(record.id, module, api)
            result.loaded.append(record.id)
            log_event(logger, "info", "plugin.load.ok", source=record.id, path=path)
        except Exception as exc:  # a broken addon must not take the app down
            if record is None:
                failed_id = slugify(name[:-3])
                record = registry.register(
                    name=failed_id, kind=KIND_PYTHON, plugin_id=failed_id
                )
            # A plugin may fail after it already contributed rules or claimed a
            # session (for example in on_start).  Withdraw both before marking
            # it failed; a dead plugin must not keep a lease alive.
            clear = getattr(host, "clear_plugin", None)
            if clear is not None:
                try:
                    clear(record.id)
                except Exception:
                    pass
            registry.set_status(record.id, STATUS_FAILED)
            registry.note_error(record.id)
            result.failed.append({"id": record.id, "error": "%s: %s" % (type(exc).__name__, exc)})
            log_event(
                logger,
                "error",
                "plugin.load.fail",
                source=record.id,
                path=path,
                error="%s: %s" % (type(exc).__name__, exc),
            )
    return result
