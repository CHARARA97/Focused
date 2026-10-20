import unittest

from focused.app.history import EpisodeBook


class NoteTests(unittest.TestCase):
    def test_the_first_note_opens_a_window(self):
        book = EpisodeBook(window=60.0)
        self.assertTrue(book.note("firefox", "blacklist.name:firefox", 1000, now=10.0))

    def test_a_repeat_inside_the_window_is_not_a_new_episode(self):
        book = EpisodeBook(window=60.0)
        book.note("firefox", "blacklist.name:firefox", 1000, now=10.0)
        self.assertFalse(book.note("firefox", "blacklist.name:firefox", 1000, now=11.0))

    def test_repeats_are_counted(self):
        book = EpisodeBook(window=60.0)
        for second in range(5):
            book.note("firefox", "blacklist.name:firefox", 1000, now=10.0 + second)
        self.assertEqual(book.count_of("firefox"), 5)

    def test_distinct_roots_are_counted_separately(self):
        book = EpisodeBook(window=60.0)
        book.note("firefox", "blacklist.name:firefox", 1000, now=10.0)
        book.note("firefox", "blacklist.name:firefox", 2000, now=11.0)
        episode = book.current()[0]
        self.assertEqual(episode.roots, 2)

    def test_names_are_independent(self):
        book = EpisodeBook(window=60.0)
        book.note("firefox", "r1", 1000, now=10.0)
        book.note("steam", "r2", 2000, now=10.0)
        self.assertEqual(len(book.current()), 2)

    def test_an_unknown_name_has_count_zero(self):
        self.assertEqual(EpisodeBook().count_of("nobody"), 0)


class SweepTests(unittest.TestCase):
    def test_a_live_window_is_not_closed(self):
        book = EpisodeBook(window=60.0)
        book.note("firefox", "rule", 1000, now=10.0)
        self.assertEqual(book.sweep(now=30.0), ())

    def test_an_expired_window_is_closed(self):
        book = EpisodeBook(window=60.0)
        book.note("firefox", "rule", 1000, now=10.0)
        closed = book.sweep(now=70.0)
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].name, "firefox")

    def test_a_closed_window_is_forgotten(self):
        book = EpisodeBook(window=60.0)
        book.note("firefox", "rule", 1000, now=10.0)
        book.sweep(now=70.0)
        self.assertEqual(book.current(), ())

    def test_a_note_after_a_sweep_opens_a_new_window(self):
        book = EpisodeBook(window=60.0)
        book.note("firefox", "rule", 1000, now=10.0)
        book.sweep(now=70.0)
        self.assertTrue(book.note("firefox", "rule", 2000, now=71.0))

    def test_the_duration_spans_the_window(self):
        book = EpisodeBook(window=60.0)
        book.note("firefox", "rule", 1000, now=10.0)
        book.note("firefox", "rule", 1000, now=40.0)
        closed = book.sweep(now=100.0)
        self.assertEqual(closed[0].duration_s, 30.0)


class OrderTests(unittest.TestCase):
    def test_current_is_sorted_by_name(self):
        book = EpisodeBook(window=60.0)
        book.note("steam", "r", 1, now=1.0)
        book.note("firefox", "r", 2, now=1.0)
        self.assertEqual([episode.name for episode in book.current()], ["firefox", "steam"])


if __name__ == "__main__":
    unittest.main()
