"""Structured log lines: stable event name, then key=value fields."""

import unittest

from focused.diagnostics import log_event


class _Logger:
    def __init__(self):
        self.lines = []

    def info(self, message):
        self.lines.append(("info", message))

    def warning(self, message):
        self.lines.append(("warning", message))

    def error(self, message):
        self.lines.append(("error", message))


class LogEventTests(unittest.TestCase):
    def test_fields_are_sorted_and_empty_values_are_omitted(self):
        logger = _Logger()

        log_event(logger, "warning", "freeze.refuse", pid=7, reason="shared-unit", rule=None)

        self.assertEqual(
            [("warning", "event=freeze.refuse pid=7 reason=shared-unit")],
            logger.lines,
        )

    def test_values_with_spaces_are_quoted(self):
        logger = _Logger()

        log_event(logger, "error", "scan.fail", error="cannot read /proc", failures=3)

        self.assertEqual(
            [('error', 'event=scan.fail error="cannot read /proc" failures=3')],
            logger.lines,
        )

    def test_no_logger_is_a_no_op(self):
        log_event(None, "info", "thing.happened", pid=1)

    def test_an_unknown_level_falls_back_to_info(self):
        logger = _Logger()

        log_event(logger, "notice", "thing.happened", pid=1)

        self.assertEqual("info", logger.lines[0][0])


if __name__ == "__main__":
    unittest.main()
