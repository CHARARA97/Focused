from __future__ import annotations

import collections
import os
import threading
import time
from typing import Any, Deque, Dict, List, Optional

from .core.killer import Killer, OsSignalSender
from .core.procfs import ProcFS
from .core.rules import RuleSet
from .app.plugin_gateway import PluginGateway
from .app.plugin_view import PluginView
from .app.process_view import ProcessView
from .domain.identity import Identity
from .app.rules import RuleSourceService
from .app.config import ConfigService
from .app.config_view import ConfigView
from .app.events import EventBus
from .app.enforcer import Enforcer
from .app.reporting import Reporting
from .app.runtime_info import RuntimeInfo
from .app.timing import Timeline
from .app.plugin_runtime import PluginRuntime
from .app.scan import ScanRequester, ScanResult, ScanService
from .app.status import StatusService
from .app.session import SessionService
from .app.state import StateService
from .diagnostics import log_event
from .domain.reasons import ActionEvent, EventKind
from .app.history import DenyBook, EpisodeBook
from .app.health import Health


EPISODE_WINDOW = 60.0


from . import __version__
from .config import Config
from .plugins import PluginRecord, PluginRegistry

#: Sessions and pauses are reported to the browser extension, which ticks every
#: few seconds; rounding keeps the JSON readable.
def _now() -> float:
    return time.time()


class FocusedEngine:

    def __init__(
        self,
        config: Config,
        procfs: Optional[ProcFS] = None,
        killer: Optional[Killer] = None,
        logger=None,
        uid: Optional[int] = None,
        self_pid: Optional[int] = None,
        clock=_now,
        signal_sender: Any = None,
    ) -> None:
        self.config = config
        self.procfs = procfs or ProcFS()
        self.logger = logger
        self._clock = clock

        self._uid = os.getuid() if uid is None else uid
        self._self_pid = os.getpid() if self_pid is None else self_pid
        self._lock = threading.RLock()

        self._killer = killer or Killer(
            sender=signal_sender or OsSignalSender(),
            reader=self.procfs,
            logger=logger,
        )
        self._history = EpisodeBook(window=EPISODE_WINDOW)
        self._denies = DenyBook(window=EPISODE_WINDOW)
        self._last_scan = ScanResult()
        self._stats: Dict[str, Any] = {
            "scans": 0,
            "matched": 0,
            "protected": 0,
            "killed": 0,
            "would_kill": 0,
            "denied": 0,
            "kill_failed": 0,
            "sessions": 0,
        }
        self._health = Health(
            degraded_after=self.DEGRADED_AFTER,
            enforcement_enabled=bool(config.enforcement.enabled),
        )

        self._reporting = Reporting(
            audit_log=lambda: getattr(self, "audit", None),
            emit=self.emit,
            logger=logger,
            health=self._health,
            stats=self._stats,
        )

        self._plugins = PluginRegistry(
            getattr(config, "registry_path", "")
            or os.path.join(Config.data_dir(), "plugins.json"),
            logger=logger,
            clock=clock,
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
            audit=self._reporting.record,
            log=self._log,
        )
        self._enforcer = Enforcer(
            killer=self._killer,
            reporting=self._reporting,
            clock=lambda: self._clock(),
            history=self._history,
            denies=self._denies,
        )
        self._scan_service = ScanService(
            timeline=Timeline(wall=lambda: self._clock(), monotonic=time.monotonic),
            procfs=self.procfs,
            rules=self._rule_sources,
            session=self._session_service,
            enforcer=self._enforcer,
            config=self.config,
            reporting=self._reporting,
            identity=Identity(pid=self._self_pid, ppid=os.getppid(), uid=self._uid),
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
        self._config_view = ConfigView(
            self.config, self._rule_sources, self._session_service
        )
        self._plugin_view = PluginView(
            registry=self._plugins,
            session=self._session_service,
            clock=lambda: self._clock(),
        )
        self._process_view = ProcessView(
            procfs=self.procfs,
            rules=self._rule_sources,
            uid=self._uid,
            self_pid=self._self_pid,
        )
        self._status_service = StatusService(
            rules=self._rule_sources,
            session=self._session_service,
            enforcer=self._enforcer,
            scan=self._scan_service,
            health=self._health,
            stats=self._stats,
            config=self.config,
            runtime=RuntimeInfo(
                pid=self._self_pid,
                version=__version__,
                addons=self.addons,
                event_seq=lambda: self._event_bus.seq,
            ),
            processes=self._process_view,
            plugins=self._plugin_view,
        )
        self._config_service = ConfigService(
            self.config,
            self._rule_sources,
            self,
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
            self._reporting.record(
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

    # -- rules -------------------------------------------------------------

    def rules(self) -> RuleSet:
        return self._rule_sources.merged()

    def _rules_changed(self, source, entry, merged: RuleSet) -> None:
        """The rule service changed the merged set; log, audit and thaw stale."""
        if entry is None:
            return
        count = len(entry.get("names") or ()) + len(entry.get("cmdline_substrings") or ()) + len(entry.get("pids") or ())
        self._reporting.record(
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

    # -- enforcement (delegated) -------------------------------------------

    def killed_total(self) -> int:
        return int(self._stats.get("killed") or 0)

    # -- the scan (delegated) ---------------------------------------------

    #: How many consecutive failed scans before the status says "degraded".
    DEGRADED_AFTER = 3

    def scan_once(self):
        self._last_scan = self._scan_service.scan_once()
        return self._last_scan

    def scan_report(self) -> Dict[str, Any]:
        return self._last_scan.to_wire()

    def scan_ms(self) -> Dict[str, float]:
        return self._scan_service.scan_ms()

    def run_forever(self, stop_event: threading.Event) -> None:
        self._scan_service.run_forever(stop_event)

    # -- events (delegated) ------------------------------------------------

    def _record_event(self, kind: str, **fields: Any) -> Dict[str, Any]:
        return self._event_bus.record(kind, **fields)

    def emit(self, kind: str, **fields: Any) -> Dict[str, Any]:
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
        self._reporting.record({"kind": "plugin." + action, "source": plugin_id, **fields})

    def health(self) -> Dict[str, Any]:
        return self._health.to_dict()


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

    def set_enforcement_enabled(self, enabled: bool) -> None:
        self._health.enforcement_enabled = bool(enabled)

    def set_http_token(self, token: str) -> None:
        setter = getattr(self, "_server_token_setter", None)
        if setter is not None:
            setter(token)

    def update_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        return self._config_service.update(patch)

    def config_dict(self) -> Dict[str, Any]:
        return self._config_view.to_dict()

    def visible_processes(self, limit: int = 400) -> List[Dict[str, Any]]:
        return self._status_service.visible_processes(limit)

    def visible_process_arrays(self, limit: int = 400) -> Dict[str, Any]:
        return self._status_service.visible_process_arrays(limit)

    # -- introspection -----------------------------------------------------

    def focus_state(self) -> Dict[str, Any]:
        return self._status_service.focus_state()

    def recent_list(self) -> List[Dict[str, Any]]:
        return self._status_service.recent_list()

    def status(self) -> Dict[str, Any]:
        return self._status_service.status()

    def plugin_payloads(self) -> List[Dict[str, Any]]:
        return self._plugin_view.payloads()
