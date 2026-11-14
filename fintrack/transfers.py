"""Matching transfers between your own accounts.

Plaid's categories catch most transfers and credit card payments, which then
count as neither spending nor income because their category's kind is
"transfer". This module is the safety net for the ones Plaid labels as
something else: if money leaves one of your accounts and the same amount
arrives in another within a few days, both halves are marked `is_transfer`,
so they can't be double counted.

To keep false matches rare, a pair must be:
  * exactly equal and opposite amounts,
  * in two different visible accounts, neither of them a loan account,
  * both posted (pending transactions change and get replaced),
  * within MATCH_WINDOW_DAYS of each other,
  * not in an income category (a paycheck must never pair with a purchase),
  * not marked "Not a transfer" by you (transfer_ignore).

Matching is recomputed from scratch each time, so it's deterministic and
always consistent with your latest choices.

Pairs you match yourself on the Transfers page (user's request 2026-10-26) are
stored in `transfer_matches` and applied first; they must meet the same rules
when you make them (the options offered are exactly the ones that do), except
"Not a transfer", which matching them clears. Removing a match there marks both
halves "Not a transfer", so neither pairs again until you match it.

A bank transfer to or from an investment account is matched with that account's own deposit or withdrawal (user's
request 2026-10-31; contributions.investment_links' "activity" pairs: same amount, within its days). That half is
investment activity, not a transaction, so the bank row's transfer_pair_id holds its id negated, as the Transactions list
shows it (investment_activity.as_transactions marks the activity row matched in turn). A transfer that only a
destination rule places has no other half, so it isn't matched (the rule still places it for Savings and Portfolio).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

MATCH_WINDOW_DAYS = 3

# Transactions that can be half of a transfer, whether or not they're paired now or marked "Not a transfer"
# (the rules above, minus the last). `te` = transactions_effective.
ELIGIBLE = """
    te.pending = 0
    AND te.account_hidden = 0
    AND te.account_type != 'loan'
    AND te.kind != 'excluded'                -- e.g. a removed transaction's return awaiting review (removals.py)
    AND te.amount_cents != 0
    AND COALESCE((SELECT kind FROM categories c WHERE c.key = te.category), 'expense') != 'income'
"""


def _gap(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _can_pair(a: sqlite3.Row, b: sqlite3.Row) -> bool:
    """Two eligible transactions could be one transfer: opposite amounts, different accounts, close in time."""
    return (a["amount_cents"] == -b["amount_cents"] and a["account_id"] != b["account_id"]
            and _gap(a["date"], b["date"]) <= MATCH_WINDOW_DAYS)


def detect_transfers(conn: sqlite3.Connection) -> int:
    """Recompute all transfer pairs. Returns the number of pairs found."""
    candidates = conn.execute(
        f"SELECT te.id, te.account_id, te.date, te.amount_cents, te.transfer_ignore FROM transactions_effective te "
        f"WHERE {ELIGIBLE} ORDER BY te.date, te.id"
    ).fetchall()
    by_id = {row["id"]: row for row in candidates}

    # Your pairs first, while both halves still qualify (an amount Plaid changed, a hidden account or an income category
    # since then sets one aside; it comes back if that changes back).
    paired: dict[int, int] = {}
    for out_id, in_id in conn.execute("SELECT out_id, in_id FROM transfer_matches"):
        out, inflow = by_id.get(out_id), by_id.get(in_id)
        if out and inflow and out["amount_cents"] > 0 and out["amount_cents"] == -inflow["amount_cents"] \
                and out["account_id"] != inflow["account_id"]:
            paired[out_id], paired[in_id] = in_id, out_id

    # Then the rest, automatically. Index inflows (negative amounts) by their absolute amount.
    inflows: dict[int, list[sqlite3.Row]] = {}
    for row in candidates:
        if row["amount_cents"] < 0 and not row["transfer_ignore"]:
            inflows.setdefault(-row["amount_cents"], []).append(row)

    for out in candidates:
        if out["amount_cents"] <= 0 or out["id"] in paired or out["transfer_ignore"]:
            continue
        best = None
        for inflow in inflows.get(out["amount_cents"], []):
            if inflow["id"] in paired or inflow["account_id"] == out["account_id"]:
                continue
            gap = _gap(inflow["date"], out["date"])
            if gap > MATCH_WINDOW_DAYS:
                continue
            # Closest date wins; ties go to the earlier transaction (lower id).
            if best is None or (gap, inflow["id"]) < best[0]:
                best = ((gap, inflow["id"]), inflow)
        if best:
            paired[out["id"]] = best[1]["id"]
            paired[best[1]["id"]] = out["id"]

    with conn:
        conn.execute("UPDATE transactions SET is_transfer = 0, transfer_pair_id = NULL WHERE is_transfer = 1 OR transfer_pair_id IS NOT NULL")
        conn.executemany(
            "UPDATE transactions SET is_transfer = 1, transfer_pair_id = ? WHERE id = ?",
            [(other, tid) for tid, other in paired.items()],
        )

    # Then bank transfers with an investment account's own activity (see the module's docstring). Worked out once the
    # pairs above are written: that matching reads which bank rows are transfers, and a row's own match must not count.
    from fintrack import contributions          # imported here: contributions imports visuals, which imports this
    with_activity = [(-link.investment_id, tid) for tid, link in contributions.investment_links(conn).items()
                     if link.how == "activity" and link.investment_id and tid in by_id and tid not in paired
                     and not by_id[tid]["transfer_ignore"]]
    with conn:
        conn.executemany("UPDATE transactions SET is_transfer = 1, transfer_pair_id = ? WHERE id = ?", with_activity)
    return len(paired) // 2 + len(with_activity)


def set_not_transfer(conn: sqlite3.Connection, transaction_id: int, ignore: bool = True) -> None:
    """'Not a transfer' (or undo it), then re-match. Unpairs both halves, and drops a pair you matched yourself."""
    if conn.execute(
        "UPDATE transactions SET transfer_ignore = ? WHERE id = ?", (1 if ignore else 0, transaction_id)
    ).rowcount == 0:
        raise LookupError(f"No transaction {transaction_id}")
    if ignore:
        conn.execute("DELETE FROM transfer_matches WHERE out_id = ? OR in_id = ?", (transaction_id, transaction_id))
    conn.commit()
    detect_transfers(conn)


# ---------------------------------------------------------------- the Transfers page (Settings)

@dataclass
class Half:
    id: int
    date: str
    account_id: int
    account: str              # the account's name
    mask: str | None
    description: str
    amount_cents: int         # Plaid's sign: positive = money out


@dataclass
class Pair:
    out: Half                 # money out of this account...
    into: Half                # ...and into this one
    manual: bool              # you matched it (transfer_matches), not the automatic matching


@dataclass
class Route:
    """All the matched transfers from one account to another in the range."""
    out_account: str
    out_mask: str | None
    in_account: str
    in_mask: str | None
    count: int
    total_cents: int


@dataclass
class Option:
    """A transaction with no match and the ones it could be matched with (all meet the rules above)."""
    txn: Half
    partners: list            # [(Half, paired_with: Half | None)]: a partner already in a pair loses it when matched


_HALF = "te.id, te.date, te.account_id, te.account_name, te.account_mask, te.description, te.amount_cents"


def _half(row: sqlite3.Row) -> Half:
    return Half(row["id"], row["date"], row["account_id"], row["account_name"], row["account_mask"],
                row["description"], row["amount_cents"])


# Investment activity as a Half: its negative id (as listed on Transactions), its account, your description or Plaid's.
# Plaid's investment sign reads like a transaction's: positive = cash out of the account.
_ACTIVITY_HALF = """-t.id AS id, t.date, t.account_id, a.name AS account_name, a.mask AS account_mask,
    COALESCE(t.user_description, t.name) AS description, t.amount_cents"""


def matched_pairs(conn: sqlite3.Connection, start: str, end: str) -> list[Pair]:
    """Every matched pair with either half dated in the range (inclusive ISO dates; blank = open-ended), newest first.
    A bank transfer matched with investment activity is a pair too: the activity is the other half."""
    rows = conn.execute(
        f"""
        SELECT {_HALF}, te.transfer_pair_id,
               EXISTS (SELECT 1 FROM transfer_matches m WHERE m.out_id = te.id) AS manual
        FROM transactions_effective te
        WHERE te.is_transfer = 1 AND te.transfer_pair_id IS NOT NULL
          AND (te.amount_cents > 0 OR te.transfer_pair_id < 0)   -- each pair once: from its money out, or its bank row
        """
    ).fetchall()
    others = {r["id"]: r for r in conn.execute(
        f"SELECT {_HALF} FROM transactions_effective te WHERE te.is_transfer = 1 AND te.amount_cents < 0")}
    others.update({r["id"]: r for r in conn.execute(
        f"""SELECT {_ACTIVITY_HALF} FROM investment_transactions t JOIN accounts a ON a.id = t.account_id
            WHERE t.id IN (SELECT -transfer_pair_id FROM transactions WHERE is_transfer = 1 AND transfer_pair_id < 0)""")})
    pairs = []
    for r in rows:
        other = others.get(r["transfer_pair_id"])
        if other is None:
            continue
        out, into = (r, other) if r["amount_cents"] > 0 else (other, r)
        if any((not start or d >= start) and (not end or d <= end) for d in (r["date"], other["date"])):
            pairs.append(Pair(_half(out), _half(into), bool(r["manual"])))
    pairs.sort(key=lambda p: (max(p.out.date, p.into.date), p.out.id), reverse=True)
    return pairs


def pair_summary(pairs: list[Pair]) -> list[Route]:
    """Matched pairs added up by route (from one account to another), largest total first."""
    routes: dict[tuple[int, int], Route] = {}
    for p in pairs:
        key = (p.out.account_id, p.into.account_id)
        if key not in routes:
            routes[key] = Route(p.out.account, p.out.mask, p.into.account, p.into.mask, 0, 0)
        routes[key].count += 1
        routes[key].total_cents += p.out.amount_cents
    return sorted(routes.values(), key=lambda r: (-r.total_cents, r.out_account, r.in_account))


def _eligible(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    return {r["id"]: r for r in conn.execute(
        f"SELECT {_HALF}, te.is_transfer, te.transfer_pair_id FROM transactions_effective te WHERE {ELIGIBLE}")}


def match_options(conn: sqlite3.Connection, start: str, end: str) -> list[Option]:
    """Transactions in the range with no match that could be matched: each with every transaction that meets the rules
    with it (paired or not, "Not a transfer" or not). When two unmatched transactions could pair with each other, only
    the money out is listed, so a pair shows once. Newest first."""
    rows = _eligible(conn)
    by_amount: dict[int, list[sqlite3.Row]] = {}
    for r in rows.values():
        by_amount.setdefault(r["amount_cents"], []).append(r)

    def partners_of(r):
        return sorted((p for p in by_amount.get(-r["amount_cents"], []) if _can_pair(r, p)),
                      key=lambda p: (_gap(p["date"], r["date"]), p["id"]))

    options = []
    for r in rows.values():
        if r["is_transfer"] or (start and r["date"] < start) or (end and r["date"] > end):
            continue
        partners = partners_of(r)
        if not partners:
            continue
        # money in that an unmatched money out could take is listed under that money out instead
        if r["amount_cents"] < 0 and any(not p["is_transfer"] for p in partners):
            continue
        listed = [(_half(p), _half(rows[p["transfer_pair_id"]]) if p["is_transfer"] and p["transfer_pair_id"] in rows
                   else None) for p in partners]
        options.append(Option(_half(r), listed))
    options.sort(key=lambda o: (o.txn.date, o.txn.id), reverse=True)
    return options


def match(conn: sqlite3.Connection, first_id: int, second_id: int) -> None:
    """Match two transactions as one transfer (yours, kept through re-matching). Both must meet the rules; any pair either
    is in now is undone, and "Not a transfer" is cleared on both."""
    rows = _eligible(conn)
    a, b = rows.get(first_id), rows.get(second_id)
    if a is None or b is None:
        raise ValueError("Those transactions can't be matched: each must be posted, in a visible account that isn't a "
                         "loan, and not income.")
    if not _can_pair(a, b):
        raise ValueError(f"Those transactions can't be matched: they need opposite amounts, in different accounts, "
                         f"within {MATCH_WINDOW_DAYS} days of each other.")
    out_id, in_id = (a["id"], b["id"]) if a["amount_cents"] > 0 else (b["id"], a["id"])
    with conn:
        conn.execute("DELETE FROM transfer_matches WHERE out_id IN (?, ?) OR in_id IN (?, ?)",
                     (out_id, in_id, out_id, in_id))
        conn.execute("INSERT INTO transfer_matches (out_id, in_id) VALUES (?, ?)", (out_id, in_id))
        conn.execute("UPDATE transactions SET transfer_ignore = 0 WHERE id IN (?, ?)", (out_id, in_id))
    detect_transfers(conn)


def unmatch(conn: sqlite3.Connection, transaction_id: int) -> None:
    """Remove a match: both halves become "Not a transfer" (so neither pairs with something else on its own), and a pair
    you matched yourself is forgotten. Either half's id will do; investment activity's (negative) names its bank row,
    which is the one marked."""
    row = conn.execute("SELECT id, transfer_pair_id FROM transactions WHERE is_transfer = 1"
                       " AND (id = ? OR (? < 0 AND transfer_pair_id = ?))",
                       (transaction_id, transaction_id, transaction_id)).fetchone()
    if row is None:
        raise ValueError("That transfer isn't matched any more.")
    ids = (row["id"], row["transfer_pair_id"])
    with conn:
        conn.execute("DELETE FROM transfer_matches WHERE out_id IN (?, ?) OR in_id IN (?, ?)", ids + ids)
        conn.execute("UPDATE transactions SET transfer_ignore = 1 WHERE id IN (?, ?)", ids)
    detect_transfers(conn)


# ---------------------------------------------------------------- the Transactions page's transfer chart (2026-10-31)

CHART_STYLES = ("circle", "flow")       # a chord diagram (each account once) or the left-to-right flow (Sankey)


def chart_style(conn: sqlite3.Connection) -> str:
    """The Transfers view's chart, from its card's gear popup (user's request 2026-10-31): "circle" unless you chose
    "flow"."""
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'transfers_chart'").fetchone()
    return row[0] if row and row[0] in CHART_STYLES else "circle"


def set_chart_style(conn: sqlite3.Connection, style: str) -> None:
    if style not in CHART_STYLES:
        raise ValueError("Choose circle or flow.")
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('transfers_chart', ?)"
                 " ON CONFLICT (key) DO UPDATE SET value = excluded.value WHERE value != excluded.value", (style,))
    conn.commit()
