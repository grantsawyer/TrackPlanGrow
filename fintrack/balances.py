"""Balances page: how the total value of your accounts changed over time (user's request 2026-10-12, replacing the
savings-rate view the page had shared with Savings).

Everything comes from the daily net-worth snapshots (`balance_snapshots`), so it agrees with the Net worth page:

    value on a day   = each account's snapshot on the latest snapshot day on or before it (whole days carried, as
                       networth.history does), signed like net worth (liabilities count negative)
    change in period = value at the period's last day - value on the day before its first day
                       (a period that starts before the first snapshot counts from the first snapshot)

So a change includes everything that moves a balance: money put in or taken out, market moves, interest, loan
paydown. Accounts are grouped by kind (GROUPS):

    retirement     investment accounts that are retirement accounts (visuals.is_retirement_account), by account type
    investment     other investment accounts, by institution (a manual account by its name)
    purposes       purpose accounts such as money lent ("Other assets"), by account name
    cash           checking & savings, by account
    credit_cards   credit cards, by account (negative: what you owe; its own card since 2026-10-12, user's request)
    property_loans everything else in net worth: property, loans, other assets / liabilities

The groups add up to the change in net worth. Known limitation: connecting an account (or adding a manual one) shows
its whole balance as a change on the day it first appears, exactly as the net worth chart jumps.
"""

from __future__ import annotations

import bisect
import calendar
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from fintrack import networth
from fintrack.spending import month_bounds, shift_month

# (key, label) in display order; the Net worth card's parts and the table's columns follow it.
# Cash first (user's request 2026-10-13).
GROUPS = [("cash", "Cash"), ("retirement", "Retirement"), ("investment", "Investment"), ("purposes", "Other assets"),
          ("credit_cards", "Credit cards"), ("property_loans", "Property & loans")]
LABELS = dict(GROUPS)


@dataclass
class PeriodChange:
    month: str                 # "YYYY-MM", or the date when by day
    partial: bool = False      # the current month, still in progress
    start: str = ""            # set when a date range cuts the month: first / last day counted (as MonthSavings)
    end: str = ""
    before: dict[tuple[str, str], int] = field(default_factory=dict)   # (group, segment) -> value before the period
    after: dict[tuple[str, str], int] = field(default_factory=dict)    # ... at its last day

    def value_before(self, group: str | None = None) -> int:
        return sum(v for (g, _s), v in self.before.items() if group in (None, g))

    def value_after(self, group: str | None = None) -> int:
        return sum(v for (g, _s), v in self.after.items() if group in (None, g))

    def change(self, group: str | None = None) -> int:
        """Change in value over the period: one group's, or net worth's (None)."""
        return self.value_after(group) - self.value_before(group)

    def pct(self, group: str | None = None) -> float | None:
        """Change as a share of the starting value's size, so the sign says whether net worth rose: a card balance
        going from -$2,000 to -$2,500 is -25%. None when it started at zero (no meaningful %)."""
        base = self.value_before(group)
        return self.change(group) / abs(base) if base else None

    def segment_changes(self, group: str) -> dict[str, int]:
        keys = {k for k in (*self.before, *self.after) if k[0] == group}
        return {s: self.after.get((g, s), 0) - self.before.get((g, s), 0) for g, s in keys}

    def has(self, group: str) -> bool:
        """Did this group hold anything at either end of the period?"""
        return any(v for (g, _s), v in (*self.before.items(), *self.after.items()) if g == group)

    @property
    def clipped_days(self) -> tuple[int, int] | None:
        """(first, last) day counted when a date range cuts the month, else None (shown as "Oct 12–31, 2025")."""
        if not (self.start or self.end):
            return None
        year, mon = int(self.month[:4]), int(self.month[5:])
        return (int(self.start[8:]) if self.start else 1,
                int(self.end[8:]) if self.end else calendar.monthrange(year, mon)[1])


def _segments(conn: sqlite3.Connection) -> dict[int, dict[str, tuple[str, str]]]:
    """account id -> {snapshot type: (group, segment)}: how the account is grouped if a snapshot recorded it as an
    investment, purpose, bank or credit card account. Other types are grouped by type alone (_group_of)."""
    from fintrack import visuals                          # lazy: visuals imports many modules

    out = {}
    for a in conn.execute("SELECT a.id, a.name, a.subtype, a.type_kind, a.type_label, a.manual_institution,"
                          " i.institution_name FROM accounts a LEFT JOIN items i ON i.id = a.item_id"):
        if visuals.is_retirement_account(a):
            investment = ("retirement", visuals.investment_type_label(a))      # by account type
        else:                                             # by institution, a manual account's own alike (2026-11-14)
            investment = ("investment", a["institution_name"] or (a["manual_institution"] or "").strip() or a["name"])
        out[a["id"]] = {"investment": investment, "receivable": ("purposes", a["name"]),
                        "depository": ("cash", a["name"]), "credit": ("credit_cards", a["name"])}
    return out


def _group_of(row: sqlite3.Row, segments: dict[int, dict[str, tuple[str, str]]]) -> tuple[str, str]:
    """(group, segment) of one snapshot row, from the type and classification recorded that day."""
    kind, side = row["account_type"], row["classification"]
    fallback = {"investment": ("investment", "Other investments"), "receivable": ("purposes", "Money lent"),
                "depository": ("cash", "Checking & savings"), "credit": ("credit_cards", "Credit cards")}
    expected_side = {"investment": "asset", "receivable": "asset", "depository": "asset", "credit": "liability"}
    if expected_side.get(kind) == side:
        return segments.get(row["account_id"], {}).get(kind, fallback[kind])
    return ("property_loans", networth.BREAKDOWN_LABELS.get((kind, side), "Other"))


class Snapshots:
    """Every snapshot day's values by (group, segment), loaded once; `on(day)` = the latest day on or before it."""

    def __init__(self, conn: sqlite3.Connection):
        segments = _segments(conn)
        self.values: dict[str, dict[tuple[str, str], int]] = {}
        for r in conn.execute("SELECT date, account_id, balance_cents, classification, account_type"
                              " FROM balance_snapshots ORDER BY date"):
            day = self.values.setdefault(r["date"], {})
            key = _group_of(r, segments)
            day[key] = day.get(key, 0) + networth.signed(r["balance_cents"], r["classification"])
        self.days = sorted(self.values)

    def on(self, day: str) -> dict[tuple[str, str], int] | None:
        """Values on the latest snapshot day on or before `day` (None before the first snapshot)."""
        index = bisect.bisect_right(self.days, day)
        return self.values[self.days[index - 1]] if index else None

    def first(self) -> dict[tuple[str, str], int]:
        return self.values[self.days[0]]


def _day_before(iso: str) -> str:
    return (date.fromisoformat(iso) - timedelta(days=1)).isoformat()


def _change(snaps: Snapshots, lo: str, hi: str) -> tuple[dict, dict] | None:
    """(values before lo, values at hi), or None when hi is before the first snapshot."""
    after = snaps.on(hi)
    if after is None:
        return None
    before = snaps.on(_day_before(lo))
    return (before if before is not None else snaps.first()), after


def range_changes(conn: sqlite3.Connection, start: str, end: str, today: date | None = None,
                  by_day: bool = False, snaps: Snapshots | None = None) -> list[PeriodChange]:
    """One PeriodChange per calendar month from `start` to `end` (inclusive ISO dates), oldest first; the first and last
    months count only their days inside the range (as savings.range_series does). `by_day`: one per day. Periods before
    the first snapshot are left out."""
    snaps = snaps or Snapshots(conn)
    today = today or date.today()
    current = f"{today:%Y-%m}"
    out = []
    month = start if by_day else start[:7]
    while month <= (end if by_day else end[:7]):
        if by_day:
            first = last = month
            following = (date.fromisoformat(month) + timedelta(days=1)).isoformat()
        else:
            first, following = month_bounds(month)
            last = _day_before(following)
        lo, hi = max(first, start), min(last, end)
        partial = not by_day and month == current and hi >= today.isoformat()
        values = _change(snaps, lo, min(hi, today.isoformat()))
        if values is not None:
            out.append(PeriodChange(month, partial, start=lo if lo != first else "",
                                    end=hi if hi != last and not partial else "", before=values[0], after=values[1]))
        month = following if by_day else shift_month(month, 1)
    return out


def summary(conn: sqlite3.Connection, start: str, end: str, today: date | None = None,
            snaps: Snapshots | None = None) -> PeriodChange | None:
    """The change over a whole period at once (the tiles and the cards' measures). None before the first snapshot."""
    snaps = snaps or Snapshots(conn)
    values = _change(snaps, start, min(end, (today or date.today()).isoformat()))
    return PeriodChange(start[:7], before=values[0], after=values[1]) if values else None


# ---------------------------------------------------------------- recent activity (the Net worth page's Detailed cards)

@dataclass
class Activity:
    """One row of a Detailed card's activity list (user's request 2026-10-13). `amount_cents` uses the Transactions page's
    sign (positive = money out of the account, negative = money in, shown "+" in green); None = not a movement (a
    Balance entry on a manual account: `value_cents` is shown instead)."""
    date: str
    description: str
    source: str                       # the account's name (coloured like its segment in the card's chart)
    info: str                         # type / category
    amount_cents: int | None
    value_cents: int | None = None
    segment: str = ""                 # the card chart's segment it belongs to (account type / institution / account),
                                      # for the source's colour (user's requests 2026-10-13)

    @property
    def detail(self) -> str:
        return f"{self.source} · {self.info}"


def _group_accounts(conn: sqlite3.Connection, group: str) -> list[sqlite3.Row]:
    """The visible accounts of one kind, by their current type (the cards' grouping, _group_of)."""
    from fintrack import visuals                          # lazy: visuals imports many modules

    rows = conn.execute("SELECT * FROM accounts WHERE is_hidden = 0").fetchall()
    if group in ("retirement", "investment"):
        return [a for a in rows if a["type"] == "investment" and a["classification"] == "asset"
                and visuals.is_retirement_account(a) == (group == "retirement")]
    kind = {"cash": ("depository", "asset"), "credit_cards": ("credit", "liability"), "purposes": ("receivable", "asset")}
    return [a for a in rows if (a["type"], a["classification"]) == kind[group]] if group in kind else []


def recent_activity(conn: sqlite3.Connection, group: str, start: str, end: str, limit: int = 7) -> list[Activity]:
    """The newest `limit` movements (7: the Detailed card's list, user's request 2026-10-13) dated inside the range on
    one kind's accounts, newest first:
    cash / credit cards = their transactions (transactions_effective: your descriptions and categories, hidden accounts
    out); retirement / investment = connected accounts' investment activity (investment_activity: sweeps hidden) + the
    events you record on manual accounts (manual_positions.events); other assets = transfers linked to purpose accounts +
    their adjustments (purposes.entries)."""
    from fintrack import investment_activity, manual_positions, purposes

    accounts = _group_accounts(conn, group)
    if not accounts:
        return []
    segments = _segments(conn)                            # account id -> the chart's segment label, per type

    def segment(account_id: int, kind: str) -> str:
        return segments.get(account_id, {}).get(kind, ("", ""))[1]

    found: list[Activity] = []
    if group in ("cash", "credit_cards"):
        marks = ",".join("?" * len(accounts))
        for r in conn.execute(
                f"""SELECT date, description, account_id, account_name, category_label, amount_cents FROM transactions_effective
                    WHERE account_id IN ({marks}) AND date BETWEEN ? AND ? ORDER BY date DESC, id DESC LIMIT ?""",
                [a["id"] for a in accounts] + [start, end, limit]):
            found.append(Activity(r["date"], r["description"], r["account_name"], r["category_label"],
                                  r["amount_cents"], segment=segment(r["account_id"],
                                                                     "depository" if group == "cash" else "credit")))
    elif group in ("retirement", "investment"):
        # every account's activity alike, connected or manual (the ledger; docs/parity-plan.md, 2026-11-14)
        ids = [a["id"] for a in accounts]
        if ids:
            where_sql, params = investment_activity.where([], [], start, end)
            where_sql += f" AND t.account_id IN ({','.join('?' * len(ids))})"
            for r in investment_activity.rows(conn, where_sql, params + ids, limit=limit):
                label = r["type_label"] + (f" {r['ticker']}" if r["ticker"] else "")
                found.append(Activity(r["date"], r["description"], r["account_name"], label, r["amount_cents"],
                                      segment=segment(r["account_id"], "investment")))
        # and a manual account's balances (a statement's, yours): they move no money, but they're its history
        for a in (a for a in accounts if a["is_manual"]):
            for e in manual_positions.events(conn, a["id"]):
                if start <= e.date <= end and e.kind == "balance" and not e.from_activity:
                    found.append(Activity(e.date, e.line, a["name"], "Balance", None, e.dollars,
                                          segment=segment(a["id"], "investment")))
    elif group == "purposes":
        for a in accounts:
            for e in purposes.entries(conn, a["id"]):
                if start <= e.date <= end:   # entries: + adds to the account (money lent) = money into it
                    found.append(Activity(e.date, e.description, a["name"], e.kind.capitalize(), -e.amount_cents,
                                          segment=segment(a["id"], "receivable")))
    found.sort(key=lambda x: x.date, reverse=True)
    return found[:limit]


# ---------------------------------------------------------------- account rows (the Balances page's account cards)

@dataclass
class AccountRow:
    """One account on a Balances account card (user's request 2026-10-13)."""
    id: int
    name: str
    institution: str
    value_cents: int                  # signed like net worth: what's owed on a card is negative
    is_manual: bool
    refreshed: str | None             # UTC timestamp of its connection's last sync (Plaid accounts)
    logo: str | None = None           # the institution's saved logo file (logos.institution_logo_files)
    statement_cents: int | None = None   # a credit card's current statement balance (cards.py; user's request 2026-10-13)
    due_date: str | None = None          # ... and when it's due (ISO)
    owed_cents: int | None = None        # ... still owed on it (cards.statements: payments since the statement date)
    overdue: bool = False
    days_to_due: int | None = None       # the countdown shown while something is owed


def account_rows(conn: sqlite3.Connection, group: str) -> list[AccountRow]:
    """The visible accounts of one kind with a balance, largest first (by size, so the card owing most comes first)."""
    from fintrack import cards, logos

    files = logos.institution_logo_files(conn)
    cards.refresh_manual_statements(conn)                 # a manual card's statement, worked out (2026-11-14)
    statements = ({r["account_id"]: r for r in conn.execute("SELECT * FROM card_statements")}
                  if group == "credit_cards" else {})
    owed = {s.card_id: s for s in cards.statements(conn)} if statements else {}
    names = {r["id"]: (r["institution_name"], r["last_synced_at"]) for r in conn.execute(
        "SELECT id, institution_name, last_synced_at FROM items")}
    rows = []
    for a in _group_accounts(conn, group):
        if a["current_balance_cents"] is None:
            continue
        institution, synced = names.get(a["item_id"], (None, None))
        if a["is_manual"]:
            institution = a["manual_institution"]        # the name you gave it (user's request 2026-10-13), if any
        rows.append(AccountRow(a["id"], a["name"], institution or "", networth.signed(a["current_balance_cents"],
                                                                                    a["classification"]),
                               bool(a["is_manual"]), None if a["is_manual"] else synced, files.get(a["id"]),
                               statements[a["id"]]["statement_balance_cents"] if a["id"] in statements else None,
                               statements[a["id"]]["due_date"] if a["id"] in statements else None))
    for r in rows:                                        # paid / overdue / countdown (user's request 2026-10-13)
        due = owed.get(r.id)
        if due is not None:
            r.owed_cents, r.overdue = due.owed_cents, due.overdue
            r.days_to_due = (due.due_date - date.today()).days if due.due_date else None
    return sorted(rows, key=lambda r: -abs(r.value_cents))


def kind_of_accounts(conn: sqlite3.Connection) -> dict[int, str]:
    """account id -> its kind (a GROUPS key) by its current type, the same rules as the cards' snapshots (_group_of):
    for the Accounts card's proportion bars (user's request 2026-10-13)."""
    segments = _segments(conn)
    return {a["id"]: _group_of({"account_id": a["id"], "account_type": a["type"], "classification": a["classification"]},
                               segments)[0]
            for a in conn.execute("SELECT id, type, classification FROM accounts")}


def proportion_bars(account_groups: list[dict], kinds: dict[int, str]) -> dict[str, list[dict]]:
    """The Accounts card's proportion bars (Balances, user's request 2026-10-13): per side ("asset" / "liability"), one
    segment per account type row with its share of the side's total, coloured like the Net worth page's cards: the
    kind's hue (the kind holding most of the type's value), shaded 0..3 by its order among the side's types of that
    kind (as charts.js shades a card's segments). `account_groups` = web/dashboard.account_groups."""
    bars = {}
    for side in account_groups:
        total = sum(max(g["total_cents"], 0) for g in side["groups"])
        seen: dict[str, int] = {}
        segs = []
        for g in side["groups"]:
            by_kind: dict[str, int] = {}
            for r in g["rows"]:
                kind = kinds.get(r["acct"]["id"], "property_loans")
                by_kind[kind] = by_kind.get(kind, 0) + abs(r["acct"]["current_balance_cents"] or 0)
            kind = max(by_kind, key=by_kind.get) if by_kind else "property_loans"
            shade = min(seen.get(kind, 0), 3)
            seen[kind] = seen.get(kind, 0) + 1
            segs.append({"label": g["label"], "kind": kind, "shade": shade,
                         "share": max(g["total_cents"], 0) / total if total > 0 else 0})
        bars[side["classification"]] = segs
    return bars


SIDE_LABELS = {("property_loans", "asset"): "Property & other", ("property_loans", "liability"): "Loans & other"}


def regroup_by_kind(account_groups: list[dict], kinds: dict[int, str]) -> list[dict]:
    """The Accounts card's sides regrouped by kind of account, in the cards' order (GROUPS: Cash, Retirement, Investment,
    Other assets, Credit cards, then Property & loans), for the Balances page (user's request 2026-10-13; the
    Overview keeps web/dashboard.account_groups' grouping by account type). Same shape as account_groups."""
    sides = []
    for side in account_groups:
        rows = [r for g in side["groups"] for r in g["rows"]]
        groups = []
        for key, label in GROUPS:
            members = [r for r in rows if kinds.get(r["acct"]["id"], "property_loans") == key]
            if members:
                members.sort(key=lambda r: -abs(r["acct"]["current_balance_cents"] or 0))
                groups.append({"label": SIDE_LABELS.get((key, side["classification"]), label), "rows": members,
                               "total_cents": sum(r["acct"]["current_balance_cents"] or 0 for r in members)})
        sides.append({**side, "groups": groups})
    return sides
