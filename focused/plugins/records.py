"""Plugin records: the stored shape of a connected plugin.

Pure data plus the id slug; the registry owns the lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple

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
                data.get("declared_capabilities") or SAFE_CAPABILITIES, strict=False
            ),
            granted_capabilities=normalize_capabilities(
                data.get("granted_capabilities") or SAFE_CAPABILITIES, strict=False
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
