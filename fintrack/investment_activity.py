"""Investment activity for display (user's request 2026-10-12): the rows of `investment_transactions` (migration 0023,
Plaid Investments) shown in the Transactions page's list with your transactions (`as_transactions`, user's request 2026-10-30; was its
own "Investment activity" kind) and on each connected investment account's own page.

Display only: buys, sells, dividends and cash moves inside a brokerage aren't spending or income, so these rows are
never mixed into `transactions_effective`, and no total, chart, budget or rule outside the Transactions page (and the Tags
page, for the ones you tag) reads them (the Savings and Portfolio transfer figures come from contributions.py). You can
edit them like transactions (migration 0052, user's request 2026-10-31): your description, your category, tags. Cash sweeps between an account's settlement fund and its
holdings are hidden, the same as every calculation ignores them (contributions.SWEEP_WORDS).

Amounts use Plaid's investment sign, which reads like the bank side: positive = cash out of the account's cash (a buy,
a withdrawal, a fee), negative = cash in (a sale, a dividend, a deposit).
"""

from __future__ import annotations

import sqlite3

# Readable type, computed in SQL so sorting by type and searching "dividend" work: Plaid's type, and for cash
# movements its subtype.
TYPE_LABEL_SQL = """CASE
    WHEN t.type = 'buy' THEN 'Buy'
    WHEN t.type = 'sell' THEN 'Sell'
    WHEN t.type = 'fee' THEN 'Fee'
    WHEN t.type = 'transfer' THEN 'Transfer'
    WHEN t.type = 'cancel' THEN 'Cancelled'
    WHEN t.subtype = 'contribution' THEN 'Contribution'
    WHEN t.subtype = 'deposit' THEN 'Deposit'
    WHEN t.subtype = 'withdrawal' THEN 'Withdrawal'
    WHEN t.subtype = 'distribution' THEN 'Distribution'
    WHEN t.subtype = 'dividend' THEN 'Dividend'
    WHEN t.subtype = 'interest' THEN 'Interest'
    ELSE upper(substr(COALESCE(t.subtype, t.type), 1, 1)) || substr(replace(COALESCE(t.subtype, t.type), '_', ' '), 2)
END"""

# One row per activity: the columns the row template shows. Ticker from the security's latest recorded price.
SELECT_SQL = f"""
    SELECT t.id, t.date, COALESCE(t.user_description, t.name) AS description, t.name AS plaid_name, t.type, t.subtype,
           t.quantity, t.price, t.amount_cents,
           {TYPE_LABEL_SQL} AS type_label,
           a.id AS account_id, a.name AS account_name, a.mask AS account_mask,
           COALESCE((SELECT sp.ticker FROM security_prices sp WHERE sp.security_id = t.security_id
                     ORDER BY sp.price_date DESC LIMIT 1),
                    -- a manual account's activity points at its line (ledger.py)
                    (SELECT mp.ticker FROM manual_positions mp WHERE 'mpos:' || mp.id = t.security_id),
                    -- an imported row whose ticker Plaid has no security for (ledger.TICKER_PREFIX)
                    CASE WHEN t.security_id LIKE 'ticker:%' THEN substr(t.security_id, 8) END) AS ticker
    FROM investment_transactions t JOIN accounts a ON a.id = t.account_id"""

# Visible accounts only; settlement-fund sweeps are internal, not activity.
BASE_WHERE = "a.is_hidden = 0 AND lower(t.name) NOT LIKE '%sweep%'"

# Sortable columns, the Transactions page's headings -> ORDER BY. Only these strings reach SQL.
SORTS = {
    "date": "t.date {d}, t.id {d}",
    "description": "LOWER(t.name) {d}, t.date DESC, t.id DESC",
    "category": "type_label {d}, t.date DESC, t.id DESC",
    "account": "LOWER(a.name) {d}, t.date DESC, t.id DESC",
    "amount": "t.amount_cents {d}, t.date DESC, t.id DESC",
}


def where(words: list[str], amounts: list[tuple[str, int]], start: str = "", end: str = "",
          account_id: int | None = None) -> tuple[str, list]:
    """WHERE clause + params: search words (description, ticker, type, account), amount terms (operator from the
    Transactions page's own parser), dates, account. All values go in as parameters."""
    clauses, params = [BASE_WHERE], []
    for word in words:
        like = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        clauses.append(f"(t.name LIKE ? ESCAPE '\\' OR t.user_description LIKE ? ESCAPE '\\' OR ({TYPE_LABEL_SQL}) LIKE ? ESCAPE '\\' OR a.name LIKE ? ESCAPE '\\'"
                       " OR t.security_id IN (SELECT security_id FROM security_prices WHERE ticker LIKE ? ESCAPE '\\')"
                       " OR t.security_id IN (SELECT 'mpos:' || id FROM manual_positions WHERE ticker LIKE ? ESCAPE '\\')"
                       " OR ('ticker:' || ?) = t.security_id)")
        params += [like] * 6 + [word.upper()]
    for operator, cents in amounts:
        clauses.append(f"ABS(t.amount_cents) {operator} ?")
        params.append(cents)
    if start:
        clauses.append("t.date >= ?")
        params.append(start)
    if end:
        clauses.append("t.date <= ?")
        params.append(end)
    if account_id is not None:
        clauses.append("t.account_id = ?")
        params.append(account_id)
    return " AND ".join(clauses), params


def rows(conn: sqlite3.Connection, where_sql: str, params: list, sort: str = "date", descending: bool = True,
         limit: int | None = None, offset: int = 0) -> list[sqlite3.Row]:
    order = SORTS.get(sort, SORTS["date"]).format(d="DESC" if descending else "ASC")
    sql = f"{SELECT_SQL} WHERE {where_sql} ORDER BY {order}"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params = params + [limit, offset]
    return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------- in the Transactions page's list (user's request 2026-10-30)

# Each activity's category, found by name (your categories as they are now; "for now", user's request 2026-10-30): the
# first that exists of (parent label, sub label), (parent label,), else a built-in key. Matched ignoring case.
CATEGORY_CHOICES = {
    "dividend": [("Investment Income", "Dividend"), ("Investment Income",), "INCOME"],
    "interest": [("Investment Income", "Interest"), ("Investment Income",), "INCOME"],
    "sell": [("Investment Income", "Sell"), ("Investment Income",), "TRANSFER_IN"],
    "buy": [("Investments", "Buy"), ("Investments",), "TRANSFER_OUT"],
    "fee": [("Investments", "Fee"), ("Investments",), "TRANSFER_OUT"],
    "in": [("Transfer in", "Deposit"), ("Transfer in",), "TRANSFER_IN"],
    "out": [("Transfer out", "Withdrawal"), ("Transfer out",), "TRANSFER_OUT"],
}

# Which of those an activity is: Plaid's type, a cash move's subtype, else the cash's direction (Plaid's investment sign:
# positive = cash out, like a deposit's opposite).
ACTIVITY_SQL = """CASE
    WHEN t.type IN ('buy', 'sell', 'fee') THEN t.type
    WHEN t.subtype IN ('dividend', 'interest') THEN t.subtype
    WHEN t.amount_cents < 0 THEN 'in'
    ELSE 'out'
END"""


def category_keys(conn: sqlite3.Connection) -> dict[str, str]:
    """{activity: category key} by CATEGORY_CHOICES."""
    by_path = {}
    for key, label, parent_label in conn.execute(
            "SELECT c.key, c.label, p.label FROM categories c LEFT JOIN categories p ON p.key = c.parent_key"):
        path = (parent_label.lower(), label.lower()) if parent_label else (label.lower(),)
        by_path.setdefault(path, key)
    keys = {}
    for activity, choices in CATEGORY_CHOICES.items():
        for choice in choices:
            found = choice if isinstance(choice, str) else by_path.get(tuple(x.lower() for x in choice))
            if found:
                keys[activity] = found
                break
    return keys


def as_transactions(conn: sqlite3.Connection) -> tuple[str, list]:
    """The Transactions page's list: transactions_effective plus investment activity as rows of the same columns
    (SELECT ... UNION ALL ..., and its params). An activity's id is negative (its own table's id, so no clash). Its
    category: yours (category_source 'you'), else one by name (category_keys; category_source 'investment'); its kind is
    that category's, or 'transfer' when a bank transfer is matched with it (transfers.detect_transfers keeps the pair on
    the bank row: transfer_pair_id = this row's negative id; here is_transfer and transfer_pair_id point back to it).
    Your description, else Plaid's. Only that page (and the Tags page) reads this: totals elsewhere
    keep leaving activity out (the bank side of a contribution is already there). Sweeps are left out, as everywhere."""
    columns = [r[1] for r in conn.execute("PRAGMA table_info(transactions_effective)")]
    picks = {"id": "-t.id", "account_id": "t.account_id", "date": "t.date", "amount_cents": "t.amount_cents",
             "iso_currency_code": "t.iso_currency_code", "name": "t.name",
             "description": "COALESCE(t.user_description, t.name)", "pending": "0",
             "is_transfer": "(b.id IS NOT NULL)", "transfer_pair_id": "b.id",
             "account_name": "a.name", "account_mask": "a.mask", "account_type": "a.type",
             "account_hidden": "a.is_hidden", "category": "COALESCE(t.user_category, m.key)",
             "category_label": "COALESCE(c.label, t.user_category, m.key)",
             "kind": "CASE WHEN b.id IS NOT NULL THEN 'transfer' ELSE COALESCE(c.kind, 'transfer') END",
             "category_source": "CASE WHEN t.user_category IS NOT NULL THEN 'you' ELSE 'investment' END"}
    keys = category_keys(conn)
    mapping = " UNION ALL ".join("SELECT ? AS activity, ? AS key" for _ in keys)
    params = [v for pair in keys.items() for v in pair]
    activity = f"""SELECT {', '.join(picks.get(col, 'NULL') + ' AS ' + col for col in columns)}
        FROM investment_transactions t JOIN accounts a ON a.id = t.account_id
        JOIN ({mapping}) m ON m.activity = ({ACTIVITY_SQL})
        LEFT JOIN categories c ON c.key = COALESCE(t.user_category, m.key)
        LEFT JOIN transactions b ON b.is_transfer = 1 AND b.transfer_pair_id = -t.id
        WHERE lower(t.name) NOT LIKE '%sweep%'"""
    return f"SELECT {', '.join(columns)} FROM transactions_effective UNION ALL {activity}", params


# ---------------------------------------------------------------- your edits (migration 0052, user's request 2026-10-31)
# Ids here are investment_transactions ids (positive); the Transactions list shows them negated.

DESCRIPTION_MAX = 120


def exists(conn: sqlite3.Connection, activity_id: int) -> bool:
    return conn.execute("SELECT 1 FROM investment_transactions WHERE id = ?", (activity_id,)).fetchone() is not None


def set_description(conn: sqlite3.Connection, activity_id: int, text: str | None) -> None:
    """Your own description, or blank/None = Plaid's (categories.set_transaction_description's twin)."""
    if not exists(conn, activity_id):
        raise LookupError(f"No investment activity {activity_id}")
    text = (text or "").strip()[:DESCRIPTION_MAX] or None
    conn.execute("UPDATE investment_transactions SET user_description = ? WHERE id = ? AND user_description IS NOT ?",
                 (text, activity_id, text))
    conn.commit()


def set_categories(conn: sqlite3.Connection, activity_ids: list[int], key: str | None) -> int:
    """Your category for these rows, or None = back to the one by type. Returns how many changed."""
    if key is not None and conn.execute("SELECT 1 FROM categories WHERE key = ?", (key,)).fetchone() is None:
        raise ValueError(f"Unknown category {key!r}.")
    with conn:
        return conn.executemany(
            "UPDATE investment_transactions SET user_category = ? WHERE id = ? AND user_category IS NOT ?",
            [(key, i, key) for i in activity_ids]).rowcount


def details(conn: sqlite3.Connection, activity_ids: list[int]) -> dict[int, sqlite3.Row]:
    """{id: SELECT_SQL's row} (ticker, quantity, price, type label, Plaid's name): what an activity row shows under its
    description and its edit panel's "From Plaid" line."""
    if not activity_ids:
        return {}
    marks = ",".join("?" * len(activity_ids))
    return {r["id"]: r for r in conn.execute(f"{SELECT_SQL} WHERE t.id IN ({marks})", activity_ids)}
