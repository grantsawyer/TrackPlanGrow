"""Live prices in balances (user's decision 2026-10-12: live prices should impact net worth, reversing the earlier
rule that they never reach it).

Every page reads an account's value from accounts.current_balance_cents. For a connected investment account that is now
the balance the institution reported at the last sync (reported_balance_cents, migration 0029) plus the market move of
its quotable holdings since: for each holding with a live quote, shares x live price - the value reported at the sync.
Cash and holdings with no live quote (e.g. mutual funds Alpaca doesn't cover) keep the reported figure, so the balance
never drifts on guesses; it is the institution's balance, marked to market. A sync resets both columns to the new
reported balance, and the next revalue adds the move again.

`revalue_all` runs wherever quotes are fetched (Overview, Investments, Portfolio, a connected investment account's
page, after Sync now and the scheduled sync): manual position accounts first (manual_positions.revalue_all), then
connected accounts. Writes only when a value changed (every write bumps the page revision), then retakes today's
net-worth snapshot so the day's history follows; past days keep their values.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from fintrack import manual_positions, networth, prices
from fintrack.money import to_cents


def live_balance(conn: sqlite3.Connection, account: sqlite3.Row, live: dict) -> int | None:
    """Reported balance + the market move of the account's quotable holdings that have a live quote."""
    if account["reported_balance_cents"] is None:
        return None
    move = 0
    for h in conn.execute(
            f"SELECT ticker, quantity, value_cents, security_type FROM holdings WHERE account_id = ? AND is_cash = 0"
            f" AND ticker IS NOT NULL AND quantity IS NOT NULL AND value_cents IS NOT NULL", (account["id"],)):
        quote = live.get(h["ticker"])
        if quote is None or (h["security_type"] or "").lower() not in prices.QUOTABLE_TYPES:
            continue
        move += to_cents(h["quantity"] * quote.price) - h["value_cents"]
    return account["reported_balance_cents"] + move


def revalue_connected(conn: sqlite3.Connection, live: dict | None, today: date | None = None) -> bool:
    """Mark connected investment accounts to market (see the module docstring). True if any balance changed."""
    if not live:
        return False
    changed = False
    for account in conn.execute(
            "SELECT * FROM accounts WHERE is_manual = 0 AND is_hidden = 0"
            " AND 'investment' IN (type, COALESCE(plaid_type, ''))").fetchall():
        value = live_balance(conn, account, live)
        if value is not None and value != account["current_balance_cents"]:
            conn.execute("UPDATE accounts SET current_balance_cents = ? WHERE id = ?", (value, account["id"]))
            changed = True
    conn.commit()
    if changed:
        networth.take_snapshot(conn, today)      # today's net-worth history follows
    return changed


def revalue_all(conn: sqlite3.Connection, live: dict | None, today: date | None = None) -> bool:
    """Manual position accounts, then connected investment accounts, from the same live quotes."""
    changed = manual_positions.revalue_all(conn, live, today)
    return revalue_connected(conn, live, today) or changed
