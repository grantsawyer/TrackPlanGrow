"""Manual accounts: assets and liabilities Plaid can't see, with dated values.

* An account's current balance is always its latest valuation (by date).
  Valuations can't be future-dated, so "latest" is never a value that hasn't
  happened yet.
* Liabilities are entered as the positive amount owed (Plaid's convention),
  so net worth needs no special handling for them.
* History: on every snapshot day, the account's value is its most recent
  valuation on or before that day (a step function). Valuations dated before
  the first snapshot don't create new days, because a chart day containing only
  the manual account would be misleading.
* Accounts that hold transactions (TRANSACTION_TYPES: checking / savings, credit cards, loans; user's request
  2026-11-13, docs/import-plan.md phase 4: imported files and transactions you add): the value on a day is your latest
  value on or before it **plus what its transactions moved since** (a purchase raises what a card owes and lowers what
  a bank account has). Before your first value, it's worked back from that value, as far as the first transaction.
  An import's running Balance column adds values of its own (engine.py), so the balance matches the bank's.
* Archive (sold the car) hides the account from today on, keeping history.
  Delete permanently (a mistake) removes it and its history.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from fintrack import networth
from fintrack.money import parse_amount


@dataclass(frozen=True)
class ManualKind:
    label: str
    account_type: str
    classification: str


# What you pick in the form -> how it's stored and counted.
KINDS = {
    "cash": ManualKind("Cash", "depository", "asset"),
    # user's request 2026-11-13: a card whose transactions come from its bank's downloads (Import page)
    "credit": ManualKind("Credit card", "credit", "liability"),
    "investment": ManualKind("Investment / retirement", "investment", "asset"),
    "property": ManualKind("Property (home, car, ...)", "property", "asset"),
    "other_asset": ManualKind("Other asset", "other", "asset"),
    # A purpose account (purposes.py, user's request 2026-10-12): money you lent; its balance is built from the
    # transfers linked to it, never typed.
    "lent": ManualKind("Money lent", "receivable", "asset"),
    "loan": ManualKind("Loan", "loan", "liability"),
    "other_liability": ManualKind("Other liability", "other", "liability"),
}


# Manual accounts of these types hold transactions (imported, or added by you) and their balance follows them.
TRANSACTION_TYPES = ("depository", "credit", "loan")


def takes_transactions(account) -> bool:
    return bool(account["is_manual"]) and account["type"] in TRANSACTION_TYPES


def takes_transactions_sql(alias: str = "") -> str:
    """SQL: accounts that can hold transactions (connected ones, and manual ones of TRANSACTION_TYPES)."""
    a = f"{alias}." if alias else ""
    return f"({a}is_manual = 0 OR {a}type IN ({', '.join(repr(t) for t in TRANSACTION_TYPES)}))"


class ManualError(ValueError):
    """A manual-account change was refused; the message is shown to you."""


# ---------------------------------------------------------------- parsing

def parse_money(text: str) -> int:
    """'$18,500' / '18500' / '18,500.00' -> cents. Must be zero or more."""
    try:
        return parse_amount(text)
    except ValueError as exc:
        if "positive" in str(exc):
            raise ManualError("Enter a positive amount. For a liability, enter how much you owe.") from None
        raise ManualError(str(exc)) from None


def parse_date(text: str, today: date | None = None) -> str:
    try:
        day = date.fromisoformat((text or "").strip())
    except ValueError:
        raise ManualError(f"{text!r} isn't a date (YYYY-MM-DD).") from None
    if day > (today or date.today()):
        raise ManualError("Values can't be dated in the future.")
    return day.isoformat()


# ---------------------------------------------------------------- reads

def list_accounts(conn: sqlite3.Connection, include_archived: bool = False) -> list[sqlite3.Row]:
    return conn.execute(
        f"""
        SELECT a.*,
               (SELECT MAX(date) FROM manual_valuations v WHERE v.account_id = a.id) AS as_of,
               (SELECT COUNT(*) FROM manual_valuations v WHERE v.account_id = a.id) AS valuation_count
        FROM accounts a
        WHERE a.is_manual = 1 {'' if include_archived else 'AND a.is_hidden = 0'}
        ORDER BY a.is_hidden, a.classification, a.name
        """
    ).fetchall()


def get_account(conn: sqlite3.Connection, account_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM accounts WHERE id = ? AND is_manual = 1", (account_id,)).fetchone()
    if row is None:
        raise LookupError(f"No manual account {account_id}")
    return row


def valuations(conn: sqlite3.Connection, account_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM manual_valuations WHERE account_id = ? ORDER BY date DESC", (account_id,)
    ).fetchall()


def kind_of(account: sqlite3.Row) -> str:
    for key, kind in KINDS.items():
        if (kind.account_type, kind.classification) == (account["type"], account["classification"]):
            return key
    return "other_asset" if account["classification"] == "asset" else "other_liability"


# ---------------------------------------------------------------- derived state

def value_on(day: str, values: list[tuple[str, int]], flows: list[tuple[str, int]], sign: int) -> int | None:
    """The account's value at the end of `day`: the latest value on or before it, moved by the transactions after that
    value's date (`flows`: (date, Plaid-signed total), in date order; `sign` +1 for what you owe, -1 for what you
    have). Before the first value: worked back from it, from the first transaction on. None before any of it."""
    before = [v for v in values if v[0] <= day]
    if before:
        start, value = before[-1]
        return value + sign * sum(amount for d, amount in flows if start < d <= day)
    if values and flows and day >= flows[0][0]:
        start, value = values[0]
        return value - sign * sum(amount for d, amount in flows if day < d <= start)
    return None


def _series(conn: sqlite3.Connection, account) -> tuple[list, list, int]:
    """(its values, its transactions' totals by day, the sign): what value_on works from."""
    values = [(r[0], r[1]) for r in conn.execute(
        "SELECT date, value_cents FROM manual_valuations WHERE account_id = ? ORDER BY date", (account["id"],))]
    flows = [(r[0], r[1]) for r in conn.execute(
        """SELECT date, SUM(amount_cents) FROM transactions WHERE account_id = ? AND removed_at IS NULL
           GROUP BY date ORDER BY date""", (account["id"],))] if takes_transactions(account) else []
    return values, flows, 1 if account["classification"] == "liability" else -1


def balance_on(conn: sqlite3.Connection, account_id: int, day: str) -> int | None:
    """A manual account's value at the end of `day` (a card's statement balance on its closing date)."""
    return value_on(day, *_series(conn, get_account(conn, account_id)))


def _refresh(conn: sqlite3.Connection, account_id: int) -> None:
    """Recompute the current balance and this account's snapshot history from its values (and, for an account that
    holds transactions, its transactions), then refresh today's snapshot. Call after any value or transaction change."""
    account = get_account(conn, account_id)
    values, flows, sign = _series(conn, account)
    # the balance: as of the latest of today, your last value and its last transaction (a value can be dated ahead)
    last_day = max([date.today().isoformat(), values[-1][0]] + ([flows[-1][0]] if flows else [])) if values else None
    current = value_on(last_day, values, flows, sign) if values else None
    days = [r[0] for r in conn.execute("SELECT DISTINCT date FROM balance_snapshots ORDER BY date")]
    with conn:
        conn.execute(
            "UPDATE accounts SET current_balance_cents = ?, balance_updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (current, account_id),
        )
        # Rebuild this account's rows on every existing snapshot day: its value then, where it had one.
        conn.execute("DELETE FROM balance_snapshots WHERE account_id = ?", (account_id,))
        if not account["is_hidden"]:
            conn.executemany(
                """INSERT INTO balance_snapshots (date, account_id, balance_cents, classification, account_type)
                   VALUES (?, ?, ?, ?, ?)""",
                [(d, account_id, v, account["classification"], account["type"]) for d in days
                 if (v := value_on(d, values, flows, sign)) is not None])
    # Today's snapshot reflects the change immediately (dashboard, net worth chart).
    networth.take_snapshot(conn)


def refresh(conn: sqlite3.Connection, account_id: int) -> None:
    """After a manual account's transactions changed (an import, its undo, one you added or deleted)."""
    _refresh(conn, account_id)


# ---------------------------------------------------------------- accounts

def create_account(
    conn: sqlite3.Connection, name: str, kind: str, value_text: str, date_text: str, note: str = ""
) -> int:
    name = (name or "").strip()
    if not name:
        raise ManualError("Give the account a name.")
    if kind not in KINDS:
        raise ManualError("Choose what kind of account this is.")
    value, day = parse_money(value_text), parse_date(date_text)
    spec = KINDS[kind]
    account_id = conn.execute(
        "INSERT INTO accounts (name, type, classification, is_manual, iso_currency_code) VALUES (?, ?, ?, 1, 'USD')",
        (name, spec.account_type, spec.classification),
    ).lastrowid
    conn.execute(
        "INSERT INTO manual_valuations (account_id, date, value_cents, note) VALUES (?, ?, ?, ?)",
        (account_id, day, value, note.strip() or None),
    )
    conn.commit()
    _refresh(conn, account_id)
    return account_id


def update_account(conn: sqlite3.Connection, account_id: int, name: str, kind: str) -> None:
    """Rename or change kind (e.g. Other asset -> Property). History is re-derived.
    While the account has a type you gave it (account_types.py), that sets its kind, so `kind` is ignored."""
    account = get_account(conn, account_id)
    name = (name or "").strip()
    if not name:
        raise ManualError("Give the account a name.")
    if account["type_kind"] is not None:
        conn.execute("UPDATE accounts SET name = ? WHERE id = ?", (name, account_id))
        conn.commit()
        return
    if kind not in KINDS:
        raise ManualError("Choose what kind of account this is.")
    spec = KINDS[kind]
    conn.execute(
        "UPDATE accounts SET name = ?, type = ?, classification = ? WHERE id = ?",
        (name, spec.account_type, spec.classification, account_id),
    )
    conn.commit()
    _refresh(conn, account_id)


def archive_account(conn: sqlite3.Connection, account_id: int, archived: bool = True) -> None:
    """Hide from today on (history kept), or bring back."""
    get_account(conn, account_id)
    conn.execute("UPDATE accounts SET is_hidden = ? WHERE id = ?", (1 if archived else 0, account_id))
    conn.commit()
    if archived:
        # Keep past snapshot rows; only today's snapshot drops it.
        networth.take_snapshot(conn)
    else:
        _refresh(conn, account_id)


def delete_account(conn: sqlite3.Connection, account_id: int, typed_name: str) -> None:
    """Permanently remove the account, its values, and its history."""
    account = get_account(conn, account_id)
    if typed_name.strip().lower() != account["name"].strip().lower():
        raise ManualError("Type the account name exactly to delete it permanently.")
    with conn:
        conn.execute("DELETE FROM balance_snapshots WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM manual_valuations WHERE account_id = ?", (account_id,))
        # Holdings mirrored from its positions (manual_positions.py); positions cascade.
        conn.execute("DELETE FROM holdings WHERE account_id = ?", (account_id,))
        # Its transactions and activity (imported or yours; their tags, links and edits cascade) and, for a card, its
        # statement and paying account (2026-11-14: manual accounts hold these too).
        conn.execute("DELETE FROM transactions WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM investment_transactions WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM card_statements WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM card_payment_accounts WHERE card_account_id = ? OR pay_from_account_id = ?",
                     (account_id, account_id))
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


# ---------------------------------------------------------------- valuations

def _refuse_if_positions(conn: sqlite3.Connection, account_id: int) -> None:
    """An account with position lines gets its values from them (manual_positions.py); a purpose account (Money lent)
    from its linked transfers and adjustments (purposes.py)."""
    if conn.execute("SELECT 1 FROM manual_positions WHERE account_id = ?", (account_id,)).fetchone():
        raise ManualError("This account's value comes from its positions. Change a position instead.")
    if conn.execute("SELECT 1 FROM accounts WHERE id = ? AND type = 'receivable'", (account_id,)).fetchone():
        raise ManualError("This account's value comes from its linked transfers. Add an adjustment instead.")


def add_valuation(conn: sqlite3.Connection, account_id: int, value_text: str, date_text: str, note: str = "") -> None:
    get_account(conn, account_id)
    _refuse_if_positions(conn, account_id)
    value, day = parse_money(value_text), parse_date(date_text)
    try:
        conn.execute(
            "INSERT INTO manual_valuations (account_id, date, value_cents, note) VALUES (?, ?, ?, ?)",
            (account_id, day, value, note.strip() or None),
        )
    except sqlite3.IntegrityError:
        conn.rollback()
        raise ManualError(f"There's already a value for {day}. Edit that one instead.") from None
    conn.commit()
    _refresh(conn, account_id)


def update_valuation(
    conn: sqlite3.Connection, valuation_id: int, value_text: str, date_text: str, note: str = ""
) -> int:
    row = conn.execute("SELECT * FROM manual_valuations WHERE id = ?", (valuation_id,)).fetchone()
    if row is None:
        raise LookupError(f"No valuation {valuation_id}")
    _refuse_if_positions(conn, row["account_id"])
    value, day = parse_money(value_text), parse_date(date_text)
    try:
        conn.execute(
            "UPDATE manual_valuations SET value_cents = ?, date = ?, note = ? WHERE id = ?",
            (value, day, note.strip() or None, valuation_id),
        )
    except sqlite3.IntegrityError:
        conn.rollback()
        raise ManualError(f"There's already a value for {day}.") from None
    conn.commit()
    _refresh(conn, row["account_id"])
    return row["account_id"]


def delete_valuation(conn: sqlite3.Connection, valuation_id: int) -> int:
    row = conn.execute("SELECT * FROM manual_valuations WHERE id = ?", (valuation_id,)).fetchone()
    if row is None:
        raise LookupError(f"No valuation {valuation_id}")
    _refuse_if_positions(conn, row["account_id"])
    remaining = conn.execute(
        "SELECT COUNT(*) FROM manual_valuations WHERE account_id = ?", (row["account_id"],)
    ).fetchone()[0]
    if remaining <= 1:
        raise ManualError("That's the account's only value. Archive or delete the account instead.")
    conn.execute("DELETE FROM manual_valuations WHERE id = ?", (valuation_id,))
    conn.commit()
    _refresh(conn, row["account_id"])
    return row["account_id"]
