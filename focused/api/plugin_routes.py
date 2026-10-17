"""HTTP routes that belong to plugins.

They return ``(status, payload)`` instead of writing the response, so the
handler keeps one place for headers, errors and auth.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from ..plugins import KIND_HTTP, slugify
from .http import (
    API_PREFIX,
    ApiError,
    bool_field,
    is_http_url,
    plugin_payload,
    plugin_route,
)


def get_plugin(engine, path: str) -> Tuple[int, Dict[str, Any]]:
    plugin_id, tail = plugin_route(path)
    if not plugin_id:
        return 404, {"ok": False, "error": "no such endpoint"}
    if tail == "":
        record = engine.plugins.get(plugin_id)
        if record is None:
            return 404, {"ok": False, "error": "unknown plugin"}
        return 200, {"ok": True, "plugin": plugin_payload(engine, record)}
    if tail == "state":
        return 200, engine.plugin_gateway.plugin_status_snapshot(plugin_id)
    if tail == "rules":
        return 200, {
            "ok": True,
            "rules": engine.plugin_gateway.list_plugin_rules(plugin_id),
        }
    return 404, {"ok": False, "error": "no such endpoint"}


def put_plugin_rules(engine, path: str, body: Dict[str, Any]):
    plugin_id = path[len(API_PREFIX + "/plugins/") : -len("/rules")]
    engine.plugins.touch(plugin_id)
    rules = engine.plugin_gateway.replace_plugin_rules(plugin_id, body)
    return 200, {
        "ok": True,
        "applied": {
            "names": sorted(rules.names),
            "cmdline_substrings": list(rules.cmdline_substrings),
            "pids": sorted(rules.pids),
        },
    }


def post_plugin(engine, path: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    if path == API_PREFIX + "/plugins/register":
        kind = str(body.get("kind") or KIND_HTTP)
        if kind != KIND_HTTP:
            # An in-process Python plugin is trusted by definition; it must never
            # be creatable over HTTP (that would bypass the token check).
            return 400, {"ok": False, "error": "HTTP registration only supports kind=http"}
        webhook_url = str(body.get("webhook_url") or "")
        if webhook_url and not is_http_url(webhook_url):
            return 400, {"ok": False, "error": "webhook_url must be http(s)"}
        record = engine.plugins.register(
            name=str(body.get("name") or "plugin"),
            version=str(body.get("version") or ""),
            kind=kind,
            webhook_url=webhook_url,
            plugin_id=str(body.get("id") or slugify(str(body.get("name") or "plugin"))),
        )
        engine._emit("plugin", source=record.id, detail="registered (%s)" % record.kind)
        return 200, {
            "ok": True,
            "id": record.id,
            "token": record.token,
            "endpoints": {
                "rules": "PUT %s/plugins/%s/rules" % (API_PREFIX, record.id),
                "session": "POST %s/plugins/%s/session" % (API_PREFIX, record.id),
                "state": "GET %s/focus" % API_PREFIX,
                "events": "GET %s/events?since=0" % API_PREFIX,
            },
        }

    plugin_id, tail = plugin_route(path)
    if not plugin_id:
        return 404, {"ok": False, "error": "no such plugin action"}

    if tail == "session":
        active = bool_field(body, "active")
        ttl = body.get("ttl_seconds")
        if ttl is not None and (isinstance(ttl, bool) or not isinstance(ttl, (int, float))):
            raise ApiError(400, "ttl_seconds must be a number")
        engine.plugins.touch(plugin_id)
        if active:
            applied = engine.plugin_gateway.claim_plugin_session(
                plugin_id, 30 if ttl is None else ttl
            )
        else:
            applied = engine.plugin_gateway.release_plugin_session(plugin_id)
        return 200, {"ok": True, "active": applied}

    if tail == "scan":
        report = engine.plugin_gateway.request_plugin_scan(plugin_id)
        return (
            200 if report.accepted else 429,
            {
                "ok": report.accepted,
                "accepted": report.accepted,
                "frozen": report.frozen,
                "reason": report.reason,
            },
        )

    if tail in ("rules/add", "rules/remove"):
        if tail == "rules/add":
            engine.plugin_gateway.add_plugin_rules(plugin_id, body)
        else:
            engine.plugin_gateway.remove_plugin_rules(plugin_id, body)
        return 200, {
            "ok": True,
            "rules": engine.plugin_gateway.list_plugin_rules(plugin_id),
        }

    if tail == "enable":
        enabled = bool_field(body, "enabled")
        record = engine.set_plugin_status(plugin_id, enabled)
        if record is None:
            return 404, {"ok": False, "error": "unknown plugin"}
        return 200, {"ok": True, "status": record.status}

    if tail == "webhook":
        url = str(body.get("url") or "")
        if url and not is_http_url(url):
            return 400, {"ok": False, "error": "webhook_url must be http(s)"}
        record = engine.plugins.get(plugin_id)
        if record is None:
            return 404, {"ok": False, "error": "unknown plugin"}
        record.webhook_url = url
        engine.plugins.save()
        return 200, {"ok": True, "webhook_url": url}

    if tail in ("remove", "delete"):
        removed = engine.remove_plugin(plugin_id)
        return (200 if removed else 404), {"ok": removed, "removed": removed}

    return 404, {"ok": False, "error": "no such plugin action"}
