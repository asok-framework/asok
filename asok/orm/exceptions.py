from __future__ import annotations

from typing import Optional

from ..exceptions import AsokException


class ModelError(AsokException):
    """Raised on ORM save/create errors with a user-friendly message."""

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        original: Optional[Exception] = None,
    ):
        self.field: Optional[str] = field
        self.original: Optional[Exception] = original
        super().__init__(message)
