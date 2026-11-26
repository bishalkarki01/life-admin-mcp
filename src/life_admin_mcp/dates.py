"""Date helpers: parsing and recurrence arithmetic."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

CYCLES_PER_YEAR: dict[str, int] = {"weekly": 52, "monthly": 12, "quarterly": 4, "yearly": 1, "once": 0}


def parse_date(value: str, field: str = "date") -> date:
    """Parse a strict ISO ``YYYY-MM-DD`` date, raising a clear ``ValueError`` otherwise."""
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError(f"{field} must be a date in YYYY-MM-DD format, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid date in YYYY-MM-DD format, got {value!r}") from exc


def add_months(d: date, months: int, anchor_day: int | None = None) -> date:
    """Add months, clamping to month end. ``anchor_day`` keeps e.g. the 31st sticky
    so Jan 31 -> Feb 28 -> Mar 31 rather than drifting to the 28th."""
    day = anchor_day or d.day
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def advance(d: date, cycle: str, anchor_day: int | None = None) -> date:
    """Return the next occurrence after ``d`` for a recurrence ``cycle``."""
    if cycle == "weekly":
        return d + timedelta(weeks=1)
    if cycle == "monthly":
        return add_months(d, 1, anchor_day)
    if cycle == "quarterly":
        return add_months(d, 3, anchor_day)
    if cycle == "yearly":
        return add_months(d, 12, anchor_day)
    return d


def next_on_or_after(d: date, cycle: str, today: date, anchor_day: int | None = None) -> date:
    """Roll a recurring date forward until it is on or after ``today``."""
    if cycle == "once":
        return d
    # Bounded loop: weekly over 100 years is ~5200 steps, so this cap is never hit in practice.
    for _ in range(10_000):
        if d >= today:
            return d
        d = advance(d, cycle, anchor_day)
    return d
