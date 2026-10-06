#!/usr/bin/env python3
"""Run the test suite behind a progress bar.

`python -m unittest` prints one dot per test.  That tells a person nothing during a
47-second run, and this suite also runs inside `makepkg`'s `check()` step, where a
wall of dots looks exactly like a hang.

The total is known up front (the suite is discovered before it runs), so the bar can
show how far along it is.  The bar is drawn only on a terminal: in a log file (a
clean chroot build, CI) the plain summary is what matters.

Usage:
    python3 scripts/test-runner.py [--plain]

Exit code: 0 when the suite passes, 1 otherwise.
"""

import functools
import os
import sys
import time
import unittest

#: The runner works on the current directory -- the repository root when it is run
#: from a published checkout, and focused/ inside this working tree.  Deriving it
#: from __file__ would only work in one of the two.
BAR_WIDTH = 24

#: Width of the running-test column.  Querying the terminal width is not worth it.
LABEL_WIDTH = 46


def _line(percent: int, label: str = "") -> None:
    filled = percent * BAR_WIDTH // 100
    bar = "█" * filled + "░" * (BAR_WIDTH - filled)
    sys.stdout.write(
        "\r\033[36m[%s]\033[0m %3d%%  %s\033[K"
        % (bar, percent, label[:LABEL_WIDTH].ljust(LABEL_WIDTH))
    )
    sys.stdout.flush()


def _name(test) -> str:
    return test.id().split(".", 2)[-1]


class ProgressResult(unittest.TextTestResult):
    """TextTestResult with the per-test dots replaced by a percentage bar."""

    def __init__(self, *args, total: int = 0, show_bar: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.total = total
        self.show_bar = show_bar
        self.started = time.time()
        # On a terminal the bar replaces the dots; in a log the dots stay, because a
        # long silent gap in a build log is worse than a line of dots.
        self.dots = not show_bar
        self.showAll = False

    def _percent(self, done: int) -> int:
        if not self.total:
            return 0
        return min(100, done * 100 // self.total)

    def startTest(self, test):
        super().startTest(test)
        if self.show_bar:
            _line(self._percent(self.testsRun), "%d/%d  %s" % (self.testsRun, self.total, _name(test)))

    def addSuccess(self, test):
        super().addSuccess(test)  # no extra dot: the bar already moved

    def stopTestRun(self):
        super().stopTestRun()
        if not self.show_bar:
            return
        # Close the bar and start a new line, so the report that follows (the
        # separator, "Ran N tests", OK) does not land on top of it.
        elapsed = time.time() - self.started
        failed = len(self.failures) + len(self.errors)
        tail = "done in %.1fs" % elapsed if not failed else "%d failed" % failed
        _line(100, "%d/%d  %s" % (self.total, self.total, tail))
        sys.stdout.write("\n")
        sys.stdout.flush()


def main() -> int:
    show_bar = "--plain" not in sys.argv and sys.stdout.isatty()

    root = os.getcwd()
    sys.path.insert(0, root)

    suite = unittest.defaultTestLoader.discover(os.path.join(root, "tests"), top_level_dir=root)
    total = suite.countTestCases()

    if show_bar:
        _line(0, "0/%d  starting" % total)
        sys.stdout.write("\n")

    runner = unittest.TextTestRunner(
        verbosity=1,
        resultclass=functools.partial(ProgressResult, total=total, show_bar=show_bar),
    )
    started = time.time()
    result = runner.run(suite)
    elapsed = time.time() - started

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
