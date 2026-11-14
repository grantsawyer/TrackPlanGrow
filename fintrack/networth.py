"""Net worth: current total, breakdown by account type, daily snapshots, history.

Sign rules (Plaid's documented meaning of `balances.current`):

    depository   money held; negative = overdraft             -> asset
    investment   total value of holdings + cash               -> asset
    credit       positive = you owe; negative = issuer owes you -> liability
    loan         principal remaining                          -> liability

Net worth = sum(asset balances) - sum(liability balances), using `current`
exactly as reported. Both edge cases fall out without special-casing: an
overdraft is a negative asset, an overpaid card a negative liability.

Only USD (or unspecified-currency) accounts are counted; others are listed as
excluded instead of being silently mixed in at the wrong exchange rate.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

# Breakdown rows, keyed by (account type, classification), in display order.
# Keyed on both so an "other" liability (e.g. a manual account in milestone 7)
# can never land in an assets row.
BREAKDOWN_LABELS = {
    ("depository", "asset"): "Cash",
    ("investment", "asset"): "Investments",
    ("property", "asset"): "Property",          # manual accounts: home, car, ...
    ("other", "asset"): "Other assets",
    ("receivable", "asset"): "Money lent",      # purpose accounts (purposes.py)
    ("credit", "liability"): "Credit cards",
    ("loan", "liability"): "Loans",
    ("other", "liability"): "Other liabilities",
}

COUNTED_CURRENCY = "USD"


def signed(balance_cents: int, classification: str) -> int:
    """A balance's contribution to net worth."""
    return balance_cents if classification == "asset" else -balance_cents


@dataclass
class TypeTotal:
    account_type: str
    label: str
    classification: str
    total_cents: int
    share: float = 0.0  # of its side (assets or liabilities), 0..1, for the bar


@dataclass
class NetWorth:
    assets_cents: int = 0
    liabilities_cents: int = 0
    by_type: list[TypeTotal] = field(default_factory=list)
    excluded: list[sqlite3.Row] = field(default_factory=list)  # non-USD or no balance

    @property
    def net_cents(self) -> int:
        return self.assets_cents - self.liabilities_cents


def _counted(row: sqlite3.Row) -> bool:
    return row["current_balance_cents"] is not None and (row["iso_currency_code"] or COUNTED_CURRENCY) == COUNTED_CURRENCY


def counted_accounts(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    """Visible accounts split into (counted, excluded)."""
    rows = conn.execute("SELECT * FROM accounts WHERE is_hidden = 0 ORDER BY type, name").fetchall()
    return [r for r in rows if _counted(r)], [r for r in rows if not _counted(r)]


def current_net_worth(conn: sqlite3.Connection) -> NetWorth:
    counted, excluded = counted_accounts(conn)
    result = NetWorth(excluded=excluded)
    totals: dict[tuple[str, str], TypeTotal] = {}
    for row in counted:
        key = (row["type"], row["classification"])
        if key not in BREAKDOWN_LABELS:
            key = ("other", row["classification"])
        bucket = totals.setdefault(key, TypeTotal(key[0], BREAKDOWN_LABELS[key], key[1], 0))
        bucket.total_cents += row["current_balance_cents"]
        if row["classification"] == "asset":
            result.assets_cents += row["current_balance_cents"]
        else:
            result.liabilities_cents += row["current_balance_cents"]

    for bucket in totals.values():
        side = result.assets_cents if bucket.classification == "asset" else result.liabilities_cents
        bucket.share = max(bucket.total_cents / side, 0.0) if side > 0 else 0.0
    result.by_type = [totals[k] for k in BREAKDOWN_LABELS if k in totals]
    return result


# ---------------------------------------------------------------- snapshots

def take_snapshot(conn: sqlite3.Connection, day: date | None = None) -> int:
    """Record today's balance for every counted account. Returns rows written.

    Re-running on the same day overwrites that day's rows. Accounts whose
    Item failed to sync are still recorded with their last known balance,
    so every snapshot day is a complete picture.
    """
    day_text = (day or date.today()).isoformat()
    counted, _ = counted_accounts(conn)
    with conn:
        # Clear the day first so an account hidden since an earlier sync today drops out.
        conn.execute("DELETE FROM balance_snapshots WHERE date = ?", (day_text,))
        conn.executemany(
            "INSERT INTO balance_snapshots (date, account_id, balance_cents, classification, account_type)"
            " VALUES (?, ?, ?, ?, ?)",
            [(day_text, r["id"], r["current_balance_cents"], r["classification"], r["type"]) for r in counted],
        )
    return len(counted)


def day_changes(conn: sqlite3.Connection, today: date | None = None) -> dict[int, int]:
    """{account id: current balance - its latest snapshot before today} (the Overview's Accounts card, 1-day change;
    user's request 2026-10-23). Balances as reported (a liability up = more owed). Accounts without an earlier
    snapshot are left out."""
    rows = conn.execute(
        """SELECT a.id, a.current_balance_cents - s.balance_cents
           FROM accounts a
           JOIN balance_snapshots s ON s.account_id = a.id
            AND s.date = (SELECT MAX(date) FROM balance_snapshots WHERE account_id = a.id AND date < ?)
           WHERE a.current_balance_cents IS NOT NULL""",
        ((today or date.today()).isoformat(),)).fetchall()
    return {r[0]: r[1] for r in rows}


@dataclass
class HistoryPoint:
    date: str
    assets_cents: int
    liabilities_cents: int
    carried: bool = False  # True when no snapshot that day; values repeat the previous day

    @property
    def net_cents(self) -> int:
        return self.assets_cents - self.liabilities_cents


def history(conn: sqlite3.Connection, account_type: str | None = None) -> list[HistoryPoint]:
    """One point per day from the first snapshot to the last.

    Days without a snapshot (Mac off, no sync) repeat the previous day's totals
    and are flagged `carried`, so the chart's x-axis spacing stays honest. A
    whole day is carried, never single accounts, so an account removed later
    doesn't linger in the totals.

    `account_type` (e.g. 'investment') limits the totals to accounts of that type, as
    recorded in each day's snapshot. Days still come from every snapshot, so a sync day
    with none of them counts as 0 rather than repeating the day before.
    """
    rows = conn.execute(
        """
        SELECT date,
               SUM(CASE WHEN classification = 'asset' AND (:type IS NULL OR account_type = :type)
                        THEN balance_cents ELSE 0 END) AS assets,
               SUM(CASE WHEN classification = 'liability' AND (:type IS NULL OR account_type = :type)
                        THEN balance_cents ELSE 0 END) AS liabilities
        FROM balance_snapshots GROUP BY date ORDER BY date
        """,
        {"type": account_type},
    ).fetchall()
    if not rows:
        return []
    by_day = {r["date"]: (r["assets"], r["liabilities"]) for r in rows}
    points: list[HistoryPoint] = []
    day, last = date.fromisoformat(rows[0]["date"]), date.fromisoformat(rows[-1]["date"])
    previous = by_day[rows[0]["date"]]
    while day <= last:
        key = day.isoformat()
        if key in by_day:
            previous = by_day[key]
            points.append(HistoryPoint(key, *previous))
        else:
            points.append(HistoryPoint(key, *previous, carried=True))
        day += timedelta(days=1)
    return points


def one_month_before(day: date) -> date:
    """Same day last month, clamped to that month's length (Mar 31 -> Feb 28/29)."""
    year, month = (day.year, day.month - 1) if day.month > 1 else (day.year - 1, 12)
    for candidate_day in (day.day, 30, 29, 28):
        try:
            return date(year, month, candidate_day)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def net_worth_on_or_before(conn: sqlite3.Connection, day: date) -> tuple[str, int] | None:
    """(snapshot date, net worth) of the latest snapshot on or before `day`."""
    row = conn.execute(
        """
        SELECT date,
               SUM(CASE WHEN classification = 'asset' THEN balance_cents ELSE -balance_cents END) AS net
        FROM balance_snapshots WHERE date = (SELECT MAX(date) FROM balance_snapshots WHERE date <= ?)
        GROUP BY date
        """,
        (day.isoformat(),),
    ).fetchone()
    return (row["date"], row["net"]) if row else None


@dataclass
class Change:
    since_date: str
    delta_cents: int


def change_since_last_month(conn: sqlite3.Connection, current_net_cents: int, today: date | None = None) -> Change | None:
    """Change vs the latest snapshot on or before the same day last month.
    None until there's at least a month of history."""
    baseline = net_worth_on_or_before(conn, one_month_before(today or date.today()))
    if baseline is None:
        return None
    return Change(since_date=baseline[0], delta_cents=current_net_cents - baseline[1])


@dataclass
class RangeChange:
    """Net worth, assets and liabilities just before a date range and at its end (the Net worth page's tiles; user's
    request 2026-10-13). Each pct = change / the starting amount, None when that was 0. Liabilities' pct is of the
    amount owed (+ = owing more), so the page shows it with the opposite colours."""
    before: HistoryPoint
    after: HistoryPoint

    @staticmethod
    def _pct(new: int, old: int) -> float | None:
        return (new - old) / abs(old) if old else None

    @property
    def net_pct(self) -> float | None:
        return self._pct(self.after.net_cents, self.before.net_cents)

    @property
    def assets_pct(self) -> float | None:
        return self._pct(self.after.assets_cents, self.before.assets_cents)

    @property
    def liabilities_pct(self) -> float | None:
        return self._pct(self.after.liabilities_cents, self.before.liabilities_cents)


def range_change(conn: sqlite3.Connection, start: str, end: str, today: date | None = None) -> RangeChange | None:
    """The change over a date range from the daily history (carried days included): the day before `start` (or the
    first snapshot, when the range starts before it) to the latest day on or before `end` (and today). The same
    baseline rule as balances.py, so the tiles agree with the cards. None when the range ends before any history."""
    last = min(end, (today or date.today()).isoformat())
    points = history(conn)
    ends = [p for p in points if p.date <= last]
    if not ends:
        return None
    before = [p for p in points if p.date < start]
    return RangeChange(before=before[-1] if before else points[0], after=ends[-1])
