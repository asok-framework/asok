"""Regression: the admin "Activity Trend" chart showed no data on the daily
period.

created_at is stored ISO with a 'T' separator ('2026-07-24T13:...'); the chart
compared against '... 23:59:59' (space). Since 'T' (0x54) sorts after ' ' (0x20),
`created_at <= '... 23:59:59'` excluded same-day rows and every daily bucket was
0. Fixed by using date-only bounds with an exclusive next-day upper bound.
"""

import pytest

from asok import Asok, Field, Model
from asok.admin import Admin
from asok.orm import MODELS_REGISTRY


class Event(Model):
    __tablename__ = "events_chart"
    title = Field.String()
    created_at = Field.CreatedAt()


@pytest.fixture
def admin():
    app = Asok()
    app.config.update({"SECRET_KEY": "test-secret", "DEBUG": True})
    MODELS_REGISTRY["Event"] = Event
    Event.get_engine().execute("DROP TABLE IF EXISTS events_chart")
    Event.create_table()
    return Admin(app)


def test_daily_bucket_counts_today(admin):
    import datetime

    for _ in range(3):
        Event.create(_trust=True, title="x")
    today = datetime.date.today()
    assert Admin._count_model_records_for_day(Event, today, 0) == 3


def test_collect_chart_daily_data_not_empty(admin):
    for _ in range(2):
        Event.create(_trust=True, title="x")
    chart = admin._collect_model_list_chart(Event)
    assert chart is not None
    assert chart["daily_data"][-1] == 2  # today (last bucket)
    assert sum(chart["daily_data"]) == 2
    # Longer periods must agree.
    assert chart["weekly_data"][-1] == 2
    assert chart["monthly_data"][-1] == 2
    assert chart["yearly_data"][-1] == 2


def test_dashboard_activity_timeline_counts_today(admin):
    """The dashboard (/) activity chart had the same daily-zero bug as the list."""
    import datetime

    for _ in range(4):
        Event.create(_trust=True, title="x")
    today = datetime.date.today()
    assert Admin._count_log_activity_for_day(Event, today, 0) == 4

    _labels, daily = admin._collect_activity_timeline_data(Event)
    assert daily[-1] == 4  # today
    assert admin._collect_weekly_timeline_data(Event)[1][-1] == 4
    assert admin._collect_monthly_timeline_data(Event)[1][-1] == 4
    assert admin._collect_yearly_timeline_data(Event)[1][-1] == 4


def test_date_range_filter_upper_bound_is_exclusive_next_day(admin):
    """List date-range filter must include rows created on the 'to' day."""
    from asok.admin.core import Admin as A

    assert A._next_day_str("2026-07-24") == "2026-07-25"
    assert A._next_day_str("bad-date") is None
