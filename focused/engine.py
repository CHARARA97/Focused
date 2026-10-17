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
from typing import Any, Deque, Dict, List, Optional

from .core import cgroupfs
from .core.freezer import Freezer, Frozen
from .core.procfs import ProcFS
from .core.rules import RuleSet
from .app.plugin_gateway import PluginGateway
from .app.rules import RuleSourceService
from .app.config import ConfigService
from .app.events import EventBus
from .app.freeze import FreezeService
from .app.plugin_runtime import PluginRuntime
from .app.scan import ScanRequester, ScanService
from .app.status import StatusService
from .app.session import SessionService
from .app.state import StateService
from .diagnostics import log_event
from .domain.health import Health

from . import __version__
from .config import Config
from .plugins import PluginRecord, PluginRegistry

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
        self._dry_run = bool(config.dry_run)
        self._freeze_enabled = bool(config.freeze.enabled)

        self._freezer = freezer or Freezer(
            procfs=self.procfs,
            state_path=config.freeze.state_path,
            logger=logger,
            confirm_timeout_ms=config.freeze.confirm_timeout_ms,
        )

        # The registry is a JSON record in the data directory; plugins_path is a
        # directory of Python modules.  Confusing the two writes a plugin list
        # where modules are expected (and breaks both).
        self._plugins = PluginRegistry(
            getattr(config, "registry_path", "")
            or os.path.join(os.path.dirname(config.freeze.state_path or "."), "plugins.json"),
            logger=logger,
            clock=clock,
        )
        self._recent: Deque[Dict[str, Any]] = collections.deque(maxlen=8)

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
        self._health = Health(
            degraded_after=self.DEGRADED_AFTER,
            freeze_enabled=self._freeze_enabled,
        )

        # Application services.  They are deliberately thin: the engine still
        # owns the state, while the services own the semantics a plugin may use.
        self._rule_sources = RuleSourceService(
            procfs=self.procfs,
            initial_rules=config.rules,
            url_patterns=config.urls.patterns or (),
            on_changed=self._rules_changed,
            log=self._log,
        )
        self._state_service = StateService(self, self._rule_sources)
        self._session_service = SessionService(
            lambda: self._clock(),
            session_config=lambda: self.config.session,
            stats=self._stats,
            audit=self._audit,
            log=self._log,
        )
        self._freeze_service = FreezeService(
            procfs=self.procfs,
            freezer=self._freezer,
            rules=self._rule_sources,
            clock=lambda: self._clock(),
            uid=self._uid,
            self_pid=self._self_pid,
            self_cgroup=self._self_cgroup,
            unified_cgroup2=self._unified_cgroup2,
            config=self.config,
            health=self._health,
            stats=self._stats,
            recent=self._recent,
            audit=self._audit,
            log=self._log,
            logger=self.logger,
        )
        self._scan_service = ScanService(
            clock=lambda: self._clock(),
            procfs=self.procfs,
            freezer=self._freezer,
            rules=self._rule_sources,
            session=self._session_service,
            freeze_service=self._freeze_service,
            health=self._health,
            config=self.config,
            stats=self._stats,
            audit=self._audit,
            log=self._log,
            logger=self.logger,
            dry_run=lambda: self._dry_run,
            freeze_enabled=lambda: self._freeze_enabled,
        )
        self._scan_requester = ScanRequester(self.scan_once, self._clock)
        self._plugin_gateway = PluginGateway(
            self,
            self._rule_sources,
            self._session_service,
            self._state_service,
            self._scan_requester,
        )
        self._event_bus = EventBus(
            lambda: self._clock(),
            addons=lambda: self._plugin_runtime.subscribers(),
            capabilities=self._plugins.capabilities_for,
            on_failure=lambda plugin_id, where, exc: self._plugin_runtime.fail(
                plugin_id, where, exc
            ),
            webhooks=lambda: [r for r in self._plugins.list() if r.webhook_url],
            log=self._log,
        )
        self._plugin_runtime = PluginRuntime(
            registry=self._plugins,
            gateway=self._plugin_gateway,
            logger=self.logger,
            clock=lambda: self._clock(),
            record_event=self._event_bus.record,
            emit=self._event_bus.emit,
            clear_source=self.clear_source,
            set_state=lambda active, source: self.set_state(active, source=source),
        )
        self._status_service = StatusService(
            clock=lambda: self._clock(),
            rules=self._rule_sources,
            session=self._session_service,
            freeze=self._freeze_service,
            scan=self._scan_service,
            registry=self._plugins,
            health=self._health,
            stats=self._stats,
            recent=self._recent,
            config=self.config,
            procfs=self.procfs,
            uid=self._uid,
            self_pid=self._self_pid,
            self_cgroup=self._self_cgroup,
            unified_cgroup2=self._unified_cgroup2,
            dry_run=lambda: self._dry_run,
            freeze_enabled=lambda: self._freeze_enabled,
            event_seq=lambda: self._event_bus.seq,
            addons=self.addons,
            version=__version__,
        )
        self._config_service = ConfigService(
            self.config,
            self._rule_sources,
            dry_run=lambda: self._dry_run,
            set_dry_run=self.set_dry_run,
            set_freeze_enabled=self._set_freeze_enabled,
            thaw_all=self.thaw_all,
            apply_rules=self.apply_rules,
            sources=self.sources,
            set_url_patterns=self.set_url_patterns,
            config_dict=self.config_dict,
            emit=self._emit,
            set_http_token=self._set_http_token,
        )

    # -- logging -----------------------------------------------------------

    def _log(self, level: str, message: str) -> None:
        if self.logger is None:
            return
        getattr(self.logger, level, self.logger.info)(message)

    # -- session state -----------------------------------------------------

    def manual_session_active(self) -> bool:
        """Whether Focused itself is running a session (breaks included)."""
        return self._session_service.manual_session_active()

    def session(self) -> Optional[Dict[str, Any]]:
        """The running manual session, as a copy."""
        return self._session_service.session()

    def live_claims(self) -> Dict[str, float]:
        """Sources that currently claim a running session, with their expiry."""
        return self._session_service.live()

    def claims(self) -> Dict[str, Dict[str, float]]:
        return self._session_service.claims()

    def delegated_active(self) -> bool:
        """Whether any frontend still claims a running session."""
        return self._session_service.delegated_active()

    def active(self) -> bool:
        """Whether a focus session is running right now.

        Own session, or somebody else's live claim: whichever comes first.  A
        pomodoro break is *not* active -- a break means the applications come back.
        """
        return self._session_service.active()

    def delegated(self) -> bool:
        """Whether any frontend owns the session (even if its lease just lapsed)."""
        return self._session_service.delegated()

    def _nearest_claim_deadline(self) -> float:
        return self._session_service.nearest_deadline()

    def _nearest_claim_ttl(self) -> float:
        return self._session_service.nearest_ttl()

    def delegated_source(self) -> str:
        return self._session_service.describe()

    def pause_until(self) -> float:
        return self._session_service.pause_until()

    def session_since(self) -> float:
        return self._session_service.session_since()

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
        now = self._clock()
        if active:
            changed = self._session_service.claim(source, now, ttl_seconds or 0.0)
        else:
            changed = self._session_service.withdraw(source)
        now_active = self.active()
        if changed:
            self._audit(
                {
                    "kind": "state",
                    "active": bool(active),
                    "source": source,
                    "ttl_seconds": ttl_seconds or 0,
                }
            )
            log_event(
                self.logger,
                "info",
                "session.claim" if active else "session.withdraw",
                source=source,
                lease=int(ttl_seconds or 0),
                active=now_active,
            )
        if source != "builtin":
            # A renewal is not a transition: update the UI's last-seen hint, but
            # do not rewrite plugins.json every two seconds.
            self._plugins.note_session(
                source, float(ttl_seconds or 0.0), persist=changed
            )
        return now_active

    def start_session(
        self,
        minutes: Optional[float] = None,
        mode: Optional[str] = None,
        work_minutes: Optional[float] = None,
        break_minutes: Optional[float] = None,
        cycles: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self._session_service.start_session(
            minutes=minutes,
            mode=mode,
            work_minutes=work_minutes,
            break_minutes=break_minutes,
            cycles=cycles,
        )

    def extend_session(self, minutes: float) -> Optional[Dict[str, Any]]:
        return self._session_service.extend_session(minutes)

    def skip_break(self) -> Optional[Dict[str, Any]]:
        return self._session_service.skip_break()

    def stop_session(self) -> bool:
        return self._session_service.stop_session()

    def _advance_session(self, now: Optional[float] = None) -> Optional[str]:
        return self._session_service.advance(now)

    def _session_payload_fields(self) -> Dict[str, Any]:
        """The session as flat scalars, for the Unity frontend."""
        return self._status_service.session_payload_fields()

    def session_state(self) -> Dict[str, Any]:
        """A flat description of the manual session, for the UI and the API."""
        return self._session_service.session_state()

    def paused(self) -> bool:
        return self._session_service.paused()

    def pause(self, seconds: float) -> float:
        """Let the user off for a while without ending the session."""
        return self._session_service.pause(seconds)

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
        return self._rule_sources.merged()

    def _rules_changed(self, source, entry, merged: RuleSet) -> None:
        """The rule service changed the merged set; log, audit and thaw stale."""
        if entry is None:
            self._thaw_stale()
            return
        count = len(entry.get("names") or ()) + len(entry.get("cmdline_substrings") or ()) + len(entry.get("pids") or ())
        self._audit(
            {
                "kind": "rules",
                "source": source,
                "names": len(entry.get("names") or ()),
                "cmdline": len(entry.get("cmdline_substrings") or ()),
                "pids": len(entry.get("pids") or ()),
                "merged_names": len(merged.names),
            }
        )
        log_event(
            self.logger,
            "info",
            "rules.applied",
            source=source,
            names=len(entry.get("names") or ()),
            cmdline=len(entry.get("cmdline_substrings") or ()),
            pids=len(entry.get("pids") or ()),
            merged=len(merged.names),
        )
        if source != "config":
            self._plugins.note_rules(source, count)
        self._thaw_stale()

    def apply_rules(self, payload: Dict[str, Any], source: str = "builtin") -> RuleSet:
        """Compatibility delegate: the rule service owns validation and state."""
        return self._rule_sources.replace(source, payload)

    def clear_source(self, source: str) -> None:
        self._rule_sources.clear(source)

    def sources(self) -> Dict[str, Dict[str, Any]]:
        return self._rule_sources.sources()

    def set_url_patterns(self, patterns, source: str = "config") -> List[str]:
        return self._rule_sources.set_url_patterns(patterns, source=source)

    def url_patterns(self) -> List[str]:
        return self._rule_sources.url_patterns()

    # -- freeze/thaw (delegated) ------------------------------------------

    def frozen(self) -> List[Frozen]:
        return self._freeze_service.frozen()

    def thaw_all(self, reason: str = "manual") -> int:
        return self._freeze_service.thaw_all(reason)

    def recover_frozen(self) -> int:
        return self._freeze_service.recover_frozen()

    def close(self) -> int:
        return self._freeze_service.close()

    def _thaw_stale(self) -> int:
        return self._freeze_service.thaw_stale()

    def _freezable_pids(self, pids) -> List[int]:
        return self._freeze_service.freezable_pids(pids)

    # -- the scan (delegated) ---------------------------------------------

    #: How many consecutive failed scans before the status says "degraded".
    DEGRADED_AFTER = 3

    def scan_once(self) -> int:
        """Run exactly one scan.  Returns how many targets were suspended."""
        return self._scan_service.scan_once()

    def scan_ms(self) -> Dict[str, float]:
        return self._scan_service.scan_ms()

    def run_forever(self, stop_event: threading.Event) -> None:
        self._scan_service.run_forever(stop_event)

    # -- events (delegated) ------------------------------------------------

    def _record_event(self, kind: str, **fields: Any) -> Dict[str, Any]:
        return self._event_bus.record(kind, **fields)

    def _emit(self, kind: str, **fields: Any) -> Dict[str, Any]:
        return self._event_bus.emit(kind, **fields)

    def events_since(self, since: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        return self._event_bus.since(since, limit)

    # -- plugins -----------------------------------------------------------

    @property
    def plugins(self) -> PluginRegistry:
        return self._plugins

    @property
    def rule_sources(self) -> RuleSourceService:
        return self._rule_sources

    @property
    def plugin_gateway(self) -> PluginGateway:
        return self._plugin_gateway

    def record_plugin_action(self, plugin_id: str, action: str, **fields: Any) -> None:
        """Audit one plugin-originated action (rules/scan/etc.)."""
        self._audit({"kind": "plugin." + action, "source": plugin_id, **fields})

    def health(self) -> Dict[str, Any]:
        return self._health.to_dict()

    def frozen_count(self) -> int:
        return self._freezer.frozen_count()

    def frozen_names(self) -> List[str]:
        return [entry.name for entry in self._freezer.entries()]

    def log_plugin(self, plugin_id: str, level: str, message: str) -> None:
        log_event(self.logger, level, "plugin.log", source=plugin_id, message=message)

    def bind_addon(self, plugin_id: str, module: Any, api: Any) -> None:
        self._plugin_runtime.bind(plugin_id, module, api)

    def addons(self) -> List[str]:
        return self._plugin_runtime.addons()

    def load_plugins(self, directory: str) -> List[Dict[str, str]]:
        return self._plugin_runtime.load(directory)

    def clear_plugin(self, plugin_id: str) -> None:
        self._plugin_runtime.clear(plugin_id)

    def set_plugin_status(self, plugin_id: str, enabled: bool) -> Optional[PluginRecord]:
        return self._plugin_runtime.set_status(plugin_id, enabled)

    def remove_plugin(self, plugin_id: str) -> bool:
        return self._plugin_runtime.remove(plugin_id)

    # -- configuration -----------------------------------------------------

    def _set_freeze_enabled(self, enabled: bool) -> None:
        self._freeze_enabled = bool(enabled)
        self._health.freeze_enabled = self._freeze_enabled

    def _set_http_token(self, token: str) -> None:
        setter = getattr(self, "_server_token_setter", None)
        if setter is not None:
            setter(token)

    def update_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        return self._config_service.update(patch)

    def config_dict(self) -> Dict[str, Any]:
        return self._status_service.config_dict()

    def visible_processes(self, limit: int = 400) -> List[Dict[str, Any]]:
        return self._status_service.visible_processes(limit)

    def visible_process_arrays(self, limit: int = 400) -> Dict[str, Any]:
        return self._status_service.visible_process_arrays(limit)

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
        return self._status_service.focus_state()

    def frozen_list(self) -> List[Dict[str, Any]]:
        return self._status_service.frozen_list()

    def status(self) -> Dict[str, Any]:
        return self._status_service.status()

    def plugin_payloads(self) -> List[Dict[str, Any]]:
        return self._status_service.plugin_payloads()
