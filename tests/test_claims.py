"""ClaimBook: leases, expiry and the "is this a transition?" question."""

import unittest

from focused.app.session import SessionService
from focused.domain.claims import ClaimBook


class ClaimBookTests(unittest.TestCase):
    def test_a_new_claim_is_a_transition_and_a_renewal_is_not(self):
        book = ClaimBook()

        self.assertTrue(book.claim("game", now=100.0, ttl_seconds=30))
        self.assertFalse(book.claim("game", now=102.0, ttl_seconds=30))
        self.assertFalse(book.claim("game", now=104.0, ttl_seconds=30))

    def test_an_expired_claim_is_a_new_transition(self):
        book = ClaimBook()
        book.claim("game", now=100.0, ttl_seconds=5)

        self.assertTrue(book.claim("game", now=106.0, ttl_seconds=5))

    def test_withdraw_is_idempotent(self):
        book = ClaimBook()
        book.claim("game", now=100.0, ttl_seconds=30)

        self.assertTrue(book.withdraw("game"))
        self.assertFalse(book.withdraw("game"))
        self.assertFalse(book.active(100.0))

    def test_live_filters_expired_claims(self):
        book = ClaimBook()
        book.claim("a", now=100.0, ttl_seconds=30)
        book.claim("b", now=100.0, ttl_seconds=5)

        self.assertEqual({"a"}, set(book.live(106.0)))
        self.assertTrue(book.active(106.0))

    def test_expire_removes_only_lapsed_claims(self):
        book = ClaimBook()
        book.claim("a", now=100.0, ttl_seconds=30)
        book.claim("b", now=100.0, ttl_seconds=5)

        self.assertEqual(["b"], book.expire(106.0))
        self.assertEqual({"a"}, set(book.all()))

    def test_a_claim_without_ttl_lasts_until_withdrawn(self):
        book = ClaimBook()
        book.claim("manual", now=100.0, ttl_seconds=0)

        self.assertFalse(book.claim("manual", now=1000.0, ttl_seconds=0))
        self.assertTrue(book.active(10**9))
        self.assertEqual([], book.expire(10**9))
        self.assertTrue(book.withdraw("manual"))

    def test_nearest_deadline_and_ttl(self):
        book = ClaimBook()
        book.claim("a", now=100.0, ttl_seconds=30)
        book.claim("b", now=100.0, ttl_seconds=5)

        self.assertEqual(105.0, book.nearest_deadline(100.0))
        self.assertEqual(5.0, book.nearest_ttl())

    def test_describe_prefers_live_sources(self):
        book = ClaimBook()
        book.claim("old", now=100.0, ttl_seconds=1)
        book.claim("live", now=100.0, ttl_seconds=30)

        self.assertEqual("live", book.describe(101.0))
        self.assertEqual("live, old", book.describe(200.0))


class SessionServiceTests(unittest.TestCase):
    def test_service_owns_claims_and_delegates_the_lease_math(self):
        now = [100.0]
        service = SessionService(lambda: now[0])

        self.assertTrue(service.claim("a", now[0], 30))
        self.assertEqual({"a": 130.0}, service.live())
        self.assertEqual({"a": {"until": 130.0, "ttl": 30.0}}, service.claims())
        self.assertTrue(service.delegated_active())
        self.assertTrue(service.delegated())
        self.assertEqual(130.0, service.nearest_deadline())
        self.assertEqual(30.0, service.nearest_ttl())
        self.assertIn("a", service.describe())

        now[0] = 131.0
        self.assertEqual(["a"], service.expire(now[0]))
        self.assertFalse(service.delegated_active())
        self.assertFalse(service.delegated())

    def test_withdraw_is_per_source(self):
        now = [100.0]
        service = SessionService(lambda: now[0])
        service.claim("a", now[0], 30)
        service.claim("b", now[0], 30)

        service.withdraw("a")

        self.assertEqual({"b": 130.0}, service.live())


if __name__ == "__main__":
    unittest.main()
