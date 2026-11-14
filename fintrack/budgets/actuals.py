"""What each line actually spent or received per month, tags superseding categories, and rollover carries.

The model (sections, amounts, rollover, the views, tags as lines) is explained in fintrack/budgets/__init__.py."""

from __future__ import annotations

import sqlite3

from fintrack import categories
from fintrack.spending import month_bounds, shift_month
from fintrack.budgets.lines import section_kind, tag_line_key
from fintrack.budgets.amounts import Budget, _effective


# ---------------------------------------------------------------- actuals

def actuals_by_month(conn: sqlite3.Connection, first_month: str, last_month: str) -> dict[tuple[str, str], int]:
    """{(month, category): spent cents} for expense-kind spending, one query for the range."""
    return {key: cents for key, (kind, cents) in _actuals(conn, first_month, last_month).items() if kind == "expense"}


def _actuals(conn: sqlite3.Connection, first_month: str, last_month: str) -> dict[tuple[str, str], tuple[str, int]]:
    """{(month, category): (kind, cents)} for spending (money out, positive) and income
    (money in, positive), visible accounts only, as on the Spending and Savings pages."""
    start, _ = month_bounds(first_month)
    _, end = month_bounds(last_month)
    return {
        (r["month"], r["category"]): (r["kind"], r["total"] if r["kind"] == "expense" else -r["total"])
        for r in conn.execute(
            """
            SELECT substr(date, 1, 7) AS month, category, kind, SUM(amount_cents) AS total
            FROM transactions_effective
            WHERE account_hidden = 0 AND kind IN ('expense', 'income') AND date >= ? AND date < ?
            GROUP BY month, category, kind
            """,
            (start, end),
        )
    }


def _actuals_with_tags(conn: sqlite3.Connection, first_month: str, last_month: str) -> dict[tuple[str, str], tuple[str, int]]:
    """_actuals with tags on the budget superseding categories (see the module docstring): each such transaction's amount
    moves from its category to the tag's line, so it still counts once."""
    raw = _actuals(conn, first_month, last_month)
    # the lines you added: (tag, '') = the tag's own, (tag, category) = one of its category budgets
    on_budget = {(r[0], r[1]) for r in conn.execute("SELECT tag_id, category_key FROM tag_budget_lines")}
    parents = categories.parent_map(conn)
    start, _ = month_bounds(first_month)
    _, end = month_bounds(last_month)
    for r in conn.execute(
            """SELECT substr(e.date, 1, 7) AS month, e.category, e.amount_cents, MIN(x.tag_id) AS tag_id
               FROM transactions_effective e
               JOIN transaction_tags x ON x.transaction_id = e.id
               JOIN tag_budget_lines l ON l.tag_id = x.tag_id
               WHERE e.account_hidden = 0 AND e.kind = 'expense' AND e.date >= ? AND e.date < ?
               GROUP BY e.id""", (start, end)):
        category, tag_id, cents = r["category"], r["tag_id"], r["amount_cents"]
        # the tag's line for its category (or its parent), else the tag's own line, else it stays in its category
        bucket = next((c for c in (category, parents.get(category)) if c and (tag_id, c) in on_budget), None)
        if bucket is None and (tag_id, "") not in on_budget:
            continue
        key = tag_line_key(tag_id, bucket)
        raw[(r["month"], category)] = ("expense", raw.get((r["month"], category), ("expense", 0))[1] - cents)
        raw[(r["month"], key)] = ("expense", raw.get((r["month"], key), ("expense", 0))[1] + cents)
    return {k: v for k, v in raw.items() if v[1]}


def _by_line(actuals: dict[tuple[str, str], tuple[str, int]], sections: dict[str, str],
             parents: dict[str, str], extras: dict[str, str] | None = None,
             ) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], tuple[str, int]]]:
    """Split actuals into ({(month, line): cents}, {(month, category): (kind, cents)} not in any line).
    A subcategory without its own line counts toward its parent's line. `extras` (split
    categories, see split_sections) keep their own key so they show in their section."""
    extras = extras or {}
    in_lines: dict[tuple[str, str], int] = {}
    outside: dict[tuple[str, str], tuple[str, int]] = {}
    for (month, category), (kind, cents) in actuals.items():
        if category in sections or category in extras:
            line = category
        else:
            line = parents.get(category)   # a removed subcategory of a split one stays outside
            line = line if line in sections else None
        section = sections.get(line) or extras.get(line)
        if section and section_kind(section) == kind:
            in_lines[(month, line)] = in_lines.get((month, line), 0) + cents
        else:
            outside[(month, category)] = (kind, cents)
    return in_lines, outside


def _carry_in(rows: list[Budget], month: str, actuals: dict[tuple[str, str], int], category: str) -> int:
    """Carry into `month`, walking the chain from the category's first budgeted month."""
    if not rows:
        return 0
    carry = 0
    m = rows[0].start_month
    while m < month:
        this = _effective(rows, m)
        nxt = _effective(rows, shift_month(m, 1))
        if this is None:
            carry = 0  # chain breaks at a month without a budget
        else:
            leftover = this.amount_cents + carry - actuals.get((m, category), 0)
            carry = leftover if (this.rollover and nxt is not None) else 0
        m = shift_month(m, 1)
    return carry


def _whole_dollars(cents: float) -> int:
    """Round a suggestion to whole dollars (half up), never below zero."""
    return max(0, int(cents + 50) // 100 * 100)
