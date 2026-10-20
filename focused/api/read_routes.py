"""Read/legacy-write routes: status, events, config, processes, focus, rules."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from .http import API_PREFIX, int_param, plugin_payloads
from .plugin_routes import get_plugin


def get_core(engine, path: str, query: Dict[str, list]) -> Tuple[int, Dict[str, Any]]:
    if path == API_PREFIX + "/status":
        return 200, engine.status()
    if path == API_PREFIX + "/events":
        since = int_param(query, "since", 0)
        limit = int_param(query, "limit", 100)
        return 200, {
            "ok": True,
            "events": engine.events_since(since, limit),
            "next_seq": engine.status()["event_seq"],
        }
    if path == API_PREFIX + "/config":
        return 200, engine.config_dict()
    if path == API_PREFIX + "/processes":
        limit = int_param(query, "limit", 400)
        payload = {"ok": True, "processes": engine.visible_processes(limit)}
        payload.update(engine.visible_process_arrays(limit))
        return 200, payload
    if path == API_PREFIX + "/plugins":
        return 200, {"ok": True, "plugins": plugin_payloads(engine)}
    if path.startswith(API_PREFIX + "/plugins/"):
        return get_plugin(engine, path)
    if path == API_PREFIX + "/focus":
        return 200, engine.focus_state()
    if path == API_PREFIX + "/focus/recent":
        recent = engine.recent_list()
        return 200, {"ok": True, "count": len(recent), "recent": recent}
    return 404, {"ok": False, "error": "no such endpoint: %s" % path}


def put_core(engine, path: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    if path == API_PREFIX + "/focus/rules":
        rules = engine.apply_rules(body)
        return 200, {
            "ok": True,
            "applied": {
                "names": sorted(rules.names),
                "cmdline_substrings": list(rules.cmdline_substrings),
                "pids": sorted(rules.pids),
                "protect_names": len(rules.protect_names),
            },
        }
    return 404, {"ok": False, "error": "no such endpoint: %s" % path}


def patch_core(engine, path: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    if path == API_PREFIX + "/config":
        return 200, engine.update_config(body)
    return 404, {"ok": False, "error": "no such endpoint: %s" % path}
