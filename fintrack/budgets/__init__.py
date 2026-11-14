"""Monthly budget plan: Income, Fixed expenses, and Flexible expenses (user's layout, 2026-10-06), or
Income, Needs, and Wants (the needs / wants / savings framework; user's request 2026-10-28).

Sections and lines
------------------
Each section holds budget lines (`budget_lines`). A line is one category: an income
category in Income, a spending category in Fixed or Flexible. A split category's lines are
its subcategories (Rent, Telephone, ...), never the category itself. A line's actual is that
category's transactions plus any subcategory of it that isn't a line of its own
("Rent & utilities" covers "Telephone" unless Telephone has its own line). Spending or
income in no line at all is listed as "Not in budget", so the totals always match the
Spending and Savings pages. Which section a line is in applies to every month.

Frameworks: every spending line is fixed or flexible (`budget_lines.section`), a need or a want
(`budget_lines.need_want`, NULL = `default_need_want`), and in one of the groups you add and name
(`budget_groups`, `budget_lines.custom_group`; NULL = your first group if fixed, else your second; user's
requests 2026-10-28). The page's settings (`app_settings` 'budget_framework') pick which the sections are;
switching never changes a line's other choices.

How a budget amount applies
---------------------------
A budget row (category, start_month, amount, rollover) applies from start_month
onward until the next row for that category. amount = None means "no budget
from this month on". Changing a budget therefore never rewrites past months.

Rollover (spending lines only; all in cents)
--------------------------------------------
    available(M) = budget(M) + carry_in(M)
    leftover(M)  = available(M) - spent(M)        # negative = overspent
    carry_in(M)  = leftover(M-1)  if rollover is on for M-1's budget
                                  and both M-1 and M have a budget
                   0              otherwise
The chain starts at the category's first budgeted month and breaks at any
month without a budget. Carries are computed from transactions every time,
never stored, so recategorizing an old transaction ripples forward correctly.

Status (spending)
-----------------
    over  : spent > available, or available <= 0 (already in deficit)
    ok    : otherwise   (no separate warning level: user removed it 2026-10-06)

Summary
-------
    left to budget = planned income - planned expenses   (budget amounts; carries are
                                                           last month's money, not this month's)

The 1-year / 10-year views (user's rules, 2026-10-06)
------------------------------------------------------
Every month has one value per line, by this rule:
    months before the current one -> actuals only
    the current month and later   -> budget amounts only (the current month is incomplete)
The 1-year view shows 12 monthly columns from any month (editable from the current month on), the
10-year view 10 annual columns (a year adds up its months by the same rule). Savings = income −
expenses for the month or range (a Savings row in each table); nothing is carried between months
(the user removed the rolled-over balance 2026-10-06; per-line rollover above is separate and stays).

Tags as lines (user's requests 2026-11-02)
------------------------------------------
A tag has a total budget and/or totals by category, set on its Tags card (tags.py). You add the tag ('tag:<id>') and/or
any of its category budgets ('tag:<id>:<category>', offered under it in + Add budget), each its own line
(`tag_budget_lines`, migration 0065; user's request 2026-11-04: they used to come with the tag). You allocate any amount
to any month on any of them (`tag_budget_allocations`): that month only, never spread or carried, no rollover.
A tag on the budget supersedes categories: its spending counts on its lines and not on its categories' lines, so a
trip's restaurant counts toward the trip and not Restaurants, and each transaction still counts once (the totals keep
matching Spending). It goes to the tag's line for its category or that category's parent if that line is on the budget,
else to the tag's own line if that is, else stays in its category (as Ride share without its own line rolls into
Transportation). With two tags on the budget, the older tag's. This is the Budgets page's view only: other
pages, and `actuals_by_month` (the projection), still read categories.
"""

from __future__ import annotations

# Everything callers use as budgets.X, from the modules that hold it (each imports only from those above it).
# Underscore names are shared between these modules, never used outside the package.
from fintrack.money import parse_amount_formula as parse_amount  # amounts or =795/12 (web layer too)
from fintrack.budgets.lines import (
    BudgetError, FRAMEWORKS, GROUP_NAME_LIMIT, INSURANCE, MAX_GROUPS, MIN_GROUPS, NEED_CATEGORIES, NEED_ENDINGS,
    SECTIONS, SERVICES, SUGGESTIONS, TAG_PREFIX, TagBudgetLine, active_sections, add_group, add_insurance_line,
    add_line, custom_groups, default_need_want, framework, line_sections, move_line, parse_tag_key,
    remove_group, remove_line, rename_group, section_kind, set_framework, set_tag_allocation, split_sections,
    tag_line_key, tag_lines,
)
from fintrack.budgets.amounts import (
    Budget, budget_for, set_budget, set_budget_scoped,
)
from fintrack.budgets.actuals import (
    actuals_by_month,
)
from fintrack.budgets.month import (
    BudgetLine, MonthBudget, Outside, Section, month_view, upcoming_recurring,
)
from fintrack.budgets.plan import (
    Plan, PlanColumn, PlanLine, PlanSection, decade_columns, month_range, plan, year_columns,
)
