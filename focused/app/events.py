"""The event stream: one ring buffer, one dispatcher, one webhook path.

Every audit record is also an event, so a plugin cannot be told about something
the audit never saw.  Delivery is best effort and must never slow a scan down;
plugin exceptions are handed to ``PluginRuntime.fail`` instead of escaping.
"""

from __future__ import annotations

import collections
import json
import queue
import threading
import urllib.request
from typing import Any, Callable, Deque, Dict, Iterable, List, Tuple

from ..plugin_api import CAP_EVENTS_READ


class EventBus:
    def __init__(
        self,
        clock: Callable[[], float],
        *,
        addons: Callable[[], Iterable[Tuple[str, Any, Any]]],
        capabilities: Callable[[str], frozenset],
        on_failure: Callable[[str, str, BaseException], None],
        webhooks: Callable[[], Iterable[Any]],
        log: Callable[[str, str], None],
    ) -> None:
        self._clock = clock
        self._addons = addons
        self._capabilities = capabilities
        self._on_failure = on_failure
        self._webhooks = webhooks
        self._log = log
        self._lock = threading.RLock()
        self._events: Deque[Dict[str, Any]] = collections.deque(maxlen=200)
        self._seq = 0
        # Webhooks are best effort and must not create one thread per event.
        # A bounded queue + one daemon worker keeps the scan path non-blocking.
        self._webhook_queue: "queue.Queue" = queue.Queue(maxsize=100)
        threading.Thread(
            target=self._drain_webhooks, name="focused-webhooks", daemon=True
        ).start()

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    def record(self, kind: str, **fields: Any) -> Dict[str, Any]:
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

    def emit(self, kind: str, **fields: Any) -> Dict[str, Any]:
        event = self.record(kind, **fields)
        self.dispatch(event)
        return event

    def since(self, since: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            events = list(self._events)
        out = [event for event in events if event["seq"] > int(since or 0)]
        return out[-max(1, int(limit or 100)) :]

    def dispatch(self, event: Dict[str, Any]) -> None:
        for plugin_id, module, api in list(self._addons()):
            if CAP_EVENTS_READ not in self._capabilities(plugin_id):
                continue
            callback = getattr(module, "on_event", None)
            if callback is None:
                continue
            try:
                callback(api, event)
            except Exception as exc:  # a plugin must not break the engine
                self._on_failure(plugin_id, "on_event", exc)
        for record in self._webhooks():
            if not record.webhook_url:
                continue
            self._post_webhook(record, event)

    def _post_webhook(self, record: Any, event: Dict[str, Any]) -> None:
        try:
            self._webhook_queue.put_nowait((record, event))
        except queue.Full:
            self._log(
                "warning",
                "webhook queue is full; dropping event for %s" % record.id,
            )

    def _drain_webhooks(self) -> None:
        while True:
            record, event = self._webhook_queue.get()
            try:
                self._deliver_webhook(record, event)
            finally:
                self._webhook_queue.task_done()

    def _deliver_webhook(self, record: Any, event: Dict[str, Any]) -> None:
        request = urllib.request.Request(
            record.webhook_url,
            data=json.dumps(event, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Focused-Token": record.token,
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=2.0).close()
        except Exception as exc:
            self._log(
                "warning",
                "webhook for %s failed: %s" % (record.id, type(exc).__name__),
            )
