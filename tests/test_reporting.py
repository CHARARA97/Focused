import unittest

from focused.app.health import Health
from focused.app.reporting import Reporting
from focused.domain.reasons import ActionEvent, EventKind, KillResult


class FakeAudit:
    def __init__(self):
        self.records = []

    def write(self, payload):
        self.records.append(payload)


class FakeEmitter:
    def __init__(self):
        self.events = []

    def __call__(self, kind, **fields):
        self.events.append((kind, fields))


def reporting():
    audit, emit, health, stats = FakeAudit(), FakeEmitter(), Health(), {}
    instance = Reporting(
        audit_log=lambda: audit,
        emit=emit,
        logger=None,
        health=health,
        stats=stats,
    )
    return instance, audit, emit, health, stats


class ReportingTests(unittest.TestCase):
    def test_an_action_event_reaches_the_audit_log_and_the_bus_once(self):
        instance, audit, emit, _, _ = reporting()
        instance.write(
            ActionEvent(
                kind=EventKind.KILL,
                at=1.0,
                scan=3,
                name="firefox",
                pids=(10, 11),
                tree=2,
                count=2,
                duration_s=0.0,
                mode="kill",
            )
        )

        self.assertEqual([EventKind.KILL.value], [payload["kind"] for payload in audit.records])
        self.assertEqual(1, len(emit.events))

    def test_a_plain_record_does_not_need_an_event_kind(self):
        instance, audit, emit, _, _ = reporting()
        instance.record({"kind": "lease-expired", "source": "plugin:a"})

        self.assertEqual("lease-expired", audit.records[0]["kind"])
        self.assertEqual("lease-expired", emit.events[0][0])

    def test_bump_counts_into_the_shared_stats(self):
        instance, _, _, _, stats = reporting()
        instance.bump("scans")
        instance.bump("scans")

        self.assertEqual(2, stats["scans"])

    def test_a_kill_failure_is_recorded_once_in_health_and_stats(self):
        instance, _, _, health, stats = reporting()
        instance.kill_failure("firefox")

        self.assertEqual(1, health.kill_failures)
        self.assertEqual(1, stats["kill_failed"])

    def test_a_scan_failure_degrades_health_and_is_counted(self):
        instance, _, _, health, _ = reporting()
        instance.scan_failed(RuntimeError("boom"))

        self.assertEqual(1, health.scan_failures)
        self.assertEqual("RuntimeError: boom", health.last_scan_error)


if __name__ == "__main__":
    unittest.main()
