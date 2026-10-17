"""ScanRequester: one scan request per source per interval."""

import unittest

from focused.app.scan import ScanRequester


class FakeClock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class ScanRequesterTests(unittest.TestCase):
    def test_a_request_runs_the_scan_and_reports_the_count(self):
        clock = FakeClock()
        calls = []

        def scan():
            calls.append(1)
            return 3

        requester = ScanRequester(scan, clock)
        report = requester.request("plugin-a")

        self.assertTrue(report.accepted)
        self.assertEqual(3, report.frozen)
        self.assertEqual(1, len(calls))

    def test_a_second_request_inside_the_interval_is_rate_limited(self):
        clock = FakeClock()
        calls = []
        requester = ScanRequester(lambda: calls.append(1) or 0, clock, min_interval=1.0)

        self.assertTrue(requester.request("plugin-a").accepted)
        second = requester.request("plugin-a")
        self.assertFalse(second.accepted)
        self.assertEqual("rate-limited", second.reason)
        self.assertEqual(1, len(calls))

        clock.now += 1.1
        self.assertTrue(requester.request("plugin-a").accepted)
        self.assertEqual(2, len(calls))

    def test_the_cooldown_is_global_not_per_source(self):
        clock = FakeClock()
        requester = ScanRequester(lambda: 0, clock)

        self.assertTrue(requester.request("a").accepted)
        # A second plugin cannot multiply the scan work by having a new id.
        self.assertFalse(requester.request("b").accepted)

    def test_to_dict_is_json_shaped(self):
        clock = FakeClock()
        requester = ScanRequester(lambda: 2, clock)
        report = requester.request("a")

        self.assertEqual(
            {"accepted": True, "frozen": 2, "reason": "ok"}, report.to_dict()
        )


if __name__ == "__main__":
    unittest.main()
