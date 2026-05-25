from __future__ import annotations

import time


class ConnectionProxy:
    """A wrapper for SQLite connections that logs execution time and queries for the Developer Toolbar."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def execute(self, sql, parameters=()):
        """Execute a SQL statement and log its performance to the current request context."""
        from ..context import request_var

        req = request_var.get()
        if not req:
            return self._conn.execute(sql, parameters)

        if not hasattr(req, "_asok_sql_log"):
            req._asok_sql_log = []

        start = time.time()
        try:
            return self._conn.execute(sql, parameters)
        finally:
            duration = (time.time() - start) * 1000
            req._asok_sql_log.append(
                {"sql": sql, "params": parameters, "duration": duration}
            )

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._conn.__exit__(exc_type, exc_val, exc_tb)
