from __future__ import annotations

import unittest

from adsb_ingest.public_access import QueryRateLimiter


class QueryRateLimiterTests(unittest.TestCase):
    def test_enforces_client_window_and_recovers_after_expiry(self) -> None:
        now = [100.0]
        limiter = QueryRateLimiter(
            per_client_limit=2,
            per_client_window_seconds=60,
            global_limit=10,
            max_concurrent=2,
            clock=lambda: now[0],
        )

        first = limiter.acquire("visitor")
        limiter.release()
        second = limiter.acquire("visitor")
        limiter.release()
        denied = limiter.acquire("visitor")

        self.assertTrue(first.allowed)
        self.assertEqual(second.client_remaining, 0)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "client_rate_limit")
        self.assertEqual(denied.retry_after_seconds, 60)

        now[0] += 61
        recovered = limiter.acquire("visitor")
        self.assertTrue(recovered.allowed)

    def test_caps_concurrent_and_global_queries(self) -> None:
        limiter = QueryRateLimiter(
            per_client_limit=5,
            global_limit=2,
            max_concurrent=1,
        )

        first = limiter.acquire("one")
        busy = limiter.acquire("two")
        limiter.release()
        second = limiter.acquire("two")
        limiter.release()
        exhausted = limiter.acquire("three")

        self.assertTrue(first.allowed)
        self.assertEqual(busy.reason, "busy")
        self.assertTrue(second.allowed)
        self.assertEqual(exhausted.reason, "global_rate_limit")


if __name__ == "__main__":
    unittest.main()
