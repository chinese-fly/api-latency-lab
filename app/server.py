"""Minimal HTTP layer.

Threaded stdlib server on purpose: the point of this repo is the query pattern,
not the web framework. Wrapping it in FastAPI would add three dependencies and
hide the thing being measured.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .repository import TierCache, fast_list_orders, slow_list_orders
from .schema import seed

DB_PATH = Path(__file__).parent.parent / "orders.db"

_local = threading.local()
_cache = TierCache()
_query_log: list[str] = []
_log_lock = threading.Lock()


def connection() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    return conn


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        conn = connection()
        counted = self.path.startswith("/orders/counted")
        if counted:
            conn.set_trace_callback(self._record)

        try:
            if self.path.startswith("/orders/slow"):
                payload = slow_list_orders(conn)
                mode = "slow"
            elif self.path.startswith("/orders/fast"):
                payload = fast_list_orders(conn, _cache)
                mode = "fast"
            elif counted:
                slow_list_orders(conn) if "slow" in self.path else fast_list_orders(conn, _cache)
                mode = "counted"
                payload = {"queries": self._drain()}
            elif self.path == "/health":
                payload, mode = {"ok": True}, "health"
            else:
                self.send_error(404, "not found")
                return
        finally:
            if counted:
                conn.set_trace_callback(None)

        body = json.dumps({"mode": mode, "count": len(payload) if isinstance(payload, list) else None,
                           "data": payload}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self, statement: str) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            with _log_lock:
                _query_log.append(statement)

    def _drain(self) -> int:
        with _log_lock:
            n = len(_query_log)
            _query_log.clear()
        return n

    def log_message(self, *args) -> None:
        pass


def serve(port: int = 0) -> ThreadingHTTPServer:
    if not DB_PATH.exists():
        conn = sqlite3.connect(DB_PATH)
        seed(conn)
        conn.close()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    return httpd


if __name__ == "__main__":
    srv = serve(8000)
    print(f"listening on http://127.0.0.1:{srv.server_port}")
    srv.serve_forever()
