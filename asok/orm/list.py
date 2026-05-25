from __future__ import annotations

from .utils import interpolate_sql


class ModelList(list):
    """List subclass that supports .count() without arguments and SQL inspection."""

    def __init__(self, iterable=None, sql: str = None, args: list = None):
        super().__init__(iterable or [])
        self._sql = sql
        self._args = args or []

    def count(self, value=None):
        """Return the number of items in the list.

        If 'value' is provided, returns the number of occurrences of 'value'.
        """
        if value is not None:
            return super().count(value)
        return len(self)

    def to_sql(self) -> str:
        """Return the SQL query string with placeholders."""
        return self._sql or ""

    def raw_sql(self) -> str:
        """Return the SQL query with parameters interpolated (for debugging only)."""
        return interpolate_sql(self._sql, self._args)
