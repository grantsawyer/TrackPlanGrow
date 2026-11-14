"""Purpose accounts (user's request 2026-10-12, migration 0028): money moved out of your bank accounts toward a
defined purpose, e.g. money lent to someone for school.

A purpose account is a manual account of kind "Money lent" (accounts.type 'receivable', an asset). Its balance is
never typed: it is the running total of the transfers linked to it (money out of a bank account adds to it, money
back reduces it) plus your adjustments (an opening balance, a part written off). That running total is written into
the account's value history (manual_valuations, note "from transfers"), so net worth, its history and the
Accounts card include it with no other changes.

Transfers are linked by rules ("bank text contains ...", applied to past transactions and after every sync) or by
hand from a transaction's edit panel; a link you set or removed by hand wins over the rules. Only transfer-kind
transactions on visible, non-investment accounts can be linked (spending stays spending).

The Savings page leaves linked transfers and adjustments out (they aren't "other transfers" either; savings.py);
the account's value, and so net worth and the Accounts page, includes them. Adjustments record money lent or received
outside a linked bank transfer (e.g. a deposit paid another way, cash back, a part written off). Every write is skipped when nothing changed (writes bump the
page revision, which reloads other open tabs).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from fintrack import manual

ACCOUNT_TYPE = "receivable"            # manual.KINDS["lent"]
VALUATION_NOTE = "from transfers"


class PurposeError(ValueError):
    """Refused; the message is shown to you."""


def is_purpose(account: sqlite3.Row) -> bool:
    return bool(account["is_manual"]) and account["type"] == ACCOUNT_TYPE


def accounts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Visible purpose accounts, by name (the transaction panel's choices)."""
    return conn.execute("SELECT id, name FROM accounts WHERE is_manual = 1 AND type = ? AND is_hidden = 0 ORDER BY name",
                        (ACCOUNT_TYPE,)).fetchall()


def _purpose_account(conn: sqlite3.Connection, account_id: int) -> sqlite3.Row:
    account = manual.get_account(conn, account_id)
    if not is_purpose(account):
        raise PurposeError("That isn't a purpose (Money lent) account.")
    return account


# Linkable: transfer-kind transactions on visible accounts other than investment accounts.
_LINKABLE = "account_hidden = 0 AND account_type <> 'investment' AND kind = 'transfer'"


# ---------------------------------------------------------------- rules and links

def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def apply_rules(conn: sqlite3.Connection) -> bool:
    """Bring rule links up to date: every linkable transaction whose bank name or merchant contains a rule's text
    links to that rule's account (lowest rule id first), unless you linked or unlinked it by hand. True if changed."""
    wanted: dict[int, int] = {}
    for rule in conn.execute("SELECT r.id, r.account_id, r.pattern FROM purpose_rules r JOIN accounts a ON a.id = r.account_id"
                             " WHERE a.is_hidden = 0 ORDER BY r.id"):
        pattern = f"%{_escape(rule['pattern'].strip())}%"
        for (txn_id,) in conn.execute(
                f"SELECT id FROM transactions_effective WHERE {_LINKABLE}"
                " AND (name LIKE ? ESCAPE '\\' OR COALESCE(merchant_name, '') LIKE ? ESCAPE '\\')", (pattern, pattern)):
            wanted.setdefault(txn_id, rule["account_id"])
    manual_ids = {r[0] for r in conn.execute("SELECT transaction_id FROM purpose_links WHERE source = 'you'")}
    current = {r[0]: r[1] for r in conn.execute("SELECT transaction_id, account_id FROM purpose_links WHERE source = 'rule'")}
    changed = False
    for txn_id, account_id in wanted.items():
        if txn_id in manual_ids or current.get(txn_id) == account_id:
            continue
        conn.execute("INSERT INTO purpose_links (transaction_id, account_id, source) VALUES (?, ?, 'rule')"
                     " ON CONFLICT (transaction_id) DO UPDATE SET account_id = excluded.account_id", (txn_id, account_id))
        changed = True
    for txn_id in set(current) - set(wanted):
        conn.execute("DELETE FROM purpose_links WHERE transaction_id = ? AND source = 'rule'", (txn_id,))
        changed = True
    conn.commit()
    return changed


def set_link(conn: sqlite3.Connection, transaction_id: int, account_id: int | None) -> bool:
    """Your choice for one transaction: link it to a purpose account, or None = not linked (rules leave it alone).
    True if anything changed."""
    t = conn.execute(f"SELECT id FROM transactions_effective WHERE id = ? AND {_LINKABLE}", (transaction_id,)).fetchone()
    if account_id is not None:
        if t is None:
            raise PurposeError("Only transfers can be linked to a purpose account.")
        _purpose_account(conn, account_id)
    current = conn.execute("SELECT account_id, source FROM purpose_links WHERE transaction_id = ?",
                           (transaction_id,)).fetchone()
    if current is None and account_id is None:
        return False                                   # nothing linked and nothing asked for
    if current is not None and current["source"] == "you" and current["account_id"] == account_id:
        return False
    with conn:
        conn.execute("INSERT INTO purpose_links (transaction_id, account_id, source) VALUES (?, ?, 'you')"
                     " ON CONFLICT (transaction_id) DO UPDATE SET account_id = excluded.account_id, source = 'you'",
                     (transaction_id, account_id))
    return True


def link_of(conn: sqlite3.Connection, transaction_id: int) -> int | None:
    row = conn.execute("SELECT account_id FROM purpose_links WHERE transaction_id = ?", (transaction_id,)).fetchone()
    return row[0] if row else None


def add_rule(conn: sqlite3.Connection, account_id: int, pattern: str) -> None:
    _purpose_account(conn, account_id)
    pattern = (pattern or "").strip()
    if len(pattern) < 3:
        raise PurposeError("Enter at least 3 characters of the bank's description.")
    with conn:
        conn.execute("INSERT INTO purpose_rules (account_id, pattern) VALUES (?, ?)", (account_id, pattern[:80]))
    update(conn)


def delete_rule(conn: sqlite3.Connection, rule_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM purpose_rules WHERE id = ?", (rule_id,))
    update(conn)


def rules(conn: sqlite3.Connection, account_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT r.*, (SELECT COUNT(*) FROM purpose_links l WHERE l.account_id = r.account_id"
                        " AND l.source = 'rule') AS linked FROM purpose_rules r WHERE r.account_id = ? ORDER BY r.id",
                        (account_id,)).fetchall()


# ---------------------------------------------------------------- adjustments

def add_adjustment(conn: sqlite3.Connection, account_id: int, amount_text: str, date_text: str, note: str = "") -> None:
    """A change that isn't money through your bank: '+5000' (e.g. an opening balance) or '-2000' (written off)."""
    _purpose_account(conn, account_id)
    day = manual.parse_date(date_text)
    text = (amount_text or "").strip().replace("−", "-")
    sign = -1 if text.startswith("-") else 1
    cents = manual.parse_money(text.lstrip("+-"))
    if cents == 0:
        raise PurposeError("Enter an amount, e.g. -2,000 for a part written off.")
    with conn:
        conn.execute("INSERT INTO purpose_adjustments (account_id, date, amount_cents, note) VALUES (?, ?, ?, ?)",
                     (account_id, day, sign * cents, (note or "").strip()[:120] or None))
    refresh(conn, account_id)


def delete_adjustment(conn: sqlite3.Connection, adjustment_id: int) -> int:
    row = conn.execute("SELECT account_id FROM purpose_adjustments WHERE id = ?", (adjustment_id,)).fetchone()
    if row is None:
        raise PurposeError("That adjustment no longer exists.")
    with conn:
        conn.execute("DELETE FROM purpose_adjustments WHERE id = ?", (adjustment_id,))
    refresh(conn, row[0])
    return row[0]


def adopt_opening_value(conn: sqlite3.Connection, account_id: int) -> None:
    """A new purpose account was created with a typed starting value (the manual-account form asks for one): turn it
    into an opening adjustment, then build the balance from links and adjustments."""
    for v in conn.execute("SELECT date, value_cents FROM manual_valuations WHERE account_id = ? AND COALESCE(note, '') <> ?",
                          (account_id, VALUATION_NOTE)).fetchall():
        if v["value_cents"]:
            conn.execute("INSERT INTO purpose_adjustments (account_id, date, amount_cents, note) VALUES (?, ?, ?, ?)",
                         (account_id, v["date"], v["value_cents"], "Opening balance"))
    conn.commit()
    update(conn)


# ---------------------------------------------------------------- balance

@dataclass
class Entry:
    date: str
    kind: str                  # "transfer" (linked) or "adjustment"
    amount_cents: int          # + adds to the balance (money lent), - reduces it (money back, written off)
    description: str
    transaction_id: int | None = None
    adjustment_id: int | None = None
    source: str | None = None  # links: "you" / "rule"
    account_name: str | None = None


def entries(conn: sqlite3.Connection, account_id: int) -> list[Entry]:
    """The account's history, newest first: linked transfers and adjustments."""
    result = [Entry(r["date"], "transfer", r["amount_cents"], r["description"], transaction_id=r["id"], source=r["source"],
                    account_name=r["account_name"])
              for r in conn.execute("""
                  SELECT t.id, t.date, t.amount_cents, t.description, t.account_name, l.source
                  FROM purpose_links l JOIN transactions_effective t ON t.id = l.transaction_id
                  WHERE l.account_id = ? AND t.account_hidden = 0 AND t.account_type <> 'investment'
                    AND t.kind = 'transfer'""", (account_id,))]
    result += [Entry(r["date"], "adjustment", r["amount_cents"], r["note"] or "Adjustment", adjustment_id=r["id"])
               for r in conn.execute("SELECT * FROM purpose_adjustments WHERE account_id = ?", (account_id,))]
    return sorted(result, key=lambda e: (e.date, e.kind), reverse=True)


def refresh(conn: sqlite3.Connection, account_id: int) -> bool:
    """Write the running balance into the account's value history: one value per day with a change (floored at $0,
    because manual values can't be negative). Skipped when unchanged. True if written."""
    account = manual.get_account(conn, account_id)
    running, wanted = 0, {}
    for e in sorted(entries(conn, account_id), key=lambda e: e.date):
        running += e.amount_cents
        wanted[e.date] = running
    if not wanted:                                     # nothing yet: $0 from the day it was created
        wanted[(account["created_at"] or date.today().isoformat())[:10]] = 0
    wanted = {d: max(v, 0) for d, v in wanted.items()}   # manual values are >= 0
    stored = {r["date"]: (r["value_cents"], r["note"]) for r in
              conn.execute("SELECT date, value_cents, note FROM manual_valuations WHERE account_id = ?", (account_id,))}
    if {d: v for d, (v, _n) in stored.items()} == wanted and all(n == VALUATION_NOTE for _v, n in stored.values()):
        return False
    with conn:
        conn.execute("DELETE FROM manual_valuations WHERE account_id = ?", (account_id,))
        conn.executemany("INSERT INTO manual_valuations (account_id, date, value_cents, note) VALUES (?, ?, ?, ?)",
                         [(account_id, d, v, VALUATION_NOTE) for d, v in sorted(wanted.items())])
    manual._refresh(conn, account_id)                  # current balance, snapshot history, today's snapshot
    return True


def update(conn: sqlite3.Connection) -> bool:
    """Apply the rules and refresh every purpose account (after a sync, a rule change, a category edit)."""
    changed = apply_rules(conn)
    for (account_id,) in conn.execute("SELECT id FROM accounts WHERE is_manual = 1 AND type = ?", (ACCOUNT_TYPE,)).fetchall():
        changed = refresh(conn, account_id) or changed
    return changed
