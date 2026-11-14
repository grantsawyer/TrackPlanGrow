"""Net worth projection (1, 5, 10 years) and what-if scenarios.

The model steps month by month from today's balances. Every assumption is
shown on the page and can be changed:

  Investments  grow at the expected annual return (compounded monthly) and
               receive each month's savings.
  Savings      default = your average monthly saved (income - spending) over
               the last 12 complete months; or a fixed amount you set.
  Cash         held constant (savings are assumed to be invested).
  Property     grows at your property growth rate (default 0%).
  Loans        with a rate AND a payment are paid down: interest added, payment
               taken off (never overshooting). When one is paid off, its payment
               is added to monthly savings (it was counted as spending), if
               "redirect" is on. A payment below the interest is flagged.
  Cards and loans without terms, other assets/liabilities: held constant.

Why this adds up: loan payments from checking already count as spending, so
they're already out of monthly savings; the principal part shows up as the
loan balance shrinking. Net worth change = savings + growth + principal repaid.

Scenarios are JSON adjustments applied to the baseline inputs; they never
change real data:
  {"category_changes": [{"category": "FOOD_AND_DRINK", "monthly_cents": -20000}],
   "recurring_changes": [{"name": "Car lease", "monthly_cents": 45000}],   # + expense / - income
   "savings_rate_pct": 30,            # optional: savings = average income x rate
   "investment_return_pct": 7,        # optional override
   "extra_payments": [{"account_id": 12, "monthly_cents": 20000}]}
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import date

from fintrack import budgets, loans, networth, savings
from fintrack.money import parse_amount
from fintrack.spending import shift_month

MONTHS = 120
AVERAGE_MONTHS = 12


class ProjectionError(ValueError):
    pass


# ---------------------------------------------------------------- settings

def _setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def _put(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def parse_percent(text: str, label: str, low: float, high: float) -> float:
    try:
        value = float((text or "").strip().rstrip("%"))
    except ValueError:
        raise ProjectionError(f"{label} must be a number.") from None
    if not low <= value <= high:
        raise ProjectionError(f"{label} must be between {low:g}% and {high:g}%.")
    return value


def save_settings(conn: sqlite3.Connection, savings_text: str, return_text: str, property_text: str,
                  redirect: bool) -> None:
    savings_text = (savings_text or "").strip()
    if savings_text:
        negative = savings_text.startswith("-")
        try:
            cents = parse_amount(savings_text.lstrip("-"))
        except ValueError as exc:
            raise ProjectionError(f"Monthly savings: {exc}") from None
        savings_value = str(-cents if negative else cents)
    else:
        savings_value = ""
    investment = parse_percent(return_text, "Investment return", -50, 50)
    prop = parse_percent(property_text, "Property growth", -50, 50)
    _put(conn, "proj_monthly_savings_cents", savings_value)
    _put(conn, "proj_investment_return_pct", str(investment))
    _put(conn, "proj_property_growth_pct", str(prop))
    _put(conn, "proj_redirect_paid_off", "1" if redirect else "0")
    conn.commit()


# ---------------------------------------------------------------- assumptions

@dataclass
class Assumptions:
    monthly_savings_cents: int
    savings_is_override: bool
    average_saved_cents: int          # 12-month average (shown even when overridden)
    average_income_cents: int
    months_averaged: int
    investment_return_pct: float
    property_growth_pct: float
    redirect_paid_off: bool


def baseline_assumptions(conn: sqlite3.Connection, today: date | None = None) -> Assumptions:
    today = today or date.today()
    series = savings.monthly_series(conn, months=AVERAGE_MONTHS + 1, today=today)
    complete = [m for m in series if not m.partial and (m.income_cents or m.spending_cents)][-AVERAGE_MONTHS:]
    n = len(complete)
    avg_saved = round(sum(m.saved_cents for m in complete) / n) if n else 0
    avg_income = round(sum(m.income_cents for m in complete) / n) if n else 0
    override = _setting(conn, "proj_monthly_savings_cents")
    return Assumptions(
        monthly_savings_cents=int(override) if override else avg_saved,
        savings_is_override=bool(override),
        average_saved_cents=avg_saved,
        average_income_cents=avg_income,
        months_averaged=n,
        investment_return_pct=float(_setting(conn, "proj_investment_return_pct", "5")),
        property_growth_pct=float(_setting(conn, "proj_property_growth_pct", "0")),
        redirect_paid_off=_setting(conn, "proj_redirect_paid_off", "1") == "1",
    )


def category_averages(conn: sqlite3.Connection, today: date | None = None) -> dict[str, int]:
    """Average monthly spending per category over the last 12 complete months (months with data)."""
    today = today or date.today()
    last = shift_month(f"{today:%Y-%m}", -1)
    first = shift_month(last, -(AVERAGE_MONTHS - 1))
    actuals = budgets.actuals_by_month(conn, first, last)
    months = len({m for (m, _), cents in actuals.items() if cents}) or 1
    totals: dict[str, int] = {}
    for (_, category), cents in actuals.items():
        totals[category] = totals.get(category, 0) + cents
    return {k: round(v / months) for k, v in totals.items()}


# ---------------------------------------------------------------- starting balances

@dataclass
class Loan:
    account_id: int
    name: str
    balance: float                 # cents, positive = owed
    apr_pct: float | None
    payment_cents: int | None
    amortizes: bool
    why_constant: str = ""         # reason when held constant

    @property
    def monthly_interest_cents(self) -> float:
        return self.balance * (self.apr_pct or 0) / 1200


@dataclass
class Start:
    cash: int = 0
    investments: int = 0
    property: int = 0
    other_assets: int = 0
    constant_liabilities: int = 0
    loans: list[Loan] = field(default_factory=list)


def starting_balances(conn: sqlite3.Connection) -> Start:
    start = Start()
    counted, _ = networth.counted_accounts(conn)
    terms = {r["id"]: r for r in loans.liabilities_with_terms(conn)}
    for a in counted:
        cents = a["current_balance_cents"]
        if a["classification"] == "asset":
            if a["type"] == "investment":
                start.investments += cents
            elif a["type"] == "property":
                start.property += cents
            elif a["type"] == "depository":
                start.cash += cents
            else:
                start.other_assets += cents
            continue
        t = terms.get(a["id"])
        apr = t["apr_percent"] if t else None
        payment = t["monthly_payment_cents"] if t else None
        if apr is None or payment is None:
            why = "no rate/payment entered"
        elif a["type"] == "credit" and t["source"] != "you":
            why = "credit card: assumed paid in full each month"
        else:
            why = ""
        start.loans.append(Loan(a["id"], a["name"], float(cents), apr, payment, amortizes=not why, why_constant=why))
    start.constant_liabilities = sum(round(loan.balance) for loan in start.loans if not loan.amortizes)
    return start


# ---------------------------------------------------------------- scenarios -> inputs

@dataclass
class Inputs:
    assumptions: Assumptions
    monthly_savings_cents: int
    investment_return_pct: float
    extra_payments: dict[int, int]
    notes: list[str]


def apply_scenario(base: Assumptions, adjustments: dict | None, averages: dict[str, int],
                   labels: dict[str, str] | None = None) -> Inputs:
    labels = labels or {}
    adjustments = adjustments or {}
    notes: list[str] = []
    monthly = base.monthly_savings_cents
    if adjustments.get("savings_rate_pct") is not None:
        rate = float(adjustments["savings_rate_pct"])
        monthly = round(base.average_income_cents * rate / 100)
        notes.append(f"Savings set to {rate:g}% of average income.")
    for change in adjustments.get("category_changes", []):
        cents = int(change["monthly_cents"])
        average = averages.get(change["category"], 0)
        if cents < -average:
            notes.append(f"{labels.get(change['category'], change['category'])}: cut capped at your average "
                         f"spending (${average / 100:,.2f}/month).")
            cents = -average
        monthly -= cents          # spending less -> saving more
    for change in adjustments.get("recurring_changes", []):
        monthly -= int(change["monthly_cents"])   # + expense lowers savings; - (income) raises it
    ret = base.investment_return_pct
    if adjustments.get("investment_return_pct") is not None:
        ret = float(adjustments["investment_return_pct"])
    extra = {int(e["account_id"]): int(e["monthly_cents"]) for e in adjustments.get("extra_payments", [])
             if int(e["monthly_cents"]) > 0}
    return Inputs(base, monthly, ret, extra, notes)


# ---------------------------------------------------------------- the simulation

@dataclass
class Point:
    month: str                 # YYYY-MM
    assets_cents: int
    liabilities_cents: int

    @property
    def net_cents(self) -> int:
        return self.assets_cents - self.liabilities_cents


@dataclass
class LoanOutcome:
    account_id: int
    name: str
    start_cents: int
    apr_pct: float | None
    payment_cents: int | None
    paid_off_month: str | None      # None: not within the horizon (or held constant)
    amortizes: bool
    why_constant: str
    payment_too_low: bool           # payment doesn't cover the first month's interest


@dataclass
class Projection:
    inputs: Inputs
    points: list[Point]             # month 0 (today) .. MONTHS
    loans: list[LoanOutcome]

    def at_years(self, years: int) -> Point:
        return self.points[min(years * 12, len(self.points) - 1)]


def project(conn: sqlite3.Connection, adjustments: dict | None = None, months: int = MONTHS,
            today: date | None = None, base: Assumptions | None = None) -> Projection:
    today = today or date.today()
    base = base or baseline_assumptions(conn, today)
    labels = dict(conn.execute("SELECT key, label FROM categories").fetchall())
    inputs = apply_scenario(base, adjustments, category_averages(conn, today), labels)
    start = starting_balances(conn)

    r_month = (1 + inputs.investment_return_pct / 100) ** (1 / 12) - 1
    g_month = (1 + base.property_growth_pct / 100) ** (1 / 12) - 1
    investments, prop = float(start.investments), float(start.property)
    fixed_assets = start.cash + start.other_assets
    # Extra payments come out of savings while the loan is open.
    monthly_savings = float(inputs.monthly_savings_cents) - sum(
        cents for acc, cents in inputs.extra_payments.items()
        if any(loan.account_id == acc and loan.amortizes for loan in start.loans)
    )
    open_loans = [replace(loan) for loan in start.loans if loan.amortizes]
    outcomes = {
        loan.account_id: LoanOutcome(
            loan.account_id, loan.name, round(loan.balance), loan.apr_pct, loan.payment_cents, None,
            loan.amortizes, loan.why_constant,
            payment_too_low=loan.amortizes and (loan.payment_cents or 0) + inputs.extra_payments.get(loan.account_id, 0)
            <= loan.monthly_interest_cents and loan.balance > 0,
        )
        for loan in start.loans
    }

    this_month = f"{today:%Y-%m}"

    def point(k: int) -> Point:
        liabilities = start.constant_liabilities + sum(max(round(loan.balance), 0) for loan in open_loans)
        return Point(shift_month(this_month, k), round(fixed_assets + investments + prop), liabilities)

    points = [point(0)]
    for k in range(1, months + 1):
        leftover_this_month = 0.0   # unused part of a final (smaller) payment
        freed_from_next_month = 0.0  # full payments of loans paid off this month
        for loan in open_loans:
            if loan.balance <= 0:
                continue
            payment = (loan.payment_cents or 0) + inputs.extra_payments.get(loan.account_id, 0)
            owed = loan.balance + loan.monthly_interest_cents
            paid = min(payment, owed)
            loan.balance = owed - paid
            if loan.balance <= 0.5:   # paid off this month
                loan.balance = 0
                outcomes[loan.account_id].paid_off_month = shift_month(this_month, k)
                if base.redirect_paid_off:
                    # Its payment (and any extra) goes to savings: only the unused
                    # remainder this month, the full amount from next month on.
                    leftover_this_month += payment - paid
                    freed_from_next_month += payment
        investments = investments * (1 + r_month) + monthly_savings + leftover_this_month
        monthly_savings += freed_from_next_month
        prop *= 1 + g_month
        points.append(point(k))
    return Projection(inputs, points, list(outcomes.values()))


def history_by_month(conn: sqlite3.Connection) -> list[Point]:
    """Actual net worth, one point per month (the month's latest snapshot)."""
    latest: dict[str, networth.HistoryPoint] = {}
    for p in networth.history(conn):
        if not p.carried:
            latest[p.date[:7]] = p
    return [Point(m, p.assets_cents, p.liabilities_cents) for m, p in sorted(latest.items())]


# ---------------------------------------------------------------- scenarios storage

def list_scenarios(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM scenarios ORDER BY name").fetchall()


def get_scenario(conn: sqlite3.Connection, scenario_id: int) -> tuple[sqlite3.Row, dict]:
    row = conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
    if row is None:
        raise LookupError(f"No scenario {scenario_id}")
    return row, json.loads(row["adjustments"])


def save_scenario(conn: sqlite3.Connection, scenario_id: int | None, name: str, adjustments: dict) -> int:
    name = (name or "").strip()
    if not name:
        raise ProjectionError("Give the scenario a name.")
    payload = json.dumps(adjustments, sort_keys=True)
    if scenario_id is None:
        scenario_id = conn.execute("INSERT INTO scenarios (name, adjustments) VALUES (?, ?)", (name, payload)).lastrowid
    else:
        if conn.execute(
            "UPDATE scenarios SET name = ?, adjustments = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (name, payload, scenario_id),
        ).rowcount == 0:
            raise LookupError(f"No scenario {scenario_id}")
    conn.commit()
    return scenario_id


def delete_scenario(conn: sqlite3.Connection, scenario_id: int) -> None:
    conn.execute("DELETE FROM scenarios WHERE id = ?", (scenario_id,))
    conn.commit()


def parse_signed_amount(text: str) -> int:
    """'-200' / '$1,200' / '+50' -> signed cents ('' -> 0)."""
    text = (text or "").strip()
    if not text:
        return 0
    sign = -1 if text.startswith("-") else 1
    try:
        return sign * parse_amount(text.lstrip("+-"))
    except ValueError as exc:
        raise ProjectionError(str(exc)) from None
