"""SessionService: the manual state machine now owns its own state."""

import unittest
from types import SimpleNamespace

from focused.app.session import SessionService


class FakeConfig:
    mode = "countdown"
    default_minutes = 5.0
    pomodoro_work_minutes = 1.0
    pomodoro_break_minutes = 1.0
    pomodoro_cycles = 1


class SessionServiceTests(unittest.TestCase):
    def make(self):
        now = [100.0]
        audits = []
        logs = []
        stats = {}
        service = SessionService(
            lambda: now[0],
            session_config=lambda: FakeConfig(),
            stats=stats,
            audit=audits.append,
            log=lambda level, message: logs.append((level, message)),
        )
        return service, now, audits, logs, stats

    def test_countdown_starts_and_completes(self):
        service, now, audits, logs, stats = self.make()

        snapshot = service.start_session(minutes=5)

        self.assertEqual("countdown", snapshot["mode"])
        self.assertTrue(service.active())
        self.assertTrue(service.manual_session_active())
        self.assertEqual(1, stats["sessions"])
        self.assertEqual("started", service.take_transition())

        now[0] += 301
        self.assertFalse(service.active())
        self.assertEqual("ended", service.take_transition())
        self.assertFalse(service.session_state()["active"])
        self.assertTrue(any(item["kind"] == "session-ended" for item in audits))
        self.assertTrue(any("session finished" in message for _level, message in logs))

    def test_pomodoro_break_is_not_active_and_then_work_resumes(self):
        service, now, _audits, _logs, _stats = self.make()
        service.start_session(
            mode="pomodoro", work_minutes=1, break_minutes=1, cycles=2
        )
        service.take_transition()

        now[0] += 61
        self.assertEqual("break", service.advance())
        self.assertFalse(service.active())
        self.assertEqual("break", service.session()["phase"])

        now[0] += 61
        self.assertEqual("work", service.advance())
        self.assertTrue(service.active())

        now[0] += 61
        self.assertEqual("ended", service.advance())
        self.assertFalse(service.manual_session_active())

    def test_skip_break_returns_to_work(self):
        service, now, _audits, _logs, _stats = self.make()
        service.start_session(
            mode="pomodoro", work_minutes=1, break_minutes=5, cycles=2
        )
        now[0] += 61
        self.assertEqual("break", service.advance())

        snapshot = service.skip_break()

        self.assertEqual("work", snapshot["phase"])
        self.assertEqual("work", service.take_transition())
        self.assertTrue(service.active())

    def test_pause_resumes_and_shifts_the_deadline(self):
        service, now, _audits, _logs, _stats = self.make()
        before = service.start_session(minutes=10)["until"]

        until = service.pause(60)

        self.assertEqual(now[0] + 60, until)
        self.assertTrue(service.paused())
        self.assertEqual(before + 60, service.session()["until"])

        now[0] += 61
        self.assertFalse(service.paused())

    def test_note_active_reports_only_the_rising_edge(self):
        service, now, _audits, _logs, _stats = self.make()

        self.assertTrue(service.note_active(True))
        self.assertFalse(service.note_active(True))
        self.assertEqual(now[0], service.session_since())
        self.assertFalse(service.note_active(False))

    def test_an_unknown_mode_is_refused(self):
        service, _now, _audits, _logs, _stats = self.make()

        with self.assertRaises(ValueError):
            service.start_session(mode="whenever")


if __name__ == "__main__":
    unittest.main()
