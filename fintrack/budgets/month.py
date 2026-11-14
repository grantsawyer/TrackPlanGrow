"""The Budgets page's month view: each section's lines with budget, carry, spent and status.

The model (sections, amounts, rollover, the views, tags as lines) is explained in fintrack/budgets/__init__.py."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from fintrack import categories, recurring
from fintrack.spending import month_bounds, shift_month
from fintrack.budgets.lines import SUGGESTIONS, active_sections, line_sections, section_kind, split_sections, tag_lines
from fintrack.budgets.amounts import _effective, _rows
from fintrack.budgets.actuals import _actuals_with_tags, _by_line, _carry_in, _whole_dollars


# ---------------------------------------------------------------- the month view

def _current_month(today: date | None = None) -> str:
    return f"{(today or date.today()):%Y-%m}"


def _status(spent: int, available: int) -> str:
    return "over" if available <= 0 or spent > available else "ok"


@dataclass
class BudgetLine:
    category: str
    label: str
    section: str
    budget_cents: int | None      # None: no budget this month
    rollover: bool
    carry_in_cents: int
    spent_cents: int              # the actual: spent, or received for income
    # Clicking a line offers these: key in SUGGESTIONS -> whole-dollar cents, None without history.
    suggestions: dict[str, int | None] = field(default_factory=dict)
    # Confirmed recurring items due this month that haven't posted yet (see upcoming_recurring).
    upcoming_cents: int = 0
    upcoming_items: list[str] = field(default_factory=list)
    tag: "TagBudgetLine | None" = None      # a tag's line: amounts are that month's allocation, set here

    @property
    def is_income(self) -> bool:
        return self.section == "income"

    @property
    def available_cents(self) -> int | None:
        return None if self.budget_cents is None else self.budget_cents + self.carry_in_cents

    @property
    def remaining_cents(self) -> int | None:
        """Left to spend, or (income) still expected; negative = over / above plan."""
        return None if self.available_cents is None else self.available_cents - self.spent_cents

    @property
    def used(self) -> float | None:
        """Share of available spent (0.86 = 86%); None when nothing is available."""
        return self.spent_cents / self.available_cents if (self.available_cents or 0) > 0 else None

    @property
    def status(self) -> str:
        """'none' (no budget), 'income' (income is never 'over'), else over / ok."""
        if self.available_cents is None:
            return "none"
        if self.is_income:
            return "income"
        return _status(self.spent_cents, self.available_cents)


@dataclass
class Section:
    key: str
    label: str
    lines: list[BudgetLine] = field(default_factory=list)
    # Spending filed under a split category itself: in this section, never budgeted.
    extras: list["Outside"] = field(default_factory=list)

    @property
    def is_income(self) -> bool:
        return self.key == "income"

    @property
    def budget_cents(self) -> int:
        return sum(line.budget_cents or 0 for line in self.lines)

    @property
    def available_cents(self) -> int:
        return sum(line.available_cents or 0 for line in self.lines)

    @property
    def spent_cents(self) -> int:
        return sum(line.spent_cents for line in self.lines) + sum(e.cents for e in self.extras)

    @property
    def upcoming_cents(self) -> int:
        return sum(line.upcoming_cents for line in self.lines) + sum(e.upcoming_cents for e in self.extras)

    @property
    def remaining_cents(self) -> int:
        """Available in the section minus everything spent in its lines (budgeted or not)."""
        return self.available_cents - self.spent_cents

    @property
    def used(self) -> float | None:
        return self.spent_cents / self.available_cents if self.available_cents > 0 else None

    @property
    def status(self) -> str:
        if not any(line.budget_cents is not None for line in self.lines):
            return "none"
        return "income" if self.is_income else _status(self.spent_cents, self.available_cents)


@dataclass
class Outside:
    """A category with actuals (or upcoming recurring items) this month that isn't in any line."""
    category: str
    label: str
    kind: str
    cents: int
    upcoming_cents: int = 0
    upcoming_items: list[str] = field(default_factory=list)


@dataclass
class MonthBudget:
    month: str
    sections: list[Section] = field(default_factory=list)
    outside: list[Outside] = field(default_factory=list)

    # ---- savings
    @property
    def actual_leftover_cents(self) -> int:
        return self.actual_income_cents - self.actual_expense_cents


    def section(self, key: str) -> Section:
        return next(s for s in self.sections if s.key == key)

    def outside_of(self, kind: str) -> list[Outside]:
        return [o for o in self.outside if o.kind == kind]

    # ---- summary card
    @property
    def planned_income_cents(self) -> int:
        return self.section("income").budget_cents

    @property
    def actual_income_cents(self) -> int:
        return self.section("income").spent_cents + sum(o.cents for o in self.outside_of("income"))

    @property
    def planned_expense_cents(self) -> int:
        return sum(s.budget_cents for s in self.sections if not s.is_income)

    @property
    def actual_expense_cents(self) -> int:
        return (sum(s.spent_cents for s in self.sections if not s.is_income)
                + sum(o.cents for o in self.outside_of("expense")))

    @property
    def left_to_budget_cents(self) -> int:
        return self.planned_income_cents - self.planned_expense_cents

    @property
    def income_used(self) -> float | None:
        return self.actual_income_cents / self.planned_income_cents if self.planned_income_cents > 0 else None

    @property
    def expense_used(self) -> float | None:
        return self.actual_expense_cents / self.planned_expense_cents if self.planned_expense_cents > 0 else None

    @property
    def expense_status(self) -> str:
        if self.planned_expense_cents <= 0:
            return "none"
        return _status(self.actual_expense_cents, self.planned_expense_cents)

    # ---- budgeted spending lines (Overview's budget card and chart)
    @property
    def lines(self) -> list[BudgetLine]:
        return [line for s in self.sections if not s.is_income for line in s.lines if line.budget_cents is not None]

    @property
    def budgeted_cents(self) -> int:
        return sum(line.available_cents for line in self.lines)

    @property
    def spent_in_budgets_cents(self) -> int:
        return sum(line.spent_cents for line in self.lines)

    @property
    def unbudgeted_cents(self) -> int:
        """Spending not counted against any budget (lines without an amount + not in budget)."""
        return self.actual_expense_cents - self.spent_in_budgets_cents

    @property
    def attention(self) -> list[BudgetLine]:
        """Spending lines over budget, worst first (for the dashboard)."""
        flagged = [line for line in self.lines if line.status == "over"]
        return sorted(flagged, key=lambda line: -(line.used or 99))


def _labels(conn: sqlite3.Connection) -> dict[str, str]:
    """{category: label}. Subcategories by their own name only ("Rent", not
    "Rent & utilities › Rent"; user's request 2026-10-06)."""
    return {r["key"]: r["label"] for r in conn.execute("SELECT key, label FROM categories")}


def month_view(conn: sqlite3.Connection, month: str, today: date | None = None) -> MonthBudget:
    sections = line_sections(conn)
    all_rows = _rows(conn)
    labels = _labels(conn)

    # One query from the earliest month anything needs (the rollover chain's start, or the
    # 6-month suggestion window) through this month.
    first = min([shift_month(month, -max(n for _, n in SUGGESTIONS.values()))]
                + [rows[0].start_month for rows in all_rows.values()])
    raw = _actuals_with_tags(conn, min(first, month), month)
    extras = split_sections(conn)
    tagged = tag_lines(conn)
    actuals, outside = _by_line(raw, {**sections, **{k: t.section for k, t in tagged.items()}},
                                categories.parent_map(conn), extras)
    # Months with any data: averages divide by these, so a new account isn't understated.
    months_with_data = {m for (m, _), (_, cents) in raw.items() if cents}

    def suggest(category: str) -> dict[str, int | None]:
        result = {}
        for key, (_, back) in SUGGESTIONS.items():
            window = [shift_month(month, -k) for k in range(1, back + 1)]
            counted = [m for m in window if m in months_with_data]
            total = sum(actuals.get((m, category), 0) for m in counted)
            result[key] = _whole_dollars(total / len(counted)) if counted else None
        return result

    upcoming = upcoming_recurring(conn, month, sections, extras, today)
    view = MonthBudget(month=month)
    for key, (label, _) in active_sections(conn).items():
        section = Section(key, label)
        for category in (c for c, s in sections.items() if s == key):
            rows = all_rows.get(category, [])
            budget = _effective(rows, month)
            section.lines.append(BudgetLine(
                category=category,
                label=labels.get(category, category),
                section=key,
                budget_cents=budget.amount_cents if budget else None,
                rollover=bool(budget and budget.rollover),
                carry_in_cents=_carry_in(rows, month, actuals, category) if budget and key != "income" else 0,
                spent_cents=actuals.get((month, category), 0),
                suggestions=suggest(category),
            ))
        # Tags' lines: left out of a month with nothing on them once the whole total is allocated elsewhere.
        for line in (t for t in tagged.values() if t.section == key):
            amount, spent = line.amounts.get(month), actuals.get((month, line.key), 0)
            if amount or spent or line.unfinished:
                section.lines.append(BudgetLine(category=line.key, label=line.label, section=key, budget_cents=amount,
                                                rollover=False, carry_in_cents=0, spent_cents=spent, tag=line))
        # Largest first (user's request 2026-10-06): by budget, then (no budget) by actual, then name.
        section.lines.sort(key=lambda line: (not line.budget_cents, -(line.budget_cents or 0),
                                             -line.spent_cents, line.label))   # unbudgeted last
        for category in (c for c, s in extras.items() if s == key):
            if actuals.get((month, category)) or upcoming.get(category):
                section.extras.append(Outside(category, labels.get(category, category),
                                              section_kind(key), actuals.get((month, category), 0)))
        view.sections.append(section)

    view.outside = sorted(
        (Outside(category, labels.get(category, category), kind, cents)
         for (m, category), (kind, cents) in outside.items() if m == month and cents),
        key=lambda o: -o.cents,
    )

    # Upcoming: each item counts where its category's actuals do (line, split category, or
    # Not in budget), so the column reads like Actual.
    rows_by_category = {line.category: line for s in view.sections for line in s.lines}
    rows_by_category.update({e.category: e for s in view.sections for e in s.extras})
    rows_by_category.update({o.category: o for o in view.outside})
    for category, (kind, cents, items) in upcoming.items():
        row = rows_by_category.get(category)
        if row is None:
            row = Outside(category, labels.get(category, category), kind, 0)
            view.outside.append(row)
        row.upcoming_cents += cents
        row.upcoming_items += items
    return view


def upcoming_recurring(conn: sqlite3.Connection, month: str, sections: dict[str, str], extras: dict[str, str],
                       today: date | None = None) -> dict[str, tuple[str, int, list[str]]]:
    """Confirmed recurring items due in `month` that haven't posted yet (user's request 2026-10-06):
    {row category: (kind, cents, ['Name, Oct 20', ...])}, positive = money out for bills and
    money in for income. The row is the line the item's category counts toward (as for actuals).

    - A past month: nothing (whatever happened is in Actual).
    - The current month: every occurrence from the item's next expected date to month-end; an
      overdue one (expected date passed, not seen yet) still counts, as the forecast assumes.
    - A future month: all its occurrences.
    """
    today = today or date.today()
    current = _current_month(today)
    if month < current:
        return {}
    first_day = date.fromisoformat(month_bounds(month)[0])
    last_day = date.fromisoformat(month_bounds(month)[1]) - timedelta(days=1)
    parents = categories.parent_map(conn)
    kinds = {r[0]: r[1] for r in conn.execute("SELECT key, kind FROM categories")}
    result: dict[str, tuple[str, int, list[str]]] = {}
    for item in recurring.list_items(conn, "confirmed"):
        category = item["category_key"]
        if not item["next_expected_date"] or not category:
            continue
        kind = "income" if item["type"] == "income" else "expense"
        if kinds.get(category) != kind:
            continue                     # e.g. a card payment filed as a transfer: not budgeted
        expected = date.fromisoformat(item["next_expected_date"])
        dates = []
        if expected < today:
            if month == current:
                dates.append(expected)   # overdue: still to come
            start = today
        else:
            start = expected
        dates += [d for d in recurring.occurrences(item["frequency"], expected, max(start, first_day), last_day,
                                                   item["days_of_month"]) if d >= expected and d not in dates]
        if not dates:
            continue
        row = category if category in sections or category in extras else parents.get(category)
        row = row if row in sections else category
        cents = abs(item["amount_cents"]) * len(dates)
        _, total, items = result.get(row, (kind, 0, []))
        result[row] = (kind, total + cents, items + [f"{item['name']}, {d:%b} {d.day}" for d in dates])
    return result
