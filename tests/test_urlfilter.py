"""URL patterns: the rule the browser extension also implements.

The extension cannot import Python, so this logic exists twice.  These tests pin
the semantics down; ``focused-extension/test/logic.test.mjs`` pins the same cases
on the other side.
"""

import unittest

from focused.core import urlfilter


class MatchesPatternTests(unittest.TestCase):
    def test_a_glob_is_anchored_to_the_whole_url(self):
        self.assertTrue(urlfilter.matches_pattern("https://twitter.com/home", "*twitter*"))
        self.assertTrue(urlfilter.matches_pattern("https://x.com", "https://*"))

    def test_a_plain_word_is_a_substring_match(self):
        self.assertTrue(urlfilter.matches_pattern("https://old.reddit.com/r/all", "reddit.com"))
        self.assertTrue(urlfilter.matches_pattern("https://news.ycombinator.com", "ycombinator"))

    def test_matching_is_case_insensitive(self):
        self.assertTrue(urlfilter.matches_pattern("https://Reddit.com", "reddit.COM"))

    def test_a_glob_that_does_not_match_the_whole_url_fails(self):
        self.assertFalse(urlfilter.matches_pattern("https://twitter.com", "https://*facebook*"))

    def test_an_empty_pattern_matches_nothing(self):
        for pattern in ("", "   ", None):
            self.assertFalse(urlfilter.matches_pattern("https://example.com", pattern))

    def test_an_empty_url_matches_nothing(self):
        self.assertFalse(urlfilter.matches_pattern("", "*"))

    def test_regex_characters_are_literal(self):
        # A user typing a '.' means a dot, not "any character".
        self.assertTrue(urlfilter.matches_pattern("https://a.b", "a.b"))
        self.assertFalse(urlfilter.matches_pattern("https://axb", "a.b"))

    def test_a_path_with_slashes_matches_a_glob(self):
        self.assertTrue(
            urlfilter.matches_pattern("https://example.com/a/b/c", "*example.com/a/*")
        )


class MatchesAnyTests(unittest.TestCase):
    def test_any_pattern_is_enough(self):
        patterns = ["reddit.com", "*twitter*"]
        self.assertTrue(urlfilter.matches_any("https://twitter.com/x", patterns))
        self.assertFalse(urlfilter.matches_any("https://example.com", patterns))

    def test_an_empty_list_matches_nothing(self):
        for patterns in ([], None, ("",)):
            self.assertFalse(urlfilter.matches_any("https://example.com", patterns))


class PublishedPatternsTests(unittest.TestCase):
    """The daemon's own config is the only server-side source of URL patterns."""

    def test_config_patterns_reach_the_focus_endpoint(self):
        import json
        import tempfile
        import urllib.request

        from focused.config import Config
        from focused.engine import FocusedEngine
        from focused.server import FocusedServer

        with tempfile.TemporaryDirectory() as tmp:
            config = Config.from_dict(
                {
                    "enabled": False,
                    "audit_log": "",
                    "game_gate": {"enabled": False},
                    "urls": {"patterns": [" reddit.com ", "reddit.com", "*twitter*"]},
                }
            )
            engine = FocusedEngine(config)
            server = FocusedServer(engine, host="127.0.0.1", port=0)
            port = server.start()
            try:
                payload = json.load(
                    urllib.request.urlopen(
                        "http://127.0.0.1:%d/api/v1/focus" % port, timeout=5
                    )
                )
            finally:
                server.stop()

        # Trimmed and deduplicated, order preserved.
        self.assertEqual(["reddit.com", "*twitter*"], payload["url_patterns"])


class SanitizeTests(unittest.TestCase):
    def test_blanks_and_duplicates_are_dropped_in_order(self):
        self.assertEqual(
            ["b", "a"],
            urlfilter.sanitize([" b ", "", "a", "b", "   "]),
        )

    def test_none_becomes_an_empty_list(self):
        self.assertEqual([], urlfilter.sanitize(None))


if __name__ == "__main__":
    unittest.main()
