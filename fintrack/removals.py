"""Posted transactions the bank removed (user's request 2026-10-22, migration 0038).

Plaid's /transactions/sync lists transactions as `removed`. For a PENDING one that's routine (its posted version
replaces it) and sync deletes it. A POSTED one is different: banks sometimes withdraw posted transactions and re-send
them later under new ids, sometimes changed (seen 2026-10-22: 7 posted checking transactions removed with nothing in
their place, and their money-lent links deleted with them). So sync keeps a removed posted transaction, sets
`removed_at`, and leaves it for you to review here. It keeps counting meanwhile, with your category, description and
links.

**Its return.** After every sync, `match_returns` looks for a transaction that seems to be the same one sent again:

    * same account, posted, not itself removed, first seen in the sync that removed it or later (a new Plaid id;
      a transaction already there before the removal is a different one, e.g. next month's autopay, seen 2026-10-22);
    * dated within RETURN_DAYS days of it;
    * the same amount, or the same merchant / bank text (the amount may have been corrected).

Same amount beats same text; then the nearest date; then the lower id. Each return pairs with one removed transaction,
and pairs you called "Not the same" are never suggested again (removal_not_same). The likely return is excluded from
every total until you decide (the transactions_effective view), so nothing is counted twice.

**Your review** (the account's page, "Removed by the bank"; user's request 2026-10-22, moved from Transactions and
the Overview):

    use_return   the return is the same transaction: your category, description, "Not a transfer", money-lent link and
                 recurring link move onto it (where it has none of its own), and the removed one is deleted
    not_same     forget this match (a new one may be found)
    keep         it's real: clear the flag, keep it (and forget any match)
    delete       accept the bank's removal: delete it (with its links)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

RETURN_DAYS = 7
SYNC_MINUTES = 10      # a sync applies its changes within seconds; this allows for a slow one


def _same_text(a: sqlite3.Row, b: sqlite3.Row) -> bool:
    """Same merchant, bank name or raw bank text (case-insensitive), ignoring blanks."""
    def texts(r):
        return {(r[k] or "").strip().lower() for k in ("merchant_name", "name", "original_description")} - {""}
    return bool(texts(a) & texts(b))


def match_returns(conn: sqlite3.Connection) -> int:
    """Pair each removed transaction without a match with its likely return (module docstring). Returns new pairs."""
    removed = conn.execute(
        "SELECT * FROM transactions WHERE removed_at IS NOT NULL AND returned_id IS NULL ORDER BY date, id").fetchall()
    if not removed:
        return 0
    taken = {r[0] for r in conn.execute("SELECT returned_id FROM transactions WHERE returned_id IS NOT NULL")}
    refused = {tuple(r) for r in conn.execute("SELECT transaction_id, candidate_id FROM removal_not_same")}   # Rows -> tuples
    found = 0
    with conn:
        for old in removed:
            day = date.fromisoformat(old["date"])
            best = None
            for new in conn.execute(
                    """SELECT * FROM transactions
                       WHERE account_id = ? AND pending = 0 AND removed_at IS NULL AND id <> ?
                         AND created_at >= datetime(?, ?)
                         AND date BETWEEN date(?, ?) AND date(?, ?)""",
                    (old["account_id"], old["id"], old["removed_at"], f"-{SYNC_MINUTES} minutes",
                     old["date"], f"-{RETURN_DAYS} days", old["date"], f"+{RETURN_DAYS} days")):
                if new["id"] in taken or (old["id"], new["id"]) in refused or new["id"] < old["id"]:
                    continue
                same_amount = new["amount_cents"] == old["amount_cents"]
                if not (same_amount or _same_text(old, new)):
                    continue
                rank = (0 if same_amount else 1, abs((date.fromisoformat(new["date"]) - day).days), new["id"])
                if best is None or rank < best[0]:
                    best = (rank, new["id"])
            if best:
                conn.execute("UPDATE transactions SET returned_id = ? WHERE id = ?", (best[1], old["id"]))
                taken.add(best[1])
                found += 1
    return found


# ---------------------------------------------------------------- review list

@dataclass
class Removed:
    old: sqlite3.Row                 # the removed transaction (transactions_effective)
    new: sqlite3.Row | None          # its likely return, if found
    changes: list[str]               # what differs between them ("Amount", "Date", "Description")
    link_name: str | None            # the money-lent account the removed one is linked to


def pending_review(conn: sqlite3.Connection, account_id: int | None = None) -> list[Removed]:
    """Removed transactions waiting for your decision (one account's, or all), newest first, each with its likely
    return."""
    rows = conn.execute("SELECT * FROM transactions_effective WHERE removed_at IS NOT NULL AND (? IS NULL OR account_id = ?)"
                        " ORDER BY date DESC, id DESC", (account_id, account_id)).fetchall()
    result = []
    for old in rows:
        new = conn.execute("SELECT * FROM transactions_effective WHERE id = ?", (old["returned_id"],)).fetchone() \
            if old["returned_id"] else None
        changes = []
        if new is not None:
            if new["amount_cents"] != old["amount_cents"]:
                changes.append("Amount")
            if new["date"] != old["date"]:
                changes.append("Date")
            if (new["merchant_name"], new["name"]) != (old["merchant_name"], old["name"]):
                changes.append("Description")
        link = conn.execute("SELECT a.name FROM purpose_links l JOIN accounts a ON a.id = l.account_id"
                            " WHERE l.transaction_id = ?", (old["id"],)).fetchone()
        result.append(Removed(old, new, changes, link[0] if link else None))
    return result


def count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM transactions WHERE removed_at IS NOT NULL").fetchone()[0]


# ---------------------------------------------------------------- your decisions

def _removed(conn: sqlite3.Connection, transaction_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM transactions WHERE id = ? AND removed_at IS NOT NULL", (transaction_id,)).fetchone()
    if row is None:
        raise LookupError("That transaction is no longer waiting for review.")
    return row


def use_return(conn: sqlite3.Connection, transaction_id: int) -> int:
    """The return is the same transaction: carry your choices over (where it has none of its own), delete the removed
    one. Returns the return's id."""
    old = _removed(conn, transaction_id)
    new_id = old["returned_id"]
    if new_id is None:
        raise LookupError("No returned transaction was found for it.")
    with conn:
        conn.execute(
            """UPDATE transactions SET
                   user_category = COALESCE(user_category, ?),
                   user_description = COALESCE(NULLIF(TRIM(user_description), ''), ?),
                   transfer_ignore = MAX(transfer_ignore, ?),
                   category_reviewed = MAX(category_reviewed, ?)
               WHERE id = ?""",
            (old["user_category"], old["user_description"], old["transfer_ignore"], old["category_reviewed"], new_id))
        # Links are one row per transaction: move the removed one's unless the return has its own.
        for table in ("purpose_links", "recurring_links"):
            conn.execute(f"UPDATE OR IGNORE {table} SET transaction_id = ? WHERE transaction_id = ?", (new_id, old["id"]))
        # Tags (tags.py): the return gets every tag the removed one had (any it has already are dropped with the removed one).
        conn.execute("UPDATE OR IGNORE transaction_tags SET transaction_id = ? WHERE transaction_id = ?", (new_id, old["id"]))
        conn.execute("DELETE FROM transactions WHERE id = ?", (old["id"],))
    return new_id


def not_same(conn: sqlite3.Connection, transaction_id: int) -> None:
    """Forget the suggested return (never suggest it again), then look for another."""
    old = _removed(conn, transaction_id)
    with conn:
        if old["returned_id"] is not None:
            conn.execute("INSERT OR IGNORE INTO removal_not_same (transaction_id, candidate_id) VALUES (?, ?)",
                         (old["id"], old["returned_id"]))
        conn.execute("UPDATE transactions SET returned_id = NULL WHERE id = ?", (old["id"],))
    match_returns(conn)


def keep(conn: sqlite3.Connection, transaction_id: int) -> None:
    """It's real: clear the flag and any match; it stays as it is."""
    old = _removed(conn, transaction_id)
    with conn:
        conn.execute("UPDATE transactions SET removed_at = NULL, returned_id = NULL WHERE id = ?", (old["id"],))


def delete(conn: sqlite3.Connection, transaction_id: int) -> None:
    """Accept the bank's removal: delete it and its links."""
    old = _removed(conn, transaction_id)
    with conn:
        conn.execute("DELETE FROM transactions WHERE id = ?", (old["id"],))
