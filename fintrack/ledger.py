"""Investment activity you enter or import, for every investment account alike (docs/parity-plan.md phase 2; user's
rule 2026-11-14: manual and connected accounts look and work the same).

**The ledger** is `investment_transactions`, the table Plaid's investment activity is in: a manual account's buys,
sells, dividends, contributions and fees are rows there too, with Plaid's types and sign (+ = cash out of the
account's cash), so they're on the Transactions list and the account's page with the same categories, edits and tags,
and the Savings page counts a contribution the same way (contributions.py reads `cash` rows with subtype
`contribution` / `deposit` / `withdrawal` / `distribution`). Ids: 'act-<random>' for yours, 'imp-<hash>' imported; a
contribution that buys shares is two rows, as Plaid shows a 401(k)'s: the contribution (cash in) and the buy (`<id>/2`).

**A manual account's holdings follow its activity.** Its rows point at its lines (`security_id` 'mpos:<line id>'; cash
moves at none, its Cash line). `rebuild_positions` turns them into the lines' changes, one per line and day
(`manual_position_changes.from_activity` = 1): shares on the line, or dollars when the row has no quantity (a fund
tracked through a proxy, or a line kept in dollars), and the cash on the Cash line; they record nothing as paid in
(`contributed_cents` 0), since the contribution rows say that. A day a line has a balance of yours (or a statement's)
keeps it: it's that day's closing amount. Any other entry of yours that day is a clash, reported. Then the lines are re-derived and the account revalued (manual_positions.py).
"""

from __future__ import annotations

import secrets
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

MANUAL_PREFIX = "act-"               # activity you added by hand
IMPORTED_PREFIX = "imp-"             # imported (imports/investments.py)
LINE_PREFIX = "mpos:"                # a manual account's line, as a security_id
TICKER_PREFIX = "ticker:"            # a ticker Plaid has no security for (activity imported into a connected account)


def line_security(line_id: int) -> str:
    return f"{LINE_PREFIX}{line_id}"


def line_of(security_id: str | None) -> int | None:
    return int(security_id[len(LINE_PREFIX):]) if security_id and security_id.startswith(LINE_PREFIX) else None


@dataclass(frozen=True)
class LedgerRow:
    id: str
    type: str                        # Plaid's: buy / sell / cash / fee / transfer
    subtype: str | None
    quantity: float | None           # shares (+ in, - out); None: the row is in dollars only
    amount_cents: int                # Plaid's investment sign: + = cash out, - = cash in


def rows_for(base_id: str, kind: str, shares: Decimal | None, cash: int, dollars: int, action: str,
             by_dollars: bool = False) -> list[LedgerRow]:
    """The ledger rows for one activity (investments.ACTION_KINDS): `shares` moved (+ / -), `cash` into the account's
    cash (+) / out (-), `dollars` its size. `by_dollars`: the shares belong to another fund (a proxy), so none are kept."""
    q = None if (shares is None or by_dollars) else float(shares)
    if kind in ("buy", "reinvest"):
        return [LedgerRow(base_id, "buy", "buy", q, dollars)]
    if kind == "sell":
        return [LedgerRow(base_id, "sell", "sell", q, -dollars)]
    if kind == "income":
        return [LedgerRow(base_id, "cash", "interest" if "INTEREST" in action.upper() else "dividend", None, -dollars)]
    if kind == "contribution":
        rows = [LedgerRow(base_id, "cash", "contribution", None, -dollars)]
        if shares is not None:                           # it bought shares: the buy, as a 401(k)'s activity shows it
            rows.append(LedgerRow(f"{base_id}/2", "buy", "buy", q, dollars))
        return rows
    if kind == "withdrawal":
        rows = [LedgerRow(base_id, "cash", "withdrawal", None, dollars)]
        if shares is not None:                           # it sold shares first
            rows.insert(0, LedgerRow(f"{base_id}/2", "sell", "sell", q, -dollars))
        return rows
    if kind == "fee":
        return [LedgerRow(base_id, "fee", "fee", q, dollars)]
    return [LedgerRow(base_id, "transfer", None, None, -cash)]     # another cash move: as written


def write(conn: sqlite3.Connection, account_id: int, day: str, name: str, rows: list[LedgerRow],
          security_id: str | None, price: Decimal | None = None, batch_id: int | None = None) -> None:
    """Insert one activity's rows (no commit: the caller's transaction)."""
    for r in rows:
        conn.execute(
            """INSERT INTO investment_transactions (plaid_investment_transaction_id, account_id, date, name, type, subtype,
                   security_id, quantity, price, amount_cents, iso_currency_code, import_batch_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'USD', ?)""",
            (r.id, account_id, day, name[:200], r.type, r.subtype,
             security_id if r.type in ("buy", "sell", "fee") or r.quantity is not None else None,
             r.quantity, float(price) if price else None, r.amount_cents, batch_id))


def is_yours(conn: sqlite3.Connection, activity_id: int) -> bool:
    """Added by you or imported, so yours to delete (Plaid's own can't be)."""
    row = conn.execute("SELECT plaid_investment_transaction_id FROM investment_transactions WHERE id = ?",
                       (activity_id,)).fetchone()
    return bool(row) and row[0].startswith((MANUAL_PREFIX, IMPORTED_PREFIX))


def delete(conn: sqlite3.Connection, activity_id: int) -> None:
    """Delete an activity you added or imported, both rows of a two-row one, and rebuild a manual account's holdings."""
    row = conn.execute("SELECT account_id, plaid_investment_transaction_id FROM investment_transactions WHERE id = ?",
                       (activity_id,)).fetchone()
    if row is None or not is_yours(conn, activity_id):
        raise ValueError("Only activity you added or imported can be deleted.")
    base = row[1].split("/", 1)[0]
    with conn:
        conn.execute("DELETE FROM investment_transactions WHERE plaid_investment_transaction_id = ?"
                     " OR plaid_investment_transaction_id LIKE ?", (base, base + "/%"))
    if conn.execute("SELECT is_manual FROM accounts WHERE id = ?", (row[0],)).fetchone()[0]:
        rebuild_positions(conn, row[0])


# ---------------------------------------------------------------- a manual account's holdings from its activity

CASH = None                          # the Cash line's key in a rebuild


def _cash_line(conn: sqlite3.Connection, account_id: int, create: bool) -> int | None:
    found = conn.execute("""SELECT id FROM manual_positions WHERE account_id = ? AND ticker IS NULL
                            ORDER BY lower(name) <> 'cash', id LIMIT 1""", (account_id,)).fetchone()
    if found or not create:
        return found[0] if found else None
    return conn.execute("INSERT INTO manual_positions (account_id, ticker, name, kind) VALUES (?, NULL, 'Cash', 'cash')",
                        (account_id,)).lastrowid


def rebuild_positions(conn: sqlite3.Connection, account_id: int) -> list[str]:
    """Rewrite a manual account's activity-made line changes from its ledger (see the module's docstring); returns the
    clashes with your own entries (a line and a day). Raises manual_positions.PositionError, changing nothing, when a
    line would go below 0."""
    from fintrack import manual_positions

    rows = conn.execute("""SELECT date, type, quantity, amount_cents, security_id FROM investment_transactions
                           WHERE account_id = ? ORDER BY date, id""", (account_id,)).fetchall()
    # (line id or CASH, day) -> [shares, dollars, all fees]
    groups: dict[tuple, list] = defaultdict(lambda: [0.0, 0, True])
    for r in rows:
        line, kind, q, amount = line_of(r["security_id"]), r["type"], r["quantity"], r["amount_cents"]
        fee = kind == "fee"
        if line is not None and kind in ("buy", "sell", "fee"):
            g = groups[(line, r["date"])]
            if q is not None:
                g[0] += q
            else:                                        # in dollars (a proxy's, or a dollars line): its size
                g[1] += abs(amount) if kind == "buy" else -abs(amount)
            g[2] = g[2] and fee
            if fee:
                continue                                 # a fee charged to a holding comes out of it, not the cash
        g = groups[(CASH, r["date"])]
        g[1] += -amount                                  # + = cash out of the account's cash
        g[2] = g[2] and fee
    clashes: list[str] = []
    started = min((r["date"] for r in rows), default=None)
    with conn:                                           # all or nothing: a PositionError rolls it back
        touched = {r[0] for r in conn.execute(
            """SELECT DISTINCT c.position_id FROM manual_position_changes c JOIN manual_positions p
               ON p.id = c.position_id WHERE p.account_id = ? AND c.from_activity = 1""", (account_id,))}
        conn.execute("""DELETE FROM manual_position_changes WHERE from_activity = 1 AND position_id IN
                        (SELECT id FROM manual_positions WHERE account_id = ?)""", (account_id,))
        for (line, day), (shares, dollars, fees) in sorted(groups.items(), key=lambda kv: (kv[0][1], str(kv[0][0]))):
            if abs(shares) < 1e-12 and not dollars:
                continue
            position_id = line if line is not CASH else _cash_line(conn, account_id, create=True)
            position = conn.execute("SELECT * FROM manual_positions WHERE id = ?", (position_id,)).fetchone()
            if position is None:
                continue
            yours = conn.execute("SELECT entry FROM manual_position_changes WHERE position_id = ? AND date = ?",
                                 (position_id, day)).fetchone()
            if yours is not None:
                # a balance of yours (or a statement's) is that day's closing amount: the day's activity is in it;
                # anything else of yours that day can't be combined with it
                if yours["entry"] not in (None, "opening"):
                    clashes.append(f"{position['name']} on {day}")
                continue
            if abs(shares) >= 1e-12:
                values = (0.0, None, None, "contribution", shares)
            elif position["ticker"]:
                values = (0.0, None, dollars, "contribution", None)
            else:
                values = (None, 0, dollars, "contribution", None)
            conn.execute("""INSERT INTO manual_position_changes (position_id, date, quantity, value_cents, cash_cents,
                            entry, delta_quantity, fee, contributed_cents, from_activity)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1)""",
                         (position_id, day, *values, 1 if fees else 0))
            touched.add(position_id)
        for position_id in touched:
            if conn.execute("SELECT 1 FROM manual_positions WHERE id = ?", (position_id,)).fetchone():
                manual_positions.rederive(conn, position_id)
    manual_positions.revalue(conn, account_id, since=started)
    return clashes


# ---------------------------------------------------------------- adding one by hand (the account page's Add activity)

def add(conn: sqlite3.Connection, account_id: int, kind: str, day_text: str, holding: str, new_ticker: str,
        quantity_text: str, price_text: str, amount_text: str, description: str) -> None:
    """One activity you enter on a manual investment account (the account page's Add activity). `holding`: a line's id,
    'cash', or 'new' (with `new_ticker`). Quantities and amounts are sizes (the kind says which way), except another
    cash move's amount (+ in, - out). With no quantity, a buy or sale is in dollars (shares at that day's price, as a
    fund tracked through a proxy is). Refused, with nothing written, if a line would go below 0 or it clashes with an
    entry of yours that day."""
    from fintrack import manual, manual_positions
    from fintrack.imports import investments
    from fintrack.imports.layout import parse_money
    from fintrack.money import to_cents

    account = manual.get_account(conn, account_id)
    if not account["is_manual"] or account["type"] != "investment":
        raise ValueError("Activity is added here for a manual investment account.")
    if kind not in investments.ACTION_KINDS or kind == "ignore":
        raise ValueError("Choose what kind of activity it is.")
    day = manual.parse_date(day_text)

    def number(text: str) -> Decimal | None:
        if not (text or "").strip():
            return None
        value = parse_money(text)
        if value is None:
            raise ValueError(f"“{text}” isn't a number.")
        return value

    quantity, price, amount = number(quantity_text), number(price_text), number(amount_text)
    if amount is None and quantity is not None and price is not None:
        amount = abs(quantity * price)
    line_id, ticker = None, None
    if holding == "new":
        ticker = manual_positions.parse_ticker(new_ticker)
    elif holding not in ("", "cash"):
        line = conn.execute("SELECT * FROM manual_positions WHERE id = ? AND account_id = ?",
                            (int(holding) if holding.isdigit() else -1, account_id)).fetchone()
        if line is None:
            raise ValueError("Choose the holding.")
        line_id, ticker = line["id"], line["ticker"]
    moves_shares = kind in ("buy", "reinvest", "sell") or (kind in ("contribution", "withdrawal", "fee")
                                                           and holding not in ("", "cash"))
    if moves_shares and line_id is None and ticker is None:
        raise ValueError("Choose the holding.")
    if amount is None and not (kind == "fee" and moves_shares and quantity is not None):
        # money moved: a buy or sale needs what it cost (or shares and a price); only a fee taken as shares needn't
        raise ValueError("Enter the amount (or the shares and the price).")
    cents = abs(to_cents(amount)) if amount is not None else 0
    shares = None
    if moves_shares:
        size = abs(quantity) if quantity is not None else Decimal(0)
        shares = size if kind in ("buy", "reinvest", "contribution") else -size
    signed = to_cents(amount) if amount is not None else 0          # another cash move: as entered
    base = MANUAL_PREFIX + secrets.token_hex(12)
    label = investments.ACTION_KINDS[kind]
    name = (description or "").strip() or (f"{label} {ticker}" if ticker else label)
    rows = rows_for(base, kind, shares, signed, cents, label, by_dollars=moves_shares and quantity is None)
    with conn:
        if moves_shares and line_id is None:
            line_id = conn.execute("INSERT INTO manual_positions (account_id, ticker, name, kind) VALUES (?, ?, ?, ?)",
                                   (account_id, ticker, ticker, "stock")).lastrowid
        write(conn, account_id, day, name, rows, line_security(line_id) if line_id else None, price)
    try:
        clashes = rebuild_positions(conn, account_id)
    except manual_positions.PositionError as exc:
        _undo_add(conn, base, account_id)
        raise ValueError(str(exc)) from None
    if clashes:
        _undo_add(conn, base, account_id)
        raise ValueError(f"{clashes[0]} already has an entry of yours: change it, or date this another day.")


def _undo_add(conn: sqlite3.Connection, base: str, account_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM investment_transactions WHERE plaid_investment_transaction_id = ? OR"
                     " plaid_investment_transaction_id LIKE ?", (base, base + "/%"))
    rebuild_positions(conn, account_id)
