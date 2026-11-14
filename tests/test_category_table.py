"""The Categories page as a table of categories and subcategories (categories.py, migration 0034; user's request
2026-10-13): add, rename, alphabetical order (2026-10-23), move a subcategory to another category, delete (transactions move to a category you
choose; Plaid's future transactions for a deleted category follow). All data is made up."""

import re
from datetime import date

import pytest

from fintrack import categories, items, rules, sync

ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    items.upsert_accounts(conn, 1, [{"account_id": "acc-1", "name": "Fake checking", "mask": "0001", "type": "depository",
                                     "subtype": "checking", "balances": {"current": 100.0}}])
    conn.commit()
    return conn


def plaid_txn(conn, tid, primary, detailed, amount=12.5, name="FAKE SHOP"):
    """One Plaid transaction through the real sync path (categories mapped as at every sync)."""
    account_id = conn.execute("SELECT id FROM accounts").fetchone()[0]
    sync._upsert_transaction(conn, account_id, {
        "transaction_id": tid, "amount": amount, "date": date(2026, 9, 10), "name": name, "pending": False,
        "personal_finance_category": {"primary": primary, "detailed": detailed}})
    conn.commit()
    return conn.execute("SELECT id FROM transactions WHERE plaid_transaction_id = ?", (tid,)).fetchone()[0]


def category_of(conn, transaction_id):
    return conn.execute("SELECT category FROM transactions_effective WHERE id = ?", (transaction_id,)).fetchone()[0]


def test_add_rename_and_alphabetical_order(db):
    sub = categories.add_subcategory(db, "FOOD_AND_DRINK", "Fake coffee")
    row = db.execute("SELECT * FROM categories WHERE key = ?", (sub,)).fetchone()
    assert (row["parent_key"], row["kind"], row["label"]) == ("FOOD_AND_DRINK", "expense", "Fake coffee")
    with pytest.raises(ValueError):
        categories.add_subcategory(db, "FOOD_AND_DRINK", "fake COFFEE")                 # no duplicates there
    with pytest.raises(ValueError):
        categories.add_subcategory(db, sub, "Nested")                                     # two levels only
    categories.rename_category(db, sub, "Fake espresso")
    assert db.execute("SELECT label FROM categories WHERE key = ?", (sub,)).fetchone()[0] == "Fake espresso"
    # alphabetical, case-insensitive: categories, and each one's subcategories right after it (user's request 2026-10-23)
    categories.add_subcategory(db, "FOOD_AND_DRINK", "apples")
    rows = categories.list_categories(db)
    # grouped by what they count as first: spending, not counted, income (user's request 2026-10-25)
    tops = [(categories.KIND_ORDER.index(c["kind"]), c["label"].lower()) for c in rows if not c["parent_key"]]
    assert tops == sorted(tops) and {k for k, _ in tops} == {0, 1, 2}
    food = [c["label"] for c in rows if c["parent_key"] == "FOOD_AND_DRINK"]
    assert food == sorted(food, key=str.lower) and food[0] == "apples"
    i = [c["key"] for c in rows].index("FOOD_AND_DRINK")
    assert [c["parent_key"] for c in rows[i + 1:i + 1 + len(food)]] == ["FOOD_AND_DRINK"] * len(food)


def test_moving_a_subcategory_takes_the_new_categorys_kind(db):
    sub = categories.add_subcategory(db, "FOOD_AND_DRINK", "Fake refunds")
    categories.move_to(db, sub, "INCOME")
    row = db.execute("SELECT parent_key, kind FROM categories WHERE key = ?", (sub,)).fetchone()
    assert tuple(row) == ("INCOME", "income")
    categories.set_kind(db, "INCOME", "transfer")                                       # subcategories follow
    assert db.execute("SELECT kind FROM categories WHERE key = ?", (sub,)).fetchone()[0] == "transfer"


def test_deleting_moves_transactions_rules_and_future_syncs(db):
    t1 = plaid_txn(db, "t1", "TRAVEL", "TRAVEL_FLIGHTS")
    t2 = plaid_txn(db, "t2", "TRAVEL", "TRAVEL_LODGING")
    db.execute("UPDATE transactions SET user_category = 'TRAVEL' WHERE id = ?", (t2,))
    rules.add_simple(db, "FAKE AIRLINE", "TRAVEL")
    db.execute("INSERT INTO budgets (category_key, start_month, amount_cents) VALUES ('TRAVEL', '2026-09', 50000)")
    db.commit()
    moved = categories.delete_category(db, "TRAVEL", "ENTERTAINMENT")
    assert moved == 2 and category_of(db, t1) == category_of(db, t2) == "ENTERTAINMENT"
    assert db.execute("SELECT value FROM rule_actions").fetchone()[0] == "ENTERTAINMENT"
    assert db.execute("SELECT COUNT(*) FROM budgets WHERE category_key = 'TRAVEL'").fetchone()[0] == 0
    assert not categories.category_exists(db, "TRAVEL")
    # Plaid keeps sending TRAVEL: it lands in Entertainment, and Travel isn't re-created
    t3 = plaid_txn(db, "t3", "TRAVEL", "TRAVEL_FLIGHTS", name="FAKE TRAIN")
    assert category_of(db, t3) == "ENTERTAINMENT" and not categories.category_exists(db, "TRAVEL")


def test_deleting_a_plaid_subcategory_isnt_re_created(db):
    categories.set_split(db, "RENT_AND_UTILITIES", True)
    t1 = plaid_txn(db, "r1", "RENT_AND_UTILITIES", "RENT_AND_UTILITIES_RENT")
    assert category_of(db, t1) == "RENT_AND_UTILITIES_RENT"
    categories.delete_category(db, "RENT_AND_UTILITIES_RENT", "RENT_AND_UTILITIES")
    t2 = plaid_txn(db, "r2", "RENT_AND_UTILITIES", "RENT_AND_UTILITIES_RENT")
    assert category_of(db, t1) == category_of(db, t2) == "RENT_AND_UTILITIES"
    assert not categories.category_exists(db, "RENT_AND_UTILITIES_RENT")
    categories.set_split(db, "RENT_AND_UTILITIES", False)
    categories.set_split(db, "RENT_AND_UTILITIES", True)                               # splitting again: still gone
    assert not categories.category_exists(db, "RENT_AND_UTILITIES_RENT")


def test_deleting_a_category_takes_its_subcategories_and_refuses_protected_ones(db):
    sub = categories.add_subcategory(db, "TRAVEL", "Fake trips")
    t1 = plaid_txn(db, "x1", "TRAVEL", "TRAVEL_FLIGHTS")
    db.execute("UPDATE transactions SET user_category = ? WHERE id = ?", (sub, t1))
    db.commit()
    with pytest.raises(ValueError):
        categories.delete_category(db, "TRAVEL", sub)                                   # not into itself
    categories.delete_category(db, "TRAVEL", "OTHER")
    assert not categories.category_exists(db, sub) and category_of(db, t1) == "OTHER"
    for key in categories.PROTECTED:
        with pytest.raises(ValueError):
            categories.delete_category(db, key, "OTHER")
    # a new category with a deleted one's name gets a fresh key (Plaid's TRAVEL keeps going to Other)
    new = categories.add_category(db, "Travel", "expense")
    assert new != "TRAVEL" and category_of(db, plaid_txn(db, "x2", "TRAVEL", "TRAVEL_FLIGHTS")) == "OTHER"


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_the_categories_page(client, db):
    plaid_txn(db, "p1", "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE")
    page = client.get("/categories").text
    assert 'value="Food &amp; drink"' in page and "1 transaction" in page
    assert page.count("Built in") == len(categories.PROTECTED)
    # a section per group, in order (user's request 2026-10-25)
    heads = [page.index(f'<h2>{h}</h2>') for h in ("Spending", "Transfers", "Income")]
    assert heads == sorted(heads)
    client.post("/categories/FOOD_AND_DRINK/add", data={"label": "Fake coffee"}, headers=ORIGIN)
    sub = db.execute("SELECT key FROM categories WHERE label = 'Fake coffee'").fetchone()[0]
    r = client.post(f"/categories/{sub}/delete", data={"target": "FOOD_AND_DRINK"}, headers=ORIGIN, follow_redirects=False)
    assert "Deleted" in r.headers["location"] and not categories.category_exists(db, sub)
    bad = client.post("/categories/UNCATEGORIZED/delete", data={"target": "OTHER"}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in bad.headers["location"]


def test_categories_become_subcategories_and_back(db):
    t1 = plaid_txn(db, "m1", "TRAVEL", "TRAVEL_FLIGHTS")
    categories.move_to(db, "TRAVEL", "ENTERTAINMENT")                                  # a category into another
    row = db.execute("SELECT parent_key, kind FROM categories WHERE key = 'TRAVEL'").fetchone()
    assert tuple(row) == ("ENTERTAINMENT", "expense") and category_of(db, t1) == "TRAVEL"   # transactions stay
    categories.move_to(db, "TRAVEL", None)                                             # and back to the top
    assert db.execute("SELECT parent_key FROM categories WHERE key = 'TRAVEL'").fetchone()[0] is None
    # a category with subcategories can't go under another (two levels only)
    categories.add_subcategory(db, "TRAVEL", "Fake trips")
    with pytest.raises(ValueError):
        categories.move_to(db, "TRAVEL", "ENTERTAINMENT")
    # a category using Plaid's subcategories stops when it becomes one (no third level)
    categories.set_split(db, "MEDICAL", True)
    categories.move_to(db, "MEDICAL", "PERSONAL_CARE")
    assert db.execute("SELECT is_split FROM categories WHERE key = 'MEDICAL'").fetchone()[0] == 0
    t2 = plaid_txn(db, "m2", "MEDICAL", "MEDICAL_PHARMACIES_AND_SUPPLEMENTS")
    assert category_of(db, t2) == "MEDICAL"                                           # not a new sub-subcategory
    # a subcategory becomes a category
    sub = db.execute("SELECT key FROM categories WHERE label = 'Fake trips'").fetchone()[0]
    categories.move_to(db, sub, "")
    assert db.execute("SELECT parent_key FROM categories WHERE key = ?", (sub,)).fetchone()[0] is None


def test_counts_link_to_their_transactions(client, db):
    """A count on Categories opens those transactions on Transactions, all dates; a category's include its
    subcategories', as its count does (user's request 2026-10-25)."""
    plaid_txn(db, "p1", "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE", name="FAKE PARENT SHOP")
    sub = categories.add_subcategory(db, "FOOD_AND_DRINK", "Fake coffee")
    categories.set_transaction_category(db, plaid_txn(db, "p2", "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE",
                                                      name="FAKE SUB SHOP"), sub)
    page = client.get("/categories").text
    assert 'href="/transactions?range=custom&amp;category=FOOD_AND_DRINK"' in page and f"category={sub}\"" in page
    parent = client.get("/transactions", params={"range": "custom", "category": "FOOD_AND_DRINK"}).text
    assert "FAKE PARENT SHOP" in parent and "FAKE SUB SHOP" in parent
    child = client.get("/transactions", params={"range": "custom", "category": sub}).text
    the_list = child.split('<table class="data txns">')[1]                  # (the review card above it ignores filters)
    assert "FAKE SUB SHOP" in the_list and "FAKE PARENT SHOP" not in the_list


def test_plaid_other_is_uncategorized_for_new_transactions_only(db):
    """Plaid's OTHER (couldn't classify it) lands in Uncategorized from now on; a transaction already stored as Other
    stays Other when Plaid sends it again (user's rule 2026-10-25)."""
    assert category_of(db, plaid_txn(db, "n1", "OTHER", "OTHER_OTHER")) == "UNCATEGORIZED"
    old = plaid_txn(db, "o1", "OTHER", "OTHER_OTHER")
    db.execute("UPDATE transactions SET plaid_category_key = 'OTHER' WHERE id = ?", (old,))     # stored before the rule
    assert plaid_txn(db, "o1", "OTHER", "OTHER_OTHER", amount=13.0) == old and category_of(db, old) == "OTHER"
    plaid_txn(db, "o1", "SHOPPING", "SHOPPING_OTHER")                                      # reclassified by Plaid
    assert category_of(db, old) == "SHOPPING"


def test_category_dropdowns_have_a_heading_per_bucket(conn):
    """User's request 2026-11-01: Spending, Transfers and Income headings; a subcategory with its parent."""
    from fintrack import categories
    from fintrack.web.deps import by_kind
    sub = categories.add_subcategory(conn, "FOOD_AND_DRINK", "Fake Sub")
    groups = by_kind(categories.list_categories(conn))
    assert [heading for heading, _ in groups] == ["Spending", "Transfers", "Income"]
    spending = [c["key"] for c in groups[0][1]]
    assert sub in spending and "FOOD_AND_DRINK" in spending
    assert all(c["group_kind"] == "income" for c in groups[2][1])


def test_move_to_lists_groups_with_their_categories_and_moves_between_them(client, db):
    """User's request 2026-11-02: replaces Counts as; headers for the groups, categories under them."""
    sub = categories.add_subcategory(db, "FOOD_AND_DRINK", "Fake coffee")
    page = client.get("/categories").text
    assert "Counts as" not in page and "Move into" not in page and "Make it a category" not in page
    food_row = page.split('action="/categories/TRAVEL/move"')[0].rsplit('class="ar-row', 1)[1]
    assert 'class="ar-count"' in food_row                                  # the count comes before Move to (2026-11-02)
    travel = page.split('action="/categories/TRAVEL/move"')[1].split("</form>")[0]
    # an icon opening a menu (2026-11-02): groups are choices, not headers; their categories follow, indented
    assert 'aria-label="Move Travel"' in travel and "<select" not in travel and "<optgroup" not in travel
    def option(html, value):
        return re.search(rf'<button type="submit" name="parent" value="{value}" class="([^"]*)" ?(disabled)?>([^<]*)<', html)
    assert option(travel, "group:income").group(1, 2, 3) == ("move-option move-group", None, "Income")
    assert option(travel, "group:expense").group(2) == "disabled"            # where it is now
    assert option(travel, "FOOD_AND_DRINK").group(1, 2) == ("move-option move-child", None)
    assert option(travel, "TRAVEL").group(2) == "disabled"                   # itself
    assert travel.index('value="group:expense"') < travel.index('value="FOOD_AND_DRINK"') < travel.index('value="group:transfer"')
    # a subcategory: its category grayed out, its own group choosable
    row = page.split(f'action="/categories/{sub}/move"')[1].split("</form>")[0]
    assert option(row, "FOOD_AND_DRINK").group(2) == "disabled" and option(row, "group:expense").group(2) is None
    # a category with subcategories can only be a category: every category grayed out, but shown
    parent = page.split('action="/categories/FOOD_AND_DRINK/move"')[1].split("</form>")[0]
    assert option(parent, "TRAVEL").group(2) == "disabled" and option(parent, "group:income").group(2) is None

    def kind_and_parent(key):
        return tuple(db.execute("SELECT kind, parent_key FROM categories WHERE key = ?", (key,)).fetchone())
    move = lambda key, to: client.post(f"/categories/{key}/move", data={"parent": to}, headers=ORIGIN, follow_redirects=False)  # noqa: E731
    assert "msg=Moved" in move("TRAVEL", "group:income").headers["location"]
    assert kind_and_parent("TRAVEL") == ("income", None)
    move(sub, "group:transfer")                                             # a subcategory: its own category, in another group
    assert kind_and_parent(sub) == ("transfer", None)
    move(sub, "TRAVEL")                                                     # into a category: takes its group
    assert kind_and_parent(sub) == ("income", "TRAVEL")
    move("FOOD_AND_DRINK", "group:transfer")
    assert kind_and_parent("FOOD_AND_DRINK") == ("transfer", None)
    move("TRAVEL", "group:expense")                                         # its subcategory follows
    assert kind_and_parent(sub) == ("expense", "TRAVEL")
    assert "error=" in move("TRAVEL", "FOOD_AND_DRINK").headers["location"]  # has a subcategory: two levels only
    assert "error=" in move("TRAVEL", "group:nonsense").headers["location"] and kind_and_parent("TRAVEL") == ("expense", None)


def test_plaid_subcategories_are_added_one_at_a_time(client, db):
    """User's request 2026-11-02: the + panel lists the ones on your transactions; adding one keeps the panel open."""
    from fintrack import rules
    coffee = plaid_txn(db, "c1", "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE", name="FAKE COFFEE")
    mine = plaid_txn(db, "c2", "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE", name="FAKE MINE")
    ruled = plaid_txn(db, "c3", "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE", name="FAKE RULED")
    plaid_txn(db, "g1", "FOOD_AND_DRINK", "FOOD_AND_DRINK_GROCERIES", name="FAKE GROCER")
    categories.set_transaction_category(db, mine, "TRAVEL")
    rules.add_simple(db, "ruled", "BANK_FEES")
    page = client.get("/categories").text
    assert "Use Plaid" not in page
    card = page.split('id="card-FOOD_AND_DRINK"')[1].split("</section>")[0]
    assert "From Plaid" in card and "Coffee" in card and "Groceries" in card
    assert 'id="add-sub-FOOD_AND_DRINK" hidden' in card                      # closed until +
    assert 'data-panel-row="add-sub-FOOD_AND_DRINK"' in page and "Cancel" not in card.split('id="add-sub-FOOD_AND_DRINK"')[1].split("</form>")[0]
    assert card.count('class="secondary add-button"') == 3                    # the name's Add and the two Plaid options: identical
    assert card.index("Coffee") < card.index("Groceries")                    # most used first
    done = client.post("/categories/FOOD_AND_DRINK/plaid-sub", data={"code": "FOOD_AND_DRINK_COFFEE"}, headers={**ORIGIN, "HX-Request": "true"})
    after = done.text.split('id="card-FOOD_AND_DRINK"')[1].split("</section>")[0]
    assert 'id="add-sub-FOOD_AND_DRINK" >' in after and 'aria-expanded="true"' in after    # still open
    assert 'hx-select="#card-FOOD_AND_DRINK"' in after and "Add Groceries" in after and "Add Coffee" not in after
    assert 'value="Coffee"' in after                                         # now a real subcategory
    assert category_of(db, coffee) == "FOOD_AND_DRINK_COFFEE"
    assert category_of(db, mine) == "TRAVEL" and category_of(db, ruled) == "BANK_FEES"   # yours and rules' stay
    # later syncs follow; other Plaid subcategories of that category don't
    assert category_of(db, plaid_txn(db, "c4", "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE")) == "FOOD_AND_DRINK_COFFEE"
    assert category_of(db, plaid_txn(db, "g2", "FOOD_AND_DRINK", "FOOD_AND_DRINK_GROCERIES")) == "FOOD_AND_DRINK"
    # not offered twice; nonsense does nothing
    client.post("/categories/FOOD_AND_DRINK/plaid-sub", data={"code": "FOOD_AND_DRINK_COFFEE"}, headers=ORIGIN)
    client.post("/categories/TRAVEL/plaid-sub", data={"code": "NOPE"}, headers=ORIGIN)
    assert "NOPE" not in {c["key"] for c in categories.list_categories(db)}
    with pytest.raises(ValueError):
        categories.add_plaid_subcategory(db, "TRAVEL", "FOOD_AND_DRINK_GROCERIES")
    # deleted: its transactions go where you sent them, and it's offered again (2026-11-02)
    def offered():
        return [o["code"] for o in categories.plaid_subcategory_options(db).get("FOOD_AND_DRINK", [])]
    categories.delete_category(db, "FOOD_AND_DRINK_COFFEE", "TRAVEL")
    assert category_of(db, coffee) == "TRAVEL" and "FOOD_AND_DRINK_COFFEE" in offered()
    later = plaid_txn(db, "c5", "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE")
    assert category_of(db, later) == "TRAVEL"
    # adding it again brings them back, and later syncs
    categories.add_plaid_subcategory(db, "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE")
    assert category_of(db, coffee) == category_of(db, later) == "FOOD_AND_DRINK_COFFEE" and "FOOD_AND_DRINK_COFFEE" not in offered()
    assert category_of(db, plaid_txn(db, "c6", "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE")) == "FOOD_AND_DRINK_COFFEE"
    assert category_of(db, mine) == "TRAVEL"                                  # your own choice was never touched


def test_your_own_subcategory_with_the_same_name_replaces_the_plaid_option(db):
    plaid_txn(db, "a", "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_CLOTHING_AND_ACCESSORIES")
    plaid_txn(db, "b", "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_SUPERSTORES")
    def offered():
        return [o["label"] for o in categories.plaid_subcategory_options(db).get("GENERAL_MERCHANDISE", [])]
    assert sorted(offered()) == ["Clothing & accessories", "Superstores"]
    mine = categories.add_subcategory(db, "GENERAL_MERCHANDISE", "CLOTHING and Accessories")
    assert offered() == ["Superstores"]
    other = categories.add_subcategory(db, "TRAVEL", "Superstores")          # under another category: no match
    assert offered() == ["Superstores"] and other
    categories.delete_category(db, mine, "GENERAL_MERCHANDISE")              # gone: offered again
    assert sorted(offered()) == ["Clothing & accessories", "Superstores"]


def test_each_group_has_add_category_and_a_card_with_its_kind(client, db):
    """User's request 2026-11-02: a button on each group's heading opening a card below it; the old bottom card is gone."""
    page = client.get("/categories").text
    assert "Add a category" not in page
    for kind in categories.KIND_ORDER:
        assert f'data-toggle-panel="add-category-{kind}"' in page
        card = page.split(f'id="add-category-{kind}"')[1].split("</section>")[0]
        assert card.lstrip().startswith("hidden") and f'name="kind" value="{kind}"' in card


def test_new_category_is_saved_with_its_icon(client, db):
    client.post("/categories", data={"label": "Fake Hobby", "kind": "income", "icon": "coffee"}, headers=ORIGIN)
    row = db.execute("SELECT kind, icon, is_custom FROM categories WHERE key = 'FAKE_HOBBY'").fetchone()
    assert tuple(row) == ("income", "coffee", 1)


def test_new_category_with_a_bad_icon_creates_nothing(client, db):
    client.post("/categories", data={"label": "Fake Hobby", "kind": "expense", "icon": "not-an-icon"}, headers=ORIGIN)
    assert not categories.category_exists(db, "FAKE_HOBBY")


def test_deleted_plaid_category_is_offered_and_comes_back_with_its_transactions(client, db):
    plaid_txn(db, "t1", "TRAVEL", "TRAVEL_FLIGHTS")
    plaid_txn(db, "t2", "TRAVEL", "TRAVEL_LODGING")
    plaid_txn(db, "t3", "TRAVEL", "TRAVEL_TAXIS")
    assert "expense" not in categories.plaid_category_options(db)              # all there: nothing to offer
    # a subcategory deleted and sent elsewhere stays there when its parent returns
    sub = "TRAVEL_TAXIS"
    categories.add_plaid_subcategory(db, "TRAVEL", sub)
    categories.delete_category(db, sub, "TRANSPORTATION")
    categories.delete_category(db, "TRAVEL", "UNCATEGORIZED")
    db.execute("UPDATE transactions SET plaid_category_key = 'UNCATEGORIZED' WHERE plaid_transaction_id = 't3'")
    db.commit()
    assert categories.plaid_category_options(db)["expense"] == [{"key": "TRAVEL", "label": "Travel", "count": 3}]

    page = client.post("/categories/plaid-category", data={"key": "TRAVEL", "kind": "expense"}, headers=ORIGIN).text
    assert 'id="card-TRAVEL"' in page
    assert page.split('id="add-category-expense"')[1].lstrip().startswith(">")       # the card stays open: not hidden
    assert "expense" not in categories.plaid_category_options(db)
    now = dict(db.execute("SELECT plaid_transaction_id, plaid_category_key FROM transactions").fetchall())
    assert now == {"t1": "TRAVEL", "t2": "TRAVEL", "t3": "UNCATEGORIZED"}
    assert "TRAVEL" not in categories.remaps(db)


def test_plaid_category_options_leave_out_a_name_you_use_and_other(db):
    assert "OTHER" not in categories.PLAID_CATEGORIES
    categories.delete_category(db, "BANK_FEES", "UNCATEGORIZED")
    assert [o["key"] for o in categories.plaid_category_options(db)["expense"]] == ["BANK_FEES"]
    mine = categories.add_category(db, "Bank  FEES", "income")
    assert "expense" not in categories.plaid_category_options(db)
    categories.delete_category(db, mine, "UNCATEGORIZED")
    categories.add_plaid_category(db, "BANK_FEES")
    with pytest.raises(ValueError):
        categories.add_plaid_category(db, "BANK_FEES")


def test_category_dropdowns_indent_subcategories(client, db):
    """User's request 2026-11-02: a subcategory is its own name, indented, right after its parent."""
    from fintrack.web.deps import category_option
    sub = categories.add_subcategory(db, "FOOD_AND_DRINK", "Fake coffee")
    assert category_option(next(c for c in categories.list_categories(db) if c["key"] == sub)) == "\u00a0" * 4 + "Fake coffee"
    assert category_option(next(c for c in categories.list_categories(db) if c["key"] == "FOOD_AND_DRINK")) == "Food & drink"
    plaid_txn(db, "drop1", "TRAVEL", "TRAVEL_FLIGHTS")                       # the group edit's drop-down needs a row
    page = client.get("/transactions").text
    assert f'<option value="{sub}">' + "\u00a0" * 4 + "Fake coffee</option>" in page
    assert "› Fake coffee</option>" not in page
