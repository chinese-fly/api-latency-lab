"""Two implementations of the same query.

`slow_list_orders` is what the original codebase did: select the order page,
then loop and fetch the customer for each row. It is correct, it passes tests,
and it dies at around 150 concurrent users because each request multiplies into
51 round trips.

`fast_list_orders` keeps the N+1 out of the picture entirely: one JOIN for the
page, plus a small TTL cache for the tier lookup, which is what actually shows
up in the flame graph.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any

ROW_LIMIT = 50


class TierCache:
    """Tiny TTL cache.

    The tier of a customer changes rarely and is read on every order row, which
    makes it the one thing worth caching. Caching the order rows themselves
    would be a mistake — they change constantly and a stale order list is a
    support ticket.
    """

    def __init__(self, ttl: float = 30.0):
        self.ttl = ttl
        self._data: dict[int, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, conn: sqlite3.Connection, customer_id: int) -> str:
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(customer_id)
            if hit and hit[1] > now:
                self.hits += 1
                return hit[0]
            self.misses += 1
        row = conn.execute(
            "SELECT tier FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
        tier = row[0] if row else "unknown"
        with self._lock:
            self._data[customer_id] = (tier, now + self.ttl)
        return tier


def slow_list_orders(conn: sqlite3.Connection, limit: int = ROW_LIMIT) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, customer_id, total_cents, status FROM orders "
        "ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()

    out = []
    for r in rows:
        # the N+1: one extra query per row
        cust = conn.execute(
            "SELECT name, tier FROM customers WHERE id = ?", (r[1],)
        ).fetchone()
        out.append({
            "order_id": r[0],
            "customer": cust[0] if cust else None,
            "tier": cust[1] if cust else None,
            "total": r[2] / 100,
            "status": r[3],
        })
    return out


def fast_list_orders(conn: sqlite3.Connection, cache: TierCache,
                     limit: int = ROW_LIMIT) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT o.id, c.name, c.tier, o.total_cents, o.status "
        "FROM orders o JOIN customers c ON c.id = o.customer_id "
        "ORDER BY o.id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [
        {"order_id": r[0], "customer": r[1], "tier": r[2],
         "total": r[3] / 100, "status": r[4]}
        for r in rows
    ]
