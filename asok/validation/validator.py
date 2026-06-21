from __future__ import annotations

from typing import Any, Callable, Optional, Union

from ._rule_dispatch import dispatch_rule
from .interpolation import _DEFAULT_MESSAGES, _interpolate
from .registry import _CUSTOM_RULES


class Validator:
    """Engine for validating dictionaries and uploaded files against a set of rules."""

    def __init__(
        self,
        data: dict[str, Any],
        files: Optional[dict[str, Any]] = None,
        translate: Optional[Callable[[str], str]] = None,
    ):
        """Initialize the validator with data to check."""
        self.data = data
        self.files = files or {}
        self.errors: dict[str, str] = {}
        self._t = translate

    def _msg(
        self,
        rule_name: str,
        messages: dict[str, str],
        arg: Optional[Any] = None,
        field: Optional[str] = None,
    ) -> str:
        """Resolve and format the error message for a specific rule failure."""
        if rule_name in messages:
            text = messages[rule_name]
        else:
            default = _DEFAULT_MESSAGES.get(rule_name, "Invalid value.")
            text = self._t(f"v_{rule_name}") if self._t else default
            # If translation key not found, _t returns the key itself — fall back to default
            if text == f"v_{rule_name}":
                text = default
        # Interpolate placeholders with contextual values
        text = _interpolate(text, field, rule_name, arg)
        return text

    def rule(
        self, field: str, rules: str, messages: Optional[dict[str, str]] = None
    ) -> bool:
        """Apply a set of rules (piped string) to a single field."""
        messages = messages or {}
        for r in rules.split("|") if "|" in rules else [rules]:
            self._apply_rule(field, r, messages)
        return len(self.errors) == 0

    def _apply_rule(self, field: str, r: str, messages: dict) -> None:
        name, arg = self._parse_rule(r)
        if dispatch_rule(self, field, name, arg, messages):
            return
        if name in _CUSTOM_RULES:
            self._apply_custom_rule(field, name, arg, messages)

    @staticmethod
    def _parse_rule(r: str) -> tuple[str, Optional[str]]:
        parts = r.split(":")
        return parts[0], (parts[1] if len(parts) > 1 else None)

    def _apply_custom_rule(self, field: str, name: str, arg, messages: dict) -> None:
        fn, default_msg = _CUSTOM_RULES[name]
        val = self.data.get(field)
        try:
            ok = fn(val, arg, self.data)
        except Exception:
            ok = False
        if not ok:
            self.errors[field] = messages.get(name, default_msg)

    def rules(self, schema: dict[str, Union[str, tuple[str, dict[str, str]]]]) -> bool:
        """Apply a full schema of rules to the current data."""
        for field, value in schema.items():
            if isinstance(value, tuple):
                rules, messages = value
                self.rule(field, rules, messages)
            else:
                self.rule(field, value)
        return len(self.errors) == 0

    def validate(self) -> bool:
        """Check if any errors were encountered during validation."""
        return len(self.errors) == 0
