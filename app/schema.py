"""Schema and seed data.

Two tables is enough to reproduce the bug that motivated this repo: a list
endpoint that reads the parent table, then issues one more query per row to
resolve a related record.
"""
from __future__ import annotations

import sqlite3

DDL = """
CREATE TABLE IF NOT EXISTS customers (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL,
    tier  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    total_cents INTEGER NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders (customer_id);
"""


def seed(conn: sqlite3.Connection, customers: int = 2000, orders: int = 20000) -> None:
    conn.executescript(DDL)
    conn.execute("DELETE FROM orders")
    conn.execute("DELETE FROM customers")

    tiers = ("free", "pro", "enterprise")
    conn.executemany(
        "INSERT INTO customers (id, name, tier) VALUES (?,?,?)",
        ((i, f"Customer {i:05d}", tiers[i % 3]) for i in range(1, customers + 1)),
    )
    conn.executemany(
        "INSERT INTO orders (id, customer_id, total_cents, status, created_at) VALUES (?,?,?,?,?)",
        (
            (i, (i % customers) + 1, 1000 + (i * 37) % 90000,
             "paid" if i % 4 else "refunded", f"2026-{(i % 12) + 1:02d}-15T10:00:00Z")
            for i in range(1, orders + 1)
        ),
    )
    conn.commit()
