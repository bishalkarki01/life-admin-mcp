"""Minimal RFC 5545 (iCalendar) writer with correct escaping and line folding."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone


@dataclass(frozen=True)
class Event:
    uid: str
    day: date
    summary: str
    description: str = ""
    alarm_days_before: int = 0


def escape(text: str) -> str:
    """Escape TEXT values per RFC 5545 §3.3.11."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold(line: str) -> str:
    """Fold content lines longer than 75 octets (RFC 5545 §3.1) without splitting UTF-8 chars."""
    out: list[str] = []
    current, size = "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        limit = 75 if not out else 74  # continuation lines start with a space
        if size + n > limit:
            out.append(current)
            current, size = ch, n
        else:
            current += ch
            size += n
    out.append(current)
    return "\r\n ".join(out)


def build_calendar(events: Iterable[Event], now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//life-admin-mcp//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:Life Admin",
    ]
    for e in events:
        day = e.day.strftime("%Y%m%d")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e.uid}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{day}",
            f"SUMMARY:{escape(e.summary)}",
        ]
        if e.description:
            lines.append(f"DESCRIPTION:{escape(e.description)}")
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{escape(e.summary)}",
            f"TRIGGER:-P{max(e.alarm_days_before, 0)}D",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"
