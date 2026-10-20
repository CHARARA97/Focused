import unittest

from focused.core.proctree import live_subtree


class FakeReader:
    def __init__(self, children=None, live=True):
        self.children = dict(children or {})
        self.live = live
        self.reads = []

    def supports_live_children(self):
        return self.live

    def child_pids(self, pid):
        self.reads.append(int(pid))
        return tuple(self.children.get(int(pid), ()))


class FallbackTests(unittest.TestCase):
    def test_the_snapshot_tree_is_used_when_live_reading_is_unsupported(self):
        reader = FakeReader(live=False)
        self.assertEqual(live_subtree(1000, reader, fallback=(1000, 1001)), (1000, 1001))

    def test_a_lone_root_is_used_when_no_fallback_exists(self):
        reader = FakeReader(live=False)
        self.assertEqual(live_subtree(1000, reader), (1000,))

    def test_no_child_reads_happen_without_support(self):
        reader = FakeReader(live=False)
        live_subtree(1000, reader, fallback=(1000,))
        self.assertEqual(reader.reads, [])


class LiveTests(unittest.TestCase):
    def test_children_are_followed(self):
        reader = FakeReader({1000: (1001, 1002)})
        self.assertEqual(live_subtree(1000, reader), (1000, 1001, 1002))

    def test_grandchildren_are_followed(self):
        reader = FakeReader({1000: (1001,), 1001: (1002,)})
        self.assertEqual(live_subtree(1000, reader), (1000, 1001, 1002))

    def test_breadth_first_order_is_kept(self):
        reader = FakeReader({1000: (1001, 1002), 1001: (1003,)})
        self.assertEqual(live_subtree(1000, reader), (1000, 1001, 1002, 1003))

    def test_a_leaf_is_its_own_tree(self):
        self.assertEqual(live_subtree(1000, FakeReader()), (1000,))

    def test_a_cycle_does_not_loop(self):
        reader = FakeReader({1000: (1001,), 1001: (1000,)})
        self.assertEqual(live_subtree(1000, reader), (1000, 1001))

    def test_a_repeated_child_is_visited_once(self):
        reader = FakeReader({1000: (1001, 1001)})
        self.assertEqual(live_subtree(1000, reader), (1000, 1001))


class LimitTests(unittest.TestCase):
    def test_the_tree_is_truncated_at_the_limit(self):
        reader = FakeReader({1000: (1001, 1002, 1003)})
        self.assertEqual(live_subtree(1000, reader, max_nodes=3), (1000, 1001, 1002))

    def test_a_limit_of_one_returns_only_the_root(self):
        reader = FakeReader({1000: (1001,)})
        self.assertEqual(live_subtree(1000, reader, max_nodes=1), (1000,))

    def test_truncation_stops_reading_children(self):
        reader = FakeReader({1000: (1001, 1002)})
        live_subtree(1000, reader, max_nodes=2)
        self.assertEqual(reader.reads, [1000, 1001])


if __name__ == "__main__":
    unittest.main()
