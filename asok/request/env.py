from __future__ import annotations

import os
from typing import Any


class _Env:
    """Helper to access environment variables with automatic type casting."""

    @staticmethod
    def _cast(val: Any) -> Any:
        if isinstance(val, str):
            lv = val.lower()
            if lv == "true":
                return True
            if lv == "false":
                return False
            if lv == "null":
                return None
        return val

    def __call__(self, key: str, default: Any = None) -> Any:
        """Get env var as property: request.env('KEY', default)."""
        val = os.environ.get(key)
        return self._cast(val) if val is not None else default

    def __getitem__(self, key: str) -> Any:
        """Get env var as index: request.env['KEY']. Raises KeyError if missing."""
        val = os.environ.get(key)
        if val is None:
            raise KeyError(key)
        return self._cast(val)

    def get(self, key: str, default: Any = None) -> Any:
        """Alias for calling the object."""
        return self(key, default)
