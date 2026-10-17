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

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import __version__
from .api.auth import RequestAuth
from .api.core_routes import post_core
from .api.plugin_routes import post_plugin, put_plugin_rules
from .api.read_routes import get_core, patch_core, put_core
from .api.security import require_json_content_type, validate_transport
from .api.http import API_PREFIX, MAX_BODY_BYTES, ApiError, plugin_route as _plugin_route
from .core.rules import RuleRejected
from .engine import FocusedEngine
from .plugin_api import PluginError
from .ui import DASHBOARD_HTML


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

    def _auth(self) -> RequestAuth:
        return RequestAuth(
            self._engine(),
            getattr(self.server, "token", "") or "",
            self._supplied_token(),
        )

    def _authorised(self) -> bool:
        """The application token: what the UI and the built-in frontends use."""
        return self._auth().authorised()

    def _plugin_authorised(self, plugin_id: str) -> bool:
        """A plugin may act with its own token, or with the application token."""
        return self._auth().plugin_authorised(plugin_id)

    def _authorised_for(self, path: str) -> bool:
        """One gate for every request, before routing."""
        return self._auth().authorised_for(path)

    def _transport_ok(self) -> bool:
        try:
            validate_transport(self.headers)
        except ApiError as exc:
            self._send_json(exc.status, {"ok": False, "error": exc.message})
            return False
        return True

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
        require_json_content_type(self.headers)
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

    def _plugin_error(self, exc: PluginError) -> None:
        status = 403 if exc.code in ("plugin.disabled", "capability.missing") else 400
        self._send_json(status, {"ok": False, "error": str(exc), "code": exc.code})

    # -- routes ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        try:
            if not self._transport_ok():
                return
            path, query = self._route()
            # The dashboard shell contains no secrets; it must load before the
            # user has entered a token.  Every API call underneath still needs one.
            if path == "/":
                self._send_html(DASHBOARD_HTML)
                return
            if not self._authorised_for(path):
                self._send_json(401, {"ok": False, "error": "missing or invalid token"})
                return
            engine = self._engine()
            status, payload = get_core(engine, path, query)
            self._send_json(status, payload)
        except ApiError as exc:
            self._send_json(exc.status, {"ok": False, "error": exc.message})
        except PluginError as exc:
            self._plugin_error(exc)
        except Exception as exc:
            self._fail(exc)

    def do_PUT(self) -> None:  # noqa: N802 - stdlib name
        try:
            if not self._transport_ok():
                return
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
                status, payload = put_plugin_rules(engine, path, body)
                self._send_json(status, payload)
            else:
                status, payload = put_core(engine, path, body)
                self._send_json(status, payload)
        except ApiError as exc:
            self._send_json(exc.status, {"ok": False, "error": exc.message})
        except RuleRejected as exc:
            # A rule set that would match everything is a client error, not a
            # server failure: show the list of problems so it can be fixed.
            self._send_json(400, {"ok": False, "error": str(exc)})
        except PluginError as exc:
            self._plugin_error(exc)
        except Exception as exc:
            self._fail(exc)

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib name
        """Partial configuration updates (the dashboard's settings page)."""
        if not self._transport_ok():
            return
        if not self._authorised():
            self._send_json(401, {"ok": False, "error": "missing or invalid token"})
            return
        try:
            path, _query = self._route()
            body = self._read_json()
            engine = self._engine()
            status, payload = patch_core(engine, path, body)
            self._send_json(status, payload)
        except ApiError as exc:
            self._send_json(exc.status, {"ok": False, "error": exc.message})
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - reported, never a crash
            self._fail(exc)

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        try:
            if not self._transport_ok():
                return
            path, _query = self._route()
            if not self._authorised_for(path):
                self._send_json(401, {"ok": False, "error": "missing or invalid token"})
                return
            body = self._read_json()
            engine = self._engine()

            if path == API_PREFIX + "/plugins/register":
                # Registration is application-level: only the global token may
                # ask for a plugin token.
                if not self._authorised():
                    self._send_json(401, {"ok": False, "error": "missing or invalid token"})
                    return
                status, payload = post_plugin(engine, path, body)
                self._send_json(status, payload)
            elif path.startswith(API_PREFIX + "/plugins/"):
                plugin_id, _tail = _plugin_route(path)
                if not self._plugin_authorised(plugin_id):
                    self._send_json(401, {"ok": False, "error": "missing or invalid token"})
                    return
                status, payload = post_plugin(engine, path, body)
                self._send_json(status, payload)
            else:
                status, payload = post_core(engine, path, body)
                self._send_json(status, payload)
        except ApiError as exc:
            self._send_json(exc.status, {"ok": False, "error": exc.message})
        except RuleRejected as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
        except PluginError as exc:
            self._plugin_error(exc)
        except Exception as exc:
            self._fail(exc)

    def _route(self) -> Tuple[str, Dict[str, list]]:
        parsed = urlparse(self.path)
        return parsed.path.rstrip("/") or "/", parse_qs(parsed.query)


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
        # Config updates must affect the running server, not only the file.
        engine._server_token_setter = self.set_token  # type: ignore[attr-defined]

    def set_token(self, token: str) -> None:
        self.token = token or ""
        if self._httpd is not None:
            self._httpd.token = self.token  # type: ignore[attr-defined]

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
