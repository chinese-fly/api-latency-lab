# api-latency-lab

A list endpoint with an N+1 query problem, the rewrite that removes it, and a load test that measures both so the claim is a number instead of a feeling.

```bash
python bench/bench.py --threads 8 --requests 40
python -m unittest discover -s tests    # 7 tests
```

No dependencies. Python 3.10+.

---

## Measured

```
queries per request : slow=51  fast=1
load: 8 threads x 40 requests

endpoint         reqs      qps     p50     p95     p99     max
--------------------------------------------------------------
/orders/slow      320    381.3    21.4    34.0    39.0    44.3
/orders/fast      320    814.5     9.6    17.6    20.8    47.4

p95 speedup: 1.9x
```

**Read that 1.9x with suspicion — it undersells the real problem.** SQLite runs in-process, so a query costs microseconds. The reason 51 queries is fatal in production is not CPU, it is that each one is a network round trip: at 0.5 ms of LAN latency, 51 queries add 25 ms of pure waiting *before* any work happens, and a remote database at 1 ms puts it at 51 ms. The same rewrite on a networked Postgres moves p95 by an order of magnitude, not a factor of two.

What is honest to claim from this repo: **51 round trips became 1**. That number does not depend on the database being local.

---

## The bug

```python
rows = conn.execute("SELECT id, customer_id, total_cents, status FROM orders "
                    "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

for r in rows:                                    # 50 rows
    cust = conn.execute(                          # 50 more queries
        "SELECT name, tier FROM customers WHERE id = ?", (r[1],)
    ).fetchone()
```

Correct. Passes tests. Fine with ten rows in development. It was shipped, and it held until traffic hit roughly 150 concurrent users, at which point p99 went to four seconds and the graph looked like a cliff with no obvious cause.

The tell, if you know where to look, is that latency scaled with **page size** rather than with **data volume**. A 50-row page was slow and a 10-row page was fast, which no amount of index tuning explains.

## The fix

```sql
SELECT o.id, c.name, c.tier, o.total_cents, o.status
FROM orders o JOIN customers c ON c.id = o.customer_id
ORDER BY o.id DESC LIMIT ?
```

One query, one round trip. The join is on an indexed primary key, so the planner does 50 index lookups in memory instead of 50 trips across the wire.

### The cache, and why it is scoped the way it is

`TierCache` caches customer tier with a 30-second TTL. Tier changes rarely and is read on every row, so it is the one field worth caching.

It deliberately does **not** cache the order rows. Orders change constantly, and a stale order list is a support ticket with a refund attached. The distinction matters more than the cache: "add caching" applied indiscriminately is how you trade a slow system for a wrong one.

`test_parity.py` pins this down — `fast_list_orders` must return a payload `assertEqual` to what `slow_list_orders` returns, and the query-count test asserts 51 vs 1. If someone "optimises" the join into something that drops rows, the test fails before the deploy does.

---

## Layout

```
app/
  schema.py      DDL + seed (2k customers, 20k orders)
  repository.py  slow_list_orders / fast_list_orders / TierCache
  server.py      threaded stdlib HTTP layer, per-thread connections
bench/
  bench.py       concurrent load, p50/p95/p99, query counting
tests/
  test_parity.py payload equality, query counts, cache behaviour
```

The server is stdlib `ThreadingHTTPServer`, not FastAPI. That is a deliberate choice: the repo is about the query pattern, and a framework would add three dependencies while hiding the thing being measured.

Query counting uses SQLite's `set_trace_callback`. That is how the 51-vs-1 figure is produced rather than asserted — `bench.py` prints it at the top of every run.

---

## Applying it to a real codebase

The order of operations that actually works:

1. **Count queries per request first.** Django Debug Toolbar, SQLAlchemy event hooks, or `pg_stat_statements`. Without the count you are guessing, and guessing is why people "optimise" an endpoint that was already fine.
2. **Look for latency that scales with page size.** That is the N+1 signature. Latency that scales with total row count is a missing index and a different fix.
3. **Write the parity test before the rewrite.** Not after. It is the only thing that makes a 51x query reduction safe to merge.
4. **Cache only what is read-often and changes-rarely.** For this endpoint that is tier, not orders.

## Known limits

- **SQLite, not Postgres.** The 1.9x understates production impact for the reason above. This repo measures the query count reliably and the wall-clock gain only for an in-process database.
- **No connection pool.** One connection per thread, created lazily. At higher concurrency this needs a real pool and the numbers would shift.
- **Single machine.** No network latency simulated. Adding an artificial delay would make the numbers more representative but also more fabricated, so it is left out and explained instead.
