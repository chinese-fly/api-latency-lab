"""Parity tests: the fast path must return exactly what the slow path returned.

This is the safety net that makes the optimisation reviewable. Without it,
"it got faster" can always be true and "it still returns the right data" is
somebody's opinion.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.repository import TierCache, fast_list_orders, slow_list_orders
from app.schema import seed


class TestParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cls.conn = sqlite3.connect(cls.tmp.name)
        seed(cls.conn, customers=50, orders=500)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        Path(cls.tmp.name).unlink(missing_ok=True)

    def test_identical_payload(self):
        slow = slow_list_orders(self.conn)
        fast = fast_list_orders(self.conn, TierCache())
        self.assertEqual(slow, fast)

    def test_default_page_size(self):
        self.assertEqual(len(slow_list_orders(self.conn)), 50)

    def test_limit_respected(self):
        self.assertEqual(len(fast_list_orders(self.conn, TierCache(), limit=7)), 7)

    def test_query_count_differs(self):
        """51 round trips versus 1 — the whole point of the repo."""
        counts = {"slow": 0, "fast": 0}
        for name, fn in (("slow", lambda: slow_list_orders(self.conn)),
                         ("fast", lambda: fast_list_orders(self.conn, TierCache()))):
            statements = []
            self.conn.set_trace_callback(
                lambda s, acc=statements: acc.append(s)
                if s.lstrip().upper().startswith("SELECT") else None
            )
            fn()
            self.conn.set_trace_callback(None)
            counts[name] = len(statements)

        self.assertEqual(counts["slow"], 51)
        self.assertEqual(counts["fast"], 1)


class TestCache(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        seed(self.conn, customers=10, orders=20)

    def tearDown(self):
        self.conn.close()

    def test_hit_after_first_read(self):
        c = TierCache(ttl=60)
        c.get(self.conn, 1)
        c.get(self.conn, 1)
        self.assertEqual(c.hits, 1)
        self.assertEqual(c.misses, 1)

    def test_expiry(self):
        c = TierCache(ttl=0.0)
        c.get(self.conn, 1)
        c.get(self.conn, 1)
        self.assertEqual(c.misses, 2)

    def test_missing_customer_is_not_cached_as_crash(self):
        c = TierCache()
        self.assertEqual(c.get(self.conn, 999999), "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
