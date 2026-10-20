"""Session-control routes: focus state/session/pause, scan and thaw."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from .http import API_PREFIX, ApiError, bool_field, number_field


def post_session(engine, path: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    if path == API_PREFIX + "/focus/state":
        active = bool_field(body, "active")
        # dry_run is a UI/CLI setting (PATCH /config); a heartbeat must not flip
        # enforcement for everybody.
        ttl = body.get("ttl_seconds")
        if ttl is not None and (isinstance(ttl, bool) or not isinstance(ttl, (int, float))):
            raise ApiError(400, "ttl_seconds must be a number")
        applied = engine.set_state(
            active,
            source=str(body.get("source") or "client"),
            ttl_seconds=ttl,
        )
        return 200, {
            "ok": True,
            "active": applied,
            "delegated_ttl": engine.status()["delegated_ttl"],
        }

    if path == API_PREFIX + "/focus/session":
        if body.get("stop") is True:
            return 200, {
                "ok": True,
                "stopped": engine.stop_session(),
                "session": engine.session_state(),
            }
        if body.get("skip_break") is True:
            phase = engine.skip_break()
            return 200, {
                "ok": True,
                "session": engine.session_state(),
                "phase": phase,
            }
        if body.get("extend_minutes") is not None:
            minutes = number_field(body, "extend_minutes")
            session = engine.extend_session(minutes)
            return 200, {
                "ok": session is not None,
                "session": engine.session_state(),
                "note": "" if session is not None else "no session is running",
            }

        seconds = body.get("seconds")
        if seconds is not None:
            minutes = number_field(body, "seconds") / 60.0
        else:
            minutes = (
                number_field(body, "minutes")
                if body.get("minutes") is not None
                else None
            )
        mode = body.get("mode")
        if mode is not None and not isinstance(mode, str):
            raise ApiError(400, "mode must be a string")
        work = (
            number_field(body, "work_minutes")
            if body.get("work_minutes") is not None
            else None
        )
        rest = (
            number_field(body, "break_minutes")
            if body.get("break_minutes") is not None
            else None
        )
        cycles = body.get("cycles")
        if cycles is not None and (
            isinstance(cycles, bool) or not isinstance(cycles, (int, float))
        ):
            raise ApiError(400, "cycles must be a number")
        try:
            started = engine.start_session(
                minutes=minutes,
                mode=mode,
                work_minutes=work,
                break_minutes=rest,
                cycles=int(cycles) if cycles is not None else None,
            )
        except ValueError as exc:
            raise ApiError(400, str(exc)) from exc
        return 200, {
            "ok": True,
            "active": engine.active(),
            "until": started.get("until", 0.0),
            "session": engine.session_state(),
        }

    if path == API_PREFIX + "/focus/pause":
        seconds = body.get("seconds")
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
            raise ApiError(400, "seconds must be a number")
        return 200, {"ok": True, "pause_until": engine.pause(seconds)}

    if path == API_PREFIX + "/scan":
        return 200, engine.scan_once().to_wire()

    return 404, {"ok": False, "error": "no such endpoint: %s" % path}
