"""Budgets: sections and lines, effective-from rows, rollover math, status, not-in-budget,
summary, suggestions, and the edit endpoints."""

import re

import pytest

from fintrack import budgets, categories, items

CHECKING = 1


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    conn.execute(
        "INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification)"
        " VALUES (1, 1, 'acc-1', 'Fake Checking', 'depository', ?)",
        (items.classify("depository"),),
    )
    conn.commit()
    return conn


_ids = iter(range(1, 100_000))


def spend(conn, month, cents, key="FOOD_AND_DRINK", day="15"):
    tid = next(_ids)
    conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key)"
        " VALUES (?, 1, ?, ?, 'Fake', ?)",
        (f"b{tid}", f"{month}-{day}", cents, key),
    )
    conn.commit()


def line(view, category="FOOD_AND_DRINK"):
    """A line from any section, budgeted or not."""
    return next(item for s in view.sections for item in s.lines if item.category == category)


# ---------------------------------------------------------------- effective-from

def test_budget_applies_from_month_until_changed(db):
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-03", "500", rollover=False)
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-06", "450", rollover=False)
    amounts = {m: budgets.budget_for(db, "FOOD_AND_DRINK", m) for m in ("2026-02", "2026-03", "2026-05", "2026-06", "2026-12")}
    assert amounts["2026-02"] is None
    assert amounts["2026-03"].amount_cents == amounts["2026-05"].amount_cents == 50000
    assert amounts["2026-06"].amount_cents == amounts["2026-12"].amount_cents == 45000


def test_clearing_a_budget_from_a_month(db):
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-03", "500", rollover=False)
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-05", "", rollover=False)   # blank = no budget from May
    assert budgets.budget_for(db, "FOOD_AND_DRINK", "2026-04").amount_cents == 50000
    assert budgets.budget_for(db, "FOOD_AND_DRINK", "2026-05") is None


def test_blank_with_no_budget_stores_nothing(db):
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-03", "", rollover=False)
    assert db.execute("SELECT COUNT(*) FROM budgets").fetchone()[0] == 0


def test_spending_and_income_categories_can_be_budgeted(db):
    budgets.set_budget(db, "INCOME", "2026-03", "5000", rollover=True)
    assert budgets.budget_for(db, "INCOME", "2026-03").rollover is False   # no rollover on income
    with pytest.raises(budgets.BudgetError):
        budgets.set_budget(db, "CREDIT_CARD_PAYMENT", "2026-03", "100", rollover=False)
    with pytest.raises(budgets.BudgetError):
        budgets.set_budget(db, "FOOD_AND_DRINK", "2026-03", "-5", rollover=False)


# ---------------------------------------------------------------- rollover math

def test_rollover_underspend_and_overspend_chain(db):
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-01", "500", rollover=True)
    spend(db, "2026-01", 40000)   # leftover +100
    spend(db, "2026-02", 70000)   # available 600, leftover -100
    spend(db, "2026-03", 30000)   # available 400, leftover +100

    jan, feb, mar, apr = (line(budgets.month_view(db, m)) for m in ("2026-01", "2026-02", "2026-03", "2026-04"))
    assert (jan.carry_in_cents, jan.available_cents, jan.remaining_cents) == (0, 50000, 10000)
    assert (feb.carry_in_cents, feb.available_cents, feb.remaining_cents) == (10000, 60000, -10000)
    assert (mar.carry_in_cents, mar.available_cents, mar.remaining_cents) == (-10000, 40000, 10000)
    assert (apr.carry_in_cents, apr.available_cents) == (10000, 60000)


def test_no_rollover_no_carry(db):
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-01", "500", rollover=False)
    spend(db, "2026-01", 10000)
    feb = line(budgets.month_view(db, "2026-02"))
    assert (feb.carry_in_cents, feb.available_cents) == (0, 50000)


def test_rollover_switched_off_midway(db):
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-01", "500", rollover=True)
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-02", "500", rollover=False)
    spend(db, "2026-01", 10000)   # +400 carries into Feb (Jan's row has rollover)
    spend(db, "2026-02", 10000)   # Feb's row has no rollover: nothing carries into Mar
    assert line(budgets.month_view(db, "2026-02")).carry_in_cents == 40000
    assert line(budgets.month_view(db, "2026-03")).carry_in_cents == 0


def test_chain_breaks_at_month_without_budget(db):
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-01", "500", rollover=True)
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-02", "", rollover=True)     # no budget in Feb
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-03", "500", rollover=True)
    spend(db, "2026-01", 10000)
    assert line(budgets.month_view(db, "2026-03")).carry_in_cents == 0


def test_recategorizing_old_spending_changes_later_carry(db):
    from fintrack import categories

    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-01", "500", rollover=True)
    spend(db, "2026-01", 20000)
    assert line(budgets.month_view(db, "2026-02")).carry_in_cents == 30000
    tid = db.execute("SELECT id FROM transactions").fetchone()[0]
    categories.set_transaction_category(db, tid, "TRAVEL")    # no longer food
    assert line(budgets.month_view(db, "2026-02")).carry_in_cents == 50000


# ---------------------------------------------------------------- status

# No warning level between ok and over (user removed it 2026-10-06).
@pytest.mark.parametrize("spent, status", [(0, "ok"), (40000, "ok"), (50000, "ok"), (50001, "over")])
def test_status_boundaries(db, spent, status):
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-09", "500", rollover=False)
    if spent:
        spend(db, "2026-09", spent)
    assert line(budgets.month_view(db, "2026-09")).status == status


def test_starting_in_deficit_is_over(db):
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-08", "100", rollover=True)
    spend(db, "2026-08", 30000)   # -200 carries in; Sep has 100 - 200 = -100 available
    sep = line(budgets.month_view(db, "2026-09"))
    assert sep.available_cents == -10000 and sep.used is None and sep.status == "over"


# ---------------------------------------------------------------- sections and lines

def test_default_sections(db):
    sections = budgets.line_sections(db)
    assert sections["INCOME"] == "income"
    assert sections["RENT_AND_UTILITIES"] == sections["LOAN_PAYMENTS"] == "fixed"
    assert sections["FOOD_AND_DRINK"] == sections["GENERAL_SERVICES"] == sections["UNCATEGORIZED"] == "flexible"
    assert "CREDIT_CARD_PAYMENT" not in sections                     # transfers are never lines
    view = budgets.month_view(db, "2026-09")
    assert [s.key for s in view.sections] == ["income", "fixed", "flexible"]


def test_insurance_default_splits_services(db):
    spend(db, "2026-09", 5000, key="GENERAL_SERVICES")     # before the split: stays on Services
    assert budgets.add_insurance_line(db) is True
    assert budgets.line_sections(db)[budgets.INSURANCE] == "fixed"
    assert db.execute("SELECT is_split FROM categories WHERE key = 'GENERAL_SERVICES'").fetchone()[0] == 1
    view = budgets.month_view(db, "2026-09")
    labels = {line.category: line.label for line in view.section("fixed").lines}
    assert labels[budgets.INSURANCE] == "Insurance"                      # the subcategory's name alone
    assert "GENERAL_SERVICES" not in budgets.line_sections(db)           # split: not a line itself
    # Spending still filed under Services itself stays in its section (Flexible), unbudgeted.
    assert view.outside_of("expense") == []
    assert [(e.category, e.cents) for e in view.section("flexible").extras] == [("GENERAL_SERVICES", 5000)]
    assert budgets.add_insurance_line(db) is True          # running again changes nothing
    assert db.execute("SELECT COUNT(*) FROM budget_lines WHERE category_key = ?", (budgets.INSURANCE,)).fetchone()[0] == 1


def test_add_move_remove_lines(db):
    budgets.remove_line(db, "TRAVEL")
    spend(db, "2026-09", 7000, key="TRAVEL")
    view = budgets.month_view(db, "2026-09")
    assert [(o.category, o.cents) for o in view.outside_of("expense")] == [("TRAVEL", 7000)]
    assert view.actual_expense_cents == 7000                  # still counted in the totals

    budgets.add_line(db, "fixed", "TRAVEL")
    assert budgets.line_sections(db)["TRAVEL"] == "fixed"
    with pytest.raises(budgets.BudgetError, match="already in Fixed"):
        budgets.add_line(db, "flexible", "TRAVEL")
    budgets.move_line(db, "TRAVEL", "flexible")
    assert budgets.line_sections(db)["TRAVEL"] == "flexible"
    with pytest.raises(budgets.BudgetError):
        budgets.move_line(db, "INCOME", "fixed")                # income stays in Income
    with pytest.raises(budgets.BudgetError, match="Only income"):
        budgets.add_line(db, "income", "MEDICAL")

    key = budgets.add_line(db, "flexible", new_label="Fake Kids Activities")   # new custom category
    assert key == "FAKE_KIDS_ACTIVITIES" and budgets.line_sections(db)[key] == "flexible"
    assert db.execute("SELECT kind FROM categories WHERE key = ?", (key,)).fetchone()[0] == "expense"


def test_line_whose_kind_changed_is_left_out(db):
    from fintrack import categories

    categories.set_kind(db, "TRAVEL", "transfer")
    assert "TRAVEL" not in budgets.line_sections(db)
    assert "TRAVEL" not in [line.category for s in budgets.month_view(db, "2026-09").sections for line in s.lines]


def test_not_in_budget_and_summary(db):
    budgets.remove_line(db, "TRAVEL")
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-09", "500", rollover=False)
    budgets.set_budget(db, "RENT_AND_UTILITIES", "2026-09", "1500", rollover=False)
    budgets.set_budget(db, "INCOME", "2026-09", "4000", rollover=False)
    spend(db, "2026-09", 10000)
    spend(db, "2026-09", 150000, key="RENT_AND_UTILITIES")
    spend(db, "2026-09", 7000, key="TRAVEL")                       # not in budget
    spend(db, "2026-09", 3000, key="ENTERTAINMENT")                # a line without an amount
    spend(db, "2026-09", 99999, key="CREDIT_CARD_PAYMENT")         # not spending: never appears
    spend(db, "2026-09", -300000, key="INCOME")
    view = budgets.month_view(db, "2026-09")

    fixed, flexible, income = view.section("fixed"), view.section("flexible"), view.section("income")
    assert (fixed.budget_cents, fixed.spent_cents, fixed.remaining_cents) == (150000, 150000, 0)
    assert (flexible.budget_cents, flexible.spent_cents, flexible.remaining_cents) == (50000, 13000, 37000)
    assert income.spent_cents == 300000 and income.lines[0].remaining_cents == 100000   # still expected
    assert income.lines[0].status == "income"

    assert view.planned_income_cents == 400000 and view.actual_income_cents == 300000
    assert view.planned_expense_cents == 200000 and view.actual_expense_cents == 170000
    assert view.left_to_budget_cents == 200000
    assert view.income_used == 0.75 and view.expense_used == 0.85 and view.expense_status == "ok"
    # Overview: budgeted spending lines only; everything else is "unbudgeted".
    assert {line.category for line in view.lines} == {"FOOD_AND_DRINK", "RENT_AND_UTILITIES"}
    assert view.unbudgeted_cents == 10000


def test_attention_lists_worst_first(db):
    for key, budget, spent in [("FOOD_AND_DRINK", "100", 9000), ("TRAVEL", "100", 15000), ("MEDICAL", "100", 1000)]:
        budgets.set_budget(db, key, "2026-09", budget, rollover=False)
        spend(db, "2026-09", spent, key=key)
    assert [item.category for item in budgets.month_view(db, "2026-09").attention] == ["TRAVEL"]   # over only


# ---------------------------------------------------------------- suggestions

def test_suggestions_prior_month_and_averages(db):
    for month, cents in [("2026-03", 60000), ("2026-04", 1000), ("2026-05", 1000),
                         ("2026-07", 40000), ("2026-08", 35550), ("2026-09", 30000)]:
        spend(db, month, cents)
    spend(db, "2026-10", 77777)                                   # the month itself: ignored
    spend(db, "2026-06", 5000, key="TRAVEL")                      # June has data (no food spending)
    food = line(budgets.month_view(db, "2026-10"))
    assert food.suggestions["prior"] == 30000
    assert food.suggestions["avg3"] == 35200                      # (400 + 355.50 + 300) / 3 = 351.83 -> $352
    assert food.suggestions["avg6"] == 17900                      # Apr..Sep: 1074.50 / 6 = 179.08 -> $179


def test_suggestions_average_only_months_with_data(db):
    spend(db, "2026-09", 12300)
    food = line(budgets.month_view(db, "2026-10"))
    assert food.suggestions == {"prior": 12300, "avg3": 12300, "avg6": 12300}   # not 12300 / 3
    assert line(budgets.month_view(db, "2026-01")).suggestions == {"prior": None, "avg3": None, "avg6": None}


# ---------------------------------------------------------------- pages

ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


HX = {**ORIGIN, "HX-Request": "true"}


def test_amount_saved_in_place_and_unchanged_writes_nothing(client, db):
    response = client.post("/budgets/amount", data={"category": "TRAVEL", "month": "2026-09", "amount": "$1,200"}, headers=HX)
    assert response.status_code == 200 and 'id="budget"' in response.text and "<html" not in response.text
    assert budgets.budget_for(db, "TRAVEL", "2026-12").amount_cents == 120000
    client.post("/budgets/amount", data={"category": "TRAVEL", "month": "2026-10", "amount": "1200"}, headers=HX)
    assert db.execute("SELECT COUNT(*) FROM budgets").fetchone()[0] == 1     # same amount: no new row

    bad = client.post("/budgets/amount", data={"category": "TRAVEL", "month": "2026-09", "amount": "lots"}, headers=HX)
    assert 'class="flash bad"' in bad.text


def test_rollover_toggle(client, db):
    refused = client.post("/budgets/rollover", data={"category": "TRAVEL", "month": "2026-09", "rollover": "on"}, headers=HX)
    assert "Set a budget amount first" in refused.text
    budgets.set_budget(db, "TRAVEL", "2026-09", "100", rollover=False)
    client.post("/budgets/rollover", data={"category": "TRAVEL", "month": "2026-09", "rollover": "on"}, headers=HX)
    assert budgets.budget_for(db, "TRAVEL", "2026-09").rollover is True
    client.post("/budgets/rollover", data={"category": "TRAVEL", "month": "2026-09"}, headers=HX)   # unchecked
    assert budgets.budget_for(db, "TRAVEL", "2026-09").rollover is False


def test_line_endpoints(client, db):
    client.post("/budgets/lines/remove", data={"category": "TRAVEL", "month": "2026-09"}, headers=HX)
    assert "TRAVEL" not in budgets.line_sections(db)
    client.post("/budgets/lines", data={"section": "fixed", "category": "TRAVEL", "month": "2026-09"}, headers=HX)
    client.post("/budgets/lines/move", data={"category": "TRAVEL", "section": "flexible", "month": "2026-09"}, headers=HX)
    assert budgets.line_sections(db)["TRAVEL"] == "flexible"
    page = client.post("/budgets/lines", data={"section": "income", "name": "Fake Side Gig", "month": "2026-09"},
                       headers=HX).text
    assert budgets.line_sections(db)["FAKE_SIDE_GIG"] == "income" and "Fake Side Gig" in page
    empty = client.post("/budgets/lines", data={"section": "fixed", "month": "2026-09"}, headers=HX)
    assert "Choose a category to add" in empty.text


def test_budget_page_and_dashboard_card(client, db):
    from datetime import date

    month = f"{date.today():%Y-%m}"
    assert "No budgets yet" in client.get("/").text

    budgets.set_budget(db, "FOOD_AND_DRINK", month, "100", rollover=False)
    budgets.set_budget(db, "TRAVEL", month, "100", rollover=False)
    card = client.get("/").text.split("Spending by budget category")[1].split("</section>")[0]
    assert "ov-budget" in card and "on track" not in card       # the chart only: no summary above it (2026-10-23)

    spend(db, month, 9000, day="01")            # food: 90%, still on track (no warning level)
    spend(db, month, 12500, key="TRAVEL", day="01")   # travel: over by $25
    dash = client.get("/").text
    card = dash.split("Spending by budget category")[1].split("</section>")[0]
    assert "Over by" not in card and "attention-list" not in card
    spend(db, month, 4000, key="MEDICAL", day="01")                 # a line without an amount
    page = client.get("/budgets", params={"month": month}).text
    assert "Left to budget" in page and "Fixed expenses" in page and "Flexible expenses" in page
    assert "fill-over" in page and "fill-ok" in page and "fill-near" not in page
    assert '<tr class="bar-row line-bar ">' in page and "Progress</th>" not in page   # bars under rows
    assert "/budgets/threshold" not in page and "Warn when" not in page
    assert 'data-panel="FOOD_AND_DRINK"' in page and "3-month average: no history" in page
    for removed in ("Pace this month", "Spent vs budget", "Month-end spending", "History", "Edit budgets"):
        assert removed not in page



# ---------------------------------------------------------------- savings, 1-year and 10-year views

TODAY = __import__("datetime").date(2026, 10, 6)


@pytest.fixture
def planned(db):
    """Budgets from Aug 2026: income $4,000, food $500, rent $1,500. Actuals Jul-Oct."""
    for key, amount in (("INCOME", "4000"), ("FOOD_AND_DRINK", "500"), ("RENT_AND_UTILITIES", "1500")):
        budgets.set_budget(db, key, "2026-08", amount, rollover=False)
    budgets.remove_line(db, "TRAVEL")
    spend(db, "2026-07", 30000)                               # before budgeting began
    spend(db, "2026-08", -380000, key="INCOME")
    spend(db, "2026-08", 60000)                               # Aug leftover 3,800 - 600 = 3,200
    spend(db, "2026-09", -400000, key="INCOME")
    spend(db, "2026-09", 40000)
    spend(db, "2026-09", 150000, key="RENT_AND_UTILITIES")
    spend(db, "2026-09", 7000, key="TRAVEL")                  # not in budget, still an expense
    spend(db, "2026-10", 10000)                               # current month: budget used instead
    return db


def test_year_view_actuals_past_budget_from_this_month(planned):
    plan = budgets.plan(planned, budgets.year_columns("2026-01"), today=TODAY)
    assert [c.basis for c in plan.columns][6:10] == ["actual", "actual", "actual", "budget"]
    food = next(line for s in plan.sections for line in s.lines if line.category == "FOOD_AND_DRINK")
    assert food.values[6:12] == [30000, 60000, 40000, 50000, 50000, 50000]
    flexible = next(s for s in plan.sections if s.key == "flexible")
    assert flexible.outside[8] == 7000 and flexible.outside[9] == 0       # "Not in budget": past only
    leftover = [i - e for i, e in zip(plan.income, plan.expense)]
    assert leftover[6:12] == [-30000, 320000, 203000, 200000, 200000, 200000]
    assert not hasattr(plan, "balance")                                # no rolled-over savings (user)

    later = budgets.plan(planned, budgets.year_columns("2027-01"), today=TODAY)
    assert {c.basis for c in later.columns} == {"budget"}


def test_decade_view_annual_columns(planned):
    plan = budgets.plan(planned, budgets.decade_columns(2025), today=TODAY)
    assert [c.key for c in plan.columns] == [str(y) for y in range(2025, 2035)]
    assert [c.basis for c in plan.columns[:3]] == ["actual", "mixed", "budget"]
    # 2026 = actual Jan-Sep + budget Oct-Dec.
    assert plan.income[1] == 380000 + 400000 + 3 * 400000
    assert plan.expense[1] == 30000 + 60000 + 197000 + 3 * 200000
    assert plan.income[2] - plan.expense[2] == 12 * 200000             # 2027: budgets only


def test_month_view_savings_is_this_month_only(planned):
    october = budgets.month_view(planned, "2026-10", today=TODAY)
    assert october.left_to_budget_cents == 200000 and october.actual_leftover_cents == -10000
    september = budgets.month_view(planned, "2026-09", today=TODAY)
    assert september.actual_leftover_cents == 400000 - 197000          # nothing carried in from August
    assert not hasattr(september, "savings_carried_cents")


def test_year_and_decade_pages(client, db):
    from datetime import date

    budgets.set_budget(db, "INCOME", f"{date.today():%Y-%m}", "4000", rollover=False)
    month_page = client.get("/budgets").text
    assert 'aria-current="page">Month<' in month_page and "Rolled over" not in month_page
    assert '<th scope="rowgroup">Savings</th>' in month_page                        # this month only
    year = client.get("/budgets", params={"view": "year", "start": "2026-01"}).text
    assert 'aria-current="page">1 year<' in year and ">Jan 2026" in year and ">Dec 2026" in year
    assert "Savings balance" not in year and 'id="amount-' not in year
    assert "start=2025-12" in year and "start=2026-02" in year               # a month at a time
    decade = client.get("/budgets", params={"view": "decade", "start": "2025"}).text
    assert ">2025" in decade and ">2034" in decade
    assert 'aria-label="Back a year (start 2024)"' in decade and 'aria-label="Ahead a year (start 2026)"' in decade
    ribbon = decade.split('<header class="tabbar">')[1].split("</header>")[0]
    assert '>←</a>' in ribbon and '2025–2034' not in ribbon                      # arrows only up there
    assert client.get("/budgets", params={"view": "year", "start": "junk"}).status_code == 200


def test_toolbar_and_non_budgeted_group(client, db):
    from datetime import date

    month = f"{date.today():%Y-%m}"
    budgets.set_budget(db, "FOOD_AND_DRINK", month, "500", rollover=False)
    budgets.set_budget(db, "TRAVEL", month, "0", rollover=False)            # $0 counts as no budget
    spend(db, month, 4000, key="MEDICAL", day="01")
    spend(db, month, 2500, key="TRAVEL", day="01")
    page = " ".join(client.get("/budgets").text.split())
    assert "+ Add budget<" in page and "data-group-toggle" in page
    assert 'class="add-line"' not in page                                      # no per-section add rows
    assert '<tr class="line nobudget has-bar" data-line="TRAVEL">' in page        # $0: over, bar shown
    assert '<tr class="line has-bar" data-line="FOOD_AND_DRINK">' in page
    flexible = page.split('data-section="flexible"')[1].split("</tbody>")[0]
    group = flexible.split('class="nobudget-group"')[1].split("</tr>")[0]
    assert "Non-budgeted items (" in group and "$65.00" in group               # Medical + Travel


# ---------------------------------------------------------------- formulas in the budget box

@pytest.mark.parametrize("text, cents", [
    ("795", 79500), ("$1,200.50", 120050),
    ("=795/12", 6625), ("795/12", 6625),                  # 66.25 exactly
    ("=1000/3", 33333), ("=2/3", 67),                     # half-up to the cent
    ("=(1200 + 300) * 12%", 18000), ("=4000*15%", 60000),
    ("=-(5)+10", 500), ("= $1,000 / 4", 25000), ("=50%%", 1), ("=10-(2*3)", 400),
])
def test_amount_formulas(text, cents):
    assert budgets.parse_amount(text) == cents


@pytest.mark.parametrize("text, message", [
    ("=795/0", "divide by zero"), ("=5-10", "positive"), ("=abc", "calculation"),
    ("=(5+3", "calculation"), ("=5+", "calculation"), ("=", "calculation"),
    ("=__import__('os')", "calculation"), ("=" + "1+" * 150 + "1", "too long"),
])
def test_bad_formulas(text, message):
    with pytest.raises(ValueError, match=message):
        budgets.parse_amount(text)


def test_formula_saved_as_its_result(client, db):
    response = client.post("/budgets/amount", data={"category": "TRAVEL", "month": "2026-09", "amount": "=795/12"}, headers=HX)
    assert budgets.budget_for(db, "TRAVEL", "2026-09").amount_cents == 6625
    assert 'value="$66.25"' in response.text                                 # the box shows the number
    bad = client.post("/budgets/amount", data={"category": "TRAVEL", "month": "2026-09", "amount": "=1/0"}, headers=HX)
    assert "divide by zero" in bad.text and budgets.budget_for(db, "TRAVEL", "2026-09").amount_cents == 6625


def test_budget_box_shows_dollars_and_commas_and_saves_them_back(client, db):
    budgets.set_budget(db, "TRAVEL", "2026-09", "1234.5", rollover=False)
    page = client.get("/budgets", params={"month": "2026-09"}).text
    assert 'value="$1,234.50"' in page
    # Leaving the box unchanged posts the formatted text back: nothing new is written.
    client.post("/budgets/amount", data={"category": "TRAVEL", "month": "2026-10", "amount": "$1,234.50"}, headers=HX)
    assert db.execute("SELECT COUNT(*) FROM budgets").fetchone()[0] == 1



# ---------------------------------------------------------------- editing in the 1-year view

def test_scoped_budget_only_this_month(db):
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-10", "500", rollover=True)
    budgets.set_budget_scoped(db, "FOOD_AND_DRINK", "2026-12", "800", "month")
    amounts = {m: budgets.budget_for(db, "FOOD_AND_DRINK", m) for m in ("2026-11", "2026-12", "2027-01", "2027-06")}
    assert [amounts[m].amount_cents for m in sorted(amounts)] == [50000, 80000, 50000, 50000]
    assert amounts["2026-12"].rollover is True and amounts["2027-01"].rollover is True   # kept
    # A month with its own row after it: that row is left alone.
    budgets.set_budget(db, "TRAVEL", "2027-02", "100", rollover=False)
    budgets.set_budget_scoped(db, "TRAVEL", "2027-01", "300", "month")
    assert budgets.budget_for(db, "TRAVEL", "2027-01").amount_cents == 30000
    assert budgets.budget_for(db, "TRAVEL", "2027-02").amount_cents == 10000
    assert budgets.budget_for(db, "TRAVEL", "2026-12") is None
    # No budget before or after: only that month gets one.
    budgets.set_budget_scoped(db, "MEDICAL", "2026-11", "50", "month")
    assert [budgets.budget_for(db, "MEDICAL", m) is None for m in ("2026-10", "2026-11", "2026-12")] == [True, False, True]


def test_scoped_budget_every_month_after_replaces_later_changes(db):
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-10", "500", rollover=False)
    budgets.set_budget(db, "FOOD_AND_DRINK", "2027-03", "650", rollover=False)
    budgets.set_budget_scoped(db, "FOOD_AND_DRINK", "2026-12", "=1200/2", "forward")
    assert budgets.budget_for(db, "FOOD_AND_DRINK", "2026-11").amount_cents == 50000
    assert budgets.budget_for(db, "FOOD_AND_DRINK", "2026-12").amount_cents == 60000
    assert budgets.budget_for(db, "FOOD_AND_DRINK", "2027-03").amount_cents == 60000      # later change replaced
    with pytest.raises(budgets.BudgetError):
        budgets.set_budget_scoped(db, "FOOD_AND_DRINK", "2026-12", "lots", "forward")
    with pytest.raises(budgets.BudgetError):
        budgets.set_budget_scoped(db, "FOOD_AND_DRINK", "2026-12", "5", "sometimes")
    assert budgets.budget_for(db, "FOOD_AND_DRINK", "2026-12").amount_cents == 60000      # unchanged


def test_year_view_cells_are_editable_from_this_month(client, db):
    from datetime import date

    from fintrack.spending import shift_month

    this = f"{date.today():%Y-%m}"
    start, later = shift_month(this, -2), shift_month(this, 1)
    budgets.set_budget(db, "FOOD_AND_DRINK", this, "500", rollover=False)
    page = client.get("/budgets", params={"view": "year", "start": start}).text
    assert f'id="plan-FOOD_AND_DRINK-{this}"' in page and f'id="plan-FOOD_AND_DRINK-{start}"' not in page   # past: actuals
    assert 'id="scope-dialog"' in page and 'id="plan-save"' in page
    assert 'value="$500" data-original="$500"' in page and 'data-exact="500.00"' in page   # whole dollars shown
    budgets.set_budget(db, "TRAVEL", this, "66.5", rollover=False)
    page = client.get("/budgets", params={"view": "year", "start": start}).text
    assert 'value="$67"' in page and 'data-exact="66.50"' in page              # stored to the cent

    response = client.post("/budgets/plan-amount", headers=HX, data={
        "category": "FOOD_AND_DRINK", "month": later, "amount": "700", "scope": "month", "start": start})
    assert response.status_code == 200 and 'id="budget-plan"' in response.text and "<html" not in response.text
    assert budgets.budget_for(db, "FOOD_AND_DRINK", later).amount_cents == 70000
    assert budgets.budget_for(db, "FOOD_AND_DRINK", shift_month(later, 1)).amount_cents == 50000
    past = client.post("/budgets/plan-amount", headers=HX, data={
        "category": "FOOD_AND_DRINK", "month": start, "amount": "1", "scope": "month", "start": start})
    assert "budgets can be set from this month on" in past.text
    assert budgets.budget_for(db, "FOOD_AND_DRINK", start) is None


def test_month_view_asks_how_far_a_change_applies(client, db):
    budgets.set_budget(db, "TRAVEL", "2026-09", "100", rollover=False)
    page = client.get("/budgets", params={"month": "2026-10"}).text
    assert 'id="scope-dialog"' in page and 'id="amount-save"' in page
    assert 'data-scope-form="amount-save"' in page and 'hx-post="/budgets/amount" hx-trigger' not in page
    # "Only Oct 2026": November goes back to $100.
    client.post("/budgets/amount", headers=HX, data={"category": "TRAVEL", "month": "2026-10", "amount": "250", "scope": "month"})
    assert [budgets.budget_for(db, "TRAVEL", m).amount_cents for m in ("2026-09", "2026-10", "2026-11")] == [10000, 25000, 10000]
    # "Every month after" from December replaces the November row too, from December on.
    client.post("/budgets/amount", headers=HX, data={"category": "TRAVEL", "month": "2026-12", "amount": "300", "scope": "forward"})
    assert [budgets.budget_for(db, "TRAVEL", m).amount_cents for m in ("2026-11", "2026-12", "2027-08")] == [10000, 30000, 30000]


def test_year_view_has_no_total_column(client, db):
    import re

    year = client.get("/budgets", params={"view": "year", "start": "2026-01"}).text
    decade = client.get("/budgets", params={"view": "decade", "start": "2025"}).text
    assert ">Total</th>" not in year and ">Total</th>" in decade
    body = year.split('class="data plan-table"')[1].split("</table>")[0]
    # Every row: the label + 12 month cells (no 14th cell anywhere).
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
        assert len(re.findall(r"<t[hd][\s>]", row)) == 13, row[:120]


def test_year_view_defaults_to_last_month_plus_eleven(client):
    from datetime import date

    from fintrack.spending import shift_month

    this = f"{date.today():%Y-%m}"
    page = client.get("/budgets", params={"view": "year"}).text
    first, last = shift_month(this, -1), shift_month(this, 10)
    assert f'name="start" value="{first}"' in page and f'id="plan-INCOME-{last}"' in page
    assert f'id="plan-INCOME-{shift_month(this, 11)}"' not in page


def test_month_view_names_its_month_in_the_summary(client):
    page = client.get("/budgets", params={"month": "2026-10"}).text
    summary = page.split('class="card budget-summary')[1].split("</section>")[0]
    assert '<h2 class="summary-month">October 2026</h2>' in summary
    assert summary.index("summary-month") < summary.index("Left to budget")           # its own row, on top
    assert 'aria-label="Previous month (Sep 2026)"' in page and 'aria-label="Next month (Nov 2026)"' in page
    ribbon = page.split('<header class="tabbar">')[1].split("</header>")[0]
    assert 'class="tabbar-controls"' in ribbon and 'class="page-head"' not in page   # in the ribbon
    assert ribbon.index("step-nav") < ribbon.index('aria-label="Budget view"')       # arrows, then toggle


# ---------------------------------------------------------------- upcoming recurring

def recurring_item(conn, name, cents, frequency, next_date, category, dom=None):
    conn.execute(
        """INSERT INTO recurring_items (name, account_id, category_key, amount_cents, frequency,
               days_of_month, next_expected_date, type, status, source)
           VALUES (?, 1, ?, ?, ?, ?, ?, ?, 'confirmed', 'manual')""",
        (name, category, cents, frequency, dom, next_date, "income" if cents < 0 else "bill"),
    )
    conn.commit()


def test_upcoming_recurring_per_line(db):
    recurring_item(db, "Fake Internet", 6000, "monthly", "2026-10-20", "RENT_AND_UTILITIES", dom="20")
    recurring_item(db, "Fake Paycheck", -200000, "semimonthly", "2026-10-15", "INCOME", dom="1,15")
    recurring_item(db, "Fake Gym", 4000, "monthly", "2026-10-02", "PERSONAL_CARE", dom="2")      # overdue
    recurring_item(db, "Fake Streaming", 1500, "monthly", "2026-10-25", "TRAVEL", dom="25")      # no line
    budgets.remove_line(db, "TRAVEL")
    db.execute("UPDATE recurring_items SET status = 'detected' WHERE name = 'Fake Gym'")       # unconfirmed:
    db.commit()                                                                                 # ignored
    recurring_item(db, "Fake Phone", 5000, "monthly", "2026-10-03", "RENT_AND_UTILITIES", dom="3")   # overdue

    view = budgets.month_view(db, "2026-10", today=TODAY)
    rent = line(view, "RENT_AND_UTILITIES")
    assert rent.upcoming_cents == 11000                                 # Internet + the overdue phone bill
    assert sorted(rent.upcoming_items) == ["Fake Internet, Oct 20", "Fake Phone, Oct 3"]
    assert line(view, "INCOME").upcoming_cents == 200000            # Oct 15 only (Oct 1 already passed)
    assert line(view, "PERSONAL_CARE").upcoming_cents == 0
    assert [(o.category, o.upcoming_cents) for o in view.outside_of("expense")] == [("TRAVEL", 1500)]
    assert view.section("fixed").upcoming_cents == 11000

    november = budgets.month_view(db, "2026-11", today=TODAY)
    assert line(november, "INCOME").upcoming_cents == 400000         # Nov 1 and Nov 15
    assert line(november, "RENT_AND_UTILITIES").upcoming_cents == 11000
    assert line(budgets.month_view(db, "2026-09", today=TODAY), "INCOME").upcoming_cents == 0   # past month


def test_upcoming_column_on_the_page(client, db):
    from datetime import date

    from fintrack.spending import shift_month

    later = shift_month(f"{date.today():%Y-%m}", 1)
    recurring_item(db, "Fake Internet", 6000, "monthly", f"{later}-20", "RENT_AND_UTILITIES", dom="20")
    page = client.get("/budgets", params={"month": later}).text
    assert ">Upcoming</th>" in page and 'title="Fake Internet, ' in page and "$60.00" in page
    head = page.split("<thead>")[1].split("</thead>")[0]
    assert head.index("Actual") < head.index("Upcoming") < head.index("Remaining")
    import re
    table = page.split('class="data budget-table"')[1].split("</table>")[0]
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
        cells = re.findall(r"<t[hd](?:\s[^>]*)?>", row)
        spans = sum(int(m) for m in re.findall(r'colspan="(\d)"', " ".join(cells))) - len(re.findall("colspan", " ".join(cells)))
        assert len(cells) + spans == 7, row[:100]


def test_plan_grouping_depends_on_the_range(db):
    budgets.set_budget(db, "TRAVEL", "2027-03", "200", rollover=False)        # a budget only from March
    spend(db, "2026-08", 4500, key="MEDICAL")                                  # an actual only in August

    def empty(plan):
        return {line.category for s in plan.sections for line in s.lines if line.empty}

    early = budgets.plan(db, budgets.year_columns("2026-09"), today=TODAY)    # Sep 2026 - Aug 2027
    later = budgets.plan(db, budgets.year_columns("2027-04"), today=TODAY)
    before = budgets.plan(db, budgets.year_columns("2025-09"), today=TODAY)   # Sep 2025 - Aug 2026
    assert "TRAVEL" not in empty(early) and "MEDICAL" in empty(early)
    assert "TRAVEL" not in empty(later) and "MEDICAL" in empty(later)
    assert "TRAVEL" in empty(before) and "MEDICAL" not in empty(before)
    decade = budgets.plan(db, budgets.decade_columns(2025), today=TODAY)
    assert "TRAVEL" not in empty(decade) and "MEDICAL" not in empty(decade) and "FOOD_AND_DRINK" in empty(decade)


def test_plan_page_has_the_grouping_toggle(client, db):
    spend(db, "2026-08", 4500, key="MEDICAL")
    page = client.get("/budgets", params={"view": "year", "start": "2026-01"}).text
    assert "data-group-toggle" in page and "Non-budgeted items (" in page
    import re
    rows = {re.search(r"category=([A-Z_]+)\">", r).group(1): cls
            for cls, r in re.findall(r'<tr class="(nobudget|)">(.*?)</tr>', page, re.S) if "category=" in r}
    assert rows["MEDICAL"] == "" and rows["TRAVEL"] == "nobudget"           # an actual keeps Medical out


def test_plan_categories_and_past_actuals_link_to_transactions(client, db):
    spend(db, "2026-08", 4500, key="MEDICAL")
    year = client.get("/budgets", params={"view": "year", "start": "2026-07"}).text
    assert '<a href="/transactions?category=MEDICAL">Medical</a>' in year
    assert '<a href="/transactions?month=2026-08&amp;category=MEDICAL">$45</a>' in year
    decade = client.get("/budgets", params={"view": "decade", "start": "2025"}).text
    assert '<a href="/transactions?category=MEDICAL">Medical</a>' in decade and "month=2026&amp;" not in decade


def test_lines_sorted_largest_first(db):
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-09", "300", rollover=False)
    budgets.set_budget(db, "TRAVEL", "2026-09", "900", rollover=False)
    spend(db, "2026-09", 5000, key="MEDICAL")                         # no budget: after budgeted, by actual
    spend(db, "2026-09", 9000, key="ENTERTAINMENT")
    order = [line.category for line in budgets.month_view(db, "2026-09", today=TODAY).section("flexible").lines]
    assert order[:4] == ["TRAVEL", "FOOD_AND_DRINK", "ENTERTAINMENT", "MEDICAL"]
    plan = budgets.plan(db, budgets.year_columns("2026-09"), today=TODAY)   # Sep actual, Oct+ budget
    flexible = [line.category for line in next(s for s in plan.sections if s.key == "flexible").lines]
    assert flexible[:4] == ["TRAVEL", "FOOD_AND_DRINK", "ENTERTAINMENT", "MEDICAL"]


def test_group_row_sits_right_above_the_rows_it_opens(client, db):
    import re
    from datetime import date

    month = f"{date.today():%Y-%m}"
    budgets.set_budget(db, "FOOD_AND_DRINK", month, "300", rollover=False)
    spend(db, month, 5000, key="MEDICAL", day="01")
    page = client.get("/budgets", params={"month": month}).text
    flexible = page.split('<tbody data-section="flexible">')[1].split("</tbody>")[0]
    kinds = []
    for cls in re.findall(r'<tr class="([^"]*)"', flexible):
        words = cls.split()
        if words and words[0] == "nobudget-group":
            kinds.append("group")
        elif words and words[0] == "line":                       # a line row (not its panel or bar)
            kinds.append("loose" if "nobudget" in words else "budgeted")
    first_loose = kinds.index("loose")
    assert kinds[first_loose - 1] == "group" and "budgeted" not in kinds[first_loose:]   # budgeted above

    plan = client.get("/budgets", params={"view": "year", "start": month}).text
    flexible = plan.split('<tbody data-section="flexible">')[1].split("</tbody>")[0]
    kinds = re.findall(r'<tr class="(nobudget-group|nobudget|)"', flexible)
    first_empty = kinds.index("nobudget")
    assert kinds[first_empty - 1] == "nobudget-group" and "" not in kinds[first_empty:]



def test_percent_of_income_columns_and_summary_savings(client, db):
    from datetime import date

    from fintrack.spending import shift_month

    month = shift_month(f"{date.today():%Y-%m}", -1)                     # a past month: all actual
    budgets.set_budget(db, "INCOME", month, "4000", rollover=False)
    budgets.set_budget(db, "FOOD_AND_DRINK", month, "600", rollover=False)
    spend(db, month, -500000, key="INCOME")
    spend(db, month, 50000)                                              # food: $500 of $5,000 = 10%
    page = " ".join(client.get("/budgets", params={"month": month}).text.split())
    food = page.split('data-line="FOOD_AND_DRINK"')[1].split("</tr>")[0]
    pcts = re.findall(r'class="num pct">([^<]*)<', food)
    assert pcts == ["15%", "10%"]                                        # $600 of $4,000 planned; $500 of $5,000
    summary = page.split('class="card budget-summary')[1].split("</section>")[0]
    assert ">Savings<" in summary and "$4,500" in summary and '<span class="slash">/</span>$3,400' in summary
    assert "of income received" not in summary and "% planned" not in summary   # removed (user)
    assert "of planned income" not in summary and " planned</span>" not in summary


def test_savings_bar_reverses_and_turns_red_when_negative(client, db):
    from datetime import date

    from fintrack.spending import shift_month

    month = shift_month(f"{date.today():%Y-%m}", -1)
    budgets.set_budget(db, "INCOME", month, "4000", rollover=False)
    budgets.set_budget(db, "FOOD_AND_DRINK", month, "3000", rollover=False)     # $1,000 planned savings
    spend(db, month, -300000, key="INCOME")
    spend(db, month, 350000)                                                    # $500 more out than in
    summary = client.get("/budgets", params={"month": month}).text.split('class="card budget-summary')[1].split("</section>")[0]
    savings = summary.split(">Savings<")[1]
    assert 'class="meter wide reverse"><span class="meter-fill fill-over" data-share="0.5000"' in savings
    assert "more than earned" not in savings                                   # no warning label (user)

    spend(db, month, -400000, key="INCOME")                                     # now $3,500 saved
    savings = client.get("/budgets", params={"month": month}).text.split(">Savings<")[1].split("</section>")[0]
    assert 'class="meter wide"><span class="meter-fill fill-ok" data-share="1.0000"' in savings
    assert "✓ 350% of plan" in savings



def test_summary_without_budgets_shows_empty_bars(client, db):
    from datetime import date

    from fintrack.spending import shift_month

    month = shift_month(f"{date.today():%Y-%m}", -1)
    spend(db, month, -300000, key="INCOME")
    spend(db, month, 50000)
    summary = client.get("/budgets", params={"month": month}).text.split('class="card budget-summary')[1].split("</section>")[0]
    assert "Set planned income" not in summary and "Set budgets in" not in summary
    assert summary.count('<span class="meter wide"></span>') == 3          # income, expenses, savings


def test_range_summary_totals_add_up(planned):
    plan = budgets.plan(planned, budgets.year_columns("2026-07"), today=TODAY)   # Jul 2026 - Jun 2027
    fixed, flexible = plan.section_total("fixed"), plan.section_total("flexible")
    assert fixed + flexible + plan.savings_total == plan.income_total
    # Jul-Sep actual (incl. $70 Travel not in budget, under Flexible) + Oct-Jun budget (9 months).
    assert plan.income_total == 380000 + 400000 + 9 * 400000
    assert fixed == 150000 + 9 * 150000 and flexible == 30000 + 60000 + 40000 + 7000 + 9 * 50000


def test_range_summary_card_on_both_views(client, db):
    budgets.set_budget(db, "INCOME", "2026-01", "4000", rollover=False)
    budgets.set_budget(db, "RENT_AND_UTILITIES", "2026-01", "1000", rollover=False)
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-01", "600", rollover=False)
    for params, title in (({"view": "year", "start": "2030-01"}, "Jan 2030 – Dec 2030"),
                          ({"view": "decade", "start": "2030"}, "2030–2039")):
        page = " ".join(client.get("/budgets", params=params).text.split())
        card = page.split('class="card budget-summary budget-side"')[1].split("</section>")[0]
        assert 'class="budget-layout"' in page and 'class="card budget-main"' in page   # same grid as Month
        assert f'<h2 class="summary-month">{title}</h2>' in card
        assert "Fixed expenses" in card and "Flexible expenses" in card and "Savings" in card
        assert 'class="share-pct">(25%)' in card and 'class="share-pct">(15%)' in card and 'class="share-pct">(60%)' in card
        assert "Fixed expenses (" not in card
        assert "of income" not in card
        assert card.count('class="swatch ') == 3 and card.count('class="meter wide') == 0       # one stacked bar
        bar = card.split('class="stack-bar r-bar"')[1].split("</div>")[0]
        assert re.findall(r'meter-fill (slot-\d)" data-share="([\d.]+)"', bar) == [
            ("slot-1", "0.2500"), ("slot-2", "0.1500"), ("slot-3", "0.6000")]


def test_savings_rows_are_income_minus_expenses_without_carry(client, planned):
    plan = budgets.plan(planned, budgets.year_columns("2026-07"), today=TODAY)
    assert plan.savings[:4] == [-30000, 320000, 203000, 200000]          # Jul..Oct, each on its own
    year = client.get("/budgets", params={"view": "year", "start": "2026-07"}).text
    row = year.split('<tbody data-section="savings">')[1].split("</tbody>")[0]
    assert ">Savings</th>" in row and "$3,200" in row and "$2,030" in row and "Rolled over" not in row
    month = client.get("/budgets", params={"month": "2026-09"}).text
    row = month.split('<tbody data-section="savings">')[1].split("</tbody>")[0]
    assert "$2,030.00" in row                                             # Sep actual: 4,000 − 1,970


# ---------------------------------------------------------------- needs / wants / savings (user's request 2026-10-28)

def sections_of(view):
    return {s.key: [line.category for line in s.lines] for s in view.sections}


def test_default_needs_are_essentials_and_the_rest_wants():
    assert budgets.default_need_want("RENT_AND_UTILITIES_RENT", "RENT_AND_UTILITIES") == "needs"   # a subcategory
    assert budgets.default_need_want("MEDICAL", None) == "needs"
    assert budgets.default_need_want("FOOD_AND_DRINK__GROCERIES", "FOOD_AND_DRINK") == "needs"     # custom, by ending
    assert budgets.default_need_want("FOOD_AND_DRINK__RESTAURANTS", "FOOD_AND_DRINK") == "wants"
    assert budgets.default_need_want("SUBSCRIPTIONS", None) == "wants"                             # fixed, still a want


def test_the_framework_regroups_lines_and_each_keeps_its_other_place(db):
    assert budgets.framework(db) == "fixed"
    fixed = sections_of(budgets.month_view(db, "2026-09"))
    assert list(fixed) == ["income", "fixed", "flexible"] and "MEDICAL" in fixed["flexible"]
    budgets.set_framework(db, "needs")
    needs = sections_of(budgets.month_view(db, "2026-09"))
    assert list(needs) == ["income", "needs", "wants"]
    assert {"RENT_AND_UTILITIES", "MEDICAL", "TRANSPORTATION"} <= set(needs["needs"]) and "TRAVEL" in needs["wants"]
    assert sorted(needs["needs"] + needs["wants"]) == sorted(fixed["fixed"] + fixed["flexible"])   # the same lines
    budgets.move_line(db, "TRAVEL", "needs")                     # a need now; still Flexible in the other framework
    budgets.move_line(db, "MEDICAL", "fixed")                    # Fixed now; still a need
    assert "TRAVEL" in sections_of(budgets.month_view(db, "2026-09"))["needs"]
    budgets.set_framework(db, "fixed")
    back = sections_of(budgets.month_view(db, "2026-09"))
    assert "TRAVEL" in back["flexible"] and "MEDICAL" in back["fixed"]
    with pytest.raises(budgets.BudgetError):
        budgets.set_framework(db, "50/30/20")
    with pytest.raises(budgets.BudgetError):
        budgets.move_line(db, "INCOME", "needs")                 # income lines don't move


def test_totals_and_not_in_budget_follow_the_framework(db):
    budgets.set_framework(db, "needs")
    budgets.add_line(db, "needs", new_label="Fake Pet Care")     # added to Needs: Flexible in the other framework
    key = db.execute("SELECT key FROM categories WHERE label = 'Fake Pet Care'").fetchone()[0]
    assert tuple(db.execute("SELECT section, need_want FROM budget_lines WHERE category_key = ?", (key,)).fetchone()) == \
        ("flexible", "needs")
    budgets.set_budget(db, "MEDICAL", "2026-09", "200", rollover=False)
    budgets.set_budget(db, "TRAVEL", "2026-09", "300", rollover=False)
    view = budgets.month_view(db, "2026-09")
    assert view.planned_expense_cents == 50000                   # both spending sections count
    budgets.remove_line(db, "ENTERTAINMENT")
    spend(db, "2026-08", 4000, key="ENTERTAINMENT")              # a past month, not in budget
    plan = budgets.plan(db, budgets.year_columns("2026-08"), today=TODAY)
    wants = next(s for s in plan.sections if s.key == "wants")
    assert wants.outside[0] == 4000                              # Not in budget goes with Wants (the last section)


def test_the_settings_popup_switches_and_comes_back(client, db):
    """The framework choice and "Group lines without a budget" are in the main card's settings popup (user's requests
    2026-10-28), not the ribbon; a choice is saved and the page comes back to the same view."""
    page = client.get("/budgets?view=year&start=2026-08").text
    ribbon, main = page.split('<div class="tabbar-controls">')[1].split("</div>")[0], page.split("<main>")[1]
    assert "/budgets/framework" not in ribbon and "data-group-toggle" not in ribbon
    popup = main.split('<details class="budget-settings" data-popover>')[1].split("</details>")[0]
    assert 'value="fixed" data-autosubmit\n          checked' in popup and 'value="custom" data-autosubmit' in popup
    assert '<input type="checkbox" data-group-toggle> Group lines without a budget or actuals' in popup
    assert 'name="back" value="/budgets?view=year&amp;start=2026-08"' in popup
    assert 'name="first"' not in popup                                           # names only while Custom is on
    assert ">Fixed expenses<" in page
    response = client.post("/budgets/framework", data={"framework": "needs", "back": "/budgets?view=year&start=2026-08"},
                           headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/budgets?view=year&start=2026-08"
    page = client.get("/budgets?view=year&start=2026-08").text
    assert ">Needs<" in page and ">Wants<" in page and ">Fixed expenses<" not in page
    month = client.get("/budgets?month=2026-09").text
    assert '<option value="wants" selected>Wants</option>' in month and "Move to Needs" in month
    assert 'name="back" value="/budgets?month=2026-09"' in month and "Group lines without a budget</label>" in month
    outside = client.post("/budgets/framework", data={"framework": "fixed", "back": "https://example.com/"},
                          headers=ORIGIN, follow_redirects=False)
    assert outside.headers["location"] == "/budgets" and budgets.framework(db) == "fixed"   # never leaves Budgets


def test_custom_groups_are_named_and_added_on_the_page(client, db):
    """Custom (user's requests 2026-10-28): spending groups you add and name in their section headers; until moved, a
    line follows Fixed (first group) / Flexible (second); moving it there changes nothing in the other frameworks."""
    budgets.set_framework(db, "custom")
    view = sections_of(budgets.month_view(db, "2026-09"))
    assert list(view) == ["income", "custom1", "custom2"] and "RENT_AND_UTILITIES" in view["custom1"]
    assert [s.label for s in budgets.month_view(db, "2026-09").sections] == ["Income", "Group 1", "Group 2"]
    page = client.get("/budgets?month=2026-09").text
    assert 'name="first"' not in page                                            # no names in the popup any more
    assert page.count('class="group-name"') == 2 and 'value="Group 1" maxlength="40" required data-autosubmit' in page
    assert 'action="/budgets/groups"' in page and "+ Add group" in page
    # rename inline (Enter / leaving the box posts the form) and come back to the same month
    response = client.post("/budgets/groups/rename", data={"key": "custom1", "name": " Fake  Musts ",
                           "back": "/budgets?month=2026-09"}, headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/budgets?month=2026-09"
    client.post("/budgets/groups", data={"name": "Fake Fun", "back": "/budgets?month=2026-09"}, headers=ORIGIN)
    assert budgets.custom_groups(db) == [("custom1", "Fake Musts"), ("custom2", "Group 2"), ("custom3", "Fake Fun")]
    page = client.get("/budgets?month=2026-09").text
    assert 'value="Fake Musts"' in page and "Move to Fake Fun" in page and "Move to Group 2" in page
    assert 'aria-label="Remove the group Fake Fun"' in page                      # empty: removable
    assert 'aria-label="Remove the group Group 2"' not in page                   # has lines
    budgets.move_line(db, "TRAVEL", "custom3")
    assert sections_of(budgets.month_view(db, "2026-09"))["custom3"] == ["TRAVEL"]
    budgets.add_line(db, "custom3", new_label="Fake Pet Care")
    key = db.execute("SELECT key FROM categories WHERE label = 'Fake Pet Care'").fetchone()[0]
    assert tuple(db.execute("SELECT section, need_want, custom_group FROM budget_lines WHERE category_key = ?",
                            (key,)).fetchone()) == ("flexible", None, "custom3")
    plan = client.get("/budgets?view=year&start=2026-08").text
    assert plan.count('class="group-name"') == 3 and 'class="swatch slot-4"' in plan   # a 3rd group: slot 4
    assert 'class="swatch slot-3" aria-hidden="true"></span>Savings' in plan            # Savings keeps its colour
    budgets.set_framework(db, "fixed")
    assert "TRAVEL" in sections_of(budgets.month_view(db, "2026-09"))["flexible"]  # its other places are kept
    # refusals come back as a message; nothing changes
    same = client.post("/budgets/groups/rename", data={"key": "custom2", "name": "fake musts",
                       "back": "/budgets?month=2026-09"}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in same.headers["location"] and dict(budgets.custom_groups(db))["custom2"] == "Group 2"
    with pytest.raises(budgets.BudgetError):
        budgets.remove_group(db, "custom3")                                      # still has lines
    for n in range(3):
        budgets.add_group(db, f"Fake Extra {n}")
    with pytest.raises(budgets.BudgetError):
        budgets.add_group(db, "Fake Seventh")                                   # MAX_GROUPS
    budgets.remove_group(db, "custom6")
    again = budgets.add_group(db, "Fake Again")
    budgets.set_framework(db, "custom")
    assert sections_of(budgets.month_view(db, "2026-09"))[again] == []           # a new group starts empty


def test_a_removed_group_needs_room_and_lines_fall_back(db):
    budgets.set_framework(db, "custom")
    with pytest.raises(budgets.BudgetError):
        budgets.remove_group(db, "custom2")                                      # at least two groups
    third = budgets.add_group(db, "Fake Spare")
    budgets.remove_group(db, third)
    assert [k for k, _ in budgets.custom_groups(db)] == ["custom1", "custom2"]


def test_add_budget_is_on_the_year_and_decade_views_too(client, db):
    """"+ Add budget" in every view (user's request 2026-10-28): from the 1-year / 10-year views it posts a plain form
    and comes back to the same range; the Month view keeps its in-place (HTMX) form."""
    budgets.remove_line(db, "TRAVEL")
    for url, back in (("/budgets?view=year&start=2026-08", "/budgets?view=year&amp;start=2026-08"),
                      ("/budgets?view=decade&start=2025", "/budgets?view=decade&amp;start=2025")):
        page = client.get(url).text
        form = page.split("+ Add budget</summary>")[1].split("</form>")[0]
        assert 'method="post" action="/budgets/lines"' in form and f'name="back" value="{back}"' in form
        assert '<option value="TRAVEL">' in form                                   # not a line now: offered
    month = client.get("/budgets?month=2026-09").text.split("+ Add budget</summary>")[1].split("</form>")[0]
    assert 'hx-post="/budgets/lines"' in month and 'name="month" value="2026-09"' in month
    response = client.post("/budgets/lines", data={"section": "flexible", "category": "TRAVEL",
                           "back": "/budgets?view=year&start=2026-08"}, headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/budgets?view=year&start=2026-08"
    assert budgets.line_sections(db)["TRAVEL"] == "flexible"
    again = client.post("/budgets/lines", data={"section": "flexible", "category": "TRAVEL",
                        "back": "/budgets?view=year&start=2026-08"}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in again.headers["location"]                                  # already a line: a message


def test_add_budget_indents_subcategories_under_a_grayed_parent(client, db):
    """User's request 2026-11-02: the Add budget drop-down indents like the others; a parent already on the budget stays
    above its subcategories but can't be chosen."""
    sub = categories.add_subcategory(db, "TRAVEL", "Fake flights")
    box = client.get("/budgets").text.split('name="category"')[1].split("</select>")[0]
    assert '<option value="TRAVEL" disabled>Travel</option>' in box
    assert f'<option value="{sub}">' + "\u00a0" * 4 + "Fake flights</option>" in box
    assert box.index('value="TRAVEL"') < box.index(f'value="{sub}"')
