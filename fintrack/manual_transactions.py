"""Transactions you add by hand (user's request 2026-11-04), from the Transactions page.

A manual transaction lives on a connected account like any other, with an id 'manual-<random>' that no sync sends, so
sync never changes or removes it (as imported rows' 'csv-…', imports/engine.py). Its category and description are your
own choices, so rules leave it alone; you tag, recategorize and search it as usual, and delete it from its edit panel.

A rewards redemption (on a card whose rewards are set up): two transactions, the cost (with its tags) and a credit
"<description> (Credit)" of the same amount in the same category carrying the rewards used (rewards.transaction_rewards_used, migration 0062). The
category nets to zero, as no money moved, while a tag on the cost shows what the trip cost. They're ordinary
spending otherwise: each earns the category's rate, and those cancel out too.
Changing the cost's category moves the rebate's too; deleting either deletes both.
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import date

from fintrack import categories, manual, rewards, tags
from fintrack.money import parse_amount

PREFIX = "manual-"
CREDIT_SUFFIX = " (Credit)"      # a rewards pair's credit: its cost's description + this (user's request 2026-11-04)
MAX_DESCRIPTION = 120


def is_manual(conn: sqlite3.Connection, transaction_id: int) -> bool:
    row = conn.execute("SELECT plaid_transaction_id FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    return bool(row) and row[0].startswith(PREFIX)


IMPORTED_PREFIX = "csv-"             # imported from a file (imports/engine.py)


def is_imported(conn: sqlite3.Connection, transaction_id: int) -> bool:
    row = conn.execute("SELECT plaid_transaction_id FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    return bool(row) and row[0].startswith(IMPORTED_PREFIX)


def deletable(conn: sqlite3.Connection, transaction_id: int) -> bool:
    """Yours to delete: one you added, or one imported from a file (user's rule 2026-11-14: alike whatever the source;
    only the bank's own, from Plaid, can't be)."""
    return is_manual(conn, transaction_id) or is_imported(conn, transaction_id)


def credit_name(description: str) -> str:
    """'Flight to Denver' -> 'Flight to Denver (Credit)', within the length a description may have."""
    return description[:MAX_DESCRIPTION - len(CREDIT_SUFFIX)] + CREDIT_SUFFIX


def pair_of(conn: sqlite3.Connection, transaction_id: int) -> int | None:
    """The other half of a rewards pair: a cost's rebate or a rebate's cost (None when it isn't one)."""
    row = conn.execute("""SELECT CASE WHEN transaction_id = ? THEN charge_id ELSE transaction_id END
                          FROM transaction_rewards_used WHERE (transaction_id = ? AND charge_id IS NOT NULL) OR charge_id = ?""",
                       (transaction_id, transaction_id, transaction_id)).fetchone()
    return row[0] if row else None


def _insert(conn, account_id: int, day: str, cents: int, description: str, category: str) -> int:
    return conn.execute(
        """INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key,
               user_category) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (PREFIX + secrets.token_hex(8), account_id, day, cents, description, category, category)).lastrowid


def add(conn: sqlite3.Connection, *, account_id: int, day: str, description: str, amount_text: str, direction: str,
        category_key: str, tag_names: str = "", rewards_text: str = "", today: date | None = None) -> int:
    """Add a transaction (the cost, when paid with rewards: `rewards_text` = the rewards used, in the card's unit) and
    return its id. Tags are names separated by commas (a new one is made); they go on the cost only."""
    if conn.execute(f"SELECT 1 FROM accounts WHERE id = ? AND is_hidden = 0 AND {manual.takes_transactions_sql()}",
                    (account_id,)).fetchone() is None:
        raise ValueError("Choose the account.")
    try:
        when = date.fromisoformat((day or "").strip())
    except ValueError:
        raise ValueError("Enter the date.") from None
    if when > (today or date.today()):
        raise ValueError("A transaction can't be dated in the future.")
    description = " ".join((description or "").split())[:MAX_DESCRIPTION]
    if not description:
        raise ValueError("Describe it.")
    try:
        cents = parse_amount(amount_text)
    except ValueError:
        raise ValueError("Enter the amount, like 750.00.") from None
    if cents == 0:
        raise ValueError("Enter the amount, like 750.00.")
    if direction not in ("out", "in"):
        raise ValueError("Choose money out or money in.")
    if not category_key or not categories.category_exists(conn, category_key):
        raise ValueError("Choose its category.")
    used = None
    if (rewards_text or "").strip():
        unit = rewards.balance_unit(conn, account_id)
        if unit is None:
            raise ValueError("Paying with rewards needs a card whose rewards are set up (Credit cards → Estimate rewards).")
        if direction != "out":
            raise ValueError("Only money out can be paid with rewards.")
        used = rewards.parse_rewards_used(unit, rewards_text)
    names = [n.strip() for n in (tag_names or "").split(",") if n.strip()]
    tag_list = [tags.find_or_create(conn, n) for n in names]
    signed = cents if direction == "out" else -cents          # Plaid's sign: + = money out
    with conn:
        cost = _insert(conn, account_id, when.isoformat(), signed, description, category_key)
        if used is not None:
            rebate = _insert(conn, account_id, when.isoformat(), -cents, credit_name(description),
                             category_key)
            conn.execute("INSERT INTO transaction_rewards_used (transaction_id, amount, unit, charge_id) VALUES (?, ?, ?, ?)",
                         (rebate, used, rewards.balance_unit(conn, account_id), cost))
    for tag in tag_list:
        tags.add(conn, [cost], tag.name)
    _follow_balance(conn, account_id)
    return cost


def _follow_balance(conn: sqlite3.Connection, account_id: int) -> None:
    """A manual account's balance follows its transactions (manual.value_on): recompute it after one changes."""
    account = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if account is not None and manual.takes_transactions(account):
        manual.refresh(conn, account_id)


def delete(conn: sqlite3.Connection, transaction_id: int) -> None:
    """Delete a transaction you added (and the other half of its rewards pair) or one imported from a file (importing
    the file again brings it back: user's request 2026-11-14). A bank's transaction from Plaid can't be deleted."""
    if not deletable(conn, transaction_id):
        raise ValueError("Only transactions you added or imported can be deleted.")
    other = pair_of(conn, transaction_id)
    account_id, source_id = conn.execute("SELECT account_id, plaid_transaction_id FROM transactions WHERE id = ?",
                                         (transaction_id,)).fetchone()
    with conn:
        conn.executemany("DELETE FROM transactions WHERE id = ?",
                         [(transaction_id,)] + ([(other,)] if other and is_manual(conn, other) else []))
    _follow_balance(conn, account_id)


def follow_category(conn: sqlite3.Connection, transaction_id: int) -> None:
    """After a category change on half of a manual rewards pair: the other half takes the same, so they still net to zero."""
    other = pair_of(conn, transaction_id)
    if other is None or not is_manual(conn, other):
        return
    mine = conn.execute("SELECT user_category FROM transactions WHERE id = ?", (transaction_id,)).fetchone()[0]
    categories.set_transaction_category(conn, other, mine)


# ---------------------------------------------------------------- a bank charge partly paid with rewards (migration 0063)
# User's request 2026-11-04: a $50 charge that was a $200 ticket, $150 of it paid with points. The $150 is a rewards pair
# of its own (ids 'manual-part-…'): a cost pointing at the charge (`rewards_part_of`) and its rebate carrying the points.
# The pair takes the charge's date, category and tags (`sync_parts`), so the category counts the $50 and a tag $200.

PART_PREFIX = PREFIX + "part-"


def part_of(conn: sqlite3.Connection, charge_id: int) -> tuple[int, int] | None:
    """(the pair's value in cents, the rewards it used) for a charge partly paid with rewards, else None."""
    row = conn.execute(
        """SELECT cost.amount_cents, u.amount FROM transactions cost
           JOIN transaction_rewards_used u ON u.charge_id = cost.id WHERE cost.rewards_part_of = ?""", (charge_id,)).fetchone()
    return (row[0], row[1]) if row else None


def set_part(conn: sqlite3.Connection, charge_id: int, value_text: str, rewards_text: str) -> None:
    """Say a bank charge on a card you've set up was partly paid with rewards: `value_text` dollars paid by `rewards_text`
    of its rewards (both blank: it wasn't, and any pair goes). Changes the pair if it has one."""
    charge = conn.execute("SELECT * FROM transactions_effective WHERE id = ?", (charge_id,)).fetchone()
    if charge is None:
        raise ValueError("That transaction is gone.")
    if not (value_text or "").strip() and not (rewards_text or "").strip():
        remove_part(conn, charge_id)
        return
    if charge["plaid_transaction_id"].startswith(PREFIX) or charge["amount_cents"] <= 0:
        raise ValueError("Only a bank's charge can be partly paid with rewards.")
    unit = rewards.balance_unit(conn, charge["account_id"])
    if unit is None:
        raise ValueError("Set up the card's rewards first (Credit cards → Estimate rewards).")
    try:
        value = parse_amount(value_text)
    except ValueError:
        raise ValueError("Enter what rewards paid, like 150.00.") from None
    if value == 0:
        raise ValueError("Enter what rewards paid, like 150.00.")
    used = rewards.parse_rewards_used(unit, rewards_text)
    existing = conn.execute("SELECT id FROM transactions WHERE rewards_part_of = ?", (charge_id,)).fetchone()
    with conn:
        if existing:
            cost = existing[0]
            conn.execute("UPDATE transactions SET amount_cents = ? WHERE id = ?", (value, cost))
            conn.execute("UPDATE transactions SET amount_cents = ? WHERE id = (SELECT transaction_id FROM transaction_rewards_used"
                         " WHERE charge_id = ?)", (-value, cost))
            conn.execute("UPDATE transaction_rewards_used SET amount = ?, unit = ? WHERE charge_id = ?", (used, unit, cost))
        else:
            what = charge["description"]
            cost = _insert_part(conn, charge, value, f"{what} (with rewards)"[:MAX_DESCRIPTION])
            conn.execute("UPDATE transactions SET rewards_part_of = ? WHERE id = ?", (charge_id, cost))
            rebate = _insert_part(conn, charge, -value, credit_name(what))
            conn.execute("INSERT INTO transaction_rewards_used (transaction_id, amount, unit, charge_id) VALUES (?, ?, ?, ?)",
                         (rebate, used, unit, cost))
    sync_parts(conn)


def _insert_part(conn, charge, cents: int, description: str) -> int:
    return conn.execute(
        """INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key,
               user_category) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (PART_PREFIX + secrets.token_hex(8), charge["account_id"], charge["date"], cents, description, charge["category"],
         charge["category"])).lastrowid


def remove_part(conn: sqlite3.Connection, charge_id: int) -> None:
    cost = conn.execute("SELECT id FROM transactions WHERE rewards_part_of = ?", (charge_id,)).fetchone()
    if cost:
        delete(conn, cost[0])


def sync_parts(conn: sqlite3.Connection) -> None:
    """Each charge's rewards pair takes the charge's date and category (the cost and rebate both) and its tags (the cost);
    a pair whose charge is gone is deleted. Plain SQL, writing only what differs, so it can follow any change: it runs
    after category and tag changes (categories.py, tags.py) and every rules.apply_all (after a sync)."""
    parts = conn.execute(
        """SELECT cost.id, u.transaction_id AS rebate, cost.rewards_part_of AS charge, cost.date, cost.user_category,
                  rebate.date AS rebate_date, rebate.user_category AS rebate_category, e.date AS charge_date,
                  e.category AS charge_category
           FROM transactions cost JOIN transaction_rewards_used u ON u.charge_id = cost.id
           JOIN transactions rebate ON rebate.id = u.transaction_id
           LEFT JOIN transactions_effective e ON e.id = cost.rewards_part_of
           WHERE cost.plaid_transaction_id LIKE ?""", (PART_PREFIX + "%",)).fetchall()
    if not parts:
        return
    with conn:
        for p in parts:
            if p["charge_date"] is None:               # its charge is gone (a pending one that never posted)
                conn.executemany("DELETE FROM transactions WHERE id = ?", [(p["id"],), (p["rebate"],)])
                continue
            for tid, day, category in ((p["id"], p["date"], p["user_category"]),
                                       (p["rebate"], p["rebate_date"], p["rebate_category"])):
                if (day, category) != (p["charge_date"], p["charge_category"]):
                    conn.execute("UPDATE transactions SET date = ?, user_category = ? WHERE id = ?",
                                 (p["charge_date"], p["charge_category"], tid))
            wanted = {r[0] for r in conn.execute("SELECT tag_id FROM transaction_tags WHERE transaction_id = ?", (p["charge"],))}
            have = {r[0] for r in conn.execute("SELECT tag_id FROM transaction_tags WHERE transaction_id = ?", (p["id"],))}
            conn.executemany("INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
                             [(p["id"], t) for t in wanted - have])
            conn.executemany("DELETE FROM transaction_tags WHERE transaction_id = ? AND tag_id = ?",
                             [(p["id"], t) for t in have - wanted])
