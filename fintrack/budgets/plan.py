"""Savings and the 1-year / 10-year views.

The model (sections, amounts, rollover, the views, tags as lines) is explained in fintrack/budgets/__init__.py."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from fintrack import categories
from fintrack.spending import shift_month
from fintrack.budgets.lines import active_sections, line_sections, split_sections, tag_lines
from fintrack.budgets.amounts import _effective, _rows
from fintrack.budgets.actuals import _actuals_with_tags, _by_line
from fintrack.budgets.month import _current_month, _labels


# ---------------------------------------------------------------- savings and the 1-year / 10-year views

def month_range(first: str, last: str) -> list[str]:
    months, m = [], first
    while m <= last:
        months.append(m)
        m = shift_month(m, 1)
    return months


@dataclass
class _Monthly:
    """Per-month values by the actual/budget rule (see the module docstring)."""
    lines: dict[tuple[str, str], int]          # (month, line) -> cents
    outside: dict[tuple[str, str], int]        # (month, kind) -> cents not in any line (past months only)
    income: dict[str, int]
    expense: dict[str, int]


def _monthly(conn: sqlite3.Connection, first: str, last: str, current: str) -> _Monthly:
    sections = line_sections(conn)
    extras = split_sections(conn)
    all_rows = _rows(conn)
    tagged = tag_lines(conn)
    actuals: dict[tuple[str, str], int] = {}
    outside: dict[tuple[str, str], int] = {}
    past_last = min(last, shift_month(current, -1))
    if first <= past_last:
        raw = _actuals_with_tags(conn, first, past_last)
        actuals, loose = _by_line(raw, {**sections, **{k: t.section for k, t in tagged.items()}},
                                  categories.parent_map(conn), extras)
        for (m, _), (kind, cents) in loose.items():
            outside[(m, kind)] = outside.get((m, kind), 0) + cents

    result = _Monthly({}, outside, {}, {})
    for m in month_range(first, last):
        income = outside.get((m, "income"), 0)
        expense = outside.get((m, "expense"), 0)
        for category, section in {**sections, **extras}.items():
            if m < current:
                cents = actuals.get((m, category), 0)
            elif category in extras:
                cents = 0      # a split category is never budgeted (its subcategories are)
            else:
                budget = _effective(all_rows.get(category, []), m)
                cents = budget.amount_cents if budget else 0
            result.lines[(m, category)] = cents
            if section == "income":
                income += cents
            else:
                expense += cents
        for key, line in tagged.items():
            cents = actuals.get((m, key), 0) if m < current else line.amounts.get(m, 0)
            result.lines[(m, key)] = cents
            expense += cents
        result.income[m], result.expense[m] = income, expense
    return result


@dataclass
class PlanColumn:
    key: str               # '2026-03' (1-year view) or '2026' (10-year view)
    months: list[str]
    basis: str             # 'actual' (all past), 'budget' (now or later), or 'mixed'


@dataclass
class PlanLine:
    category: str
    label: str
    values: list[int]      # one per column
    # 1-year view: the budget amount per single-month column (None: no budget), and whether the
    # line takes a budget at all (a split category's own spending doesn't).
    budgets: list[int | None] = field(default_factory=list)
    editable: bool = False
    tag: "TagBudgetLine | None" = None      # a tag's line: its budget cells set that month only

    @property
    def empty(self) -> bool:
        """No budget and no actual in any column of this range: folded into "Non-budgeted
        items" when grouping is on (user's rule 2026-10-06; recomputed for every range)."""
        return not any(self.values) and not any(self.budgets)


@dataclass
class PlanSection:
    key: str
    label: str
    lines: list[PlanLine]
    outside: list[int] | None   # "Not in budget" per column (None when it's all zero)

    @property
    def totals(self) -> list[int]:
        rows = [line.values for line in self.lines] + ([self.outside] if self.outside else [])
        return [sum(col) for col in zip(*rows)] if rows else []


@dataclass
class Plan:
    columns: list[PlanColumn]
    sections: list[PlanSection]
    income: list[int]
    expense: list[int]

    @property
    def savings(self) -> list[int]:
        """Savings per column: income − expenses (that column only; nothing carried over)."""
        return [i - e for i, e in zip(self.income, self.expense)]

    # ---- the summary card: totals over the whole range (actuals past, budgets ahead)
    def section_total(self, key: str) -> int:
        section = next(s for s in self.sections if s.key == key)
        return sum(section.totals) if section.totals else 0

    @property
    def income_total(self) -> int:
        return sum(self.income)

    @property
    def savings_total(self) -> int:
        return sum(self.income) - sum(self.expense)


def plan(conn: sqlite3.Connection, columns: list[tuple[str, list[str]]], today: date | None = None) -> Plan:
    """Lines x columns by the actual/budget rule, with income and expense totals per column.
    `columns` = [(key, [months...]), ...] in order."""
    current = _current_month(today)
    data = _monthly(conn, columns[0][1][0], columns[-1][1][-1], current)
    labels = _labels(conn)
    sections = line_sections(conn)

    def total(values: dict[str, int], months: list[str]) -> int:
        return sum(values.get(m, 0) for m in months)

    cols = []
    for key, months in columns:
        basis = "actual" if months[-1] < current else "budget" if months[0] >= current else "mixed"
        cols.append(PlanColumn(key, months, basis))

    all_rows = _rows(conn)

    def budgets_for(category: str) -> list[int | None]:
        out = []
        for col in cols:
            budget = _effective(all_rows.get(category, []), col.months[0]) if len(col.months) == 1 else None
            out.append(budget.amount_cents if budget else None)
        return out

    result_sections = []
    shown = active_sections(conn)
    for key, (label, kind) in shown.items():
        lines = [
            PlanLine(category, labels.get(category, category),
                     [sum(data.lines.get((m, category), 0) for m in col.months) for col in cols],
                     budgets_for(category) if category in sections else [None] * len(cols),
                     category in sections)
            for category, section in {**sections, **split_sections(conn)}.items()
            if section == key and (category in sections or any(data.lines.get((m, category)) for m in data.income))
        ]
        for line in (t for t in tag_lines(conn).values() if t.section == key):
            values = [sum(data.lines.get((m, line.key), 0) for m in col.months) for col in cols]
            if any(values) or line.unfinished:
                lines.append(PlanLine(line.key, line.label, values,
                                      [line.amounts.get(col.months[0]) if len(col.months) == 1 else None for col in cols],
                                      False, line))
        # Largest first: by the row's total over the columns shown (actuals past, budgets ahead).
        lines.sort(key=lambda line: (line.empty, -sum(line.values), line.label))   # empty lines last
        result_sections.append(PlanSection(key, label, lines, None))
    # "Not in budget" totals go with Income and with the last spending section (Flexible, or Wants).
    for section, kind in (("income", "income"), (list(shown)[-1], "expense")):
        values = [sum(data.outside.get((m, kind), 0) for m in col.months) for col in cols]
        if any(values):
            next(s for s in result_sections if s.key == section).outside = values

    return Plan(
        columns=cols,
        sections=result_sections,
        income=[total(data.income, col.months) for col in cols],
        expense=[total(data.expense, col.months) for col in cols],
    )


def year_columns(start: str) -> list[tuple[str, list[str]]]:
    """1-year view: 12 consecutive months from `start` ('2026-10'), moved a month at a time."""
    return [(m, [m]) for m in month_range(start, shift_month(start, 11))]


def decade_columns(first_year: int) -> list[tuple[str, list[str]]]:
    """10-year view: 10 calendar years from `first_year`."""
    return [(str(y), month_range(f"{y}-01", f"{y}-12")) for y in range(first_year, first_year + 10)]
