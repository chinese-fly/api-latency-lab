"""Order queries."""
from __future__ import annotations

import sqlite3
from typing import Any

ROW_LIMIT = 50


def list_orders(conn: sqlite3.Connection, limit: int = ROW_LIMIT) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, customer_id, total_cents, status FROM orders "
        "ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()

    out = []
    for r in rows:
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
