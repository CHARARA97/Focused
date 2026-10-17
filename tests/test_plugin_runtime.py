"""PluginRuntime: failure cleanup and addon lifecycle."""

import os
import tempfile
import unittest

from focused.app.plugin_runtime import PluginRuntime
from focused.plugins import PluginRegistry


class FakeGateway:
    def bind_addon(self, plugin_id, module, api):
        pass

    def clear_plugin(self, plugin_id):
        pass


class PluginRuntimeTests(unittest.TestCase):
    def make(self, tmp):
        registry = PluginRegistry(os.path.join(tmp, "plugins.json"))
        cleared = []
        states = []
        emitted = []
        recorded = []
        runtime = PluginRuntime(
            registry=registry,
            gateway=FakeGateway(),
            logger=None,
            clock=lambda: 100.0,
            record_event=lambda kind, **fields: recorded.append((kind, fields)),
            emit=lambda kind, **fields: emitted.append((kind, fields)),
            clear_source=cleared.append,
            set_state=lambda active, source: states.append((active, source)),
        )
        return runtime, registry, cleared, states, emitted, recorded

    def test_five_failures_clear_rules_and_lease_and_disable(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime, registry, cleared, states, _emitted, recorded = self.make(tmp)
            registry.register(name="bad", plugin_id="bad")

            for _ in range(4):
                runtime.fail("bad", "on_event", RuntimeError("boom"))
            self.assertEqual("enabled", registry.get("bad").status)
            self.assertEqual([], cleared)

            runtime.fail("bad", "on_event", RuntimeError("boom"))

            self.assertEqual("disabled", registry.get("bad").status)
            self.assertEqual(["bad"], cleared)
            self.assertEqual([(False, "bad")], states)
            self.assertTrue(any(kind == "error" for kind, _ in recorded))

    def test_bind_stop_and_addons(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _registry, _cleared, _states, _emitted, _recorded = self.make(tmp)
            calls = []

            class Module:
                def on_stop(self, api):
                    calls.append(api)

            runtime.bind("p", Module(), "api")
            self.assertEqual(["p"], runtime.addons())
            self.assertEqual(1, len(runtime.subscribers()))

            runtime.stop("p")

            self.assertEqual([], runtime.addons())
            self.assertEqual(["api"], calls)


if __name__ == "__main__":
    unittest.main()
