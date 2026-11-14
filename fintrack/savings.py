"""Monthly savings: income, spending, where the money saved went, and the savings rate.

    income (post-tax) = money in from income-kind categories (paychecks as they reach the bank)
    pre-tax income    = contributions seen only inside an investment account, connected or manual alike (its
                        activity's contribution rows not paired with a bank transfer: payroll / employer money;
                        contributions.py; manual accounts' entries became activity 2026-11-14, docs/parity-plan.md)
    spending          = expense-kind spending (same definition as the Spending page)
    retirement        = net money moved into retirement accounts (those contributions included)
    investment        = net money moved into other investment accounts (withdrawals count against it)
    net cash flow     = post-tax income - spending - net money moved into investments through the bank
                        - net other transfers (transfer-kind transactions on checking / savings: Zelle, Venmo, transfers
                        to accounts fintrack doesn't track; NOT pairs between your own connected accounts, credit card
                        payments, anything already counted as an investment move, or transfers linked to a purpose
                        account; user's request 2026-10-12)
    saved             = retirement + investment + net cash flow
    rate              = saved / (pre-tax + post-tax income)   (None when there's no income: no meaningful %)

With no pre-tax amounts and no investment flows, saved = income - spending and the rate is the old one. The columns
and rate were the user's request 2026-10-11; recorded contributions replaced a monthly pre-tax amount 2026-10-12.
Savings = what was saved from income (user's request 2026-10-12): money moved from investment / retirement accounts into
a bank account is left out, and so are purpose accounts (money lent, its repayments and adjustments), also from Net
cash. Every movement in account values is on the Accounts page instead (balances.py).

Totals come from the `transactions_effective` view, so Savings, Spending, and
the dashboard always agree. Multi-month rates are total saved / total income,
not an average of monthly rates, so a low-income month can't distort them.
"""

from __future__ import annotations

import calendar
import sqlite3
from dataclasses import dataclass
from datetime import date

from fintrack import contributions
from fintrack.spending import month_bounds, shift_month


@dataclass
class MonthSavings:
    month: str
    income_cents: int
    spending_cents: int
    partial: bool = False  # the current month, still in progress
    start: str = ""        # set when a date range cuts the month: the first / last day counted (inclusive ISO dates)
    end: str = ""
    pretax_cents: int = 0          # pre-tax income (see the module docstring)
    retirement_cents: int = 0      # net saved into retirement accounts
    investment_cents: int = 0      # net saved into other investment accounts
    bank_invested_cents: int = 0   # net money moved from the bank into investments (part of the two above)
    other_transfers_cents: int = 0  # net other transfers out of checking / savings (+ = money left)

    @property
    def total_income_cents(self) -> int:
        return self.income_cents + self.pretax_cents

    @property
    def net_cash_cents(self) -> int:
        return self.income_cents - self.spending_cents - self.bank_invested_cents - self.other_transfers_cents

    @property
    def saved_cents(self) -> int:
        return self.retirement_cents + self.investment_cents + self.net_cash_cents

    @property
    def clipped_days(self) -> tuple[int, int] | None:
        """(first, last) day of the month counted when a date range cuts it, else None (shown as "Oct 12–31, 2025")."""
        if not (self.start or self.end):
            return None
        year, mon = int(self.month[:4]), int(self.month[5:])
        return (int(self.start[8:]) if self.start else 1,
                int(self.end[8:]) if self.end else calendar.monthrange(year, mon)[1])

    @property
    def rate(self) -> float | None:
        income = self.total_income_cents
        return self.saved_cents / income if income > 0 else None


def combined_rate(months: list[MonthSavings]) -> float | None:
    income = sum(m.total_income_cents for m in months)
    saved = sum(m.saved_cents for m in months)
    return saved / income if income > 0 else None


def _other_transfers(conn: sqlite3.Connection, start: str, end: str) -> int:
    """Net transfers out (+) of visible checking / savings accounts for dates in [start, end) that aren't pairs between
    your own connected accounts (is_transfer), credit card payments (the card's purchases are already spending) or
    Plaid's investment-transfer codes (contributions.py counts those). Transfers that paired with an investment row are
    still in here, matched or not (a match with investment activity: transfer_pair_id < 0, transfers.py); range_series
    takes them back out (MonthFlow.paired_transfers)."""
    return conn.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0) FROM transactions_effective
        WHERE account_hidden = 0 AND account_type = 'depository' AND kind = 'transfer'
          AND (is_transfer = 0 OR transfer_pair_id < 0)
          AND category <> 'CREDIT_CARD_PAYMENT'
          AND COALESCE(plaid_category_detailed, '') NOT IN (?, ?)
          AND date >= ? AND date < ?
          AND id NOT IN (SELECT transaction_id FROM purpose_links WHERE account_id IS NOT NULL)   -- money lent: left out
        """,
        (contributions.INTO_CODE, contributions.OUT_CODE, start, end),
    ).fetchone()[0]


def _totals(conn: sqlite3.Connection, start: str, end: str) -> tuple[int, int]:
    """(income, spending) in cents for dates in [start, end)."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN kind = 'income'  THEN -amount_cents END), 0),
               COALESCE(SUM(CASE WHEN kind = 'expense' THEN  amount_cents END), 0)
        FROM transactions_effective
        WHERE account_hidden = 0 AND date >= ? AND date < ?
        """,
        (start, end),
    ).fetchone()
    return row[0], row[1]


def monthly_series(conn: sqlite3.Connection, months: int = 12, today: date | None = None) -> list[MonthSavings]:
    """The last `months` months ending with the current one, oldest first (whole calendar months).
    Months with no transactions are included as zeros so the chart spacing is honest."""
    today = today or date.today()
    current = f"{today:%Y-%m}"
    first = month_bounds(shift_month(current, -(months - 1)))[0]
    last = date.fromordinal(date.fromisoformat(month_bounds(current)[1]).toordinal() - 1).isoformat()
    return range_series(conn, first, last, today)


def range_series(conn: sqlite3.Connection, start: str, end: str, today: date | None = None,
                 by_day: bool = False) -> list[MonthSavings]:
    """One MonthSavings per calendar month from `start` to `end` (inclusive ISO dates), oldest first, for the Savings
    page's date range (user's request 2026-10-11). The first and last months count only their days inside the range
    (as Spending's monthly totals do) and record those days in `start` / `end`. Empty months are zeros.
    `by_day`: one per day instead (`month` is the date), for the cards' daily bars on This / Last month."""
    today = today or date.today()
    current = f"{today:%Y-%m}"
    flows = {f.month: f for f in contributions.between(conn, date.fromisoformat(start), date.fromisoformat(end), by_day,
                                                       to_bank=False)}
    series = []
    month = start if by_day else start[:7]
    while month <= (end if by_day else end[:7]):
        if by_day:
            first = last = month
            after = date.fromordinal(date.fromisoformat(month).toordinal() + 1).isoformat()
        else:
            first, after = month_bounds(month)
            last = date.fromordinal(date.fromisoformat(after).toordinal() - 1).isoformat()
        lo, hi = max(first, start), min(last, end)
        after_hi = date.fromordinal(date.fromisoformat(hi).toordinal() + 1).isoformat()
        income, spending = _totals(conn, lo, after_hi)
        partial = not by_day and month == current and hi >= today.isoformat()
        flow = flows.get(month) or contributions.MonthFlow(month)
        series.append(MonthSavings(
            month, income, spending, partial=partial,
            start=lo if lo != first else "", end=hi if hi != last and not partial else "",
            pretax_cents=flow.outside_into,
            retirement_cents=flow.into["retirement"] - flow.out["retirement"],
            investment_cents=flow.into["investment"] - flow.out["investment"],
            bank_invested_cents=flow.bank_net,
            other_transfers_cents=_other_transfers(conn, lo, after_hi) - flow.paired_transfers))
        month = after if by_day else shift_month(month, 1)
    return series


def trim_leading_empty(series: list[MonthSavings]) -> list[MonthSavings]:
    """Drop empty months before the first month with any data (before you connected)."""
    for index, m in enumerate(series):
        if m.income_cents or m.spending_cents or m.pretax_cents:
            return series[index:]
    return series[-1:]  # keep the current month so the page isn't blank


# ---------------------------------------------------------------- month-to-date comparison

@dataclass
class MonthToDate:
    day: int                       # comparing days 1..day
    spending_cents: int            # this month so far
    last_month_same_period_cents: int
    last_month_total_cents: int
    last_month: str
    last_day: int = 0                          # last month's days 1..last_day (day, clamped to its length)
    last_month_same_period_rate: float | None = None   # its savings rate over those days (the Overview's tile)


def month_to_date(conn: sqlite3.Connection, today: date | None = None) -> MonthToDate:
    """This month's spending (and last month's savings rate) so far vs. last month through the same day.

    Comparing 3 days of October with all of September would always look like a
    big drop, so the like-for-like figure is days 1..N of each month, with the
    day clamped to last month's length (Mar 31 compares with Feb 1..28).
    """
    today = today or date.today()
    this_month = f"{today:%Y-%m}"
    last_month = shift_month(this_month, -1)
    last_year, last_mon = (int(p) for p in last_month.split("-"))
    last_day = min(today.day, calendar.monthrange(last_year, last_mon)[1])

    def through(month: str, day: int) -> int:
        start, _ = month_bounds(month)
        # end is exclusive: the day after `day`
        end = date(int(month[:4]), int(month[5:]), day).toordinal() + 1
        return _totals(conn, start, date.fromordinal(end).isoformat())[1]

    return MonthToDate(
        day=today.day,
        spending_cents=through(this_month, today.day),
        last_month_same_period_cents=through(last_month, last_day),
        last_month_total_cents=_totals(conn, *month_bounds(last_month))[1],
        last_month=last_month,
        last_day=last_day,
        # the savings rate over the same days, so the Overview's Savings rate tile compares like the Spent tile
        # (user's request 2026-10-23)
        last_month_same_period_rate=range_series(conn, f"{last_month}-01", f"{last_month}-{last_day:02d}", today)[0].rate,
    )
