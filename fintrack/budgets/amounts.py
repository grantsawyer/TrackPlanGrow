"""Budget amounts: each category's rows (start month, amount, rollover) and changing them.

The model (sections, amounts, rollover, the views, tags as lines) is explained in fintrack/budgets/__init__.py."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from fintrack.money import parse_amount_formula as parse_amount  # amounts or =795/12 (web layer too)
from fintrack.spending import shift_month
from fintrack.budgets.lines import BudgetError


# ---------------------------------------------------------------- budget amounts

@dataclass(frozen=True)
class Budget:
    category: str
    start_month: str
    amount_cents: int | None
    rollover: bool


def _rows(conn: sqlite3.Connection) -> dict[str, list[Budget]]:
    """All budget rows per category, oldest first."""
    rows: dict[str, list[Budget]] = {}
    for r in conn.execute("SELECT * FROM budgets ORDER BY category_key, start_month"):
        rows.setdefault(r["category_key"], []).append(
            Budget(r["category_key"], r["start_month"], r["amount_cents"], bool(r["rollover"]))
        )
    return rows


def _effective(rows: list[Budget], month: str) -> Budget | None:
    """The row in effect for `month`, or None if no budget applies."""
    current = None
    for row in rows:
        if row.start_month <= month:
            current = row
        else:
            break
    return current if current and current.amount_cents is not None else None


def budget_for(conn: sqlite3.Connection, category: str, month: str) -> Budget | None:
    return _effective(_rows(conn).get(category, []), month)


def set_budget(conn: sqlite3.Connection, category: str, month: str, amount_text: str, rollover: bool) -> None:
    """Set (or clear, with a blank amount) a category's budget from `month` onward.

    Later rows for the category are left alone: they still take over from their own month.
    Income lines are planned amounts; rollover applies to spending only.
    """
    row = conn.execute("SELECT kind FROM categories WHERE key = ?", (category,)).fetchone()
    if row is None or row["kind"] not in ("expense", "income"):
        raise BudgetError(f"{category!r} isn't a spending or income category.")
    if row["kind"] == "income":
        rollover = False
    amount_text = (amount_text or "").strip()
    if amount_text:
        try:
            amount = parse_amount(amount_text)
        except ValueError as exc:
            raise BudgetError(str(exc)) from None
    else:
        # Blank: only record "no budget from here" if one would otherwise apply.
        if budget_for(conn, category, month) is None:
            conn.execute("DELETE FROM budgets WHERE category_key = ? AND start_month = ?", (category, month))
            conn.commit()
            return
        amount = None
    conn.execute(
        "INSERT INTO budgets (category_key, start_month, amount_cents, rollover) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (category_key, start_month) DO UPDATE SET"
        " amount_cents = excluded.amount_cents, rollover = excluded.rollover",
        (category, month, amount, 1 if rollover else 0),
    )
    conn.commit()


def set_budget_scoped(conn: sqlite3.Connection, category: str, month: str, amount_text: str, scope: str) -> None:
    """The 1-year view's edit, after you choose (user's request 2026-10-06):

    scope='month'   : only `month`. The amount that applied the month after is put back from
                      the next month (unless that month already has its own row).
    scope='forward' : `month` and every month after: later rows for the category are removed,
                      so nothing you set later overrides it.
    Rollover stays whatever was in effect for `month`.
    """
    if scope not in ("month", "forward"):
        raise BudgetError("Choose this month only, or this month and every month after.")
    row = conn.execute("SELECT kind FROM categories WHERE key = ?", (category,)).fetchone()
    if row is None or row["kind"] not in ("expense", "income"):
        raise BudgetError(f"{category!r} isn't a spending or income category.")
    if amount_text.strip():
        try:
            parse_amount(amount_text)          # validate before changing anything
        except ValueError as exc:
            raise BudgetError(str(exc)) from None
    rows = _rows(conn).get(category, [])
    current = _effective(rows, month)
    rollover = current.rollover if current else False
    following = shift_month(month, 1)
    if scope == "forward":
        conn.execute("DELETE FROM budgets WHERE category_key = ? AND start_month > ?", (category, month))
    elif not any(r.start_month == following for r in rows):
        # Next month keeps what it had: the row in effect for it now (NULL = no budget).
        after = _effective(rows, following)
        if after is not None or amount_text.strip():
            conn.execute(
                "INSERT INTO budgets (category_key, start_month, amount_cents, rollover) VALUES (?, ?, ?, ?)",
                (category, following, after.amount_cents if after else None, 1 if after and after.rollover else 0),
            )
    set_budget(conn, category, month, amount_text, rollover)   # commits
