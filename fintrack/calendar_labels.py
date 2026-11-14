"""What the Tags calendar writes on a day (user's requests 2026-11-02): US federal holidays, major US shopping days, and
your own labels (`calendar_labels`). Holidays are worked out from rules for any year, with no service or stored table.

Observed days are an estimate by the federal rule: a fixed-date federal holiday on a Saturday is observed the Friday
before, on a Sunday the Monday after, and the calendar shows both days. So New Year's Day on a Saturday puts
"(observed)" on December 31 of the year before. Holidays that always fall on a weekday by rule never need it. Employers
and banks can differ: the Federal Reserve, for one, doesn't close on the Friday before a Saturday holiday.

Prime Day and similar sales aren't here: a retailer picks those dates each year, so no rule can give them.
"""

from __future__ import annotations

import sqlite3
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

LABEL_LIMIT = 40
MON, THU, SAT, SUN = 0, 3, 5, 6


class LabelError(ValueError):
    """A label was refused; the message is shown to you."""


@dataclass(frozen=True)
class DayLabel:
    text: str
    kind: str                   # 'custom', 'federal' or 'shopping'
    id: int | None = None       # a label of yours
    yearly: bool = False


def _nth(year: int, month: int, weekday: int, n: int) -> date:
    """The nth weekday of a month (n = 1..4)."""
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last(year: int, month: int, weekday: int) -> date:
    end = date(year, month, monthrange(year, month)[1])
    return end - timedelta(days=(end.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Western Easter (the anonymous Gregorian computus)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * l) // 433
    month = (h + l - 7 * m + 90) // 25
    return date(year, month, (h + l - 7 * m + 33 * month + 19) % 32)


def observed_day(actual: date) -> date:
    """When a fixed-date federal holiday is observed: Saturday -> Friday before, Sunday -> Monday after."""
    if actual.weekday() == SAT:
        return actual - timedelta(days=1)
    if actual.weekday() == SUN:
        return actual + timedelta(days=1)
    return actual


def holidays(year: int) -> list[tuple[date, str, str]]:
    """[(day, name, kind)] for one year's holidays, each observed day dated by its own holiday's year."""
    fixed = [(date(year, 1, 1), "New Year's Day"), (date(year, 6, 19), "Juneteenth"), (date(year, 7, 4), "Independence Day"),
             (date(year, 11, 11), "Veterans Day"), (date(year, 12, 25), "Christmas Day")]
    found = [(day, name, "federal") for day, name in fixed]
    found += [(observed_day(day), f"{name} (observed)", "federal") for day, name in fixed if observed_day(day) != day]
    thanksgiving = _nth(year, 11, THU, 4)
    christmas = date(year, 12, 25)
    found += [(day, name, "federal") for day, name in (
        (_nth(year, 1, MON, 3), "Martin Luther King Jr. Day"), (_nth(year, 2, MON, 3), "Presidents' Day"),
        (_last(year, 5, MON), "Memorial Day"), (_nth(year, 9, MON, 1), "Labor Day"),
        (_nth(year, 10, MON, 2), "Columbus Day"), (thanksgiving, "Thanksgiving"))]
    found += [(day, name, "shopping") for day, name in (
        (date(year, 2, 14), "Valentine's Day"), (_easter(year), "Easter"), (_nth(year, 5, SUN, 2), "Mother's Day"),
        (_nth(year, 6, SUN, 3), "Father's Day"), (date(year, 10, 31), "Halloween"),
        (thanksgiving + timedelta(days=1), "Black Friday"), (thanksgiving + timedelta(days=2), "Small Business Saturday"),
        (thanksgiving + timedelta(days=4), "Cyber Monday"),
        (christmas - timedelta(days=(christmas.weekday() - SAT - 1) % 7 + 1), "Super Saturday"),
        (date(year, 12, 24), "Christmas Eve"), (date(year, 12, 31), "New Year's Eve"))]
    return found


def day_labels(conn: sqlite3.Connection, first: date, last: date) -> dict[str, list[DayLabel]]:
    """{ISO day: its labels} from `first` to `last`: yours first, then holidays."""
    found: dict[str, list[DayLabel]] = {}

    def put(day: date, label: DayLabel) -> None:
        if first <= day <= last:
            found.setdefault(day.isoformat(), []).append(label)

    for r in conn.execute("SELECT id, date, label, yearly FROM calendar_labels ORDER BY lower(label), id"):
        start = date.fromisoformat(r[1])
        if not r[3]:
            put(start, DayLabel(r[2], "custom", r[0]))
            continue
        for year in range(max(first.year, start.year), last.year + 1):
            # February 29 shows only in leap years
            if start.day <= monthrange(year, start.month)[1]:
                put(date(year, start.month, start.day), DayLabel(r[2], "custom", r[0], True))
    # one year further on: next January 1's observed day can be this December 31
    for year in range(first.year, last.year + 2):
        for day, name, kind in holidays(year):
            put(day, DayLabel(name, kind))
    return found


def upcoming(conn: sqlite3.Connection, today: date) -> list[dict]:
    """Each holiday and label of yours once, at its next date within a year: {key, name, date, group}, soonest first."""
    found, seen = [], set()
    first, last = today, today + timedelta(days=366)
    for day, labels in sorted(day_labels(conn, first, last).items()):
        for label in labels:
            key = f"label:{label.id}" if label.kind == "custom" else f"holiday:{label.text}"
            if key not in seen:
                seen.add(key)
                found.append({"key": key, "name": label.text, "date": day,
                              "group": "Your dates" if label.kind == "custom" else "Holidays"})
    return found


def add(conn: sqlite3.Connection, day: str, label: str, yearly: bool = False) -> None:
    label = " ".join((label or "").split())
    if not label:
        raise LabelError("Give the day a label.")
    if len(label) > LABEL_LIMIT:
        raise LabelError(f"Keep labels to {LABEL_LIMIT} characters.")
    try:
        parsed = date.fromisoformat((day or "").strip())
    except ValueError:
        raise LabelError("That doesn't look like a date.") from None
    conn.execute("INSERT INTO calendar_labels (date, label, yearly) VALUES (?, ?, ?)",
                 (parsed.isoformat(), label, 1 if yearly else 0))
    conn.commit()


def remove(conn: sqlite3.Connection, label_id: int) -> None:
    conn.execute("DELETE FROM calendar_labels WHERE id = ?", (label_id,))
    conn.commit()
