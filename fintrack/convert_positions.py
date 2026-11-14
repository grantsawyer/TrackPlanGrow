"""Converting manual investment accounts' entries into activity (docs/parity-plan.md phase 4, user's decision
2026-11-14): `fintrack convert-positions`.

Before the ledger (ledger.py), a manual investment account's contributions, withdrawals and fees were entries on its
lines (`manual_position_changes`, entry 'contribution', typed by you). They become activity rows, as Add activity writes
them (a contribution: a contribution and a buy; a withdrawal: a sale and a withdrawal; a fee: a fee; in dollars, as they
were typed), and the account's holdings are rebuilt from its activity. Balances and opening values you typed stay: they
are balances (a day's closing amount).

**Checked first** (decision 3): the database is copied, the copy converted, and compared with the original: every
manual investment account's values, snapshots, current balance and holdings, and every month's Savings figures. Any
difference of a cent or more is reported and nothing is converted. Only when the copy matches is the real database
converted (after a backup), and compared again.
"""

from __future__ import annotations

import dataclasses
import hashlib
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from fintrack import ledger, manual_positions


# ---------------------------------------------------------------- what's converted

def entries(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Your typed contributions / withdrawals / fees on manual investment accounts (not balances, not activity-made)."""
    return conn.execute(
        """SELECT c.rowid AS rid, c.*, p.account_id, p.ticker, p.name AS line_name, p.id AS line_id
           FROM manual_position_changes c JOIN manual_positions p ON p.id = c.position_id
           JOIN accounts a ON a.id = p.account_id
           WHERE a.is_manual = 1 AND a.type = 'investment' AND c.from_activity = 0 AND c.entry = 'contribution'
           ORDER BY p.account_id, c.date""").fetchall()


def convert(conn: sqlite3.Connection) -> int:
    """Convert every entry (see the module's docstring); returns how many. Raises ValueError for an entry it can't
    convert (shares typed in or out, which have no dollars to record), changing nothing."""
    rows = entries(conn)
    unsupported = [r for r in rows if r["cash_cents"] is None]
    if unsupported:
        r = unsupported[0]
        raise ValueError(f"{r['line_name']} on {r['date']} moves shares without dollars: it can't be converted yet.")
    accounts = sorted({r["account_id"] for r in rows})
    with conn:
        for r in rows:
            cents = r["cash_cents"]
            kind = "fee" if r["fee"] else ("contribution" if cents > 0 else "withdrawal")
            base = "act-conv-" + hashlib.sha256(f"{r['line_id']}|{r['date']}".encode()).hexdigest()[:20]
            cash_line = ledger._cash_line(conn, r["account_id"], create=False)
            on_cash = r["ticker"] is None and r["line_id"] == cash_line
            label = {"contribution": "Contribution", "withdrawal": "Withdrawal", "fee": "Fee"}[kind]
            # on the Cash line itself: cash in or out only; on a holding: its dollars (no units: as typed)
            rows_out = ledger.rows_for(base, kind, None if on_cash else Decimal(0), cents, abs(cents), label,
                                       by_dollars=True)
            ledger.write(conn, r["account_id"], r["date"], f"{label} · {r['line_name']}", rows_out,
                         None if on_cash else ledger.line_security(r["line_id"]))
            conn.execute("DELETE FROM manual_position_changes WHERE rowid = ?", (r["rid"],))
    for account_id in accounts:
        clashes = ledger.rebuild_positions(conn, account_id)
        if clashes:
            raise ValueError(f"{clashes[0]}: an entry of yours clashes with the converted activity.")
    return len(rows)


# ---------------------------------------------------------------- the check

def snapshot(conn: sqlite3.Connection, today: date | None = None) -> dict:
    """What must come out the same: per manual investment account its values, snapshots, balance and holdings; and
    every month's Savings figures."""
    from fintrack import savings

    today = today or date.today()
    found: dict = {"accounts": {}, "savings": {}}
    for (account_id,) in conn.execute("SELECT id FROM accounts WHERE is_manual = 1 AND type = 'investment'"):
        found["accounts"][account_id] = {
            "values": [tuple(r) for r in conn.execute(
                "SELECT date, value_cents FROM manual_valuations WHERE account_id = ? ORDER BY date", (account_id,))],
            "snapshots": [tuple(r) for r in conn.execute(
                "SELECT date, balance_cents FROM balance_snapshots WHERE account_id = ? ORDER BY date", (account_id,))],
            "balance": conn.execute("SELECT current_balance_cents FROM accounts WHERE id = ?",
                                    (account_id,)).fetchone()[0],
            "holdings": sorted((r[0], round(r[1] or 0, 6), r[2]) for r in conn.execute(
                "SELECT COALESCE(ticker, name), quantity, value_cents FROM holdings WHERE account_id = ?", (account_id,))),
        }
    first = conn.execute("SELECT MIN(date) FROM (SELECT date FROM transactions UNION ALL SELECT date FROM"
                         " manual_position_changes UNION ALL SELECT date FROM investment_transactions)").fetchone()[0]
    if first:
        for m in savings.range_series(conn, first[:7] + "-01", today.isoformat(), today):
            found["savings"][m.month] = dataclasses.asdict(m)
    return found


def compare(before: dict, after: dict) -> list[str]:
    """Every difference, as a line of text (no amounts beyond the difference itself)."""
    out: list[str] = []
    for account_id, b in before["accounts"].items():
        a = after["accounts"].get(account_id, {})
        for key in ("values", "snapshots", "balance", "holdings"):
            if b.get(key) != a.get(key):
                if key in ("values", "snapshots"):
                    bd, ad = dict(b[key]), dict(a.get(key, []))
                    days = sorted(d for d in set(bd) | set(ad) if bd.get(d) != ad.get(d))
                    worst = max((abs((ad.get(d) or 0) - (bd.get(d) or 0)) for d in days), default=0)
                    out.append(f"account {account_id}: {key} differ on {len(days)} day(s), {days[0]} to {days[-1]};"
                               f" largest difference {worst / 100:.2f}")
                else:
                    out.append(f"account {account_id}: {key} differ")
    for month, b in before["savings"].items():
        a = after["savings"].get(month)
        if a != b:
            fields = [k for k in b if a is None or a.get(k) != b[k]]
            out.append(f"Savings {month}: " + ", ".join(
                f"{k} {((a or {}).get(k) or 0) - (b[k] or 0):+d} cents" if isinstance(b[k], int) else k for k in fields))
    return out


def check_on_copy(db_path: Path, scratch: Path) -> tuple[int, list[str]]:
    """Copy the database to `scratch`, convert the copy, compare. Returns (entries converted, differences). The copy is
    deleted afterwards."""
    from fintrack import db

    source = db.connect(db_path)
    try:
        copy = sqlite3.connect(scratch)
        scratch.chmod(0o600)                             # your data: owner only, like the database
        with copy:
            source.backup(copy)
        copy.close()
    finally:
        source.close()
    try:
        conn = db.connect(scratch)
        try:
            before = snapshot(conn)
            count = convert(conn)
            after = snapshot(conn)
        finally:
            conn.close()
        return count, compare(before, after)
    finally:
        for suffix in ("", "-wal", "-shm"):
            Path(str(scratch) + suffix).unlink(missing_ok=True)
