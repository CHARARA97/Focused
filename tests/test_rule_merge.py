import unittest

from focused.app.rule_merge import merge_sources


def source(names=(), cmdline=(), pids=(), guards=None, protect=(), urls=()):
    return {
        "names": list(names),
        "cmdline_substrings": list(cmdline),
        "pids": list(pids),
        "pid_guards": dict(guards or {}),
        "protect_names": list(protect),
        "protect_cmdline_substrings": [],
        "url_patterns": list(urls),
    }


class MergeTests(unittest.TestCase):
    def test_sources_are_unioned_without_duplicates(self):
        merged = merge_sources(
            {
                "config": source(names=["firefox"], cmdline=["youtube"]),
                "plugin:a": source(names=["firefox", "steam"], cmdline=["youtube", "netflix"]),
            },
            protect_names=[],
        )

        self.assertEqual({"firefox", "steam"}, set(merged.rules.names))
        self.assertEqual(["youtube", "netflix"], list(merged.rules.cmdline_substrings))

    def test_built_in_protection_is_always_present(self):
        merged = merge_sources({}, protect_names=["Bash", "systemd"])

        self.assertIn("bash", merged.rules.protect_names)
        self.assertIn("systemd", merged.rules.protect_names)

    def test_a_source_cannot_lower_the_bar_for_protection(self):
        merged = merge_sources(
            {"plugin:a": source(protect=["Firefox"])},
            protect_names=["bash"],
        )

        self.assertEqual({"bash", "firefox"}, set(merged.rules.protect_names))

    def test_url_patterns_keep_their_first_seen_order(self):
        merged = merge_sources(
            {
                "config": source(urls=["*://a.example/*"]),
                "plugin:a": source(urls=["*://a.example/*", "*://b.example/*"]),
            },
            protect_names=[],
        )

        self.assertEqual(["*://a.example/*", "*://b.example/*"], merged.urls)

    def test_pid_guards_are_carried_over(self):
        merged = merge_sources(
            {"config": source(pids=[42], guards={42: 900})}, protect_names=[]
        )

        self.assertEqual({42: 900}, dict(merged.rules.pid_guards))


if __name__ == "__main__":
    unittest.main()
