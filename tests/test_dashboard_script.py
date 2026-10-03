"""Behavioural tests for the dashboard's script, run in Node.

The page refreshes itself every three seconds and writes server state into its
forms.  That is exactly how "I typed 50 and it went back to 25" happens, and no
amount of reading the Python can prove it is fixed -- so these tests lift the real
functions out of the page, give them a stub DOM, and check what they actually do.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from focused.ui import DASHBOARD_HTML


def script_source() -> str:
    return DASHBOARD_HTML.split("<script>", 1)[1].split("</script>")[0]


def extract_function(source: str, name: str) -> str:
    """Pull one function out of the page by brace matching."""
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


class StubElement:
    def __init__(self, value="", kind="text"):
        self.value = value
        self.type = kind
        self.checked = value is True
        self.dataset = {}
        self.textContent = ""
        self.style = {}


class SettingsFormTests(unittest.TestCase):
    """A field being edited must survive the periodic refresh."""

    def setUp(self):
        if shutil.which("node") is None:
            self.skipTest("node is not installed")
        self.source = script_source()

    def run_in_node(self, body: str) -> dict:
        """Run a snippet against the page's own helpers and return its results."""
        helpers = "\n".join(
            extract_function(self.source, name)
            for name in ("isFieldBusy", "setFieldValue", "markDirty")
        )
        harness = """
const fields = {};
function makeField(id, value, kind) {
  fields[id] = { value: value, type: kind || 'text', checked: value === true,
                 dataset: {}, style: {} };
  return fields[id];
}
makeField('setSessionMinutes', '25');
makeField('setPomodoroWork', '25');
makeField('setDryRun', false, 'checkbox');
const document = { activeElement: null, getElementById: (id) => fields[id] || null };
function $(id) { return fields[id] || null; }
function show() {}
const config = { session: { default_minutes: 25, pomodoro_work_minutes: 25 }, dry_run: false };
%s
%s
const dirtyFields = new Set();
%s
console.log(JSON.stringify({ fields: fields, result: RESULT }));
""" % (
            helpers,
            "const SETTINGS_FIELDS = [];",
            body,
        )
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(harness)
            path = handle.name
        try:
            result = subprocess.run(
                ["node", path], capture_output=True, text=True, timeout=60
            )
        finally:
            os.unlink(path)
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.payload = payload
        return payload

    def test_an_edited_field_is_not_overwritten_by_a_refresh(self):
        self.run_in_node("""
const field = fields['setSessionMinutes'];
field.value = '50';              // what the user typed
dirtyFields.add('setSessionMinutes');
document.activeElement = null;   // they clicked away
setFieldValue('setSessionMinutes', 25);   // ...now a refresh arrives
const RESULT = { after: fields['setSessionMinutes'].value };
""")

        self.assertEqual("50", self.payload["fields"]["setSessionMinutes"]["value"])

    def test_a_field_being_typed_into_is_not_overwritten_either(self):
        self.run_in_node("""
const field = fields['setPomodoroWork'];
field.value = '4';               // mid-keystroke
document.activeElement = field;  // still focused, not yet marked
setFieldValue('setPomodoroWork', 25);
const RESULT = { after: fields['setPomodoroWork'].value };
""")

        self.assertEqual("4", self.payload["fields"]["setPomodoroWork"]["value"])

    def test_an_untouched_field_still_follows_the_server(self):
        self.run_in_node("""
setFieldValue('setSessionMinutes', 45);
const RESULT = { after: fields['setSessionMinutes'].value };
""")

        self.assertEqual(45, self.payload["fields"]["setSessionMinutes"]["value"])

    def test_a_checkbox_follows_the_same_rule(self):
        self.run_in_node("""
fields['setDryRun'].checked = true;
dirtyFields.add('setDryRun');
setFieldValue('setDryRun', false);
const RESULT = { after: fields['setDryRun'].checked };
""")

        self.assertTrue(self.payload["fields"]["setDryRun"]["checked"])

    def test_the_header_labels_follow_the_configured_values(self):
        self.run_in_node("""
config.session = {
  mode: 'pomodoro', pomodoro_work_minutes: 50, pomodoro_break_minutes: 10,
  pomodoro_cycles: 3, pause_minutes: 7, default_minutes: 25,
};
const elements = {
  startPomodoro: { classList: { toggle() {} }, title: '', textContent: '' },
  startStopwatch: { classList: { toggle() {} }, title: '', textContent: '' },
  pauseButton: { textContent: '' },
  pauseCardButton: { textContent: '' },
};
for (const key of Object.keys(elements)) fields[key] = elements[key];
function sessionDefaults() {
  const session = config.session;
  return { mode: session.mode, work: session.pomodoro_work_minutes,
           rest: session.pomodoro_break_minutes, cycles: session.pomodoro_cycles,
           pause: session.pause_minutes };
}
%s
renderHeaderActions();
const RESULT = {
  pomodoro: elements.startPomodoro.textContent,
  pause: elements.pauseButton.textContent,
};
""" % extract_function(self.source, "renderHeaderActions"))

        self.assertIn("50", self.payload["result"]["pomodoro"])
        self.assertIn("3 轮", self.payload["result"]["pomodoro"])
        self.assertEqual("放行 7 分钟", self.payload["result"]["pause"])

    def test_the_script_uses_the_guard_everywhere_in_the_settings_form(self):
        # A shape check: a single direct assignment would reintroduce the bug.
        body = extract_function(self.source, "renderSettings")

        direct = [
            line.strip()
            for line in body.splitlines()
            if re.search(r"\$\('set\w+'\)\.(value|checked)\s*=", line)
        ]
        self.assertEqual([], direct, "renderSettings writes fields directly: %s" % direct)


if __name__ == "__main__":
    unittest.main()
