import re
import unittest
from pathlib import Path

from focused.web import render
from focused.web.text import TEXT

WEB = Path(__file__).resolve().parents[1] / "focused" / "web"
TOKEN = re.compile(r"\[\[([\w.]+)\]\]")
JS_CALL = re.compile(r"T\('([\w.]+)'\)")


def referenced_keys():
    found = set()
    for path in WEB.rglob("*.py"):
        if path.name == "text.py":
            continue
        source = path.read_text(encoding="utf-8")
        found |= set(TOKEN.findall(source))
        found |= set(JS_CALL.findall(source))
    return found


class CatalogueTests(unittest.TestCase):
    def test_every_referenced_key_exists(self):
        missing = referenced_keys() - set(TEXT)
        self.assertEqual(set(), missing)

    def test_the_page_renders_without_a_missing_key(self):
        self.assertIn("<section", render())

    def test_no_visible_text_is_left_in_the_tab_modules(self):
        chinese = re.compile(r"[\u4e00-\u9fff]")
        offenders = [
            path.name
            for path in WEB.rglob("*.py")
            if path.name != "text.py" and chinese.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual([], offenders)

    def test_every_value_is_a_non_empty_string(self):
        for key, value in TEXT.items():
            self.assertIsInstance(value, str, key)
            self.assertTrue(value.strip(), key)


if __name__ == "__main__":
    unittest.main()
