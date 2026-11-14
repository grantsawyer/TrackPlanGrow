"""Optional subcategories (split a Plaid category into its detailed codes) and budget lines at
either level. Fixed fake data; no Plaid calls."""

import pytest

from fintrack import budgets, categories, items, visuals
from fintrack.sync import _upsert_transaction

CHECKING = 1
RENT, POWER, PHONE = "RENT_AND_UTILITIES_RENT", "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY", "RENT_AND_UTILITIES_TELEPHONE"


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification)"
                 " VALUES (1, 1, 'acc-1', 'Fake Checking', 'depository', ?)", (items.classify("depository"),))
    conn.commit()
    return conn


_ids = iter(range(1, 100_000))


def add(conn, detailed, cents, day="2026-10-02", primary="RENT_AND_UTILITIES"):
    """Insert a transaction the way sync would (including creating a new subcategory)."""
    tid = next(_ids)
    key = categories.plaid_key(primary, detailed, categories.split_set(conn))
    if key == detailed:
        categories.ensure_subcategory(conn, primary, detailed)
    conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name,"
        " plaid_category_primary, plaid_category_detailed, plaid_category_key) VALUES (?, 1, ?, ?, 'FAKE', ?, ?, ?)",
        (f"s{tid}", day, cents, primary, detailed, key),
    )
    conn.commit()
    return conn.execute("SELECT id FROM transactions WHERE plaid_transaction_id = ?", (f"s{tid}",)).fetchone()[0]


def category_of(conn, transaction_id):
    return conn.execute("SELECT category FROM transactions_effective WHERE id = ?", (transaction_id,)).fetchone()[0]


def test_labels():
    assert categories.subcategory_label("RENT_AND_UTILITIES", POWER) == "Gas & electricity"
    assert categories.subcategory_label("RENT_AND_UTILITIES", RENT) == "Rent"


def test_split_moves_transactions_and_merge_moves_them_back(db):
    rent, power = add(db, RENT, 250000), add(db, POWER, 9000)
    assert category_of(db, rent) == "RENT_AND_UTILITIES"

    categories.set_split(db, "RENT_AND_UTILITIES", True)
    assert (category_of(db, rent), category_of(db, power)) == (RENT, POWER)
    subs = {c["key"]: c for c in categories.list_categories(db) if c["parent_key"] == "RENT_AND_UTILITIES"}
    assert set(subs) == {RENT, POWER} and subs[RENT]["label"] == "Rent" and subs[RENT]["kind"] == "expense"
    order = [c["key"] for c in categories.list_categories(db)]
    assert order.index("RENT_AND_UTILITIES") + 1 == order.index(POWER)        # subcategories follow the parent

    categories.set_split(db, "RENT_AND_UTILITIES", False)
    assert (category_of(db, rent), category_of(db, power)) == ("RENT_AND_UTILITIES", "RENT_AND_UTILITIES")
    assert not [c for c in categories.list_categories(db) if c["parent_key"]]  # unused subcategories removed


def test_merge_keeps_subcategories_you_use(db):
    rent = add(db, RENT, 250000)
    categories.set_split(db, "RENT_AND_UTILITIES", True)
    categories.set_transaction_category(db, rent, RENT)                        # your own choice
    categories.set_split(db, "RENT_AND_UTILITIES", False)
    assert categories.category_exists(db, RENT) and category_of(db, rent) == RENT


def test_sync_uses_subcategories_and_creates_new_ones(db):
    categories.set_split(db, "RENT_AND_UTILITIES", True)
    with db:
        _upsert_transaction(db, CHECKING, {
            "transaction_id": "p1", "date": "2026-10-03", "amount": 45.0, "name": "FAKE PHONE CO",
            "personal_finance_category": {"primary": "RENT_AND_UTILITIES", "detailed": PHONE}})
    row = db.execute("SELECT plaid_category_key FROM transactions WHERE plaid_transaction_id = 'p1'").fetchone()
    assert row[0] == PHONE and categories.category_exists(db, PHONE)           # created on first sight


def test_fixed_splits_and_unsplittable_categories(db):
    categories.set_split(db, "LOAN_PAYMENTS", True)
    assert categories.plaid_key("LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
                                categories.split_set(db)) == "CREDIT_CARD_PAYMENT"   # fixed split still wins
    for key in ("CREDIT_CARD_PAYMENT", "UNCATEGORIZED"):
        with pytest.raises(ValueError):
            categories.set_split(db, key, True)


# ---------------------------------------------------------------- budgets at either level

def test_split_category_lines_are_its_subcategories(db):
    categories.set_split(db, "RENT_AND_UTILITIES", True)
    add(db, RENT, 250000)
    add(db, POWER, 9000)                                   # first sight: joins the parent's section
    sections = budgets.line_sections(db)
    assert sections[RENT] == sections[POWER] == "fixed" and "RENT_AND_UTILITIES" not in sections
    budgets.set_budget(db, RENT, "2026-10", "2600", rollover=False)
    view = budgets.month_view(db, "2026-10")
    lines = {line.category: (line.label, line.spent_cents) for line in view.section("fixed").lines}
    assert lines[RENT] == ("Rent", 250000) and lines[POWER] == ("Gas & electricity", 9000)
    with pytest.raises(budgets.BudgetError, match="split"):
        budgets.add_line(db, "flexible", "RENT_AND_UTILITIES")

    # Spending still filed under the split category itself stays in its section, unbudgeted.
    add(db, "FAKE_UNMAPPED_CODE", 5000)                        # Plaid code outside the subcategories
    view = budgets.month_view(db, "2026-10")
    fixed = view.section("fixed")
    assert [(e.category, e.label, e.cents) for e in fixed.extras] == [("RENT_AND_UTILITIES", "Rent & utilities", 5000)]
    assert view.outside_of("expense") == [] and fixed.spent_cents == 250000 + 9000 + 5000
    year = budgets.plan(db, budgets.year_columns("2026-01"), today=__import__("datetime").date(2026, 11, 1))
    rows = {line.category: line.values[9] for s in year.sections if s.key == "fixed" for line in s.lines}
    assert rows["RENT_AND_UTILITIES"] == 5000 and year.sections[2].outside is None

    # A subcategory taken off the budget shows under "Not in budget" (its parent isn't a line).
    budgets.remove_line(db, POWER)
    view = budgets.month_view(db, "2026-10")
    assert [(o.label, o.cents) for o in view.outside_of("expense")] == [("Gas & electricity", 9000)]

    # Merging back makes the parent a line again, covering its former subcategories.
    categories.set_split(db, "RENT_AND_UTILITIES", False)
    assert budgets.line_sections(db)["RENT_AND_UTILITIES"] == "fixed"


def test_overview_bars_add_other_for_unbudgeted_spending(db):
    add(db, POWER, 9000)
    add(db, "FOOD_AND_DRINK_GROCERIES", 4500, primary="FOOD_AND_DRINK")        # no budget
    budgets.set_budget(db, "RENT_AND_UTILITIES", "2026-10", "500", rollover=False)
    bars = visuals.budget_bars(budgets.month_view(db, "2026-10"), with_other=True)
    assert [b["label"] for b in bars] == ["Rent & utilities", "Other"]
    assert bars[-1] == {"label": "Other", "spent": 45.0, "budget": None, "status": "other"}
    assert visuals.budget_bars(budgets.month_view(db, "2026-10"))[-1]["label"] != "Other"   # opt-in only

