from __future__ import annotations

from typing import Optional


class QueryDict(dict):
    """A dict that also keeps all values for repeated keys (e.g. ``?a=1&a=2``)."""

    def __init__(self, pairs: list[tuple[str, str]]):
        super().__init__(pairs)
        self._lists: dict[str, list[str]] = {}
        for k, v in pairs:
            self._lists.setdefault(k, []).append(v)

    def getlist(self, key: str, default: Optional[list[str]] = None) -> list[str]:
        """Return all values for *key*, or *default* if absent."""
        return self._lists.get(key, default if default is not None else [])
