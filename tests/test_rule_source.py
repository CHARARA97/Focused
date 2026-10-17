"""RuleSourceService: add/remove/replace one source without touching others."""

import os
import tempfile
import unittest

from focused.config import Config
from focused.engine import FocusedEngine


def make_engine(tmp, **extra):
    payload = {
        "audit_log": "",
        "freeze": {"enabled": True, "state_path": os.path.join(tmp, "frozen.json")},
    }
    payload.update(extra)
    config = Config.from_dict(payload)
    return FocusedEngine(
        config,
        logger=None,
        uid=os.getuid(),
        self_pid=999_001,
        self_cgroup="/user.slice/user-1000.slice/user@1000.service/app.slice/focusedd.service",
        unified_cgroup2=True,
    )


class RuleSourceTests(unittest.TestCase):
    def test_add_and_remove_are_scoped_to_one_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(tmp)
            service = engine.rule_sources
            engine.apply_rules({"names": ["other-app"]}, source="other")

            service.add("plugin", names=["firefox"])
            service.add("plugin", cmdline_substrings=["--profile"])
            self.assertEqual(["firefox"], service.list("plugin")["names"])
            self.assertEqual(["--profile"], service.list("plugin")["cmdline_substrings"])

            service.remove("plugin", names=["firefox"])
            self.assertEqual([], service.list("plugin")["names"])
            self.assertIn("other-app", engine.rules().names)
            self.assertIn("other-app", engine.sources()["other"]["names"])

    def test_replace_replaces_only_the_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(tmp)
            service = engine.rule_sources
            engine.apply_rules({"names": ["other-app"]}, source="other")

            service.replace("plugin", {"names": ["firefox"]})
            self.assertEqual(["firefox"], service.list("plugin")["names"])
            self.assertIn("other-app", engine.rules().names)

    def test_pid_add_is_guarded_and_removal_cleans_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(tmp)
            service = engine.rule_sources

            service.add("plugin", pids=[os.getpid()])
            entry = service.list("plugin")
            self.assertEqual([os.getpid()], entry["pids"])
            self.assertEqual(
                engine.procfs.starttime(os.getpid()),
                engine.sources()["plugin"]["pid_guards"][os.getpid()],
            )

            service.remove("plugin", pids=[os.getpid()])
            self.assertEqual([], service.list("plugin")["pids"])

    def test_config_pids_get_a_guard_at_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(tmp, blacklist={"pids": [os.getpid()]})

            self.assertIn(os.getpid(), engine.rules().pids)
            self.assertEqual(
                engine.procfs.starttime(os.getpid()),
                engine.rules().pid_guards[os.getpid()],
            )

    def test_a_source_has_a_rule_count_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(tmp)
            service = engine.rule_sources

            with self.assertRaises(ValueError):
                service.replace(
                    "plugin",
                    {"names": ["n%d" % index for index in range(5001)]},
                )

    def test_a_stale_pid_does_not_block_name_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(tmp)
            service = engine.rule_sources
            service._sources["p"] = {
                "names": [],
                "cmdline_substrings": [],
                "pids": [999_999_999],
                "pid_guards": {999_999_999: 123},
                "protect_names": [],
                "protect_cmdline_substrings": [],
                "url_patterns": [],
            }

            service.add("p", names=["new-name"])
            self.assertEqual(["new-name"], service.list("p")["names"])
            self.assertEqual({999_999_999: 123}, engine.sources()["p"]["pid_guards"])

            service.remove("p", names=["new-name"])
            self.assertEqual([], service.list("p")["names"])

    def test_preview_does_not_change_the_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(tmp)
            service = engine.rule_sources
            service.add("p", names=["old"])

            entry = service.preview("p", {"names": ["new"]})

            self.assertEqual(["new"], entry["names"])
            self.assertEqual(["old"], service.list("p")["names"])

    def test_url_patterns_must_be_a_sequence_not_a_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(tmp)
            service = engine.rule_sources

            with self.assertRaises(ValueError):
                service.replace("plugin", {"url_patterns": "ab"})
            with self.assertRaises(ValueError):
                service.set_url_patterns("ab")

            service.replace("plugin", {"url_patterns": ["https://example.com"]})
            self.assertEqual(["https://example.com"], engine.url_patterns())

    def test_a_gone_pid_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(tmp)
            service = engine.rule_sources

            with self.assertRaises(ValueError):
                service.add("plugin", pids=[999_999_999])


if __name__ == "__main__":
    unittest.main()
