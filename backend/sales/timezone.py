"""JAZ Sales - the ONE timezone in which a calendar DATE is read.

Every stored timestamp is UTC (timestamptz) and stays that way. What is decided here is only what a calendar DATE means when a
user says "today", picks "this week", types a `created_from=2026-09-25` filter or reads a day of a chart: the day that started at
midnight in Baghdad, UTC+3 (the platform's home timezone). Before this module those readings used UTC midnight, so between
00:00 and 03:00 in Baghdad "today" was still yesterday - and the dashboard, the lists and the users' own clocks disagreed.

A FIXED offset, not a timezone database: Iraq abolished daylight saving time in 2008, so UTC+3 is exact for every date the
application can hold, and nothing here depends on tzdata being installed in the container. To move the application to another
zone, change APP_UTC_OFFSET (one line) - every Sales date boundary follows.

Used by: sales/metrics.py (the dashboard / report periods), repositories/work.py and repositories/leads.py (the date filters of
the lists), repositories/reports.py (the days of the conversion trend) and the routes' "today". The frontend mirrors the offset
in utils/salesReports.js (APP_UTC_OFFSET_HOURS).
"""
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

APP_TZ_NAME = "Asia/Baghdad"
APP_UTC_OFFSET = timedelta(hours=3)
APP_TZ = timezone(APP_UTC_OFFSET, APP_TZ_NAME)
APP_UTC_OFFSET_HOURS = int(APP_UTC_OFFSET.total_seconds() // 3600)     # for SQL: `+ interval '<n> hours'`


def app_today(now: Optional[datetime] = None) -> date:
    """The calendar date it is right now in the application timezone (`now` is for tests: any aware datetime)."""
    return (now or datetime.now(timezone.utc)).astimezone(APP_TZ).date()


def day_start(day: date) -> datetime:
    """The instant `day` STARTS in the application timezone, as an aware datetime (the driver converts it to UTC). A period
    of dates [a, b] is the half-open interval [day_start(a), day_start(b + 1 day))."""
    return datetime.combine(day, time.min, tzinfo=APP_TZ)
