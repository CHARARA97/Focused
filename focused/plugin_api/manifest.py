"""PluginManifest: what a plugin declares at registration time."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Tuple

from .capabilities import DEFAULT_CAPABILITIES, normalize_capabilities
from .errors import PluginError


@dataclass(frozen=True)
class PluginManifest:
    """What a plugin says about itself at registration time."""

    id: str
    name: str
    version: str = ""
    description: str = ""
    capabilities: Tuple[str, ...] = DEFAULT_CAPABILITIES

    def __post_init__(self) -> None:
        # Direct construction (tests, third-party tooling) gets the same alias
        # expansion as register(); a manifest never carries an unknown name.
        object.__setattr__(self, "capabilities", normalize_capabilities(self.capabilities))

    @classmethod
    def from_registration(cls, info: Any, fallback_id: str) -> "PluginManifest":
        if info is None:
            info = {}
        if not isinstance(info, dict):
            raise PluginError("manifest.type", "register() must return a dict")
        plugin_id = str(info.get("id") or info.get("name") or fallback_id or "").strip()
        name = str(info.get("name") or plugin_id).strip()
        if not plugin_id or not name:
            raise PluginError("manifest.id", "a plugin needs a non-empty id and name")
        return cls(
            id=plugin_id,
            name=name,
            version=str(info.get("version") or ""),
            description=str(info.get("description") or ""),
            capabilities=normalize_capabilities(info.get("capabilities")),
        )
