import unittest

from focused.domain.session_timer import (
    DefaultSessionConfig,
    SessionState,
    advance,
    extend,
    shift,
    skip_break,
    start,
    stop,
    view,
)

CONFIG = DefaultSessionConfig()


def countdown(minutes=25.0, now=100.0):
    return start(None, CONFIG, now, minutes=minutes).state


class StartTests(unittest.TestCase):
    def test_countdown_sets_a_deadline(self):
        result = start(None, CONFIG, 100.0, minutes=5)

        self.assertEqual("started", result.transition)
        self.assertEqual("countdown", result.state.mode)
        self.assertEqual(100.0, result.state.started_at)
        self.assertEqual(100.0 + 300.0, result.state.until)

    def test_a_start_is_audited_and_counted(self):
        result = start(None, CONFIG, 100.0, minutes=5)

        self.assertEqual("session-started", result.audit[0]["kind"])
        self.assertTrue(result.counts_session)
        self.assertEqual(1, len(result.logs))

    def test_pomodoro_starts_in_work_with_one_cycle(self):
        result = start(None, CONFIG, 100.0, mode="pomodoro", work_minutes=2, break_minutes=1, cycles=3)

        self.assertEqual("work", result.state.phase)
        self.assertEqual(1, result.state.cycle)
        self.assertEqual(3, result.state.cycles)
        self.assertEqual(100.0 + 120.0, result.state.phase_until)

    def test_a_stopwatch_has_no_deadline(self):
        result = start(None, CONFIG, 100.0, mode="stopwatch")

        self.assertEqual(0.0, result.state.until)
        self.assertEqual(0.0, result.state.phase_until)

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            start(None, CONFIG, 100.0, mode="whenever")

    def test_asking_for_more_minutes_extends_a_running_countdown(self):
        running = countdown(minutes=5)
        result = start(running, CONFIG, 200.0, minutes=3)

        self.assertIsNone(result.transition)
        self.assertEqual(running.until + 180.0, result.state.until)
        self.assertEqual("session-extended", result.audit[0]["kind"])

    def test_a_pomodoro_is_restarted_rather_than_extended_by_start(self):
        running = start(None, CONFIG, 100.0, mode="pomodoro").state
        result = start(running, CONFIG, 200.0, mode="pomodoro", minutes=3)

        self.assertEqual("pomodoro", result.state.mode)
        self.assertEqual(200.0, result.state.started_at)


class ExtendTests(unittest.TestCase):
    def test_a_countdown_deadline_moves(self):
        running = countdown(minutes=5)
        result = extend(running, 2)

        self.assertEqual(running.until + 120.0, result.state.until)
        self.assertEqual("session-extended", result.audit[0]["kind"])

    def test_a_pomodoro_phase_moves(self):
        running = start(None, CONFIG, 100.0, mode="pomodoro").state
        result = extend(running, 2)

        self.assertEqual(running.phase_until + 120.0, result.state.phase_until)

    def test_a_stopwatch_is_unchanged(self):
        running = start(None, CONFIG, 100.0, mode="stopwatch").state
        self.assertIs(running, extend(running, 2).state)

    def test_nothing_running_stays_stopped(self):
        self.assertIsNone(extend(None, 2).state)


class SkipBreakTests(unittest.TestCase):
    def _on_break(self):
        running = start(None, CONFIG, 100.0, mode="pomodoro", work_minutes=1, break_minutes=1).state
        return advance(running, 100.0 + 60.0).state

    def test_a_break_becomes_work_again(self):
        on_break = self._on_break()
        self.assertEqual("break", on_break.phase)

        result = skip_break(on_break, 200.0)

        self.assertEqual("work", result.state.phase)
        self.assertEqual(200.0 + 60.0, result.state.phase_until)
        self.assertEqual("work", result.transition)

    def test_a_work_phase_is_unchanged(self):
        running = countdown()
        self.assertIs(running, skip_break(running, 200.0).state)

    def test_nothing_running_stays_stopped(self):
        self.assertIsNone(skip_break(None, 200.0).state)


class StopTests(unittest.TestCase):
    def test_stop_ends_the_session(self):
        result = stop()
        self.assertIsNone(result.state)
        self.assertEqual("ended", result.transition)


class AdvanceTests(unittest.TestCase):
    def test_a_countdown_ends_at_its_deadline(self):
        running = countdown(minutes=1)
        result = advance(running, running.until)

        self.assertIsNone(result.state)
        self.assertEqual("ended", result.transition)

    def test_a_countdown_before_its_deadline_is_untouched(self):
        running = countdown(minutes=1)
        result = advance(running, running.until - 1.0)

        self.assertIs(running, result.state)
        self.assertIsNone(result.transition)

    def test_a_stopwatch_never_ends(self):
        running = start(None, CONFIG, 100.0, mode="stopwatch").state
        self.assertIs(running, advance(running, 10_000.0).state)

    def test_work_becomes_break(self):
        running = start(None, CONFIG, 100.0, mode="pomodoro", work_minutes=1, break_minutes=1).state
        result = advance(running, 160.0)

        self.assertEqual("break", result.state.phase)
        self.assertEqual("break", result.transition)

    def test_break_becomes_the_next_work_round(self):
        running = start(None, CONFIG, 100.0, mode="pomodoro", work_minutes=1, break_minutes=1, cycles=3).state
        on_break = advance(running, 160.0).state
        result = advance(on_break, 220.0)

        self.assertEqual("work", result.state.phase)
        self.assertEqual(2, result.state.cycle)
        self.assertEqual("work", result.transition)

    def test_the_last_work_round_ends_the_session(self):
        running = start(None, CONFIG, 100.0, mode="pomodoro", work_minutes=1, break_minutes=1, cycles=1).state
        result = advance(running, 160.0)

        self.assertIsNone(result.state)
        self.assertEqual("ended", result.transition)

    def test_a_late_scan_starts_the_next_phase_from_now(self):
        running = start(None, CONFIG, 100.0, mode="pomodoro", work_minutes=1, break_minutes=1, cycles=3).state
        result = advance(running, 100.0 + 300.0)

        self.assertEqual("break", result.state.phase)
        self.assertEqual(400.0 + 60.0, result.state.phase_until)

    def test_a_degenerate_phase_cannot_loop_forever(self):
        stuck = SessionState(
            mode="pomodoro",
            started_at=0.0,
            phase="work",
            phase_until=100.0,
            work_minutes=0.0,
            break_minutes=0.0,
            cycle=1,
            cycles=0,
        )
        result = advance(stuck, 100.0)

        self.assertIsNotNone(result.state)
        self.assertEqual(100.0, result.state.phase_until)

    def test_nothing_running_stays_stopped(self):
        self.assertIsNone(advance(None, 100.0).state)


class ShiftTests(unittest.TestCase):
    def test_a_countdown_moves_with_the_pause(self):
        running = countdown(minutes=5)
        self.assertEqual(running.until + 30.0, shift(running, 30.0).until)

    def test_a_pomodoro_phase_moves_with_the_pause(self):
        running = start(None, CONFIG, 100.0, mode="pomodoro").state
        self.assertEqual(running.phase_until + 30.0, shift(running, 30.0).phase_until)

    def test_a_zero_pause_changes_nothing(self):
        running = countdown()
        self.assertIs(running, shift(running, 0))

    def test_nothing_running_stays_stopped(self):
        self.assertIsNone(shift(None, 30.0))


class ViewTests(unittest.TestCase):
    def test_an_empty_view_is_all_zeroes(self):
        payload = view(None, 100.0)
        self.assertFalse(payload["active"])
        self.assertEqual(0.0, payload["remaining"])

    def test_a_countdown_view_reports_remaining_time(self):
        running = countdown(minutes=5, now=100.0)
        payload = view(running, 100.0 + 60.0)

        self.assertTrue(payload["active"])
        self.assertEqual("countdown", payload["mode"])
        self.assertEqual(240.0, payload["remaining"])
        self.assertEqual(60.0, payload["elapsed"])

    def test_a_pomodoro_break_is_not_active(self):
        running = start(None, CONFIG, 100.0, mode="pomodoro", work_minutes=1, break_minutes=1).state
        on_break = advance(running, 160.0).state

        self.assertFalse(view(on_break, 160.0)["active"])
        self.assertEqual("break", view(on_break, 160.0)["phase"])


class StateTests(unittest.TestCase):
    def test_working_depends_on_the_phase_only_for_pomodoros(self):
        self.assertTrue(SessionState(mode="countdown", started_at=0.0).is_working)
        self.assertFalse(
            SessionState(mode="pomodoro", started_at=0.0, phase="break").is_working
        )
        self.assertTrue(SessionState(mode="stopwatch", started_at=0.0).is_working)


if __name__ == "__main__":
    unittest.main()
