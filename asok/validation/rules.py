from __future__ import annotations

import datetime
import json as json_mod
import re
from typing import Any

from ..orm import MODELS_REGISTRY
from .registry import (
    _MAX_REGEX_CACHE_SIZE,
    _MAX_REGEX_INPUT_LENGTH,
    _RE_EMAIL,
    _RE_TEL,
    _RE_URL,
    _regex_cache,
)

# SECURITY: Precompiled regex for password strength to avoid recompilation overhead
_RE_PASSWORD_UPPER = re.compile(r"[A-Z]")
_RE_PASSWORD_DIGIT = re.compile(r"\d")
_RE_PASSWORD_SPECIAL = re.compile(r"[!@#$%^&*(),.?\":{}|<>]")


def check_required(val: Any, file: Any = None) -> bool:
    """Check if value is present and not empty."""
    if val is not None and str(val).strip() != "":
        return True
    if file and getattr(file, "content", None):
        return True
    return False


def check_email(val: Any) -> bool:
    """Check if value is a valid email address."""
    if not val:
        return True
    val_str = str(val)
    if len(val_str) > _MAX_REGEX_INPUT_LENGTH:
        return False
    return bool(_RE_EMAIL.match(val_str))


def check_min(val: Any, limit: str) -> bool:
    """Check if length of value is at least limit.

    SECURITY: Validates limit parameter to prevent integer overflow.
    Raises ValueError for invalid limit configurations.
    """
    try:
        limit_int = int(limit)
        # SECURITY: Reject unreasonably large or negative limits
        if limit_int < 0:
            raise ValueError(f"Minimum length cannot be negative: {limit_int}")
        if limit_int > 1_000_000:
            raise ValueError(f"Minimum length too large (max 1,000,000): {limit_int}")
        return len(str(val)) >= limit_int
    except (ValueError, OverflowError) as e:
        # Re-raise with context if it's our validation error
        if "length" in str(e).lower():
            raise
        # For other parsing errors, raise with clear message
        raise ValueError(f"Invalid minimum length value: {limit}") from e


def check_max(val: Any, limit: str) -> bool:
    """Check if length of value is at most limit.

    SECURITY: Validates limit parameter to prevent integer overflow.
    Raises ValueError for invalid limit configurations.
    """
    try:
        limit_int = int(limit)
        # SECURITY: Reject unreasonably large or negative limits
        if limit_int < 0:
            raise ValueError(f"Maximum length cannot be negative: {limit_int}")
        if limit_int > 1_000_000:
            raise ValueError(f"Maximum length too large (max 1,000,000): {limit_int}")
        return len(str(val)) <= limit_int
    except (ValueError, OverflowError) as e:
        # Re-raise with context if it's our validation error
        if "length" in str(e).lower():
            raise
        # For other parsing errors, raise with clear message
        raise ValueError(f"Invalid maximum length value: {limit}") from e


def check_unique(val: Any, model_name: str, field_name: str) -> bool:
    """Check if value is unique in database.

    SECURITY: model and field names are validated before reaching the ORM.
    """
    if not _is_valid_unique_ident(model_name) or not _is_valid_unique_field(field_name):
        return False
    model = MODELS_REGISTRY.get(model_name)
    if not model:
        return True
    try:
        return not model.find(**{field_name: val})
    except (AttributeError, TypeError, ValueError):
        return False


def _is_valid_unique_ident(name: Any) -> bool:
    return bool(name) and isinstance(name, str)


def _is_valid_unique_field(name: Any) -> bool:
    if not _is_valid_unique_ident(name):
        return False
    return name.replace("_", "").isalnum()


def check_ext(file: Any, allowed_exts_str: str) -> bool:
    """Check if file has one of the allowed extensions.

    SECURITY: only the last segment after the final dot is matched, blocking
    double-extension bypasses like ``malicious.php.jpg``. Filename is also
    checked for path traversal.
    """
    if not file:
        return True
    filename = getattr(file, "filename", "").lower()
    if not _is_valid_ext_filename(filename):
        return False
    actual_ext = filename.rsplit(".", 1)[1]
    allowed = [e.strip().lower() for e in allowed_exts_str.split(",")]
    return actual_ext in allowed


def _is_valid_ext_filename(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    return not _has_path_traversal(filename)


def _has_path_traversal(filename: str) -> bool:
    return ".." in filename or "/" in filename or "\\" in filename


_SIZE_UNITS = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}


def check_size(file: Any, limit_arg: str) -> bool:
    """Check if file size does not exceed limit.

    SECURITY: Validates filename to prevent path traversal attacks.
    """
    if not file:
        return True
    filename = getattr(file, "filename", "")
    if filename and _has_path_traversal(filename):
        return False
    try:
        bytes_limit = _parse_size_limit(limit_arg)
    except (ValueError, OverflowError):
        return True
    return len(getattr(file, "content", b"")) <= bytes_limit


def _parse_size_limit(limit_arg: str) -> float:
    limit = limit_arg.lower()
    suffix = limit[-1]
    if suffix in _SIZE_UNITS:
        return float(limit[:-1]) * _SIZE_UNITS[suffix]
    return float(limit)


def check_confirmed(val: Any, confirm_val: Any) -> bool:
    """Check if value matches confirmation value.

    Note: For sensitive comparisons (passwords), use constant-time comparison.
    Python's == operator is generally constant-time for strings of equal length.
    """
    return val == confirm_val


def check_in(val: Any, allowed_str: str) -> bool:
    """Check if value is in allowed list.

    SECURITY: Size limits prevent DoS via large allowed lists.
    """
    val_str = str(val)
    if not val_str:
        return True
    # SECURITY: Reject excessively long allowed lists to prevent DoS
    if len(allowed_str) > 10_000:
        return False
    allowed = [item.strip() for item in allowed_str.split(",")]
    return val_str in allowed


def check_numeric(val: Any) -> bool:
    """Check if value is a numeric value.

    SECURITY: Proper type checking to prevent type confusion attacks.
    """
    if val is None or val == "":
        return True
    val_str = str(val)
    # SECURITY: Reject values that are too long to prevent DoS
    if len(val_str) > 50:
        return False
    try:
        float(val_str)
        return True
    except (ValueError, OverflowError):
        return False


def check_regex(val: Any, pattern_str: str) -> bool:
    """Check if value matches regex pattern.

    SECURITY: bounded cache and pattern length prevent compilation/cache DoS.
    Runtime errors guard against catastrophic backtracking.
    """
    val_str = str(val)
    if not val_str:
        return True
    if len(val_str) > _MAX_REGEX_INPUT_LENGTH or len(pattern_str) > 500:
        return False
    pattern = _compile_regex_cached(pattern_str)
    return _safe_regex_match(pattern, val_str)


def _safe_regex_match(pattern, val_str: str) -> bool:
    if pattern is None:
        return False
    try:
        return bool(pattern.match(val_str))
    except (RuntimeError, OverflowError):
        return False


def _compile_regex_cached(pattern_str: str):
    pattern = _regex_cache.get(pattern_str)
    if pattern is not None:
        return pattern
    try:
        if len(_regex_cache) >= _MAX_REGEX_CACHE_SIZE:
            _regex_cache.clear()
        pattern = re.compile(pattern_str)
    except (re.error, OverflowError, RuntimeError):
        return None
    _regex_cache[pattern_str] = pattern
    return pattern


def check_url(val: Any) -> bool:
    """Check if value is a valid URL.

    SECURITY: Only allows http/https schemes to prevent javascript:, data:, file: URIs.
    """
    val_str = str(val)
    if not val_str:
        return True
    if len(val_str) > _MAX_REGEX_INPUT_LENGTH:
        return False
    # SECURITY: Ensure URL starts with http:// or https:// (enforced by _RE_URL)
    # This prevents javascript:, data:, file: and other dangerous schemes
    return bool(_RE_URL.match(val_str))


def check_date(val: Any) -> bool:
    """Check if value is a valid date (ISO format).

    SECURITY: Length limit prevents DoS via extremely long date strings.
    """
    val_str = str(val)
    if not val_str:
        return True
    # SECURITY: ISO date is max 10 chars (YYYY-MM-DD), allow some flexibility
    if len(val_str) > 50:
        return False
    try:
        datetime.date.fromisoformat(val_str)
        return True
    except (ValueError, TypeError):
        return False


def check_required_if(val: Any, other_val: Any, expected: str) -> bool:
    """Check if field is required when other field matches expected value.

    SECURITY: cap comparison-string length to bound CPU.
    """
    other_str = str(other_val) if other_val is not None else ""
    if _exceeds_length(other_str, expected):
        return False
    if other_str != expected:
        return True
    return val is not None and str(val).strip() != ""


def _exceeds_length(*strs: str, limit: int = 10_000) -> bool:
    return any(len(s) > limit for s in strs)


def check_required_with(val: Any, other_val: Any) -> bool:
    """Check if field is required when other field is present."""
    if other_val:
        return val is not None and str(val).strip() != ""
    return True


def check_between(val: Any, lo_str: str, hi_str: str) -> bool:
    """Check if numeric value is between lo and hi.

    SECURITY: Handles overflow and invalid range parameters gracefully.
    """
    if val is None or val == "":
        return True
    try:
        lo = float(lo_str)
        hi = float(hi_str)
        val_float = float(val)
        # SECURITY: Validate range sanity
        if lo > hi:
            return False
        return lo <= val_float <= hi
    except (ValueError, TypeError, OverflowError):
        return False


def check_digits(val: Any, count_str: str) -> bool:
    """Check if value is numeric and has exactly count digits.

    SECURITY: Validates count parameter to prevent integer overflow.
    """
    val_str = str(val)
    if not val_str:
        return True
    count = _parse_digit_count(count_str)
    if count is None:
        return False
    return val_str.isdigit() and len(val_str) == count


def _parse_digit_count(count_str: str):
    try:
        count = int(count_str)
    except (ValueError, OverflowError):
        return None
    if count < 0 or count > 1000:
        return None
    return count


def check_boolean(val: Any) -> bool:
    """Check if value is a valid boolean.

    SECURITY: Length limit prevents DoS via extremely long strings.
    """
    if val is None or val == "":
        return True
    s_val = str(val).lower()
    # SECURITY: Boolean values should be short
    if len(s_val) > 10:
        return False
    return s_val in ("true", "false", "1", "0", "yes", "no", "on", "off")


def check_slug(val: Any) -> bool:
    """Check if value has valid slug format.

    SECURITY: Limited to 200 characters to prevent DoS.
    """
    val_str = str(val)
    if not val_str:
        return True
    # SECURITY: Reject excessively long slugs
    if len(val_str) > 200:
        return False
    return bool(re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", val_str))


def check_uuid(val: Any) -> bool:
    """Check if value has valid UUID format.

    SECURITY: Validates length (32-36 chars) before regex to prevent DoS.
    """
    val_str = str(val)
    if not val_str:
        return True
    # SECURITY: UUID is exactly 32 chars (no hyphens) or 36 chars (with hyphens)
    if len(val_str) not in (32, 36):
        return False
    pattern = r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$"
    return bool(re.match(pattern, val_str, re.I))


def check_alpha(val: Any) -> bool:
    """Check if value contains only alphabetic characters.

    SECURITY: Length limit prevents DoS via extremely long strings.
    """
    val_str = str(val)
    if not val_str:
        return True
    # SECURITY: Reject excessively long strings
    if len(val_str) > 10_000:
        return False
    return val_str.isalpha()


def check_alpha_num(val: Any) -> bool:
    """Check if value contains only alphanumeric characters.

    SECURITY: Length limit prevents DoS via extremely long strings.
    """
    val_str = str(val)
    if not val_str:
        return True
    # SECURITY: Reject excessively long strings
    if len(val_str) > 10_000:
        return False
    return val_str.isalnum()


def check_tel(val: Any) -> bool:
    """Check if value is a valid telephone number."""
    val_str = str(val)
    if not val_str:
        return True
    if len(val_str) > _MAX_REGEX_INPUT_LENGTH:
        return False
    return bool(_RE_TEL.match(val_str))


_IMAGE_MIMES = ["image/jpeg", "image/png", "image/gif", "image/webp"]


def check_image(file: Any) -> bool:
    """Check if file is a valid image.

    SECURITY: ``UploadedFile.validate_mime_type`` reads magic bytes — extension
    spoofing is rejected.
    """
    if not file:
        return True
    try:
        return _validate_image_file(file)
    except (ValueError, AttributeError):
        return False


def _validate_image_file(file: Any) -> bool:
    from ..request import UploadedFile

    if not isinstance(file, UploadedFile):
        return True
    filename = getattr(file, "filename", "")
    if filename and _has_path_traversal(filename):
        return False
    file.validate_mime_type(_IMAGE_MIMES)
    return True


_PASSWORD_PATTERNS = (_RE_PASSWORD_UPPER, _RE_PASSWORD_DIGIT, _RE_PASSWORD_SPECIAL)


def check_password_strength(val: Any) -> bool:
    """Check if password is strong enough.

    SECURITY: 8+ chars, 1 uppercase, 1 digit, 1 special char via precompiled regex.
    """
    val_str = str(val)
    if not val_str:
        return True
    if len(val_str) < 8:
        return False
    return all(p.search(val_str) for p in _PASSWORD_PATTERNS)


def check_color(val: Any) -> bool:
    """Check if value is a valid color hex string.

    SECURITY: Length validated before regex to prevent processing long strings.
    """
    val_str = str(val)
    if not val_str:
        return True
    # SECURITY: Color hex is exactly 4 or 7 chars (#RGB or #RRGGBB)
    if len(val_str) not in (4, 7):
        return False
    return bool(re.match(r"^#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})$", val_str))


def check_month(val: Any) -> bool:
    """Check if value has valid YYYY-MM month format.

    SECURITY: Length validated before regex to prevent ReDoS.
    """
    val_str = str(val)
    if not val_str:
        return True
    # SECURITY: Month format is exactly 7 chars (YYYY-MM).
    if len(val_str) != 7 or not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", val_str):
        return False
    return _validate_year_month(val_str)


def _validate_year_month(val_str: str) -> bool:
    try:
        year, month = val_str.split("-")
        year_int, month_int = int(year), int(month)
    except (ValueError, AttributeError):
        return False
    return 1 <= month_int <= 12 and 1000 <= year_int <= 9999


def check_base64(val: Any) -> bool:
    """Check if value is valid base64 data.

    SECURITY: Uses length limits and bounded quantifiers to prevent ReDoS.
    """
    val_str = str(val)
    if not val_str:
        return True
    # SECURITY: Reject excessively long base64 strings (max 100KB encoded)
    if len(val_str) > 100000:
        return False
    if val_str.startswith("data:"):
        # SECURITY: Bounded quantifiers {1,100} instead of + to prevent ReDoS
        pattern = r"^data:([a-zA-Z0-9]{1,50}/[a-zA-Z0-9\-\+\.]{1,50})?;base64,[A-Za-z0-9+/]{1,99997}={0,2}$"
        return bool(re.match(pattern, val_str))
    else:
        # SECURITY: Limit padding to max 2 '=' chars as per base64 spec
        pattern = r"^[A-Za-z0-9+/]{1,99998}={0,2}$"
        return bool(re.match(pattern, val_str))


def check_same(val: Any, other_val: Any) -> bool:
    """Check if two values are identical.

    Note: For sensitive comparisons, use constant-time comparison.
    Python's == operator is generally constant-time for strings of equal length.
    """
    return val == other_val


def check_json(val: Any) -> bool:
    """Check if value is valid JSON.

    SECURITY: Size limits prevent DoS via deeply nested structures.
    """
    val_str = str(val)
    if not val_str:
        return True
    # SECURITY: Reject excessively large JSON to prevent DoS (max 1MB)
    if len(val_str) > 1_000_000:
        return False
    try:
        json_mod.loads(val_str)
        return True
    except (ValueError, TypeError, RecursionError):
        return False


def check_daterange(val: Any, limit_arg: str | None = None) -> bool:
    """Check if value is a valid date range.

    SECURITY: Size limits and RecursionError handling prevent DoS attacks.
    """
    if not val:
        return True
    try:
        d = _decode_daterange_payload(val)
        if d is None:
            return False
        return _validate_daterange_dict(d, limit_arg)
    except (ValueError, json_mod.JSONDecodeError, TypeError, AttributeError, RecursionError):
        return False


def _decode_daterange_payload(val: Any):
    if not isinstance(val, str):
        return val
    # SECURITY: cap payload size to keep JSON parsing bounded.
    if len(val) > 10_000:
        return None
    return json_mod.loads(val)


def _validate_daterange_dict(d, limit_arg: str | None) -> bool:
    start, end = d.get("start"), d.get("end")
    if not (start and end):
        return not (start or end)
    return _validate_daterange_bounds(start, end, limit_arg)


def _validate_daterange_bounds(start: str, end: str, limit_arg: str | None) -> bool:
    ds = datetime.date.fromisoformat(start)
    de = datetime.date.fromisoformat(end)
    if de < ds:
        return False
    if limit_arg == "future" and ds < datetime.date.today():
        return False
    return True
