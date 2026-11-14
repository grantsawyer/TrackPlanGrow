"""Budgets for tags (user's requests 2026-11-02): a total and/or totals by category on the tag's card; on the Budgets page,
any amount allocated to any month on its lines, and its spending counted there instead of on its categories' lines.
All data is made up."""

import pytest

from fintrack import budgets, categories, tags

ORIGIN = {"origin": "https://localhost:8443"}
MONTH = "2026-09"


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification)"
                 " VALUES (1, 1, 'a1', 'Fake checking', 'depository', 'asset')")
    conn.commit()
    return conn


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.web.app import create_app

    with TestClient(create_app(settings, gateway_factory=lambda: gateway, cipher_factory=lambda: cipher),
                    base_url="https://localhost:8443") as c:
        yield c


_ids = iter(range(1, 10_000))


def spend(conn, cents, key, month=MONTH):
    tid = conn.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key)"
                       " VALUES (?, 1, ?, ?, 'FAKE SHOP', ?)", (f"t{next(_ids)}", f"{month}-10", cents, key)).lastrowid
    conn.commit()
    return tid


def tagged_line(conn, name="Fake trip", section="flexible"):
    tag = tags.create(conn, name)
    budgets.add_line(conn, section, budgets.tag_line_key(tag.id))
    return tag


def line(view, key):
    return next((l for s in view.sections for l in s.lines if l.category == key), None)


def test_a_tag_on_the_budget_supersedes_the_category(db):
    spend(db, 1000, "TRAVEL")
    tagged = spend(db, 2500, "TRAVEL")
    before = budgets.month_view(db, MONTH)
    assert line(before, "TRAVEL").spent_cents == 3500
    tag = tags.create(db, "Fake trip")
    tags.add(db, [tagged], "Fake trip")
    # not on the budget yet: nothing changes, or the spending would show nowhere
    assert line(budgets.month_view(db, MONTH), "TRAVEL").spent_cents == 3500
    budgets.add_line(db, "flexible", budgets.tag_line_key(tag.id))
    view = budgets.month_view(db, MONTH)
    assert line(view, "TRAVEL").spent_cents == 1000
    assert line(view, budgets.tag_line_key(tag.id)).spent_cents == 2500
    assert view.actual_expense_cents == before.actual_expense_cents == 3500     # still counted once
    budgets.remove_line(db, budgets.tag_line_key(tag.id))
    assert line(budgets.month_view(db, MONTH), "TRAVEL").spent_cents == 3500


def test_category_budget_gets_its_categorys_spending_and_its_subcategories(db):
    tag = tagged_line(db)
    sub = categories.add_subcategory(db, "FOOD_AND_DRINK", "Fake restaurants")
    tags.set_category_budget(db, tag.id, "FOOD_AND_DRINK", "500")
    budgets.add_line(db, "flexible", budgets.tag_line_key(tag.id, "FOOD_AND_DRINK"))   # added on its own (2026-11-04)
    food, restaurant, other = spend(db, 100, "FOOD_AND_DRINK"), spend(db, 200, sub), spend(db, 400, "TRAVEL")
    tags.add(db, [food, restaurant, other], "Fake trip")
    view = budgets.month_view(db, MONTH)
    assert line(view, budgets.tag_line_key(tag.id, "FOOD_AND_DRINK")).spent_cents == 300
    assert line(view, budgets.tag_line_key(tag.id)).spent_cents == 400
    assert view.actual_expense_cents == 700


def test_allocation_applies_to_that_month_only_and_is_your_amount(db):
    tag = tagged_line(db)
    tags.set_budget(db, tag.id, "1000")
    key = budgets.tag_line_key(tag.id)
    budgets.set_tag_allocation(db, key, MONTH, "700")
    view = budgets.month_view(db, MONTH)
    assert line(view, key).budget_cents == 70000 and view.planned_expense_cents >= 70000
    assert line(view, key).tag.allocated_cents == 70000
    assert line(budgets.month_view(db, "2026-10"), key).budget_cents is None      # not carried, not spread
    # more than the total is your choice; blank clears it
    budgets.set_tag_allocation(db, key, "2026-10", "900")
    assert line(budgets.month_view(db, "2026-10"), key).tag.allocated_cents == 160000
    budgets.set_tag_allocation(db, key, "2026-10", "")
    assert line(budgets.month_view(db, "2026-10"), key).budget_cents is None


def test_a_fully_allocated_tag_leaves_months_with_nothing_on_it(db):
    tag = tagged_line(db)
    key = budgets.tag_line_key(tag.id)
    assert line(budgets.month_view(db, "2026-10"), key)                           # no total yet: there to set up
    tags.set_budget(db, tag.id, "100")
    budgets.set_tag_allocation(db, key, MONTH, "100")
    assert line(budgets.month_view(db, "2026-10"), key) is None
    assert line(budgets.month_view(db, MONTH), key)


def test_total_is_yours_else_the_sum_of_category_budgets(db):
    tag = tags.create(db, "Fake trip")
    tags.set_category_budget(db, tag.id, "TRAVEL", "300")
    tags.set_category_budget(db, tag.id, "FOOD_AND_DRINK", "200")
    summary = next(s for s in tags.summaries(db) if s.tag.id == tag.id)
    assert summary.budget_total_cents == 50000
    tags.set_budget(db, tag.id, "650")                 # yours, with the category budgets part of it (2026-11-04)
    summary = next(s for s in tags.summaries(db) if s.tag.id == tag.id)
    assert summary.budget_total_cents == 65000 and summary.other_budget_cents == 15000
    with pytest.raises(tags.TagError, match="can't be less"):
        tags.set_budget(db, tag.id, "450")
    tags.set_budget(db, tag.id, "")
    tags.set_category_budget(db, tag.id, "TRAVEL", "")
    tags.set_category_budget(db, tag.id, "FOOD_AND_DRINK", "")
    assert next(s for s in tags.summaries(db) if s.tag.id == tag.id).budget is None


def test_card_shows_spent_against_budget(db):
    tag = tags.create(db, "Fake trip")
    tags.set_budget(db, tag.id, "10")
    tags.add(db, [spend(db, 1500, "TRAVEL")], "Fake trip")
    tags.set_category_budget(db, tag.id, "TRAVEL", "5")                # within the total (2026-11-04)
    summary = next(s for s in tags.summaries(db) if s.tag.id == tag.id)
    assert summary.budget["over"] and summary.budget["left"] == -500 and summary.budget["share"] == 1
    assert summary.category_budgets[0].spent_cents == 1500


def test_two_tags_on_the_budget_count_a_transaction_once(db):
    first, second = tagged_line(db, "Fake one"), tagged_line(db, "Fake two")
    tid = spend(db, 800, "TRAVEL")
    tags.add(db, [tid], "Fake one")
    tags.add(db, [tid], "Fake two")
    view = budgets.month_view(db, MONTH)
    assert line(view, budgets.tag_line_key(first.id)).spent_cents == 800
    assert line(view, budgets.tag_line_key(second.id)).spent_cents == 0
    assert view.actual_expense_cents == 800


def test_plan_past_is_actual_ahead_is_your_allocation(db):
    tag = tagged_line(db)
    key = budgets.tag_line_key(tag.id)
    tags.add(db, [spend(db, 600, "TRAVEL", "2026-09")], "Fake trip")
    budgets.set_tag_allocation(db, key, "2026-11", "50")
    from datetime import date
    plan = budgets.plan(db, budgets.year_columns("2026-09"), today=date(2026, 10, 15))
    row = next(l for s in plan.sections for l in s.lines if l.category == key)
    assert row.values[:3] == [600, 0, 5000] and row.tag and not row.editable
    assert plan.expense[0] == 600                                                  # once, not twice


def test_move_and_remove_and_deleting_the_tag_or_category(db):
    tag = tagged_line(db)
    key = budgets.tag_line_key(tag.id)
    budgets.move_line(db, key, "fixed")
    assert budgets.tag_lines(db)[key].section == "fixed"
    tags.set_category_budget(db, tag.id, "TRAVEL", "20")
    budgets.add_line(db, "flexible", budgets.tag_line_key(tag.id, "TRAVEL"))
    budgets.set_tag_allocation(db, budgets.tag_line_key(tag.id, "TRAVEL"), MONTH, "20")
    categories.delete_category(db, "TRAVEL", "UNCATEGORIZED")                      # a budget doesn't block it
    assert budgets.tag_line_key(tag.id, "TRAVEL") not in budgets.tag_lines(db)
    tags.delete(db, tag.id)
    assert budgets.tag_lines(db) == {}
    assert db.execute("SELECT COUNT(*) FROM tag_budget_allocations").fetchone()[0] == 0


def test_a_group_with_a_tag_line_cant_be_removed(db):
    group = budgets.add_group(db, "Fake group")
    tag = tags.create(db, "Fake trip")
    budgets.add_line(db, group, budgets.tag_line_key(tag.id))
    with pytest.raises(budgets.BudgetError):
        budgets.remove_group(db, group)


def test_tags_dont_go_in_income_or_twice(db):
    tag = tags.create(db, "Fake trip")
    with pytest.raises(budgets.BudgetError):
        budgets.add_line(db, "income", budgets.tag_line_key(tag.id))
    budgets.add_line(db, "flexible", budgets.tag_line_key(tag.id))
    with pytest.raises(budgets.BudgetError):
        budgets.add_line(db, "fixed", budgets.tag_line_key(tag.id))


def test_projection_still_reads_categories(db):
    tagged_line(db)
    tags.add(db, [spend(db, 900, "TRAVEL")], "Fake trip")
    assert budgets.actuals_by_month(db, MONTH, MONTH)[(MONTH, "TRAVEL")] == 900


def test_pages_and_posts(client, db):
    tag = tags.create(db, "Fake trip")
    page = client.get("/budgets?month=" + MONTH).text
    assert f'<option value="tag:{tag.id}">Fake trip</option>' in page              # offered as a line
    client.post("/budgets/lines", data={"section": "flexible", "category": f"tag:{tag.id}", "month": MONTH}, headers=ORIGIN)
    client.post(f"/tags/{tag.id}/budget", data={"amount": "$1,000"}, headers=ORIGIN)
    client.post(f"/tags/{tag.id}/category-budget", data={"category": "TRAVEL", "amount": "400"}, headers=ORIGIN)
    page = client.get("/budgets?month=" + MONTH).text                              # offered under the tag (2026-11-04)
    assert f'<option value="tag:{tag.id}" disabled>Fake trip</option><option value="tag:{tag.id}:TRAVEL">\u00a0\u00a0\u00a0\u00a0Travel</option>' in page
    client.post("/budgets/lines", data={"section": "flexible", "category": f"tag:{tag.id}:TRAVEL", "month": MONTH}, headers=ORIGIN)
    client.post("/budgets/amount", data={"category": f"tag:{tag.id}:TRAVEL", "month": MONTH, "amount": "150", "scope": ""}, headers=ORIGIN)
    assert budgets.tag_lines(db)[f"tag:{tag.id}:TRAVEL"].amounts == {MONTH: 15000}
    page = client.get("/budgets?month=" + MONTH).text
    assert f'data-direct-form="amount-save" data-category="tag:{tag.id}:TRAVEL"' in page
    assert f'data-scope-form="amount-save" data-category="tag:{tag.id}' not in page
    assert "Fake trip › Travel" in page and "$850 left to allocate" in page
    assert f'/transactions?month={MONTH}&amp;tag={tag.id}' in page or f'/transactions?month={MONTH}&tag={tag.id}' in page
    assert f'<option value="tag:{tag.id}">' not in page and f'value="tag:{tag.id}:TRAVEL"' not in page   # both on it
    year = client.get("/budgets?view=year&start=2030-01").text
    assert f'data-direct-form="plan-save" data-category="tag:{tag.id}"' in year
    client.post("/budgets/plan-amount", data={"category": f"tag:{tag.id}", "month": "2030-02", "amount": "75", "start": "2030-01"}, headers=ORIGIN)
    assert budgets.tag_lines(db)[f"tag:{tag.id}"].amounts == {"2030-02": 7500}
    assert client.get("/budgets?view=decade&start=2026").status_code == 200
    card = client.get("/tags").text
    assert "<th>Total" in card and "$1,000" in card and 'value="$1,000.00"' not in card     # read, not boxes
    card = client.get(f"/tags?edit={tag.id}").text
    assert 'value="$1,000.00"' in card                                                         # boxes in edit mode
    bad = client.post(f"/tags/{tag.id}/budget", data={"amount": "lots"}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in bad.headers["location"]


def test_card_removes_a_category_budget(client, db):
    """User's request 2026-11-02: a trash can on the row; what was allocated goes, and the spending returns to the tag's line."""
    tag = tagged_line(db)
    tags.set_category_budget(db, tag.id, "TRAVEL", "400")
    key = budgets.tag_line_key(tag.id, "TRAVEL")
    budgets.add_line(db, "flexible", key)
    budgets.set_tag_allocation(db, key, MONTH, "150")
    tags.add(db, [spend(db, 900, "TRAVEL")], "Fake trip")
    assert line(budgets.month_view(db, MONTH), key).spent_cents == 900
    assert 'aria-label="Remove the Travel budget from Fake trip"' in client.get(f"/tags?edit={tag.id}").text   # edit mode
    client.post(f"/tags/{tag.id}/category-budget", data={"category": "TRAVEL", "amount": ""}, headers=ORIGIN)
    assert 'aria-label="Remove the Travel budget from Fake trip"' not in client.get(f"/tags?edit={tag.id}").text
    view = budgets.month_view(db, MONTH)
    assert line(view, key) is None and line(view, budgets.tag_line_key(tag.id)).spent_cents == 900
    assert db.execute("SELECT COUNT(*) FROM tag_budget_allocations").fetchone()[0] == 0


# ---------------------------------------------------------------- a tag's category lines are yours to add (2026-11-04)

def test_a_category_budget_adds_no_line_by_itself_so_the_tag_line_gets_it(db):
    tag = tagged_line(db)
    tags.set_category_budget(db, tag.id, "TRAVEL", "400")
    tags.add(db, [spend(db, 900, "TRAVEL"), spend(db, 100, "FOOD_AND_DRINK")], "Fake trip")
    view = budgets.month_view(db, MONTH)
    assert line(view, budgets.tag_line_key(tag.id, "TRAVEL")) is None
    assert line(view, budgets.tag_line_key(tag.id)).spent_cents == 1000             # the whole trip


def test_only_a_category_line_on_the_budget_leaves_the_rest_in_its_categories(db):
    tag = tags.create(db, "Fake trip")
    tags.set_category_budget(db, tag.id, "TRAVEL", "400")
    budgets.add_line(db, "flexible", budgets.tag_line_key(tag.id, "TRAVEL"))
    tags.add(db, [spend(db, 900, "TRAVEL"), spend(db, 100, "FOOD_AND_DRINK")], "Fake trip")
    view = budgets.month_view(db, MONTH)
    assert line(view, budgets.tag_line_key(tag.id, "TRAVEL")).spent_cents == 900
    assert line(view, budgets.tag_line_key(tag.id)) is None and view.actual_expense_cents == 1000
    with pytest.raises(budgets.BudgetError, match="no budget for that category"):
        budgets.add_line(db, "flexible", budgets.tag_line_key(tag.id, "FOOD_AND_DRINK"))
