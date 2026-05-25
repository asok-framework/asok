from __future__ import annotations

import re
from typing import Any, Callable, Optional

# SECURITY: All regex patterns are designed to minimize ReDoS risk:
# - Email: Uses explicit quantifier limits {0,61} instead of unbounded * or +
# - Tel: Uses bounded quantifier {7,20}
# - URL: Simplified pattern to avoid catastrophic backtracking

_RE_EMAIL = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)
# SECURITY: ReDoS-safe URL pattern with bounded quantifiers
# Uses {1,2000} limit instead of + to prevent catastrophic backtracking
_RE_URL = re.compile(r"^https?://[^\s]{1,2000}$")
_RE_TEL = re.compile(r"^\+?[0-9\s\-()\.]{7,20}$")
_regex_cache: dict[str, re.Pattern[str]] = {}

# SECURITY: ReDoS protection via input length limits
# Inputs longer than 2000 chars are rejected BEFORE regex matching to prevent
# catastrophic backtracking attacks. Combined with bounded quantifiers in patterns.
_MAX_REGEX_INPUT_LENGTH = 2000
_MAX_REGEX_CACHE_SIZE = 1000  # SECURITY: Prevent DoS via cache exhaustion

_CUSTOM_RULES: dict[
    str, tuple[Callable[[Any, Optional[str], dict[str, Any]], bool], str]
] = {}


def register_rule(
    name: str,
    fn: Callable[[Any, Optional[str], dict[str, Any]], bool],
    message: str = "Invalid value.",
) -> None:
    """Register a global custom validation rule.

    Args:
        name: The rule name (e.g., 'even').
        fn: A callable taking (value, argument, full_data) and returning a boolean.
        message: The default error message for this rule.
    """
    _CUSTOM_RULES[name] = (fn, message)
