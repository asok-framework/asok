from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union


class Response:
    """Standard HTTP response container for Asok applications."""

    def __init__(
        self,
        body: Any = "",
        status_code: int = 200,
        headers: Optional[Union[Dict[str, str], List[Tuple[str, str]]]] = None,
        content_type: str = "text/html; charset=utf-8",
    ) -> None:
        self.body = body
        self.status_code = status_code
        self.content_type = content_type
        if headers is None:
            self.headers: Dict[str, str] = {}
        elif isinstance(headers, dict):
            self.headers = dict(headers)
        elif isinstance(headers, (list, tuple)):
            self.headers = dict(headers)
        else:
            self.headers = {}

    def __str__(self) -> str:
        return str(self.body)

    def __bytes__(self) -> bytes:
        if isinstance(self.body, bytes):
            return self.body
        return str(self.body).encode("utf-8")
