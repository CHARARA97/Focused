"""PluginAPI: identity, registration buffering and the narrow host contract.

Authorization is enforced by the host gateway; validation lives in
``validation.py`` and the namespaced surfaces in ``namespaces.py``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .capabilities import (
    CAP_RULES_READ,
    CAP_RULES_WRITE,
    CAP_SCAN_REQUEST,
    CAP_SESSION_CLAIM,
    CAP_STATE_HISTORY,
    CAP_STATE_READ,
)
from .errors import PluginError
from .manifest import PluginManifest
from .namespaces import _EventsApi, _RulesApi, _ScanApi, _SessionApi, _StateApi
from .validation import pids, strings


class PluginAPI:
    """The object passed to ``register(api)`` and ``on_event(api, event)``."""

    __slots__ = (
        "_id",
        "_host",
        "_manifest",
        "_bound",
        "_pending_rules_actions",
        "_pending_session",
        "_pending_release",
    )

    def __init__(self, plugin_id: str, host: Any, manifest: Optional[PluginManifest] = None) -> None:
        self._id = plugin_id
        self._host = host
        self._manifest = manifest or PluginManifest(id=plugin_id, name=plugin_id)
        # Calls made from register() happen before the registry record exists;
        # buffer them and flush once the real id and grants are known.
        self._bound = False
        self._pending_rules_actions = []
        self._pending_session: Optional[float] = None
        self._pending_release = False

    # -- identity ----------------------------------------------------------

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._manifest.name

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    def _bind(self, plugin_id: str, manifest: PluginManifest) -> None:
        self._id = plugin_id
        self._manifest = manifest
        self._bound = True
        if self._pending_rules_actions:
            self._require(CAP_RULES_WRITE)
            actions, self._pending_rules_actions = self._pending_rules_actions, []
            for action, payload in actions:
                self._rules_dispatch(action, payload)
        if self._pending_release:
            self._require(CAP_SESSION_CLAIM)
            self._pending_release = False
            self._pending_session = None
            self._host.release_plugin_session(self._id)
        elif self._pending_session is not None:
            self._require(CAP_SESSION_CLAIM)
            ttl, self._pending_session = self._pending_session, None
            self._host.claim_plugin_session(self._id, ttl)

    def _require(self, capability: str) -> None:
        if capability not in self._manifest.capabilities:
            raise PluginError(
                "capability.missing",
                "plugin %s did not declare the %s capability" % (self.id, capability),
            )

    # -- namespaces --------------------------------------------------------

    @property
    def rules(self) -> _RulesApi:
        return _RulesApi(self)

    @property
    def session(self) -> _SessionApi:
        return _SessionApi(self)

    @property
    def state(self) -> _StateApi:
        return _StateApi(self)

    @property
    def scan(self) -> _ScanApi:
        return _ScanApi(self)

    @property
    def events(self) -> _EventsApi:
        return _EventsApi(self)

    # -- rules dispatch ----------------------------------------------------

    def _rules_request(self, action: str, payload: Dict[str, Any]) -> None:
        self._require(CAP_RULES_WRITE)
        normalised = {
            "names": strings(payload.get("names"), "names"),
            "cmdline_substrings": strings(
                payload.get("cmdline_substrings"), "cmdline_substrings"
            ),
            "pids": pids(payload.get("pids")),
            "protect_names": strings(payload.get("protect_names"), "protect_names"),
            "protect_cmdline_substrings": strings(
                payload.get("protect_cmdline_substrings"), "protect_cmdline_substrings"
            ),
        }
        if not self._bound:
            self._pending_rules_actions.append((action, normalised))
            return
        self._rules_dispatch(action, normalised)

    def _rules_dispatch(self, action: str, payload: Dict[str, Any]) -> None:
        host = self._host
        if action == "add":
            host.add_plugin_rules(self.id, payload)
        elif action == "remove":
            host.remove_plugin_rules(self.id, payload)
        else:
            host.replace_plugin_rules(self.id, payload)

    # -- session dispatch --------------------------------------------------

    def _session_request(self, action: str, ttl_seconds: Optional[float]) -> None:
        self._require(CAP_SESSION_CLAIM)
        if action == "release":
            if not self._bound:
                self._pending_session = None
                self._pending_release = True
                return
            self._host.release_plugin_session(self.id)
            return
        try:
            ttl = float(ttl_seconds)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise PluginError("session.ttl", "ttl_seconds must be a number") from exc
        if not 5 <= ttl <= 3600:
            raise PluginError("session.ttl", "ttl_seconds must be between 5 and 3600")
        if not self._bound:
            self._pending_session = ttl
            self._pending_release = False
            return
        self._host.claim_plugin_session(self.id, ttl)

    # -- read-only ---------------------------------------------------------

    def _api_status(self) -> Dict[str, Any]:
        return self._host.plugin_status_snapshot(self.id)

    def log(self, message: str, level: str = "info") -> None:
        if level not in ("debug", "info", "warning", "error"):
            raise PluginError("log.level", "level must be debug/info/warning/error")
        self._host.log_plugin(self.id, level, str(message))

    # -- compatibility aliases --------------------------------------------

    def replace_rules(self, **payload) -> None:
        self.rules.replace(**payload)

    def claim_session(self, ttl_seconds: float = 30) -> None:
        self.session.claim(ttl_seconds)

    def release_session(self) -> None:
        self.session.release()

    def status(self) -> Dict[str, Any]:
        return self.state.summary()
