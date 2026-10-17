"""EventBus: one ring, capability-scoped delivery, failures isolated."""

import unittest

from focused.app.events import EventBus
from focused.plugin_api import CAP_EVENTS_READ


class FakeAddon:
    def __init__(self):
        self.seen = []

    def on_event(self, api, event):
        self.seen.append(event)


class FailingAddon:
    def on_event(self, api, event):
        raise RuntimeError("boom")


class EventBusTests(unittest.TestCase):
    def make(self, addons, capabilities, failures=None):
        failures = failures if failures is not None else []
        return EventBus(
            lambda: 100.0,
            addons=lambda: addons,
            capabilities=capabilities,
            on_failure=lambda pid, where, exc: failures.append((pid, where, str(exc))),
            webhooks=lambda: [],
            log=lambda level, message: None,
        )

    def test_record_is_buffered_and_emit_dispatches_to_capable_addons(self):
        capable = FakeAddon()
        silent = FakeAddon()
        bus = self.make(
            [("capable", capable, object()), ("silent", silent, object())],
            lambda pid: frozenset({CAP_EVENTS_READ}) if pid == "capable" else frozenset(),
        )

        recorded = bus.record("freeze", pid=42, name="firefox")
        self.assertEqual(1, recorded["seq"])
        self.assertEqual([], capable.seen)
        self.assertEqual([recorded], bus.since(0))

        emitted = bus.emit("freeze", pid=42, name="firefox")
        self.assertEqual([emitted], capable.seen)
        self.assertEqual([], silent.seen)
        self.assertEqual(2, bus.seq)

    def test_since_filters_and_limits(self):
        bus = self.make([], lambda pid: frozenset())
        for index in range(3):
            bus.record("x", detail=str(index))

        self.assertEqual([3], [event["seq"] for event in bus.since(2)])
        self.assertEqual(1, len(bus.since(0, 1)))

    def test_a_failing_addon_is_reported_and_does_not_break_dispatch(self):
        failures = []
        bus = self.make(
            [("bad", FailingAddon(), object())],
            lambda pid: frozenset({CAP_EVENTS_READ}),
            failures,
        )

        bus.emit("x")

        self.assertEqual([("bad", "on_event", "boom")], failures)


if __name__ == "__main__":
    unittest.main()
