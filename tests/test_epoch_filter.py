"""Tests for the `epoch` template filter used by the admin to show Unix
timestamp integer fields (last_seen, revoked_at, ...) as human dates."""

import datetime

from asok.templates.filters import _epoch_filter


def test_formats_unix_epoch_integer():
    assert _epoch_filter(1784888169, "%Y-%m-%d") == "2026-07-24"


def test_small_integers_are_left_untouched():
    # ids / user_ids must never be mistaken for timestamps.
    assert _epoch_filter(5) == 5
    assert _epoch_filter(123) == 123
    assert _epoch_filter(999_999_999) == 999_999_999  # just below the epoch window


def test_none_and_empty_passthrough():
    assert _epoch_filter(None) is None
    assert _epoch_filter("") == ""


def test_datetime_and_iso_string_are_formatted():
    assert _epoch_filter(datetime.datetime(2026, 7, 24, 10, 0), "%Y-%m-%d %H:%M") == (
        "2026-07-24 10:00"
    )
    assert _epoch_filter("2026-07-24T10:00:00", "%Y-%m-%d") == "2026-07-24"


def test_non_numeric_string_passthrough():
    assert _epoch_filter("hello") == "hello"


def test_numeric_epoch_string_is_formatted():
    assert _epoch_filter("1784888169", "%Y-%m-%d") == "2026-07-24"
