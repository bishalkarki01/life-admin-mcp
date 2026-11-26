from datetime import date

import pytest

from life_admin_mcp.dates import add_months, advance, next_on_or_after, parse_date
from life_admin_mcp.ics import Event, build_calendar, escape, fold


@pytest.mark.parametrize("bad", ["2026-13-01", "2026-02-30", "15/01/2026", "2026-1-5", "", "tomorrow"])
def test_parse_date_rejects_bad_input(bad):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_date(bad)


def test_month_end_clamps_then_recovers_with_anchor():
    jan31 = date(2026, 1, 31)
    feb = advance(jan31, "monthly", anchor_day=31)
    assert feb == date(2026, 2, 28)
    assert advance(feb, "monthly", anchor_day=31) == date(2026, 3, 31)


def test_leap_day_yearly():
    assert advance(date(2028, 2, 29), "yearly", anchor_day=29) == date(2029, 2, 28)
    assert add_months(date(2029, 2, 28), 12, anchor_day=29) == date(2030, 2, 28)


def test_next_on_or_after_skips_many_cycles():
    assert next_on_or_after(date(2020, 1, 10), "monthly", date(2026, 1, 15), 10) == date(2026, 2, 10)
    assert next_on_or_after(date(2020, 1, 10), "once", date(2026, 1, 15)) == date(2020, 1, 10)


def test_ics_escaping_and_folding():
    assert escape("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"
    long = "SUMMARY:" + "é" * 100
    folded = fold(long)
    for part in folded.split("\r\n"):
        assert len(part.encode()) <= 75
    assert folded.replace("\r\n ", "") == long


def test_calendar_has_required_fields():
    cal = build_calendar([Event(uid="x@y", day=date(2026, 2, 1), summary="Rent, flat 3", alarm_days_before=3)])
    assert cal.startswith("BEGIN:VCALENDAR\r\n") and cal.endswith("END:VCALENDAR\r\n")
    for needle in ("UID:x@y", "DTSTAMP:", "DTSTART;VALUE=DATE:20260201", "SUMMARY:Rent\\, flat 3", "TRIGGER:-P3D"):
        assert needle in cal
