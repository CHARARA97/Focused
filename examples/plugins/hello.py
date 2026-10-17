"""A minimal in-process Focused plugin.

Install: copy this file to ``~/.config/focused/plugins/hello.py`` and restart
Focused.  It contributes one rule and adds one protect name; it does not claim a
session, so it cannot freeze anything on its own.
"""


def register(api):
    api.log("hello plugin loaded")
    return {
        "id": "hello",
        "name": "Hello",
        "version": "0.1.0",
        "description": "Example: contribute a rule and a protect name",
        "capabilities": ["rules", "events", "status"],
    }


def on_start(api):
    api.rules.replace(
        names=["example-distraction"],
        protect_names=["my-editor"],
    )
    api.log("rules installed; backend=%s" % api.state.summary()["version"])


def on_event(api, event):
    if event["kind"] in ("freeze", "thaw"):
        api.log("%s %s" % (event["kind"], event["name"] or event["pid"]))
