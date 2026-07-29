from __future__ import annotations

from typing import Any


class Undefined:
    """Sentinel class representing an undefined variable in a template.

    When strict mode is enabled (STRICT_VARIABLES=True), converting an Undefined
    object to a string raises a TemplateError, preventing silent empty renders.
    Filters like `default` and tests like `is defined` handle Undefined objects
    gracefully without error in both modes.
    """

    __slots__ = ("_name", "_strict")

    def __init__(self, name: str = "", strict: bool = False):
        self._name = name
        self._strict = strict

    def __str__(self) -> str:
        if self._strict:
            from asok.exceptions import TemplateError

            name_str = f" '{self._name}'" if self._name else ""
            raise TemplateError(f"Undefined variable{name_str} in template context")
        return ""

    def __repr__(self) -> str:
        return f"<Undefined '{self._name}'>"

    def __bool__(self) -> bool:
        return False

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Undefined):
            return True
        return False

    def __len__(self) -> int:
        return 0

    def __iter__(self):
        return iter(())

    def __getattr__(self, item: str) -> Any:
        if item.startswith("_"):
            raise AttributeError(item)
        child_name = f"{self._name}.{item}" if self._name else item
        return Undefined(child_name, strict=self._strict)

    def __getitem__(self, item: Any) -> Any:
        child_name = f"{self._name}[{item!r}]" if self._name else str(item)
        return Undefined(child_name, strict=self._strict)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self
