"""Transport checks: Host/Origin and JSON content type."""

import unittest

from focused.api.http import ApiError
from focused.api.security import (
    is_loopback_host,
    is_loopback_origin,
    require_json_content_type,
    validate_transport,
)


class LoopbackTests(unittest.TestCase):
    def test_loopback_hosts(self):
        for value in ("127.0.0.1:8766", "localhost:8766", "[::1]:8766", "127.0.0.1"):
            self.assertTrue(is_loopback_host(value), value)

    def test_non_loopback_hosts(self):
        for value in ("evil.com", "127.0.0.1.evil.com", "0.0.0.0:8766", ""):
            self.assertFalse(is_loopback_host(value), value)

    def test_loopback_origins(self):
        for value in ("http://127.0.0.1:8766", "https://localhost", "http://[::1]"):
            self.assertTrue(is_loopback_origin(value), value)

    def test_non_loopback_origins(self):
        for value in ("https://evil.com", "null", "file:///tmp/x", ""):
            self.assertFalse(is_loopback_origin(value), value)


class TransportTests(unittest.TestCase):
    def test_a_loopback_request_is_accepted(self):
        validate_transport({"Host": "127.0.0.1:8766", "Origin": "http://127.0.0.1:8766"})

    def test_a_foreign_host_is_refused(self):
        with self.assertRaises(ApiError) as caught:
            validate_transport({"Host": "evil.com"})
        self.assertEqual(403, caught.exception.status)

    def test_a_foreign_origin_is_refused(self):
        with self.assertRaises(ApiError) as caught:
            validate_transport({"Host": "127.0.0.1", "Origin": "https://evil.com"})
        self.assertEqual(403, caught.exception.status)

    def test_json_content_type_is_required_for_bodies(self):
        require_json_content_type({"Content-Type": "application/json; charset=utf-8"})
        with self.assertRaises(ApiError) as caught:
            require_json_content_type({"Content-Type": "text/plain"})
        self.assertEqual(415, caught.exception.status)


if __name__ == "__main__":
    unittest.main()
