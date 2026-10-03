"""The dashboard's own consistency checks.

The page is one HTML string with a bit of hand-written JS, which means a renamed
id or a deleted function is a *silent* breakage: the browser logs it, the user
sees a button that does nothing, and no test notices.  These checks are cheap and
catch exactly that class of mistake.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

from focused.ui import DASHBOARD_HTML


def extract_function(source: str, name: str) -> str:
    start = source.index("function %s(" % name)
    depth = 0
    index = source.index("{", start)
    for position in range(index, len(source)):
        if source[position] == "{":
            depth += 1
        elif source[position] == "}":
            depth -= 1
            if depth == 0:
                return source[start : position + 1]
    raise AssertionError("unbalanced braces in %s" % name)


def html_ids(html):
    return set(re.findall(r'id="([\w-]+)"', html))


def js_id_lookups(js):
    return set(re.findall(r"\$\('([\w-]+)'\)", js)) | set(
        re.findall(r'getElementById\([\'"]([\w-]+)[\'"]\)', js)
    )


def js_functions(js):
    return set(re.findall(r"function\s+(\w+)\s*\(", js)) | set(
        re.findall(r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(", js)
    )


def inline_handlers(html):
    names = set()
    for call in re.findall(r'on\w+="([^"]+)"', html):
        names |= set(re.findall(r"(\w+)\s*\(", call))
    return names


class DashboardScriptTests(unittest.TestCase):
    """The page's script has to at least parse.

    A syntax error in that string takes the whole dashboard down -- every button
    stops working, with nothing in the HTML to show for it.  Node is the cheapest
    way to be sure, and this is exactly the mistake it catches: a listener block
    pasted inside a function.
    """

    def test_the_inline_script_parses(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>")[0]
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(script)
            path = handle.name
        try:
            result = subprocess.run(
                [node, "--check", path], capture_output=True, text=True, timeout=60
            )
        finally:
            os.unlink(path)

        self.assertEqual(0, result.returncode, result.stderr)

    def test_no_listener_is_registered_inside_a_function_body(self):
        # The specific shape of the bug above: registering a listener as a side
        # effect of calling a function that also builds markup.
        script = DASHBOARD_HTML.split("<script>", 1)[1]
        for name in ("function selectMode", "function selectAppsMode"):
            start = script.index(name)
            body = script[start : script.index("\n}", start)]
            self.assertNotIn(
                "addEventListener", body, "%s registers a listener inside itself" % name
            )


class DashboardConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.html = DASHBOARD_HTML
        self.js = self.html.split("<script>", 1)[1]

    def test_every_element_the_script_reaches_for_exists(self):
        missing = sorted(js_id_lookups(self.js) - html_ids(self.html))

        self.assertEqual([], missing, "the script reads ids the page does not define: %s" % missing)

    def test_every_inline_handlers_function_is_defined(self):
        builtins = {"Number", "String", "Math", "Date", "alert", "confirm", "prompt",
                    "JSON", "Array", "Object", "encodeURIComponent", "fetch", "setInterval"}
        missing = sorted(
            name
            for name in inline_handlers(self.html) - js_functions(self.js) - builtins
            if name not in ("return", "if", "for", "while", "catch", "typeof")
        )

        self.assertEqual([], missing, "inline handlers call undefined functions: %s" % missing)

    def test_the_two_session_modes_are_reachable(self):
        # Countdown was dropped: one round of pomodoro is the same thing, and a
        # second name for it was one more concept to keep aligned.
        self.assertIn('data-mode="pomodoro"', self.html)
        self.assertIn('data-mode="stopwatch"', self.html)
        self.assertNotIn('data-mode="countdown"', self.html)

        self.assertIn("work_minutes", self.js)
        self.assertIn("break_minutes", self.js)
        self.assertIn("cycles", self.js)

    def test_the_header_actions_are_bound_to_the_configured_values(self):
        # The complaint this fixes: the buttons said "25 分钟" no matter what the
        # settings said.  Nothing in the header may hard-code a duration.
        for needle in ("startPomodoroFromSettings", "startStopwatch",
                       "pauseFromSettings", "renderHeaderActions"):
            self.assertIn(needle, self.html, needle)

        header = self.html.split("</header>", 1)[0]
        self.assertNotIn("pickPreset(", header)
        for hardcoded in ("25 分钟", "45 分钟", "放行 10 分钟"):
            self.assertNotIn(hardcoded, header, hardcoded)

        body = extract_function(self.js, "renderHeaderActions")
        self.assertIn("sessionDefaults()", body)

    def test_the_page_has_the_controls_the_user_asked_for(self):
        for needle in (
            'id="modes"',
            'id="workMinutes"',       # customisable duration
            'id="breakMinutes"',      # pomodoro break
            'id="cycles"',            # pomodoro rounds
            'id="presets"',           # one-click durations
            'id="clock"',             # the running countdown / count-up
            "extendSession",          # +5 minutes
            "skipBreak",              # cut the break short
            "stopSession",
        ):
            self.assertIn(needle, self.html, needle)

    def test_the_dashboard_writes_every_session_default(self):
        # The settings page has to cover the same fields the API accepts, or a
        # "customisable" value would only be editable in the config file.
        for field in ("mode", "pause_minutes", "pomodoro_work_minutes",
                      "pomodoro_break_minutes", "pomodoro_cycles"):
            self.assertIn(field, self.js, field)


if __name__ == "__main__":
    unittest.main()
