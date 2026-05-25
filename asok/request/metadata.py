from __future__ import annotations

from typing import Any, Optional


class Metadata:
    """SEO and social metadata manager. Accessible via request.meta."""

    def __init__(self):
        self._title: Optional[str] = None
        self._description: Optional[str] = None
        self._items: list[tuple[str, str, str, dict[str, Any]]] = []

    def title(self, val: str) -> str:
        """Set page title."""
        self._title = val
        return ""

    def description(self, val: str) -> str:
        """Set meta description."""
        self._description = val
        return ""

    def name(self, name: str, content: str, **kwargs: Any) -> str:
        """Add a <meta name="..." content="..."> tag."""
        self._items = [i for i in self._items if not (i[0] == "name" and i[1] == name)]
        self._items.append(("name", name, content, kwargs))
        return ""

    def property(self, prop: str, content: str, **kwargs: Any) -> str:
        """Add a <meta property="..." content="..."> tag (OpenGraph)."""
        self._items = [
            i for i in self._items if not (i[0] == "property" and i[1] == prop)
        ]
        self._items.append(("property", prop, content, kwargs))
        return ""

    def link(self, rel: str, href: str, **kwargs: Any) -> str:
        """Add a <link rel="..." href="..."> tag."""
        self._items.append(("link", rel, href, kwargs))
        return ""

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "title" and not callable(value):
            self._title = value
        elif name == "description" and not callable(value):
            self._description = value
        else:
            super().__setattr__(name, value)

    def __str__(self) -> str:
        return ""
