#!/usr/bin/env python3
"""Load test both endpoints and report the latency distribution.

Reports p50/p95/p99 rather than the mean. A mean of 40ms sounds fine until you
see p99 at 900ms — which is the number the user who waited four seconds
actually experienced.
"""
from __future__ import annotations

import argparse
import http.client
import statistics
import threading
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.server import serve  # noqa: E402


def _worker(port: int, path: str, requests: int, out: list[float]) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        for _ in range(requests):
            t0 = time.perf_counter()
            conn.request("GET", path)
            resp = conn.getresponse()
            resp.read()
            out.append((time.perf_counter() - t0) * 1000)
    finally:
        conn.close()


def run(path: str, port: int, threads: int, per_thread: int) -> dict:
    lat: list[float] = []
    workers = [threading.Thread(target=_worker, args=(port, path, per_thread, lat))
               for _ in range(threads)]
    start = time.perf_counter()
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    elapsed = time.perf_counter() - start
    lat.sort()

    def pct(p: float) -> float:
        idx = min(int(len(lat) * p), len(lat) - 1)
        return lat[idx]

    return {
        "path": path,
        "requests": len(lat),
        "qps": round(len(lat) / elapsed, 1),
        "p50": round(pct(0.50), 1),
        "p95": round(pct(0.95), 1),
        "p99": round(pct(0.99), 1),
        "mean": round(statistics.fmean(lat), 1),
        "max": round(lat[-1], 1),
    }


def query_counts(port: int) -> tuple[int, int]:
    counts = []
    for path in ("/orders/counted/slow", "/orders/counted/fast"):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        conn.request("GET", path)
        body = conn.getresponse().read().decode()
        conn.close()
        counts.append(int(body.split('"queries": ')[1].split("}")[0]))
    return counts[0], counts[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--requests", type=int, default=60, help="per thread")
    args = ap.parse_args()

    srv = serve(0)
    port = srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.4)

    slow_q, fast_q = query_counts(port)
    print(f"queries per request : slow={slow_q}  fast={fast_q}")
    print(f"load: {args.threads} threads x {args.requests} requests\n")

    rows = [
        run("/orders/slow", port, args.threads, args.requests),
        run("/orders/fast", port, args.threads, args.requests),
    ]

    hdr = f"{'endpoint':14s} {'reqs':>6s} {'qps':>8s} {'p50':>7s} {'p95':>7s} {'p99':>7s} {'max':>7s}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['path']:14s} {r['requests']:>6d} {r['qps']:>8.1f} "
              f"{r['p50']:>7.1f} {r['p95']:>7.1f} {r['p99']:>7.1f} {r['max']:>7.1f}")

    speedup = rows[0]["p95"] / rows[1]["p95"] if rows[1]["p95"] else 0
    print(f"\np95 speedup: {speedup:.1f}x")
    srv.shutdown()


if __name__ == "__main__":
    main()
