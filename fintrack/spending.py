"""Monthly spending and income, by category.

Everything is read from the `transactions_effective` view, so the rules for
"what counts" live in one place:

    spending = sum(amount) of kind 'expense'   (refunds are negative, so they reduce it)
    income   = -sum(amount) of kind 'income'   (Plaid: money in is negative)
    transfers / card payments (kind 'transfer') and loan accounts' own
    entries (kind 'excluded') count as neither, and are reported separately
    so you can see what was left out.

Pending transactions are included (they're replaced when they post).
Accounts from removed connections are left out. If you reconnect the same
bank, Plaid re-sends its history under new IDs, and keeping both would count it twice.
"""

from __future__ import annotations

import calendar
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta


# ---------------------------------------------------------------- months

def parse_month(text: str | None, today: date | None = None) -> str:
    """'2026-09' -> '2026-09'; anything invalid -> the current month."""
    if text:
        try:
            year, month = (int(p) for p in text.split("-"))
            return f"{date(year, month, 1):%Y-%m}"
        except (ValueError, TypeError):
            pass
    return f"{today or date.today():%Y-%m}"


def shift_month(month: str, delta: int) -> str:
    year, mon = (int(p) for p in month.split("-"))
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


# The Transactions page's card on Spending / Income, from its title dropdown (user's requests 2026-11-01): the pie by
# category, or the top descriptions.
BREAKDOWNS = ("category", "description")


def breakdown(conn: sqlite3.Connection) -> str:
    """What the card is by: "category" unless you chose "description"."""
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'transactions_breakdown'").fetchone()
    return row[0] if row and row[0] in BREAKDOWNS else "category"


def set_breakdown(conn: sqlite3.Connection, by: str) -> None:
    if by not in BREAKDOWNS:
        raise ValueError("Choose category or description.")
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('transactions_breakdown', ?)"
                 " ON CONFLICT (key) DO UPDATE SET value = excluded.value WHERE value != excluded.value", (by,))
    conn.commit()


def month_bounds(month: str) -> tuple[str, str]:
    """('2026-09-01', '2026-10-01'): start inclusive, end exclusive."""
    return f"{month}-01", f"{shift_month(month, 1)}-01"


# ---------------------------------------------------------------- report

@dataclass
class CategoryLine:
    category: str
    label: str
    amount_cents: int
    count: int
    previous_cents: int = 0
    share: float = 0.0  # of total spending, 0..1

    @property
    def change_cents(self) -> int:
        return self.amount_cents - self.previous_cents


@dataclass
class MonthReport:
    month: str
    spending_cents: int = 0
    income_cents: int = 0
    previous_spending_cents: int = 0
    transfers_out_cents: int = 0      # outflows not counted because they're transfers/card payments
    transfers_count: int = 0
    loan_entries_count: int = 0
    lines: list[CategoryLine] = field(default_factory=list)
    # Set while `month` is in progress: the previous month is compared through
    # this day only (like-for-like), not in full.
    compare_through_day: int | None = None


def _by_category_between(conn: sqlite3.Connection, start: str, end: str) -> dict[str, tuple[str, int, int]]:
    """{category: (label, total, count)} of spending (kind 'expense') for dates in [start, end)."""
    return {
        r["category"]: (r["category_label"], r["total"], r["n"])
        for r in conn.execute(
            """
            SELECT category, category_label, SUM(amount_cents) AS total, COUNT(*) AS n
            FROM transactions_effective
            WHERE account_hidden = 0 AND kind = 'expense' AND date >= ? AND date < ?
            GROUP BY category
            """,
            (start, end),
        )
    }


def _build_report(conn: sqlite3.Connection, report: MonthReport, start: str, end: str,
                  prev_start: str, prev_end: str) -> MonthReport:
    """Fill `report` for dates in [start, end), compared with [prev_start, prev_end)."""
    current = _by_category_between(conn, start, end)
    previous = _by_category_between(conn, prev_start, prev_end)
    report.spending_cents = sum(total for _, total, _ in current.values())
    report.previous_spending_cents = sum(total for _, total, _ in previous.values())

    for key, (label, total, count) in current.items():
        report.lines.append(CategoryLine(key, label, total, count, previous.get(key, (None, 0, 0))[1]))
    report.lines.sort(key=lambda line: line.amount_cents, reverse=True)
    # ...then the categories with spending only in the prior period, at $0, largest prior first (user's request 2026-11-04),
    # so a drop to nothing shows too. Nothing's spent in them, so they're no treemap tile and no share.
    gone = [CategoryLine(key, label, 0, 0, total) for key, (label, total, _) in previous.items()
            if key not in current and total]
    report.lines += sorted(gone, key=lambda line: line.previous_cents, reverse=True)
    if report.spending_cents > 0:
        for line in report.lines:
            line.share = max(line.amount_cents / report.spending_cents, 0.0)

    totals = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN kind = 'income' THEN -amount_cents END), 0) AS income,
            COALESCE(SUM(CASE WHEN kind = 'transfer' AND amount_cents > 0 THEN amount_cents END), 0) AS transfers_out,
            COUNT(CASE WHEN kind = 'transfer' THEN 1 END) AS transfers_n,
            COUNT(CASE WHEN kind = 'excluded' THEN 1 END) AS loan_n
        FROM transactions_effective
        WHERE account_hidden = 0 AND date >= ? AND date < ?
        """,
        (start, end),
    ).fetchone()
    report.income_cents = totals["income"]
    report.transfers_out_cents = totals["transfers_out"]
    report.transfers_count = totals["transfers_n"]
    report.loan_entries_count = totals["loan_n"]
    return report


def month_report(conn: sqlite3.Connection, month: str, today: date | None = None) -> MonthReport:
    start, end = month_bounds(month)
    report = MonthReport(month=month)
    today = today or date.today()

    previous_month = shift_month(month, -1)
    prev_start, prev_end = month_bounds(previous_month)
    if month == f"{today:%Y-%m}":
        # In progress: compare days 1..N of last month (N clamped to its length),
        # the same like-for-like rule as the dashboard (savings.month_to_date).
        prev_year, prev_mon = (int(p) for p in previous_month.split("-"))
        report.compare_through_day = min(today.day, calendar.monthrange(prev_year, prev_mon)[1])
        prev_end = (date.fromisoformat(f"{previous_month}-{report.compare_through_day:02d}") + timedelta(days=1)).isoformat()
    return _build_report(conn, report, start, end, prev_start, prev_end)


def range_report(conn: sqlite3.Connection, start: str, end: str) -> MonthReport:
    """The same report for any inclusive date range (Spending's 90 days, YTD, Custom, ...), compared with
    the equal-length period right before it. `month` is blank: it isn't one calendar month."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    prev_start, prev_end = prior_period(start, end)
    return _build_report(conn, MonthReport(month=""), start, (last + timedelta(days=1)).isoformat(),
                         prev_start, (date.fromisoformat(prev_end) + timedelta(days=1)).isoformat())


# ---------------------------------------------------------------- the Spending page's date range

# The ribbon's date range (same choices and keys as the Transactions page, which imports them from here).
RANGES = {"this-month": "This month", "last-month": "Last month", "90": "90 days", "ytd": "YTD",
          "last-year": "Last year", "365": "365 days", "custom": "Custom"}
DEFAULT_RANGE = "this-month"           # Spending opens on this month; Transactions has its own default
AVERAGE_RANGES = ("90", "ytd", "last-year", "365")   # the monthly-totals chart also draws the monthly average for these
MONTH_RANGES = ("this-month", "last-month")   # one calendar month: cumulative "vs the month before" chart


def range_bounds(key: str, today: date | None = None) -> tuple[str, str]:
    """(from, to), inclusive ISO dates, for a preset range; ("", "") = no limit."""
    today = today or date.today()
    first = today.replace(day=1)
    if key == "this-month":
        return first.isoformat(), today.isoformat()
    if key == "last-month":
        last_end = first - timedelta(days=1)
        return last_end.replace(day=1).isoformat(), last_end.isoformat()
    if key in ("90", "365"):
        return (today - timedelta(days=int(key) - 1)).isoformat(), today.isoformat()
    if key == "ytd":
        return date(today.year, 1, 1).isoformat(), today.isoformat()
    if key == "last-year":
        return date(today.year - 1, 1, 1).isoformat(), date(today.year - 1, 12, 31).isoformat()
    return "", ""


AVG_DAYS_PER_MONTH = 365.25 / 12


def average_per_month_cents(total_cents: int, start: str, end: str) -> int | None:
    """Monthly average of `total_cents` spent between two inclusive dates: the total divided by the range's
    length in months (days / 30.44). Not the mean of per-month totals, which would count a clipped or
    in-progress month as a whole one. None when nothing was spent."""
    if total_cents <= 0:
        return None
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    return round(total_cents / (days / AVG_DAYS_PER_MONTH))


def trailing_average_cents(conn: sqlite3.Connection, today: date | None = None, days: int = 365) -> int | None:
    """The 12-month average spend: counted spending over the last `days` days (ending today), per month.
    The same figure as the 365-day range's average line on the monthly chart."""
    today = today or date.today()
    start, end = (today - timedelta(days=days - 1)).isoformat(), today.isoformat()
    total = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM transactions_effective"
        " WHERE account_hidden = 0 AND kind = 'expense' AND date >= ? AND date <= ?",
        (start, end),
    ).fetchone()[0]
    return average_per_month_cents(total, start, end)


def iso_or_blank(value: str) -> str:
    """A valid YYYY-MM-DD date, else ''. (Chrome reports a half-typed year like 0002 as a date.)"""
    try:
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError):
        return ""


def prior_period(start: str, end: str) -> tuple[str, str]:
    """The equal-length period right before [start, end] (inclusive dates)."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    prev_end = first - timedelta(days=1)
    return (prev_end - (last - first)).isoformat(), prev_end.isoformat()


@dataclass
class Period:
    key: str            # a RANGES key
    start: str          # inclusive ISO dates
    end: str
    # "days" = one calendar month (this/last month): the cumulative chart and two month tables.
    # "months" = any other range: monthly totals and one table.
    mode: str
    month: str = ""     # days mode: the month shown (YYYY-MM)


def resolve_period(conn: sqlite3.Connection, query, today: date | None = None, default: str = DEFAULT_RANGE) -> Period:
    """The date range from query parameters: range (a key) > an old ?month=YYYY-MM link (= Custom with
    that month) > From/To given (= Custom) > `default` (this month on Spending, 365 days on Savings). A preset
    ignores the dates in the fields; a blank Custom side is open-ended (earliest spending / today)."""
    today = today or date.today()
    picked = query.get("range") or ""
    start, end = iso_or_blank(query.get("from", "")), iso_or_blank(query.get("to", ""))
    month = query.get("month", "")
    if not picked and month:
        first, after = month_bounds(parse_month(month, today))
        picked, start, end = "custom", first, (date.fromisoformat(after) - timedelta(days=1)).isoformat()
    if picked not in RANGES:
        picked = "custom" if (start or end) else default
    if picked != "custom":
        start, end = range_bounds(picked, today)
    else:
        if not start:
            start = conn.execute(
                "SELECT MIN(date) FROM transactions_effective WHERE account_hidden = 0 AND kind = 'expense'"
            ).fetchone()[0] or today.isoformat()
        end = end or today.isoformat()
        if start > end:
            start, end = end, start
    return Period(picked, start, end, "days" if picked in MONTH_RANGES else "months",
                  start[:7] if picked in MONTH_RANGES else "")


# ---------------------------------------------------------------- the month's transactions

def range_transactions(conn: sqlite3.Connection, start: str, end: str) -> list[sqlite3.Row]:
    """Every transaction that counts as spending (kind 'expense', so the list adds up to the report's
    total) from `start` to `end` (inclusive dates), newest first. `day` is the day of the month and `month`
    is YYYY-MM, for the Spending page's chart filter."""
    return conn.execute(
        """
        SELECT id, date, CAST(substr(date, 9, 2) AS INTEGER) AS day, substr(date, 1, 7) AS month,
               description, category, category_label, amount_cents, pending
        FROM transactions_effective
        WHERE account_hidden = 0 AND kind = 'expense' AND date >= ? AND date <= ?
        ORDER BY date DESC, id DESC
        """,
        (start, end),
    ).fetchall()


def month_transactions(conn: sqlite3.Connection, month: str) -> list[sqlite3.Row]:
    """The month's counted spending, newest first (a calendar month of range_transactions)."""
    start, end = month_bounds(month)
    return range_transactions(conn, start, (date.fromisoformat(end) - timedelta(days=1)).isoformat())


# ---------------------------------------------------------------- by credit card

@dataclass
class CardSpending:
    """One credit card's spending in a date range (Analysis → Credit cards, user's request 2026-10-25)."""
    account_id: int
    name: str
    mask: str | None
    spending_cents: int = 0
    lines: list[CategoryLine] = field(default_factory=list)    # largest first


def card_spending(conn: sqlite3.Connection, start: str, end: str) -> list[CardSpending]:
    """Every visible credit card with its spending by category from `start` to `end` (inclusive dates), by name.
    Spending is counted as everywhere else (kind 'expense' in transactions_effective): refunds reduce it, payments
    to the card and transfers don't count. A card with no spending in the range is still listed, with no lines."""
    cards = {
        r["id"]: CardSpending(r["id"], r["name"], r["mask"])
        for r in conn.execute(
            "SELECT id, name, mask FROM accounts WHERE type = 'credit' AND is_hidden = 0 ORDER BY LOWER(name), id"
        )
    }
    for r in conn.execute(
        """
        SELECT account_id, category, category_label, SUM(amount_cents) AS total, COUNT(*) AS n
        FROM transactions_effective
        WHERE account_type = 'credit' AND account_hidden = 0 AND kind = 'expense' AND date >= ? AND date <= ?
        GROUP BY account_id, category
        """,
        (start, end),
    ):
        card = cards.get(r["account_id"])
        if card:
            card.lines.append(CategoryLine(r["category"], r["category_label"], r["total"], r["n"]))
    for card in cards.values():
        card.lines.sort(key=lambda line: line.amount_cents, reverse=True)
        card.spending_cents = sum(line.amount_cents for line in card.lines)
        if card.spending_cents > 0:
            for line in card.lines:
                line.share = max(line.amount_cents / card.spending_cents, 0.0)
    return list(cards.values())
