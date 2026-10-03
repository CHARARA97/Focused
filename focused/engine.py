"""Focused's engine: find the blacklisted applications and suspend them.

This is the application the user asked for -- the one that owns "find it and
freeze it".  ChillFocus (the game mod's daemon) can hand a session over to it;
Focused can also run a session by itself, with no game involved at all.

What it shares with ChillFocus, and why:

* ``focused.core.*`` -- the low-level decision and signal code (reading /proc,
  the cgroup rules, the freezer itself, the rule engine, the URL matcher).  Those
  modules are covered by 160+ tests, and keeping a second copy of the
  *dangerous* half (which process may be suspended, and how to resume it) is how
  a thaw gets lost.  Focused owns the application: its own config, its own
  session clock, its own protocol, its own state file.

Rules that keep it honest (mirrors ``docs/freeze-mode.md``):

* freeze only, never terminate;
* a session end, a hand-over, a crash or ``--thaw-all`` always resumes
  everything -- the state file is the source of truth;
* ``dry_run`` reports what it would do and touches nothing.
"""

from __future__ import annotations

import collections
import os
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Tuple

from .core import cgroupfs, urlfilter
from .core.freezer import MODE_PID, MODE_UNIT, Freezer, Frozen, Target
from .core.procfs import ProcessInfo, ProcFS
from .core.rules import ACTION_PROTECT, EvalContext, RuleSet, evaluate

from . import __version__
from .config import SESSION_MODES, Config, load_protect_names
from .plugins import (
    KIND_PYTHON,
    STATUS_DISABLED,
    STATUS_ENABLED,
    PluginRecord,
    PluginRegistry,
    load_addons,
)

#: How long a PID stays "already handled" so a scan does not re-freeze the same
#: target over and over while the kernel finishes the freeze.
HANDLED_TTL_SECONDS = 30.0

#: Sessions and pauses are reported to the browser extension, which ticks every
#: few seconds; rounding keeps the JSON readable.
def _now() -> float:
    return time.time()


class FocusedEngine:
    """The standalone freeze engine."""

    def __init__(
        self,
        config: Config,
        procfs: Optional[ProcFS] = None,
        freezer: Optional[Freezer] = None,
        logger=None,
        uid: Optional[int] = None,
        self_pid: Optional[int] = None,
        self_cgroup: Optional[str] = None,
        unified_cgroup2: Optional[bool] = None,
        clock=_now,
    ) -> None:
        self.config = config
        self.procfs = procfs or ProcFS()
        self.logger = logger
        self._clock = clock

        self._uid = os.getuid() if uid is None else uid
        self._self_pid = os.getpid() if self_pid is None else self_pid
        self._self_cgroup = (
            cgroupfs.read_self_cgroup() if self_cgroup is None else self_cgroup
        )
        self._unified_cgroup2 = (
            cgroupfs.is_unified_cgroup2() if unified_cgroup2 is None else unified_cgroup2
        )

        self._lock = threading.RLock()
        self._scan_lock = threading.Lock()
        self._rules: RuleSet = config.rules
        self._dry_run = bool(config.dry_run)
        self._freeze_enabled = bool(config.freeze.enabled)
        self._url_patterns = list(config.urls.patterns or [])

        self._freezer = freezer or Freezer(
            procfs=self.procfs,
            state_path=config.freeze.state_path,
            logger=logger,
            confirm_timeout_ms=config.freeze.confirm_timeout_ms,
        )

        #: Every session claim, keyed by the frontend that made it:
        #: ``source -> (expires_at, ttl)``.  A claim with ``expires_at == 0`` lasts
        #: until it is withdrawn; everything else has to be renewed, because a
        #: frontend that dies mid-session must not leave applications suspended.
        self._claims: Dict[str, Tuple[float, float]] = {}
        #: Rules contributed per source (plugin id, "config", "builtin").  Merged
        #: on every change; each source owns its own list and cannot disturb
        #: another's -- that is what makes several small plugins coexist.
        self._sources: Dict[str, Dict[str, Any]] = {}
        #: The event stream a plugin polls (or subscribes to by webhook).
        self._events: Deque[Dict[str, Any]] = collections.deque(maxlen=200)
        self._seq = 0
        self._addons: Dict[str, Any] = {}
        # The registry is a JSON record in the data directory; plugins_path is a
        # directory of Python modules.  Confusing the two writes a plugin list
        # where modules are expected (and breaks both).
        self._plugins = PluginRegistry(
            getattr(config, "registry_path", "")
            or os.path.join(os.path.dirname(config.freeze.state_path or "."), "plugins.json"),
            logger=logger,
            clock=clock,
        )
        #: A session started inside Focused itself.  One dict, because the three
        #: shapes (a fixed block, pomodoro rounds, an open-ended stopwatch) differ
        #: only in which fields they use, and every reader has to cope with a
        #: session that just advanced past a phase boundary.
        self._session: Optional[Dict[str, Any]] = None
        #: Set by the last advance, so housekeeping can react to the transition
        #: (a pomodoro break has to *resume* everything, a new work phase clears
        #: leftovers) without re-deriving it.
        self._session_transition: Optional[str] = None
        #: Temporary pass for the browser side, as an epoch deadline.
        self._pause_until = 0.0
        self._session_since = 0.0
        self._was_active = False

        # The configuration file is just another rule source: the UI edits it,
        # plugins contribute their own, and the merge is the same code path.
        self._sources["config"] = {
            "names": sorted(config.rules.names),
            "cmdline_substrings": list(config.rules.cmdline_substrings),
            "pids": sorted(config.rules.pids),
            "pid_guards": dict(config.rules.pid_guards),
            "protect_names": sorted(config.rules.protect_names),
            "protect_cmdline_substrings": list(config.rules.protect_cmdline_substrings),
            "url_patterns": list(config.urls.patterns or []),
        }
        self._handled: Dict[int, Tuple[Optional[int], float]] = {}
        self._recent: Deque[Dict[str, Any]] = collections.deque(maxlen=8)
        # The configuration file is just another rule source: the UI edits it,
        # plugins contribute their own, and the merge is one code path.
        self._sources["config"] = {
            "names": sorted(config.rules.names),
            "cmdline_substrings": list(config.rules.cmdline_substrings),
            "pids": sorted(config.rules.pids),
            "pid_guards": dict(config.rules.pid_guards),
            "protect_names": sorted(config.rules.protect_names),
            "protect_cmdline_substrings": list(config.rules.protect_cmdline_substrings),
            "url_patterns": list(config.urls.patterns or []),
        }
        self._recompute()

        self._stats: Dict[str, Any] = {
            "scans": 0,
            "matched": 0,
            "protected": 0,
            "frozen": 0,
            "thawed": 0,
            "thaw_failed": 0,
            "freeze_skipped": 0,
            "sessions": 0,
        }

    # -- logging -----------------------------------------------------------

    def _log(self, level: str, message: str) -> None:
        if self.logger is None:
            return
        getattr(self.logger, level, self.logger.info)(message)

    # -- session state -----------------------------------------------------

    def manual_session_active(self) -> bool:
        """Whether Focused itself is running a session (breaks included)."""
        self._advance_session()
        with self._lock:
            return self._session is not None

    def session(self) -> Optional[Dict[str, Any]]:
        """The running manual session, as a copy."""
        self._advance_session()
        with self._lock:
            return dict(self._session) if self._session else None

    def live_claims(self) -> Dict[str, float]:
        """Sources that currently claim a running session, with their expiry."""
        now = self._clock()
        with self._lock:
            return {
                source: until
                for source, (until, _ttl) in self._claims.items()
                if until == 0.0 or until > now
            }

    def claims(self) -> Dict[str, Dict[str, float]]:
        with self._lock:
            return {
                source: {"until": until, "ttl": ttl}
                for source, (until, ttl) in self._claims.items()
            }

    def delegated_active(self) -> bool:
        """Whether any frontend still claims a running session."""
        return bool(self.live_claims())

    def active(self) -> bool:
        """Whether a focus session is running right now.

        Own session, or somebody else's live claim: whichever comes first.  A
        pomodoro break is *not* active -- a break means the applications come back,
        which is the whole point of taking one.  Every claim has to be renewed
        (unless it was made without a lease), so a frontend that disappears cannot
        hold the session open.
        """
        self._advance_session()
        with self._lock:
            session = self._session
            manual = bool(session) and session.get("phase", "work") == "work"
        return bool(manual or self.live_claims())

    def delegated(self) -> bool:
        """Whether any frontend owns the session (even if its lease just lapsed)."""
        with self._lock:
            return bool(self._claims)

    def _nearest_claim_deadline(self) -> float:
        live = self.live_claims()
        deadlines = [until for until in live.values() if until]
        return min(deadlines) if deadlines else 0.0

    def _nearest_claim_ttl(self) -> float:
        with self._lock:
            ttls = [ttl for _until, ttl in self._claims.values() if ttl]
        return min(ttls) if ttls else 0.0

    def delegated_source(self) -> str:
        live = self.live_claims()
        if live:
            return ", ".join(sorted(live))
        with self._lock:
            return ", ".join(sorted(self._claims))

    def pause_until(self) -> float:
        with self._lock:
            return self._pause_until

    def session_since(self) -> float:
        with self._lock:
            if self._session:
                return float(self._session.get("started_at") or 0.0)
            return self._session_since

    def set_state(
        self, active: bool, source: str = "client", ttl_seconds: Optional[float] = None
    ) -> bool:
        """Add or withdraw one frontend's session claim.

        ``ttl_seconds`` makes the claim a lease: the frontend has to renew it
        before it expires, and when it does not -- because it crashed, was killed,
        or the machine froze it -- that claim lapses and, if nobody else is
        claiming, the session ends and everything is resumed.  Without a lease a
        dead frontend would leave applications suspended with nobody left to say
        "the session is over".
        """
        was = self.active()
        with self._lock:
            if active:
                ttl = max(1.0, float(ttl_seconds)) if ttl_seconds else 0.0
                until = self._clock() + ttl if ttl else 0.0
                self._claims[source] = (until, ttl)
            else:
                self._claims.pop(source, None)
        now_active = self.active()
        if active or was:
            self._audit(
                {
                    "kind": "state",
                    "active": bool(active),
                    "source": source,
                    "ttl_seconds": ttl_seconds or 0,
                }
            )
            self._log(
                "info",
                "claim %s by %s%s | session %s"
                % (
                    "made" if active else "withdrawn",
                    source,
                    " (lease %ss)" % int(ttl_seconds)
                    if active and ttl_seconds
                    else "",
                    "active" if now_active else "inactive",
                ),
            )
        if source != "builtin":
            self._plugins.note_session(source, float(ttl_seconds or 0.0))
        return now_active

    def start_session(
        self,
        minutes: Optional[float] = None,
        mode: Optional[str] = None,
        work_minutes: Optional[float] = None,
        break_minutes: Optional[float] = None,
        cycles: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Start (or extend) a session inside Focused itself.

        Three shapes, one state machine:

        ``countdown``
            N minutes, then it stops itself.
        ``pomodoro``
            work/break rounds.  Only the work phases are *active*: a break resumes
            everything, which is what a break is for.
        ``stopwatch``
            open-ended; it runs until the user stops it.
        """
        session_config = self.config.session
        chosen = (mode or session_config.mode or "countdown").strip().lower()
        if chosen not in SESSION_MODES:
            raise ValueError("mode must be one of: %s" % ", ".join(SESSION_MODES))

        now = self._clock()
        with self._lock:
            running = self._session

            # Asking for more time on a countdown is an extension, not a restart.
            if running and chosen == running.get("mode") and minutes is not None and chosen == "countdown":
                running["until"] += max(1.0, float(minutes)) * 60.0
                self._stats["sessions"] += 1
                snapshot = dict(running)
                self._audit({"kind": "session-extended", "until": snapshot["until"]})
                self._log("info", "session extended by %.1f minute(s)" % float(minutes))
                return snapshot

            if chosen == "countdown":
                duration = float(
                    minutes if minutes is not None else session_config.default_minutes
                )
                session = {
                    "mode": chosen,
                    "started_at": now,
                    "minutes": max(1.0, duration),
                    "phase": "work",
                    "until": now + max(1.0, duration) * 60.0,
                }
            elif chosen == "pomodoro":
                work = float(
                    work_minutes if work_minutes is not None else session_config.pomodoro_work_minutes
                )
                rest = float(
                    break_minutes if break_minutes is not None else session_config.pomodoro_break_minutes
                )
                rounds = int(cycles if cycles is not None else session_config.pomodoro_cycles)
                session = {
                    "mode": chosen,
                    "started_at": now,
                    "phase": "work",
                    "phase_started": now,
                    "phase_until": now + max(1.0, work) * 60.0,
                    "work_minutes": max(1.0, work),
                    "break_minutes": max(1.0, rest),
                    "cycle": 1,
                    "cycles": max(0, rounds),
                    "until": 0.0,
                }
            else:  # stopwatch
                session = {
                    "mode": chosen,
                    "started_at": now,
                    "phase": "work",
                    "until": 0.0,
                }

            self._session = session
            self._session_transition = "started"
            self._stats["sessions"] += 1
            snapshot = dict(session)

        self._audit({"kind": "session-started", **snapshot})
        self._log(
            "info",
            "session started (%s)" % snapshot["mode"]
            + (
                " %g min" % snapshot.get("minutes", 0)
                if snapshot["mode"] == "countdown"
                else " %g/%g min x%s"
                % (
                    snapshot.get("work_minutes", 0),
                    snapshot.get("break_minutes", 0),
                    snapshot.get("cycles") or "∞",
                )
                if snapshot["mode"] == "pomodoro"
                else ""
            ),
        )
        return snapshot

    def extend_session(self, minutes: float) -> Optional[Dict[str, Any]]:
        """Add time to whatever is running: the current phase, or the block."""
        minutes = max(1.0, float(minutes))
        self._advance_session()
        with self._lock:
            session = self._session
            if session is None:
                return None
            if session["mode"] == "stopwatch":
                # Nothing to extend -- it has no end.  Say so instead of pretending.
                return dict(session)
            if session["mode"] == "pomodoro":
                session["phase_until"] += minutes * 60.0
            else:
                session["until"] += minutes * 60.0
            snapshot = dict(session)
        self._audit({"kind": "session-extended", "minutes": minutes})
        self._log("info", "session extended by %g minute(s)" % minutes)
        return snapshot

    def skip_break(self) -> Optional[Dict[str, Any]]:
        """Cut a pomodoro break short and get back to work."""
        self._advance_session()
        with self._lock:
            session = self._session
            if session is None or session["mode"] != "pomodoro" or session["phase"] != "break":
                return dict(session) if session else None
            now = self._clock()
            session["phase"] = "work"
            session["phase_started"] = now
            session["phase_until"] = now + session["work_minutes"] * 60.0
            self._session_transition = "work"
            snapshot = dict(session)
        self._audit({"kind": "session-phase", "phase": "work", "reason": "break-skipped"})
        self._log("info", "break skipped; back to work")
        return snapshot

    def stop_session(self) -> bool:
        self._advance_session()
        with self._lock:
            was = self._session is not None
            self._session = None
            self._session_transition = "ended"
        self._audit({"kind": "session-stopped"})
        return was

    def _advance_session(self, now: Optional[float] = None) -> Optional[str]:
        """Move the session past any phase boundaries that are now due.

        Returns the transition that happened, if any: ``break`` / ``work`` /
        ``ended``.  Idempotent, and safe to call from a read path.
        """
        now = self._clock() if now is None else now
        with self._lock:
            session = self._session
            if session is None:
                return None

            if session["mode"] == "countdown":
                if session["until"] and session["until"] <= now:
                    self._session = None
                    self._session_transition = "ended"
                    self._audit({"kind": "session-ended", "reason": "completed"})
                    self._log("info", "session finished")
                    return "ended"
                return None

            if session["mode"] != "pomodoro":
                return None  # a stopwatch never ends by itself

            transition: Optional[str] = None
            guards = 0
            while session.get("phase_until") and session["phase_until"] <= now:
                guards += 1
                if guards > 64:  # a long sleep must not loop forever
                    session["phase_until"] = now
                    break
                if session["phase"] == "work":
                    if session["cycles"] and session["cycle"] >= session["cycles"]:
                        self._session = None
                        self._session_transition = "ended"
                        self._audit({"kind": "session-ended", "reason": "completed"})
                        self._log("info", "pomodoro finished: %d round(s)" % session["cycle"])
                        return "ended"
                    session["phase"] = "break"
                    session["phase_started"] = now
                    session["phase_until"] = now + session["break_minutes"] * 60.0
                    transition = "break"
                    self._audit({"kind": "session-phase", "phase": "break", "cycle": session["cycle"]})
                    self._log("info", "break for %g minute(s)" % session["break_minutes"])
                else:
                    session["cycle"] += 1
                    if session["cycles"] and session["cycle"] > session["cycles"]:
                        self._session = None
                        self._session_transition = "ended"
                        self._audit({"kind": "session-ended", "reason": "completed"})
                        self._log("info", "pomodoro finished: %d round(s)" % (session["cycle"] - 1))
                        return "ended"
                    session["phase"] = "work"
                    session["phase_started"] = now
                    session["phase_until"] = now + session["work_minutes"] * 60.0
                    transition = "work"
                    self._audit(
                        {"kind": "session-phase", "phase": "work", "cycle": session["cycle"]}
                    )
                    self._log("info", "round %d started" % session["cycle"])
            if transition:
                self._session_transition = transition
            return transition

    def _session_payload_fields(self) -> Dict[str, Any]:
        """The session as flat scalars: a Unity frontend cannot bind anything else."""
        state = self.session_state()
        return {
            "session_mode": state["mode"],
            "session_phase": state["phase"],
            "session_started_at": state["started_at"],
            "session_until": state["until"],
            "session_phase_ends_at": state["phase_ends_at"],
            "session_cycle": state["cycle"],
            "session_cycles": state["cycles"],
            "session_work_minutes": state["work_minutes"],
            "session_break_minutes": state["break_minutes"],
            "session_elapsed": state["elapsed"],
            "session_remaining": state["remaining"],
        }

    def session_state(self) -> Dict[str, Any]:
        """A flat description of the manual session, for the UI and the API."""
        self._advance_session()
        now = self._clock()
        with self._lock:
            session = dict(self._session) if self._session else None
        if session is None:
            return {
                "active": False,
                "mode": "",
                "phase": "",
                "started_at": 0.0,
                "until": 0.0,
                "phase_ends_at": 0.0,
                "cycle": 0,
                "cycles": 0,
                "work_minutes": 0.0,
                "break_minutes": 0.0,
                "elapsed": 0.0,
                "remaining": 0.0,
            }

        mode = session["mode"]
        deadline = session.get("phase_until") or session.get("until") or 0.0
        return {
            "active": mode != "pomodoro" or session.get("phase") == "work",
            "mode": mode,
            "phase": session.get("phase", "work"),
            "started_at": float(session.get("started_at") or 0.0),
            "until": float(session.get("until") or 0.0),
            "phase_ends_at": float(deadline),
            "cycle": int(session.get("cycle") or 0),
            "cycles": int(session.get("cycles") or 0),
            "work_minutes": float(session.get("work_minutes") or 0.0),
            "break_minutes": float(session.get("break_minutes") or 0.0),
            "elapsed": max(0.0, now - float(session.get("started_at") or now)),
            "remaining": max(0.0, deadline - now) if deadline else 0.0,
        }

    def paused(self) -> bool:
        with self._lock:
            return self._pause_until > self._clock()

    def pause(self, seconds: float) -> float:
        """Let the user off for a while without ending the session.

        This resumes what is suspended *and* holds off new freezes until it
        expires: "放行 5 分钟" has to mean the applications come back, not that
        the browser is excused while the editors stay stuck.
        """
        seconds = max(0.0, float(seconds))
        self._advance_session()
        with self._lock:
            now = self._clock()
            self._pause_until = now + seconds if seconds else 0.0
            until = self._pause_until
            # A pass is time off, not time served: shift the session's own clock so
            # "放行 10 分钟" does not silently consume ten minutes of the block.
            session = self._session
            if session is not None and seconds:
                if session["mode"] == "pomodoro" and session.get("phase_until"):
                    session["phase_until"] += seconds
                elif session.get("until"):
                    session["until"] += seconds
        self._audit({"kind": "pause", "seconds": seconds})
        return until

    def set_dry_run(self, value: bool) -> bool:
        with self._lock:
            self._dry_run = bool(value)
            return self._dry_run

    @property
    def dry_run(self) -> bool:
        with self._lock:
            return self._dry_run

    # -- rules -------------------------------------------------------------

    def rules(self) -> RuleSet:
        with self._lock:
            return self._rules

    def apply_rules(self, payload: Dict[str, Any], source: str = "builtin") -> RuleSet:
        """Record one source's rules and re-merge.

        The blacklist is the **union** of every source's list: a plugin that wants
        to withdraw its rules sends an empty list and only its own entry changes.
        The protect list is also a union, and it can never shrink -- no source may
        weaken the rails, because losing one is how a desktop freezes.
        """
        payload = payload or {}
        entry: Dict[str, Any] = {
            "names": [str(item) for item in (payload.get("names") or []) if str(item).strip()],
            "cmdline_substrings": [
                str(item) for item in (payload.get("cmdline_substrings") or []) if str(item).strip()
            ],
            "pids": [int(item) for item in (payload.get("pids") or [])],
            "pid_guards": dict(payload.get("pid_guards") or {}),
            "protect_names": [
                str(item).lower() for item in (payload.get("protect_names") or []) if str(item).strip()
            ],
            "protect_cmdline_substrings": [
                str(item)
                for item in (payload.get("protect_cmdline_substrings") or [])
                if str(item).strip()
            ],
            "url_patterns": [str(item) for item in (payload.get("url_patterns") or []) if str(item).strip()],
        }
        with self._lock:
            self._sources[source] = entry
        rules = self._recompute()
        self._audit(
            {
                "kind": "rules",
                "source": source,
                "names": len(rules.names),
                "cmdline": len(rules.cmdline_substrings),
                "pids": len(rules.pids),
            }
        )
        self._log(
            "info",
            "rules from %s: %d name(s), %d cmdline, %d pid(s) (merged: %d name(s))"
            % (
                source,
                len(entry["names"]),
                len(entry["cmdline_substrings"]),
                len(entry["pids"]),
                len(rules.names),
            ),
        )
        if source != "config":
            self._plugins.note_rules(source, len(entry["names"]) + len(entry["cmdline_substrings"]) + len(entry["pids"]))
        return rules

    def clear_source(self, source: str) -> None:
        """Forget everything a source contributed (plugin removed or disabled)."""
        with self._lock:
            self._sources.pop(source, None)
        self._recompute()
        self._log("info", "rules from %s withdrawn" % source)

    def sources(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {key: dict(value) for key, value in self._sources.items()}

    def _recompute(self) -> RuleSet:
        """Merge the config file and every plugin into the live rule set."""
        with self._lock:
            sources = {key: dict(value) for key, value in self._sources.items()}

        names: set = set()
        cmdline: List[str] = []
        pids: set = set()
        guards: Dict[int, int] = {}
        protect_names = {name.lower() for name in load_protect_names()}
        protect_substrings: List[str] = []
        urls: List[str] = []

        for entry in sources.values():
            names |= set(entry.get("names") or ())
            for text in entry.get("cmdline_substrings") or ():
                if text not in cmdline:
                    cmdline.append(text)
            pids |= set(entry.get("pids") or ())
            guards.update(entry.get("pid_guards") or {})
            protect_names |= set(entry.get("protect_names") or ())
            for text in entry.get("protect_cmdline_substrings") or ():
                if text not in protect_substrings:
                    protect_substrings.append(text)
            for pattern in entry.get("url_patterns") or ():
                if pattern not in urls:
                    urls.append(pattern)

        rules = RuleSet.from_dict(
            {
                "names": sorted(names),
                "cmdline_substrings": cmdline,
                "pids": sorted(pids),
                "pid_guards": {str(k): v for k, v in guards.items()},
                "protect_names": sorted(protect_names),
                "protect_cmdline_substrings": sorted(protect_substrings),
            }
        )
        with self._lock:
            self._rules = rules
            self._url_patterns = urlfilter.sanitize(urls)
        self._thaw_stale()
        return rules

    def set_url_patterns(self, patterns, source: str = "config") -> List[str]:
        """Publish a browser blacklist under ``source`` (union of all sources)."""
        with self._lock:
            entry = dict(self._sources.get(source) or {})
            entry["url_patterns"] = [str(item) for item in (patterns or []) if str(item).strip()]
            self._sources[source] = entry
        self._recompute()
        return self.url_patterns()

    def url_patterns(self) -> List[str]:
        with self._lock:
            return urlfilter.sanitize(self._url_patterns)

    # -- freeze/thaw -------------------------------------------------------

    def frozen(self) -> List[Frozen]:
        return self._freezer.entries()

    def thaw_all(self, reason: str = "manual") -> int:
        self._freezer.ensure_loaded()
        entries = self._freezer.entries()
        count = self._freezer.thaw_all(reason)
        if entries:
            self._stats["thawed"] += count
            self._stats["thaw_failed"] += max(0, len(entries) - count)
            self._audit({"kind": "thaw", "count": count, "reason": reason})
            self._log("info", "resumed %d target(s) (%s)" % (count, reason))
        return count

    def recover_frozen(self) -> int:
        entries = self._freezer.load()
        if not entries:
            return 0
        self._log(
            "warning",
            "found %d suspended target(s) from a previous run; resuming them"
            % len(entries),
        )
        return self.thaw_all("startup-recovery")

    def close(self) -> int:
        return self.thaw_all("shutdown")

    def _thaw_stale(self) -> int:
        """Resume targets that no longer match the current rules."""
        count = 0
        for entry in self._freezer.entries():
            proc = self.procfs.read(entry.pid)
            if proc is None:
                self._freezer.thaw(entry, "process-gone")
                count += 1
                continue
            decision = evaluate(proc, self.rules(), self._eval_context())
            if decision.action == ACTION_PROTECT or not decision.should_kill:
                self._freezer.thaw(entry, "no-longer-matches")
                count += 1
        if count:
            self._stats["thawed"] += count
            self._log("info", "resumed %d target(s) that no longer match" % count)
        return count

    def _eval_context(self) -> EvalContext:
        return EvalContext(
            uid=self._uid,
            self_pid=self._self_pid,
            self_ppid=os.getppid(),
            # -1 can never match a real process group, so Focused never claims to
            # protect a group it does not own -- each target is judged on its own.
            self_pgid=-1,
            hard_protect_pids=frozenset({self._self_pid, 1, 0}),
            protect_own_process_group=False,
        )

    def _is_handled(self, proc: ProcessInfo, now: float) -> bool:
        entry = self._handled.get(proc.pid)
        if entry is None:
            return False
        starttime, expiry = entry
        if now >= expiry:
            del self._handled[proc.pid]
            return False
        if starttime is not None and proc.starttime is not None and starttime != proc.starttime:
            del self._handled[proc.pid]
            return False
        return True

    def _mark_handled(self, proc: ProcessInfo, now: float) -> None:
        self._handled[proc.pid] = (proc.starttime, now + HANDLED_TTL_SECONDS)
        if len(self._handled) > 4096:
            for pid in [p for p, (_, exp) in self._handled.items() if exp <= now]:
                del self._handled[pid]

    def _freezable_pids(self, pids) -> List[int]:
        """The part of a process tree Focused may safely suspend.

        Focused never suspends itself, its own process group, or anything on the
        protect list -- a blacklisted shell that once started Focused is exactly
        that case, and freezing the engine means nobody is left to resume
        anything.
        """
        out: List[int] = []
        for index, raw in enumerate(pids):
            pid = int(raw)
            if pid <= 1 or pid == self._self_pid:
                if index == 0:
                    # The root itself is off limits: suspending only part of an
                    # application is worse than leaving it alone.
                    return []
                continue
            out.append(pid)
        return out

    # -- the scan ----------------------------------------------------------

    def scan_once(self) -> int:
        """Run exactly one scan.  Returns how many targets were suspended."""
        if not self._scan_lock.acquire(blocking=False):
            return 0
        try:
            return self._scan_locked()
        except Exception as exc:  # a bad scan must never kill the daemon
            self._log("error", "scan failed: %s: %s" % (type(exc).__name__, exc))
            return 0
        finally:
            self._scan_lock.release()

    def _scan_locked(self) -> int:
        active = self.active()
        paused = self.paused()
        self._housekeeping(active, paused)
        if not active or paused:
            return 0

        with self._lock:
            rules = self._rules
            dry_run = self._dry_run
            freeze_enabled = self._freeze_enabled

        now = self._clock()
        self._stats["scans"] += 1
        if not freeze_enabled:
            return 0

        ctx = self._eval_context()
        procs = self.procfs.snapshot()
        parents = {p.pid: p.ppid for p in procs}
        starttimes = {p.pid: p.starttime for p in procs}

        frozen_now = 0
        for proc in procs:
            if self._is_handled(proc, now):
                continue
            decision = evaluate(proc, rules, ctx)
            if decision.action == ACTION_PROTECT:
                if decision.rule.startswith("protect."):
                    self._stats["protected"] += 1
                continue
            if not decision.should_kill:
                continue
            self._stats["matched"] += 1

            if len(self._freezer) >= int(self.config.freeze.max_per_scan):
                self._stats["freeze_skipped"] += 1
                continue

            if self._freeze_target(proc, parents, starttimes, decision.rule, now, dry_run):
                frozen_now += 1

        return frozen_now

    def _housekeeping(self, active: bool, paused: bool = False) -> None:
        """Resume when the session ends or a pass was handed out, and clear
        leftovers when a session starts."""
        rising = active and not self._was_active
        self._was_active = active
        if rising:
            self._session_since = self._clock()

        # Lapsed claims are a session end even when nothing was suspended, so the
        # bookkeeping happens before the "is there anything to resume?" shortcut.
        now = self._clock()
        with self._lock:
            lapsed = [
                source
                for source, (until, _ttl) in self._claims.items()
                if until and until <= now
            ]
            for source in lapsed:
                self._claims.pop(source, None)
        lease_lapsed = bool(lapsed)
        for source in lapsed:
            self._log(
                "warning",
                "session lease from %s expired; treating its claim as over" % source,
            )
            self._audit({"kind": "lease-expired", "source": source})

        transition, self._session_transition = self._session_transition, None
        self._freezer.ensure_loaded()
        if not len(self._freezer):
            return
        if not active:
            if lease_lapsed:
                self.thaw_all("lease-expired")
            elif transition == "break":
                self.thaw_all("break")
            else:
                self.thaw_all("session-ended")
        elif paused:
            # A temporary pass means the applications come back now; when it
            # expires the next scan freezes them again.
            self.thaw_all("paused")
        elif rising:
            # A leftover from a run that did not shut down cleanly.
            self.thaw_all("session-start")

    def _freeze_target(
        self,
        proc: ProcessInfo,
        parents: Dict[int, Optional[int]],
        starttimes: Dict[int, Optional[int]],
        rule: str,
        now: float,
        dry_run: bool,
    ) -> bool:
        cgroup = self.procfs.cgroup(proc.pid)
        plan = cgroupfs.plan_for(cgroup, self._self_cgroup, unified=self._unified_cgroup2)
        use_units = bool(self.config.freeze.use_units)
        mode = MODE_UNIT if (use_units and plan.plan == cgroupfs.PLAN_UNIT) else MODE_PID

        pids = self._freezable_pids(cgroupfs.process_tree(proc.pid, parents) or (proc.pid,))
        if not pids:
            self._stats["freeze_skipped"] += 1
            return False

        if dry_run:
            self._audit(
                {
                    "kind": "dry-run",
                    "pid": proc.pid,
                    "name": proc.display_name(),
                    "mode": mode,
                    "rule": rule,
                }
            )
            self._log(
                "info",
                "would freeze pid=%d (%s) mode=%s tree=%d"
                % (proc.pid, proc.display_name(), mode, len(pids)),
            )
            self._mark_handled(proc, now)
            return False

        guard = {int(pid): starttimes.get(int(pid)) for pid in pids}
        if proc.starttime is not None:
            guard[proc.pid] = proc.starttime
        entry = self._freezer.freeze(
            Target(
                pid=proc.pid,
                name=proc.display_name(),
                pids=tuple(pids),
                guard=guard,
                mode=mode,
                cgroup=cgroup or "",
                unit=plan.unit or "",
            ),
            reason=rule,
        )
        if entry is None:
            self._stats["freeze_skipped"] += 1
            return False

        self._stats["frozen"] += 1
        self._recent.append(
            {
                "name": entry.name,
                "pid": entry.pid,
                "mode": entry.mode,
                "unit": entry.unit,
                "at": entry.since,
                "rule": rule,
            }
        )
        self._audit(
            {
                "kind": "freeze",
                "pid": entry.pid,
                "name": entry.name,
                "mode": entry.mode,
                "unit": entry.unit,
                "pids": list(entry.pids),
                "rule": rule,
            }
        )
        self._log(
            "info",
            "froze pid=%d (%s) mode=%s unit=%s tree=%d via %s"
            % (entry.pid, entry.name, entry.mode, entry.unit or "-", len(entry.pids), rule),
        )
        self._mark_handled(proc, now)
        return True

    def run_forever(self, stop_event: threading.Event) -> None:
        self._log(
            "info",
            "scan loop started (interval=%.2fs, dry_run=%s)"
            % (self.config.scan_interval, self._dry_run),
        )
        while not stop_event.is_set():
            started = self._clock()
            self.scan_once()
            elapsed = self._clock() - started
            stop_event.wait(max(0.0, self.config.scan_interval - elapsed))

    # -- events ------------------------------------------------------------

    def _record_event(self, kind: str, **fields: Any) -> Dict[str, Any]:
        """Append one event to the ring buffer (no delivery)."""
        with self._lock:
            self._seq += 1
            event = {
                "seq": self._seq,
                "ts": self._clock(),
                "kind": kind,
                "source": str(fields.get("source") or ""),
                "name": str(fields.get("name") or ""),
                "pid": int(fields.get("pid") or 0),
                "detail": str(fields.get("detail") or fields.get("rule") or ""),
            }
            self._events.append(event)
        return event

    def _emit(self, kind: str, **fields: Any) -> Dict[str, Any]:
        """Append one event and hand it to whoever subscribed.

        The stream is what the UI shows and what plugins poll; a webhook is the
        same payload pushed.  Neither may slow a scan down, so delivery failures
        are recorded and forgotten.
        """
        event = self._record_event(kind, **fields)
        self._dispatch_event(event)
        return event

    def events_since(self, since: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            events = list(self._events)
        out = [event for event in events if event["seq"] > int(since or 0)]
        return out[-max(1, int(limit or 100)) :]

    def _dispatch_event(self, event: Dict[str, Any]) -> None:
        for plugin_id, module in list(self._addons.items()):
            callback = getattr(module, "on_event", None)
            if callback is None:
                continue
            try:
                callback(self, event)
            except Exception as exc:  # a plugin must not break the engine
                self._plugin_failed(plugin_id, "on_event", exc)
        for record in self._plugins.list():
            if not record.webhook_url:
                continue
            self._post_webhook(record, event)

    def _post_webhook(self, record: PluginRecord, event: Dict[str, Any]) -> None:
        import json as _json
        import threading
        import urllib.request

        def deliver() -> None:
            request = urllib.request.Request(
                record.webhook_url,
                data=_json.dumps(event, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Focused-Token": record.token},
                method="POST",
            )
            try:
                urllib.request.urlopen(request, timeout=2.0).close()
            except Exception as exc:
                # Best effort: a plugin that cannot be reached is its own problem.
                self._log(
                    "warning",
                    "webhook for %s failed: %s" % (record.id, type(exc).__name__),
                )

        threading.Thread(target=deliver, daemon=True).start()

    # -- plugins -----------------------------------------------------------

    @property
    def plugins(self) -> PluginRegistry:
        return self._plugins

    def _plugin_failed(self, plugin_id: str, where: str, exc: BaseException) -> None:
        """Record a plugin failure, and stop trusting a plugin that keeps failing.

        The error event is appended *without* dispatching: handing it back to the
        plugin that just threw would be a loop.
        """
        self._plugins.note_error(plugin_id)
        record = self._plugins.get(plugin_id)
        errors = record.errors if record is not None else 1
        self._record_event(
            "error",
            source=plugin_id,
            detail="plugin %s failed: %s: %s" % (where, type(exc).__name__, exc),
        )
        if errors >= 5:
            with self._lock:
                self._addons.pop(plugin_id, None)
            self._plugins.set_status(plugin_id, "disabled")
            self._record_event(
                "plugin",
                source=plugin_id,
                detail="disabled after %d failures" % errors,
            )
            self._log("error", "plugin %s disabled after %d failures" % (plugin_id, errors))

    def bind_addon(self, plugin_id: str, module: Any) -> None:
        with self._lock:
            self._addons[plugin_id] = module

    def addons(self) -> List[str]:
        with self._lock:
            return sorted(self._addons)

    def load_plugins(self, directory: str) -> List[Dict[str, str]]:
        """Load the in-process plugins; returns the ones that failed."""
        self._plugins.load()
        result = load_addons(directory, self, self._plugins, self.logger)
        for plugin_id in result.loaded:
            self._emit("plugin", source=plugin_id, detail="plugin loaded")
        for failure in result.failed:
            self._emit("error", source=failure["id"], detail="plugin failed: " + failure["error"])
        return result.failed

    def set_plugin_status(self, plugin_id: str, enabled: bool) -> Optional[PluginRecord]:
        """Disable a plugin: withdraw its rules and its claim immediately."""
        status = STATUS_ENABLED if enabled else STATUS_DISABLED
        record = self._plugins.set_status(plugin_id, status)
        if record is None:
            return None
        if not enabled:
            self.clear_source(plugin_id)
            self.set_state(False, source=plugin_id)
            with self._lock:
                self._addons.pop(plugin_id, None)
        self._emit("plugin", source=plugin_id, detail=status)
        return record

    def remove_plugin(self, plugin_id: str) -> bool:
        self.clear_source(plugin_id)
        self.set_state(False, source=plugin_id)
        with self._lock:
            self._addons.pop(plugin_id, None)
        removed = self._plugins.remove(plugin_id)
        if removed:
            self._emit("plugin", source=plugin_id, detail="removed")
        return removed

    def plugin_rules(self, plugin_id: str, payload: Dict[str, Any]) -> RuleSet:
        record = self._plugins.get(plugin_id)
        if record is None or record.status != STATUS_ENABLED:
            raise PermissionError("plugin %s is not enabled" % plugin_id)
        return self.apply_rules(payload, source=plugin_id)

    def plugin_session(
        self, plugin_id: str, active: bool, ttl_seconds: Optional[float] = None
    ) -> bool:
        record = self._plugins.get(plugin_id)
        if record is None or record.status != STATUS_ENABLED:
            raise PermissionError("plugin %s is not enabled" % plugin_id)
        return self.set_state(active, source=plugin_id, ttl_seconds=ttl_seconds)

    # -- configuration -----------------------------------------------------

    def update_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """Apply a partial configuration change and persist it.

        Only what the UI can safely change is accepted; the port, the state file
        and the plugin directory need a restart, so they are reported instead of
        being changed under a running engine.
        """
        patch = patch or {}
        warnings: List[str] = []
        config = self.config

        if "dry_run" in patch:
            self.set_dry_run(bool(patch["dry_run"]))
            config.dry_run = self.dry_run
        if "scan_interval" in patch:
            value = float(patch["scan_interval"])
            if value < 0.1:
                raise ValueError("scan_interval must be >= 0.1")
            config.scan_interval = value
        if isinstance(patch.get("session"), dict):
            session = patch["session"]
            if "mode" in session:
                mode = str(session["mode"]).strip().lower()
                if mode not in SESSION_MODES:
                    raise ValueError("mode must be one of: %s" % ", ".join(SESSION_MODES))
                config.session.mode = mode
            if "default_minutes" in session:
                config.session.default_minutes = max(1, int(session["default_minutes"]))
            if "pause_minutes" in session:
                config.session.pause_minutes = max(1, int(session["pause_minutes"]))
            if "pomodoro_work_minutes" in session:
                config.session.pomodoro_work_minutes = max(
                    1, int(session["pomodoro_work_minutes"])
                )
            if "pomodoro_break_minutes" in session:
                config.session.pomodoro_break_minutes = max(
                    1, int(session["pomodoro_break_minutes"])
                )
            if "pomodoro_cycles" in session:
                config.session.pomodoro_cycles = max(0, int(session["pomodoro_cycles"]))
        if isinstance(patch.get("http"), dict):
            http = patch["http"]
            if "token" in http:
                config.http.token = str(http["token"] or "")
                if hasattr(self, "_server_token_setter") and self._server_token_setter:
                    self._server_token_setter(config.http.token)
            for forbidden in ("port", "host", "enabled"):
                if forbidden in http:
                    warnings.append("http.%s needs a restart" % forbidden)
        if isinstance(patch.get("freeze"), dict):
            freeze = patch["freeze"]
            if "enabled" in freeze:
                config.freeze.enabled = bool(freeze["enabled"])
                self._freeze_enabled = config.freeze.enabled
            if "max_per_scan" in freeze:
                config.freeze.max_per_scan = max(1, int(freeze["max_per_scan"]))
            for forbidden in ("state_path", "use_units", "confirm_timeout_ms"):
                if forbidden in freeze:
                    warnings.append("freeze.%s needs a restart" % forbidden)
        if isinstance(patch.get("urls"), dict) and "patterns" in patch["urls"]:
            config.urls.patterns = urlfilter.sanitize(patch["urls"]["patterns"])
            self.set_url_patterns(config.urls.patterns, source="config")

        # The blacklist/protect sections are just another rule source, so a change
        # here can never disturb what a plugin contributed.
        if isinstance(patch.get("blacklist"), dict) or isinstance(patch.get("protect"), dict):
            blacklist = patch.get("blacklist") or {}
            protect = patch.get("protect") or {}
            entry = {
                "names": blacklist.get("names", sorted(_config_blacklist(config)["names"])),
                "cmdline_substrings": blacklist.get(
                    "cmdline_substrings", _config_blacklist(config)["cmdline_substrings"]
                ),
                "pids": blacklist.get("pids", sorted(_config_blacklist(config)["pids"])),
                "protect_names": protect.get("names", sorted(_config_protect(config))),
                "protect_cmdline_substrings": protect.get(
                    "cmdline_substrings", list(config.rules.protect_cmdline_substrings)
                ),
            }
            self.apply_rules(entry, source="config")

        if config.path:
            try:
                config.save(config.path)
            except OSError as exc:
                warnings.append("could not write %s: %s" % (config.path, exc))
        else:
            warnings.append("no config path: changes apply until Focused restarts")

        payload = self.config_dict()
        payload["warnings"] = warnings
        self._emit("config", detail="configuration updated")
        return payload

    def config_dict(self) -> Dict[str, Any]:
        config = self.config
        source = config_source_rules(self)
        return {
            "ok": True,
            "path": config.path or "",
            "dry_run": self.dry_run,
            "scan_interval": config.scan_interval,
            "blacklist": {
                "names": sorted(source["names"]),
                "cmdline_substrings": list(source["cmdline_substrings"]),
                "pids": sorted(source["pids"]),
            },
            "protect": {
                "names": sorted(source["protect_names"]),
                "cmdline_substrings": list(source["protect_cmdline_substrings"]),
            },
            "urls": {"patterns": self.url_patterns()},
            "session": {
                "mode": config.session.mode,
                "default_minutes": config.session.default_minutes,
                "pause_minutes": config.session.pause_minutes,
                "pomodoro_work_minutes": config.session.pomodoro_work_minutes,
                "pomodoro_break_minutes": config.session.pomodoro_break_minutes,
                "pomodoro_cycles": config.session.pomodoro_cycles,
            },
            "session_state": self.session_state(),
            "http": {
                "enabled": config.http.enabled,
                "host": config.http.host,
                "port": config.http.port,
                "token_set": bool(config.http.token),
            },
            "freeze": {
                "enabled": config.freeze.enabled,
                "max_per_scan": config.freeze.max_per_scan,
                "use_units": config.freeze.use_units,
                "state_path": config.freeze.state_path,
            },
            "plugins_path": getattr(config, "plugins_path", ""),
            "protected_count": len(self.rules().protect_names),
        }

    # -- process list ------------------------------------------------------

    def visible_processes(self, limit: int = 400) -> List[Dict[str, Any]]:
        """Distinct process names of this user, for the picker in the UI."""
        counts: Dict[str, int] = {}
        shielded: Dict[str, str] = {}
        rules = self.rules()
        for proc in self.procfs.snapshot():
            if proc.is_kernel_thread or proc.is_zombie:
                continue
            if self._uid is not None and proc.uid is not None and proc.uid != self._uid:
                continue
            name = proc.comm or proc.exe_basename
            if not name:
                continue
            counts[name] = counts.get(name, 0) + 1
            if name not in shielded:
                decision = evaluate(proc, rules, self._eval_context())
                if decision.action == ACTION_PROTECT:
                    shielded[name] = decision.rule
        items = [
            {
                "name": name,
                "count": counts[name],
                "protected": name in shielded,
                "protect_rule": shielded.get(name, ""),
            }
            for name in sorted(counts)
        ]
        return items[: max(1, int(limit or 400))]

    def visible_process_arrays(self, limit: int = 400) -> Dict[str, Any]:
        """The same list as parallel primitive arrays.

        A frontend inside Unity cannot bind an array of objects (JsonUtility
        leaves it null), so the arrays are what the game plugin reads and the
        objects are for the web UI.
        """
        items = self.visible_processes(limit)
        return {
            "names": [item["name"] for item in items],
            "counts": [int(item["count"]) for item in items],
            "protected_flags": [bool(item["protected"]) for item in items],
            "protect_rules": [item["protect_rule"] for item in items],
        }

    # -- introspection -----------------------------------------------------

    def _audit(self, record: Dict[str, Any]) -> None:
        logger = getattr(self, "audit", None)
        if logger is not None:
            logger.write(record)
        # The same record is the event stream: one place to write, one place to
        # notify, so a plugin cannot be told about something the audit never saw.
        fields = {key: value for key, value in record.items() if key != "kind"}
        self._emit(record.get("kind") or "event", **fields)

    def focus_state(self) -> Dict[str, Any]:
        """What the built-in frontends read: is a session on, and what is banned.

        Deliberately flat: Unity's JsonUtility binds public fields and primitive
        arrays only, so anything the game plugin needs must be a scalar or an
        array of strings/ints here -- never a nested object.
        """
        now = self._clock()
        return {
            "ok": True,
            "active": self.active(),
            "since": self.session_since(),
            "pause_until": self._pause_until,
            "paused": now < self._pause_until,
            "url_patterns": self.url_patterns(),
            "manual_session": self.manual_session_active(),
            "delegated": self.delegated(),
            "delegated_active": self.delegated_active(),
            "delegated_until": self._nearest_claim_deadline(),
            "delegated_ttl": self._nearest_claim_ttl(),
            "delegated_source": self.delegated_source(),
            "dry_run": self._dry_run,
            **self._session_payload_fields(),
            "frozen_count": len(self._freezer),
            "frozen_names": [entry.name for entry in self._freezer.entries()],
            "frozen_units": [
                entry.unit for entry in self._freezer.entries() if entry.unit
            ],
            "frozen_total": int(self._stats.get("frozen") or 0),
            "recent_names": [item.get("name", "") for item in list(self._recent)],
            "protect_count": len(self.rules().protect_names),
            "pid": int(self._self_pid or 0),
            "version": __version__,
        }

    def frozen_list(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": entry.name,
                "pid": entry.pid,
                "mode": entry.mode,
                "unit": entry.unit,
                "cgroup": entry.cgroup,
                "since": entry.since,
                "reason": entry.reason,
                "pids": list(entry.pids),
            }
            for entry in self._freezer.entries()
        ]

    def status(self) -> Dict[str, Any]:
        with self._lock:
            rules = self._rules
        return {
            "ok": True,
            "version": __version__,
            "active": self.active(),
            "manual_session": self.manual_session_active(),
            "delegated": self.delegated(),
            "delegated_active": self.delegated_active(),
            "delegated_until": self._nearest_claim_deadline(),
            "delegated_ttl": self._nearest_claim_ttl(),
            "delegated_source": self.delegated_source(),
            "session_since": self.session_since(),
            "session_state": self.session_state(),
            **self._session_payload_fields(),
            "pause_until": self._pause_until,
            "dry_run": self._dry_run,
            "freeze_enabled": self._freeze_enabled,
            "unified_cgroup2": self._unified_cgroup2,
            "self_cgroup": self._self_cgroup or "",
            "state_path": self.config.freeze.state_path,
            "blacklist": {
                "names": sorted(rules.names),
                "cmdline_substrings": list(rules.cmdline_substrings),
                "pids": sorted(rules.pids),
            },
            "protect_count": len(rules.protect_names),
            "url_patterns": self.url_patterns(),
            "frozen_count": len(self._freezer),
            "frozen_names": [entry.name for entry in self._freezer.entries()],
            "frozen": self.frozen_list(),
            "recent": list(self._recent),
            "stats": dict(self._stats),
            # -- plugins, claims and the event stream ----------------------
            "delegated_active": self.delegated_active(),
            "claims": self.claims(),
            "sources": {
                source: len(entry.get("names") or ()) + len(entry.get("cmdline_substrings") or ())
                for source, entry in self.sources().items()
            },
            "plugins": self.plugin_payloads(),
            "addons": self.addons(),
            "event_seq": self._seq,
            "plugins_path": getattr(self.config, "plugins_path", ""),
        }

    def plugin_payloads(self) -> List[Dict[str, Any]]:
        """The plugin list as the UI shows it: who, when, what, and are they on."""
        now = self._clock()
        claims = self.claims()
        live = self.live_claims()
        out: List[Dict[str, Any]] = []
        for record in self._plugins.list():
            payload = record.to_dict()
            payload.update(
                {
                    "enabled": record.status == STATUS_ENABLED,
                    "last_seen_ago": round(max(0.0, now - record.last_seen), 1)
                    if record.last_seen
                    else None,
                    "session_claim": claims.get(record.id),
                    "session_live": record.id in live,
                }
            )
            out.append(payload)
        return out


def _config_blacklist(config) -> Dict[str, Any]:
    return {
        "names": set(config.rules.names),
        "cmdline_substrings": list(config.rules.cmdline_substrings),
        "pids": set(config.rules.pids),
    }


def _config_protect(config) -> set:
    return set(config.rules.protect_names)


def config_source_rules(engine) -> Dict[str, Any]:
    """What the configuration file alone contributes (for the config editor)."""
    entry = engine.sources().get("config") or {}
    return {
        "names": set(entry.get("names") or ()),
        "cmdline_substrings": list(entry.get("cmdline_substrings") or ()),
        "pids": set(entry.get("pids") or ()),
        "protect_names": set(entry.get("protect_names") or ()),
        "protect_cmdline_substrings": list(entry.get("protect_cmdline_substrings") or ()),
    }
