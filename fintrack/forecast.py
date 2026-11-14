"""Cash-flow forecast for the next 90 days (shown on Recurring and Overview).

Deliberately simple, and it shows its assumptions. (The month-end spending projection
was removed with the Budgets redesign, user's request 2026-10-06.)

Cash flow (each visible checking/savings account from Plaid)
    balance(day) = balance(day - 1)
                 + confirmed recurring items scheduled that day
                 + card statements still owed, due that day, from this paying account
  Only KNOWN money in and out: there is no averaged day-to-day trend (user's decision,
  2026-10-05). Everyday spending happens on credit cards and reaches checking as the
  statement payment, so between events the balance is flat.
  - Overdue items (next date already passed, not seen yet): a late BILL is
    assumed to hit today; a late PAYCHECK is not assumed to arrive. A cash
    forecast should err toward warning you. Today's point stays the actual balance;
    today's events (late or due) show from tomorrow's point.
  - A day is flagged when a balance is projected below your low-balance threshold.
  - Credit card statements (cards.py): what's still owed on each card's current statement
    leaves its PAYING account on the due date (overdue -> today). Confirmed recurring card
    payments on a paying account are skipped, so a payment is never subtracted twice.
    The paying account is forecast even when it isn't checking/savings.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from fintrack import cards, recurring
from fintrack.money import parse_amount

HORIZON_DAYS = 90
DEFAULT_LOW_BALANCE_CENTS = 50000


class ForecastError(ValueError):
    pass


# ---------------------------------------------------------------- settings

def low_balance_threshold(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'low_balance_threshold_cents'").fetchone()
    try:
        return int(row[0]) if row else DEFAULT_LOW_BALANCE_CENTS
    except ValueError:
        return DEFAULT_LOW_BALANCE_CENTS


def set_low_balance_threshold(conn: sqlite3.Connection, text: str) -> int:
    try:
        cents = parse_amount(text)
    except ValueError as exc:
        raise ForecastError(str(exc)) from None
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES ('low_balance_threshold_cents', ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (str(cents),),
    )
    conn.commit()
    return cents


# ---------------------------------------------------------------- shared pieces

def _confirmed(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT r.*, c.label AS category_label FROM recurring_items r
        LEFT JOIN categories c ON c.key = r.category_key
        WHERE r.status = 'confirmed'
        """
    ).fetchall()


@dataclass
class Event:
    day: date
    name: str
    change_cents: int       # effect on the balance: + money in, - money out
    overdue: bool = False
    category: str | None = None   # its category key (a phone shows its icon in the Cash flow lists; 2026-11-06)


def _events(item: sqlite3.Row, today: date, end: date) -> list[Event]:
    """An item's projected occurrences in (today, end], plus an overdue bill today."""
    anchor = date.fromisoformat(item["next_expected_date"])
    change = -item["amount_cents"]   # Plaid sign -> balance effect
    events = []
    if anchor <= today:
        if item["amount_cents"] > 0:   # late bill: assume it hits today
            events.append(Event(today, item["name"], change, overdue=True, category=item["category_key"]))
        first = today + timedelta(days=1)
        # Skip the overdue occurrence itself; continue with the following ones.
        upcoming = [d for d in recurring.occurrences(item["frequency"], anchor, first, end, item["days_of_month"])]
    else:
        upcoming = recurring.occurrences(item["frequency"], anchor, anchor, end, item["days_of_month"])
    events += [Event(d, item["name"], change, category=item["category_key"]) for d in upcoming if d > today]
    return events


# ---------------------------------------------------------------- cash flow

@dataclass
class AccountForecast:
    account_id: int | None          # None for the combined total
    name: str
    mask: str | None
    start_cents: int
    points: list[tuple[date, int]] = field(default_factory=list)   # day 0 (today) .. HORIZON_DAYS
    events: list[Event] = field(default_factory=list)
    threshold_cents: int = 0

    def at(self, days: int) -> int:
        return self.points[min(days, len(self.points) - 1)][1]

    @property
    def lowest(self) -> tuple[date, int]:
        return min(self.points, key=lambda p: (p[1], p[0]))

    @property
    def first_below(self) -> date | None:
        for day, cents in self.points:
            if cents < self.threshold_cents:
                return day
        return None


@dataclass
class CashFlow:
    today: date
    threshold_cents: int
    accounts: list[AccountForecast]
    combined: AccountForecast | None
    items_used: list[sqlite3.Row]

    @property
    def warnings(self) -> list[AccountForecast]:
        return [a for a in self.accounts if a.first_below is not None]


def cash_flow(conn: sqlite3.Connection, today: date | None = None, horizon: int = HORIZON_DAYS) -> CashFlow:
    today = today or date.today()
    end = today + timedelta(days=horizon)
    threshold = low_balance_threshold(conn)
    items = _confirmed(conn)

    # Card statement payments, by paying account.
    card_events: dict[int, list[Event]] = {}
    # ... the known statements, then estimated later ones (average monthly spend on the usual payment day; 2026-10-23)
    estimated = cards.estimated_payment_events(conn, today, end)
    for pay_from, day, name, change, overdue in cards.payment_events(conn, today, end) + estimated:
        card_events.setdefault(pay_from, []).append(Event(day, name, change, overdue, cards.CARD_PAYMENT_CATEGORY))
    # Accounts that pay a card statement, known or estimated (a confirmed recurring card payment there is skipped).
    pays_statements = {d.pay_from for d in cards.statements(conn, today) if d.pay_from is not None}
    pays_statements |= {pay_from for pay_from, *_ in estimated}

    accounts = conn.execute(
        f"""
        SELECT * FROM accounts
        WHERE is_hidden = 0 AND current_balance_cents IS NOT NULL
          AND (type = 'depository'                 -- connected or manual alike (docs/parity-plan.md, 2026-11-14)
               OR id IN ({','.join('?' * len(pays_statements)) or 'NULL'}))
        ORDER BY type = 'depository' DESC, name
        """,
        sorted(pays_statements),
    ).fetchall()

    forecasts = []
    for acct in accounts:
        events = [e for item in items if item["account_id"] == acct["id"]
                  # statements replace a confirmed recurring card payment on the paying account
                  and not (acct["id"] in pays_statements and item["category_key"] == cards.CARD_PAYMENT_CATEGORY)
                  for e in _events(item, today, end)]
        events += card_events.get(acct["id"], [])
        by_day: dict[date, int] = {}
        for e in events:
            by_day[e.day] = by_day.get(e.day, 0) + e.change_cents

        # Only known events move the balance; between them it stays flat. Today's point is the actual balance: what's
        # overdue or still due today hasn't left the account yet, so it lowers tomorrow's point (user's report
        # 2026-11-03: the chart's today didn't match the balance).
        running = acct["current_balance_cents"]
        points = [(today, running)]
        running += by_day.get(today, 0)
        for offset in range(1, horizon + 1):
            day = today + timedelta(days=offset)
            running += by_day.get(day, 0)
            points.append((day, running))
        forecasts.append(AccountForecast(
            acct["id"], acct["name"], acct["mask"], acct["current_balance_cents"],
            points, sorted(events, key=lambda e: e.day), threshold,
        ))

    combined = None
    # "All checking & savings" sums exactly those (a non-depository paying account is shown on its own).
    depository_ids = {a["id"] for a in accounts if a["type"] == "depository"}
    cash = [f for f in forecasts if f.account_id in depository_ids]
    if cash:
        combined = AccountForecast(
            None, "All checking & savings", None,
            sum(f.start_cents for f in cash),
            [(cash[0].points[i][0], sum(f.points[i][1] for f in cash)) for i in range(horizon + 1)],
            sorted((e for f in cash for e in f.events), key=lambda e: e.day), threshold,
        )
    return CashFlow(today, threshold, forecasts, combined, items)


# ---------------------------------------------------------------- Cash flow page: one account (2026-10-24)

def balance_history(conn: sqlite3.Connection, account_id: int, days: int, today: date | None = None) -> list[tuple[date, int]]:
    """(day, closing balance) for today - `days` .. today, worked back from the current balance through the account's
    posted transactions: a day's closing balance = today's + everything that moved after it (Plaid's sign: + out).
    Snapshots start only at the first sync, so they can't cover 90 or 180 days. Pending transactions and likely returns
    of bank-removed ones (kind 'excluded') are left out."""
    today = today or date.today()
    current = conn.execute("SELECT current_balance_cents FROM accounts WHERE id = ?", (account_id,)).fetchone()[0] or 0
    start = today - timedelta(days=days)
    moved = dict(conn.execute(
        """SELECT date, SUM(amount_cents) FROM transactions_effective
           WHERE account_id = ? AND pending = 0 AND kind <> 'excluded' AND date > ? GROUP BY date""",
        (account_id, start.isoformat())).fetchall())
    points, running = [], current
    for offset in range(days + 1):                     # today first, then back a day at a time
        day = today - timedelta(days=offset)
        points.append((day, running))
        running += moved.get(day.isoformat(), 0)       # undo that day's transactions for the day before
    return points[::-1]


def past_for(conn: sqlite3.Connection, account_id: int, days: int, today: date | None = None) -> list[dict]:
    """The account's posted transactions in the last `days` days, newest first, in upcoming_30's shape, each with
    `balance_after` (the balance once it went through; the Cash flow page's past card, user's request 2026-10-24).
    The same transactions as balance_history (pending and likely returns left out), so the two agree."""
    today = today or date.today()
    current = conn.execute("SELECT current_balance_cents FROM accounts WHERE id = ?", (account_id,)).fetchone()[0] or 0
    rows = conn.execute(
        """SELECT date, description, amount_cents, category FROM transactions_effective
           WHERE account_id = ? AND pending = 0 AND kind <> 'excluded' AND date > ? AND date <= ?
           ORDER BY date DESC, id DESC""",
        (account_id, (today - timedelta(days=days)).isoformat(), today.isoformat())).fetchall()
    result, running = [], current
    for r in rows:                              # newest first: each row's balance after it, then undo it
        result.append({"date": r["date"], "overdue": False, "name": r["description"], "amount_cents": r["amount_cents"],
                       "type": "income" if r["amount_cents"] < 0 else "bill", "needs_payer": False,
                       "balance_after": running, "category": r["category"]})
        running += r["amount_cents"]
    return result


AVERAGE_MONTH_DAYS = 365.25 / 12


@dataclass
class CashMeasures:
    """The Cash flow page's three tiles for one account and range."""
    current_cents: int
    change_cents: int              # vs the balance `days` ago
    since: date
    monthly_cents: int             # projected change per month over the next `days`
    end_cents: int                 # projected balance at the end of the range
    lowest_cents: int
    lowest_day: date
    below_threshold: bool


def cash_measures(projection: AccountForecast, history: list[tuple[date, int]], days: int) -> CashMeasures:
    points = projection.points[:days + 1]
    lowest_day, lowest = min(points, key=lambda p: (p[1], p[0]))
    start, end = points[0][1], points[-1][1]
    return CashMeasures(
        current_cents=projection.start_cents, change_cents=projection.start_cents - history[0][1], since=history[0][0],
        monthly_cents=round((end - start) / days * AVERAGE_MONTH_DAYS), end_cents=end,
        lowest_cents=lowest, lowest_day=lowest_day, below_threshold=lowest < projection.threshold_cents)


def upcoming_for(projection: AccountForecast, today: date, days: int = 30) -> list[dict]:
    """One account's "Coming up" rows (the Cash flow page, user's request 2026-10-24): its projection's events in the
    next `days` days (so the list matches the chart), in upcoming_30's shape, each with `balance_after`: the projected
    balance once it (and every row above it) has gone through (user's request 2026-10-24)."""
    end = today + timedelta(days=days)
    rows = [{"date": e.day.isoformat(), "overdue": e.overdue, "name": e.name, "amount_cents": -e.change_cents,
             "type": "income" if e.change_cents > 0 else ("card" if e.name.endswith(("statement", "(estimated)")) else "bill"),
             "account_name": projection.name, "needs_payer": False, "category": e.category}
            for e in projection.events if e.day <= end]
    rows.sort(key=lambda r: (not r["overdue"], r["date"], r["type"] != "income"))
    running = projection.start_cents
    for r in rows:
        running -= r["amount_cents"]           # Plaid's sign: + out
        r["balance_after"] = running
    return rows


# ---------------------------------------------------------------- Overview / Cash flow page

def main_payer(conn: sqlite3.Connection) -> int | None:
    """The account that pays the most card statements (the default account of the Overview's and Cash flow page's
    balance chart)."""
    counts: dict[int, int] = {}
    for p in cards.payers(conn).values():
        if p["pay_from"] is not None:
            counts[p["pay_from"]] = counts.get(p["pay_from"], 0) + 1
    return max(counts, key=counts.get) if counts else None


def upcoming_30(conn: sqlite3.Connection, today: date | None = None) -> list[dict]:
    """Confirmed recurring items plus card statement payments (known and estimated) in the next 30 days,
    overdue first, then by date. Amounts use Plaid's sign (out positive). The "Coming up" list (_coming_up.html)."""
    today = today or date.today()
    rows = recurring.upcoming(conn, today, days=30)
    names = {r["id"]: r for r in conn.execute("SELECT id, name, mask FROM accounts")}
    end = today.toordinal() + 30
    for d in cards.statements(conn, today):
        if d.owed_cents <= 0 or d.due_date is None:
            continue
        if not d.overdue and d.due_date.toordinal() > end:
            continue
        payer = names.get(d.pay_from)
        rows.append({"date": (today if d.overdue else d.due_date).isoformat(), "overdue": d.overdue,
                     "name": f"{d.card_name} statement",          # no masks in cash flow (user's request 2026-10-23)
                     "amount_cents": d.owed_cents, "type": "card",
                     "account_name": payer["name"] if payer else None,
                     "needs_payer": d.pay_from is None, "card_id": d.card_id})
    # estimated statements (cards.estimated_payment_events) due in the window
    for pay_from, day, name, change, _ in cards.estimated_payment_events(conn, today, date.fromordinal(end)):
        payer = names.get(pay_from)
        rows.append({"date": day.isoformat(), "overdue": False, "name": name, "amount_cents": -change, "type": "card",
                     "account_name": payer["name"] if payer else None, "needs_payer": False, "estimated": True})
    rows.sort(key=lambda r: (not r["overdue"], r["date"], r["type"] != "income"))
    return rows

