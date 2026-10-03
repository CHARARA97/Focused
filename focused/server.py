"""Focused's loopback HTTP API, plus a tiny dashboard served at ``/``.

The API is the contract in ``docs/focused-protocol.md``:

* ``PUT  /api/v1/focus/rules``   -- ChillFocus (or a script) hands the rules over
* ``POST /api/v1/focus/state``   -- ...and says whether a session is running
* ``GET  /api/v1/focus/frozen``  -- ...and reads back what Focused suspended
* ``GET  /api/v1/focus``         -- the browser extension's view of the session

Everything else is convenience: a manual session, a temporary pass, a thaw, and
the dashboard.  All of it is loopback-only, and a token is optional (set
``http.token`` and every request must carry ``X-Focused-Token``).
"""

from __future__ import annotations

import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import __version__
from .engine import FocusedEngine
from .plugins import KIND_HTTP, STATUS_ENABLED, slugify
from .ui import DASHBOARD_HTML

API_PREFIX = "/api/v1"
MAX_BODY_BYTES = 256 * 1024


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class FocusedRequestHandler(BaseHTTPRequestHandler):
    server_version = "Focused/" + __version__
    protocol_version = "HTTP/1.1"

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt, *args):  # keep the daemon's log clean
        logger = getattr(self.server, "logger", None)
        if logger is not None:
            logger.debug("%s - %s" % (self.address_string(), fmt % args))

    def _engine(self) -> FocusedEngine:
        return self.server.engine  # type: ignore[attr-defined]

    def _supplied_token(self) -> str:
        return self.headers.get("X-Focused-Token", "") or ""

    def _authorised(self) -> bool:
        """The application token: what the UI and the built-in frontends use."""
        token = getattr(self.server, "token", "") or ""
        if not token:
            return True
        return hmac.compare_digest(self._supplied_token(), token)

    def _plugin_authorised(self, plugin_id: str) -> bool:
        """A plugin may act with its own token, or with the application token."""
        if self._authorised():
            return True
        return self._engine().plugins.verify(plugin_id, self._supplied_token())

    def _authorised_for(self, path: str) -> bool:
        """One gate for every request.

        A plugin path accepts the plugin's own token; everything else needs the
        application token.  This has to happen *before* routing, or a plugin token
        would be rejected by the global check and its own endpoints would be
        unreachable.
        """
        if self._authorised():
            return True
        if path.startswith(API_PREFIX + "/plugins/"):
            plugin_id, _tail = _plugin_route(path)
            return bool(plugin_id) and self._plugin_authorised(plugin_id)
        return False

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, html: str) -> None:
        raw = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "invalid Content-Length")
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ApiError(413, "request body too large")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "body is not valid JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "body must be a JSON object")
        return data

    def _fail(self, exc: Exception) -> None:
        self._send_json(500, {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})

    # -- routes ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        try:
            path, query = self._route()
            if not self._authorised_for(path):
                self._send_json(401, {"ok": False, "error": "missing or invalid token"})
                return
            engine = self._engine()
            if path == "/":
                self._send_html(DASHBOARD_HTML)
            elif path == API_PREFIX + "/status":
                self._send_json(200, engine.status())
            elif path == API_PREFIX + "/events":
                since = _int_param(query, "since", 0)
                limit = _int_param(query, "limit", 100)
                events = engine.events_since(since, limit)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "events": events,
                        "next_seq": engine.status()["event_seq"],
                    },
                )
            elif path == API_PREFIX + "/config":
                self._send_json(200, engine.config_dict())
            elif path == API_PREFIX + "/processes":
                limit = _int_param(query, "limit", 400)
                payload = {"ok": True, "processes": engine.visible_processes(limit)}
                payload.update(engine.visible_process_arrays(limit))
                self._send_json(200, payload)
            elif path == API_PREFIX + "/plugins":
                self._send_json(
                    200, {"ok": True, "plugins": _plugin_payloads(engine)}
                )
            elif path.startswith(API_PREFIX + "/plugins/"):
                plugin_id, tail = _plugin_route(path)
                if tail or not plugin_id:
                    self._send_json(404, {"ok": False, "error": "no such endpoint"})
                else:
                    record = engine.plugins.get(plugin_id)
                    if record is None:
                        self._send_json(404, {"ok": False, "error": "unknown plugin"})
                    else:
                        self._send_json(200, {"ok": True, "plugin": _plugin_payload(engine, record)})
            elif path == API_PREFIX + "/focus":
                # What the browser extension polls.
                self._send_json(200, engine.focus_state())
            elif path == API_PREFIX + "/focus/frozen":
                frozen = engine.frozen_list()
                self._send_json(
                    200, {"ok": True, "count": len(frozen), "frozen": frozen}
                )
            else:
                self._send_json(404, {"ok": False, "error": "no such endpoint: %s" % path})
        except ApiError as exc:
            self._send_json(exc.status, {"ok": False, "error": exc.message})
        except Exception as exc:
            self._fail(exc)

    def do_PUT(self) -> None:  # noqa: N802 - stdlib name
        try:
            path, _query = self._route()
            if not self._authorised_for(path):
                self._send_json(401, {"ok": False, "error": "missing or invalid token"})
                return
            body = self._read_json()
            engine = self._engine()
            if path.startswith(API_PREFIX + "/plugins/") and path.endswith("/rules"):
                plugin_id = path[len(API_PREFIX + "/plugins/") : -len("/rules")]
                if not self._plugin_authorised(plugin_id):
                    self._send_json(401, {"ok": False, "error": "missing or invalid token"})
                    return
                engine.plugins.touch(plugin_id)
                try:
                    rules = engine.plugin_rules(plugin_id, body)
                except PermissionError as exc:
                    self._send_json(403, {"ok": False, "error": str(exc)})
                    return
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "applied": {
                            "names": sorted(rules.names),
                            "cmdline_substrings": list(rules.cmdline_substrings),
                            "pids": sorted(rules.pids),
                        },
                    },
                )
            elif path == API_PREFIX + "/focus/rules":
                rules = engine.apply_rules(body)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "applied": {
                            "names": sorted(rules.names),
                            "cmdline_substrings": list(rules.cmdline_substrings),
                            "pids": sorted(rules.pids),
                            "protect_names": len(rules.protect_names),
                        },
                    },
                )
            else:
                self._send_json(404, {"ok": False, "error": "no such endpoint: %s" % path})
        except ApiError as exc:
            self._send_json(exc.status, {"ok": False, "error": exc.message})
        except Exception as exc:
            self._fail(exc)

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib name
        """Partial configuration updates (the dashboard's settings page)."""
        if not self._authorised():
            self._send_json(401, {"ok": False, "error": "missing or invalid token"})
            return
        try:
            path, _query = self._route()
            body = self._read_json()
            engine = self._engine()
            if path == API_PREFIX + "/config":
                self._send_json(200, engine.update_config(body))
            else:
                self._send_json(404, {"ok": False, "error": "no such endpoint"})
        except ApiError as exc:
            self._send_json(exc.status, {"ok": False, "error": exc.message})
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - reported, never a crash
            self._fail(exc)

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        try:
            path, _query = self._route()
            if not self._authorised_for(path):
                self._send_json(401, {"ok": False, "error": "missing or invalid token"})
                return
            body = self._read_json()
            engine = self._engine()

            if path == API_PREFIX + "/plugins/register":
                # Registration is an application-level action: only the global
                # token (or an open install) may ask for a plugin token.
                if not self._authorised():
                    self._send_json(401, {"ok": False, "error": "missing or invalid token"})
                    return
                record = engine.plugins.register(
                    name=str(body.get("name") or "plugin"),
                    version=str(body.get("version") or ""),
                    kind=str(body.get("kind") or KIND_HTTP),
                    webhook_url=str(body.get("webhook_url") or ""),
                    plugin_id=str(body.get("id") or slugify(str(body.get("name") or "plugin"))),
                )
                engine._emit(
                    "plugin", source=record.id, detail="registered (%s)" % record.kind
                )
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "id": record.id,
                        "token": record.token,
                        "endpoints": {
                            "rules": "PUT %s/plugins/%s/rules" % (API_PREFIX, record.id),
                            "session": "POST %s/plugins/%s/session" % (API_PREFIX, record.id),
                            "state": "GET %s/focus" % API_PREFIX,
                            "events": "GET %s/events?since=0" % API_PREFIX,
                        },
                    },
                )
            elif path.startswith(API_PREFIX + "/plugins/"):
                plugin_id, tail = _plugin_route(path)
                if not self._plugin_authorised(plugin_id):
                    self._send_json(401, {"ok": False, "error": "missing or invalid token"})
                    return
                if tail == "session":
                    active = _bool_field(body, "active")
                    ttl = body.get("ttl_seconds")
                    if ttl is not None and (
                        isinstance(ttl, bool) or not isinstance(ttl, (int, float))
                    ):
                        raise ApiError(400, "ttl_seconds must be a number")
                    engine.plugins.touch(plugin_id)
                    try:
                        applied = engine.plugin_session(plugin_id, active, ttl)
                    except PermissionError as exc:
                        self._send_json(403, {"ok": False, "error": str(exc)})
                        return
                    self._send_json(200, {"ok": True, "active": applied})
                elif tail == "enable":
                    enabled = _bool_field(body, "enabled")
                    record = engine.set_plugin_status(plugin_id, enabled)
                    if record is None:
                        self._send_json(404, {"ok": False, "error": "unknown plugin"})
                    else:
                        self._send_json(200, {"ok": True, "status": record.status})
                elif tail == "webhook":
                    url = str(body.get("url") or "")
                    record = engine.plugins.get(plugin_id)
                    if record is None:
                        self._send_json(404, {"ok": False, "error": "unknown plugin"})
                    else:
                        record.webhook_url = url
                        engine.plugins.save()
                        self._send_json(200, {"ok": True, "webhook_url": url})
                elif tail == "remove" or tail == "delete":
                    removed = engine.remove_plugin(plugin_id)
                    self._send_json(
                        200 if removed else 404,
                        {"ok": removed, "removed": removed},
                    )
                else:
                    self._send_json(404, {"ok": False, "error": "no such plugin action"})
            elif path == API_PREFIX + "/config":
                try:
                    payload = engine.update_config(body)
                except ValueError as exc:
                    self._send_json(400, {"ok": False, "error": str(exc)})
                    return
                self._send_json(200, payload)
            elif path == API_PREFIX + "/focus/state":
                active = _bool_field(body, "active")
                dry_run = body.get("dry_run")
                if isinstance(dry_run, bool):
                    engine.set_dry_run(dry_run)
                ttl = body.get("ttl_seconds")
                if ttl is not None and (
                    isinstance(ttl, bool) or not isinstance(ttl, (int, float))
                ):
                    raise ApiError(400, "ttl_seconds must be a number")
                applied = engine.set_state(
                    active,
                    source=str(body.get("source") or "client"),
                    ttl_seconds=ttl,
                )
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "active": applied,
                        "delegated_ttl": engine.status()["delegated_ttl"],
                    },
                )

            elif path == API_PREFIX + "/focus/session":
                # One endpoint for the whole session control surface: start with a
                # mode and its own durations, add time to what is running, cut a
                # break short, or stop.
                if body.get("stop") is True:
                    stopped = engine.stop_session()
                    self._send_json(
                        200,
                        {"ok": True, "stopped": stopped, "session": engine.session_state()},
                    )
                    return
                if body.get("skip_break") is True:
                    session = engine.skip_break()
                    self._send_json(
                        200, {"ok": True, "session": engine.session_state(), "phase": session}
                    )
                    return
                if body.get("extend_minutes") is not None:
                    minutes = _number_field(body, "extend_minutes")
                    session = engine.extend_session(minutes)
                    self._send_json(
                        200,
                        {
                            "ok": session is not None,
                            "session": engine.session_state(),
                            "note": ""
                            if session is not None
                            else "no session is running",
                        },
                    )
                    return

                seconds = body.get("seconds")
                if seconds is not None:
                    # "Start me a session of exactly this long."
                    minutes = _number_field(body, "seconds") / 60.0
                else:
                    minutes = (
                        _number_field(body, "minutes")
                        if body.get("minutes") is not None
                        else None
                    )
                mode = body.get("mode")
                if mode is not None and not isinstance(mode, str):
                    raise ApiError(400, "mode must be a string")
                work = (
                    _number_field(body, "work_minutes")
                    if body.get("work_minutes") is not None
                    else None
                )
                rest = (
                    _number_field(body, "break_minutes")
                    if body.get("break_minutes") is not None
                    else None
                )
                cycles = body.get("cycles")
                if cycles is not None and (
                    isinstance(cycles, bool) or not isinstance(cycles, (int, float))
                ):
                    raise ApiError(400, "cycles must be a number")
                try:
                    session = engine.start_session(
                        minutes=minutes,
                        mode=mode,
                        work_minutes=work,
                        break_minutes=rest,
                        cycles=int(cycles) if cycles is not None else None,
                    )
                except ValueError as exc:
                    raise ApiError(400, str(exc))
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "active": engine.active(),
                        "until": session.get("until", 0.0),
                        "session": engine.session_state(),
                    },
                )

            elif path == API_PREFIX + "/focus/pause":
                seconds = body.get("seconds")
                if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
                    raise ApiError(400, "seconds must be a number")
                until = engine.pause(seconds)
                self._send_json(200, {"ok": True, "pause_until": until})

            elif path == API_PREFIX + "/scan":
                frozen = engine.scan_once()
                self._send_json(200, {"ok": True, "frozen": frozen})

            elif path == API_PREFIX + "/thaw":
                count = engine.thaw_all("api")
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "thawed": count,
                        "detail": "resumed %d target(s)" % count,
                        "error": "",
                    },
                )

            elif path == API_PREFIX + "/urls":
                patterns = engine.set_url_patterns(body.get("patterns") or [])
                self._send_json(200, {"ok": True, "url_patterns": patterns})

            else:
                self._send_json(404, {"ok": False, "error": "no such endpoint: %s" % path})
        except ApiError as exc:
            self._send_json(exc.status, {"ok": False, "error": exc.message})
        except Exception as exc:
            self._fail(exc)

    def _route(self) -> Tuple[str, Dict[str, list]]:
        parsed = urlparse(self.path)
        return parsed.path.rstrip("/") or "/", parse_qs(parsed.query)


def _plugin_route(path: str):
    """Split ``/api/v1/plugins/{id}[/action]`` into (id, action)."""
    rest = path[len(API_PREFIX + "/plugins/") :].strip("/")
    if not rest:
        return "", ""
    parts = rest.split("/", 1)
    return parts[0], (parts[1] if len(parts) > 1 else "")


def _plugin_payload(engine, record) -> Dict[str, Any]:
    claims = engine.claims()
    live = engine.live_claims()
    now = time.time()
    return {
        **record.to_dict(),
        "enabled": record.status == STATUS_ENABLED,
        "last_seen_ago": round(max(0.0, now - record.last_seen), 1) if record.last_seen else None,
        "session_claim": claims.get(record.id),
        "session_live": record.id in live,
    }


def _plugin_payloads(engine) -> list:
    return [_plugin_payload(engine, record) for record in engine.plugins.list()]


def _int_param(query: Dict[str, list], key: str, default: int) -> int:
    values = query.get(key)
    if not values:
        return default
    try:
        return int(values[0])
    except (TypeError, ValueError):
        return default


def _number_field(body: Dict[str, Any], key: str) -> float:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApiError(400, "%s must be a number" % key)
    return float(value)


def _bool_field(body: Dict[str, Any], key: str) -> bool:
    value = body.get(key)
    if not isinstance(value, bool):
        raise ApiError(400, "field '%s' must be true or false" % key)
    return value


class FocusedServer:
    """The HTTP half.  ``start()`` returns the bound port."""

    def __init__(
        self,
        engine: FocusedEngine,
        host: str = "127.0.0.1",
        port: int = 8766,
        token: str = "",
        logger=None,
    ) -> None:
        self.engine = engine
        self.host = host
        self.port = port
        self.token = token or ""
        self.logger = logger
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> int:
        httpd = ThreadingHTTPServer((self.host, self.port), FocusedRequestHandler)
        httpd.daemon_threads = True
        httpd.engine = self.engine  # type: ignore[attr-defined]
        httpd.token = self.token  # type: ignore[attr-defined]
        httpd.logger = self.logger  # type: ignore[attr-defined]
        self._httpd = httpd
        self.port = httpd.server_address[1]
        self._thread = threading.Thread(
            target=httpd.serve_forever, name="focused-http", daemon=True
        )
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
