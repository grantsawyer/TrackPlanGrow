"""The Transactions page's "Categories to review" card (user's request 2026-11-01). Made-up data only."""

import pytest

from fintrack import categories, db as database, items, sync

CHECKING, CARD = 1, 2
ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    for acc_id, kind in [(CHECKING, "depository"), (CARD, "credit")]:
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification) VALUES (?, 1, ?, ?, ?, ?)",
                     (acc_id, f"acc-{acc_id}", f"Fake {kind}", kind, items.classify(kind)))
    conn.commit()
    return conn


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def add(conn, pid, cents, key="FOOD_AND_DRINK", name="FAKE CAFE", account=CARD, **columns):
    names = ", ".join(columns)
    conn.execute(f"INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key"
                 f"{', ' + names if names else ''}) VALUES (?, ?, '2026-10-02', ?, ?, ?{', ?' * len(columns)})",
                 (pid, account, cents, name, key, *columns.values()))
    conn.commit()
    return conn.execute("SELECT id FROM transactions WHERE plaid_transaction_id = ?", (pid,)).fetchone()[0]


def listed(conn):
    return [r["id"] for r in categories.review_rows(conn)]


def test_only_new_automatic_first_level_categories_with_subcategories_are_listed(db):
    sub = categories.add_subcategory(db, "FOOD_AND_DRINK", "Fake Sub")
    income_sub = categories.add_subcategory(db, "INCOME", "Fake Pay")
    assert income_sub
    wanted = add(db, "a", 1200)
    pay = add(db, "pay", -5000, key="INCOME", name="FAKE PAY", account=CHECKING)
    add(db, "no-subs", 900, key="TRAVEL")                                  # nothing finer to choose
    add(db, "already-sub", 900, key=sub)
    add(db, "transfer", 900, key="TRANSFER_OUT")                           # not counted
    add(db, "yours", 900, user_category="FOOD_AND_DRINK")                  # you chose it
    add(db, "accepted", 900, category_reviewed=1)
    assert sorted(listed(db)) == sorted([wanted, pay])


def test_the_migration_counts_what_is_already_here_as_accepted():
    sql = next(p for p in database.Path(database.__file__).parent.joinpath("migrations").glob("0053_*")).read_text()
    assert "UPDATE transactions SET category_reviewed = 1;" in sql


def test_the_card_starts_collapsed_with_count_and_value(client, db):
    assert "Categories to review" not in client.get("/transactions").text  # nothing to review: no card
    categories.add_subcategory(db, "FOOD_AND_DRINK", "Fake Sub")
    add(db, "a", 1200)
    add(db, "b", 800, name="FAKE DELI")
    page = client.get("/transactions").text
    card = page.split('<section class="card review-card"')[1].split("</section>")[0]
    assert "<details>" in card and "2 transactions" in card and "$20" in card and "Fake Sub" in card
    # the whole list to choose from, by bucket, not just this category's subcategories (2026-11-01)
    assert 'value="TRAVEL"' in card and '<optgroup label="Income">' in card
    # just above the table, its rows like the table's: same cells and edit panel, no id or box (2026-11-01)
    assert page.index('id="txn-filters"') < page.index("review-card") < page.index('<table class="data txns">')
    assert 'class="data txns review-table"' in card and 'class="logo-col"' in card and 'class="account-col"' in card
    assert 'id="txn-' not in card and "row-select" not in card and 'class="link-button row-open"' in card
    assert '/edit?view=review"' in card and card.count('class="table-wrap txns-wrap txn-columns"') == 1
    assert 'class="table-wrap txns-wrap txn-columns"' in page.split("</section>")[-1]    # the list's too: same columns
    filtered = client.get("/transactions?category=TRAVEL").text            # no rows match: still there
    assert "review-card" in filtered and "No transactions match" in filtered


def test_dismiss_accepts_only_what_was_shown(client, db):
    categories.add_subcategory(db, "FOOD_AND_DRINK", "Fake Sub")
    shown = add(db, "a", 1200)
    later = add(db, "b", 800)                                              # arrives after the page was drawn
    done = client.post("/transactions/category-review/accept", data={"ids": [str(shown)], "return_url": "/transactions?kind=expense"},
                       headers=ORIGIN, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "/transactions?kind=expense"
    assert listed(db) == [later]
    row = db.execute("SELECT category, category_source FROM transactions_effective WHERE id = ?", (shown,)).fetchone()
    assert tuple(row) == ("FOOD_AND_DRINK", "plaid")                       # the category didn't change


def test_picking_a_subcategory_moves_it_off_the_card(client, db):
    sub = categories.add_subcategory(db, "FOOD_AND_DRINK", "Fake Sub")
    tid, other = add(db, "a", 1200), add(db, "b", 800)
    page = client.get("/transactions?kind=expense").text
    assert 'name="return_url" value="/transactions?kind=expense&amp;review=1"' in page
    # the first entry (what it already has) changes nothing
    client.post(f"/transactions/category-review/{tid}", data={"category": ""}, headers=ORIGIN)
    assert sorted(listed(db)) == sorted([tid, other])
    done = client.post(f"/transactions/category-review/{tid}", data={"category": sub, "return_url": "/transactions?kind=expense&review=1"},
                       headers=ORIGIN, follow_redirects=False)
    assert done.headers["location"] == "/transactions?kind=expense&review=1" and listed(db) == [other]
    assert "<details open>" in client.get(done.headers["location"]).text
    away = client.post(f"/transactions/category-review/{other}", data={"return_url": "https://elsewhere.invalid/"},
                       headers=ORIGIN, follow_redirects=False)
    assert away.headers["location"] == "/transactions"


def test_accepting_a_pending_one_carries_to_its_posted_one(db):
    from datetime import date
    categories.add_subcategory(db, "FOOD_AND_DRINK", "Fake Sub")
    pending = add(db, "pend", 1200, pending=1)
    categories.accept_reviewed(db, [pending])
    sync._upsert_transaction(db, 1, {
        "transaction_id": "posted", "pending_transaction_id": "pend", "account_id": f"acc-{CARD}", "date": date(2026, 10, 3),
        "amount": 12.0, "name": "FAKE CAFE", "pending": False, "iso_currency_code": "USD",
        "personal_finance_category": {"primary": "FOOD_AND_DRINK", "detailed": "FOOD_AND_DRINK_OTHER"}})
    db.commit()
    assert db.execute("SELECT category_reviewed FROM transactions WHERE plaid_transaction_id = 'posted'").fetchone()[0] == 1


def test_the_cards_rows_open_the_edit_panel_and_a_save_reloads_with_the_card_open(client, db):
    """User's request 2026-11-01: the same panel; a save comes back as a reload, since the row may leave the card."""
    sub = categories.add_subcategory(db, "FOOD_AND_DRINK", "Fake Sub")
    tid = add(db, "a", 1200)
    panel = client.get(f"/transactions/{tid}/edit?view=review").text
    assert 'name="view" value="review"' in panel and 'hx-target="this"' in panel and f'hx-target="#txn-{tid}"' not in panel
    assert f"/rules/new?from={tid}" in panel
    current = {**ORIGIN, "HX-Request": "true", "HX-Current-URL": "https://localhost:8443/transactions?kind=expense&review=1"}
    saved = client.post(f"/transactions/{tid}/edit", data={"description": "FAKE CAFE", "category": sub, "view": "review"},
                        headers=current)
    assert saved.headers["HX-Redirect"] == "/transactions?kind=expense&review=1" and listed(db) == []
    # not opened by its link: the card stays open anyway; another page's address can't be sent back
    other = add(db, "b", 800)
    away = client.post(f"/transactions/{other}/edit", data={"description": "FAKE CAFE", "category": "", "view": "review"},
                       headers={**ORIGIN, "HX-Current-URL": "https://localhost:8443/elsewhere"})
    assert away.headers["HX-Redirect"] == "/transactions?review=1" and listed(db) == [other]
    # the list's own rows still swap in place
    plain = client.post(f"/transactions/{other}/edit", data={"description": "FAKE CAFE", "category": ""}, headers=ORIGIN)
    assert "HX-Redirect" not in plain.headers and f'id="txn-{other}"' in plain.text



def test_one_save_applies_everything_in_the_panel(client, db):
    """User's request 2026-11-01: description, category, tag, recurring and reward bonus go with the one Save; a bonus it
    can't read comes back in the panel and nothing is saved."""
    from fintrack import recurring, rewards, tags
    tid = add(db, "gym", 1200, name="FAKE GYM")
    panel = client.get(f"/transactions/{tid}/edit").text
    assert panel.count('type="submit"') == 1 and "From the bank: FAKE GYM" in panel and 'name="recurring"' in panel
    saved = client.post(f"/transactions/{tid}/edit", data={"description": "My fake gym", "category": "TRAVEL", "new_tag": "Fake tag",
                                                          "recurring": "new:monthly"}, headers=ORIGIN).text
    assert "My fake gym" in saved and 'aria-label="Recurring"' in saved
    assert [g.name for g in tags.tags_of(db, [tid])[tid]] == ["Fake tag"]
    item = recurring.list_items(db, "confirmed")[0]
    # adding to an item takes its category, unless you chose another in the same save
    other = add(db, "other", 1200, name="FAKE GYM TOO")
    client.post(f"/transactions/{other}/edit", data={"description": "FAKE GYM TOO", "category": "ENTERTAINMENT", "recurring": f"item:{item['id']}"},
                headers=ORIGIN)
    assert db.execute("SELECT category FROM transactions_effective WHERE id = ?", (other,)).fetchone()[0] == "ENTERTAINMENT"
    client.post(f"/transactions/{other}/edit", data={"description": "FAKE GYM TOO", "category": "ENTERTAINMENT", "recurring": "exclude"}, headers=ORIGIN)
    assert recurring.link_of(db, other) == (True, None)
    # a reward bonus on a card that's set up
    rewards.set_up(db, CARD, "points", 100)
    assert 'name="bonus"' in client.get(f"/transactions/{other}/edit").text and 'name="bonus_shown"' in client.get(f"/transactions/{other}/edit").text
    bad = client.post(f"/transactions/{other}/edit", data={"description": "Not saved", "category": "ENTERTAINMENT", "bonus": "lots", "bonus_shown": "1"}, headers=ORIGIN)
    assert bad.headers["HX-Retarget"] == f"#bonus-msg-{other}"
    assert db.execute("SELECT description FROM transactions_effective WHERE id = ?", (other,)).fetchone()[0] != "Not saved"
    good = {"description": "FAKE GYM TOO", "category": "ENTERTAINMENT", "bonus": "5,000", "bonus_shown": "1"}
    client.post(f"/transactions/{other}/edit", data=good, headers=ORIGIN)
    assert rewards.bonus_of(db, other) == 5000
    client.post(f"/transactions/{other}/edit", data={**good, "bonus": ""}, headers=ORIGIN)   # blank = none
    assert not rewards.bonus_of(db, other)
    client.post(f"/transactions/{other}/edit", data=good, headers=ORIGIN)
    client.post(f"/transactions/{other}/edit", data={"description": "FAKE GYM TOO", "category": "ENTERTAINMENT"}, headers=ORIGIN)
    assert rewards.bonus_of(db, other) == 5000                             # not on the form: left alone



# ---------------------------------------------------------------- filters, sorting, bulk edits (user's request 2026-11-14)

def card(page: str) -> str:
    return page.split('class="card review-card"')[1].split("</section>")[0]


def shown(page: str) -> list[str]:
    """The card's rows' ids, in order (their boxes)."""
    import re
    return re.findall(r'class="review-select" name="ids" value="(\d+)"', card(page))


def test_the_card_filters_and_sorts_on_its_own(client, db):
    categories.add_subcategory(db, "FOOD_AND_DRINK", "Fake Sub")
    categories.add_subcategory(db, "INCOME", "Fake Pay")
    cafe, diner = add(db, "a", 1200), add(db, "b", 4500, name="FAKE DINER")
    pay = add(db, "c", -5000, key="INCOME", name="FAKE PAY", account=CHECKING)
    page = client.get("/transactions?kind=expense&review=1").text
    assert sorted(shown(page)) == sorted(map(str, [cafe, diner, pay]))                  # the list's filters don't apply
    assert shown(client.get("/transactions?review=1&r_q=diner").text) == [str(diner)]
    one = client.get("/transactions?kind=expense&review=1&r_cat=INCOME").text
    assert shown(one) == [str(pay)] and "1 of 3 transactions" in card(one)
    assert 'name="kind" value="expense"' in card(one)                                  # the list's filters kept
    assert shown(client.get(f"/transactions?review=1&r_account={CHECKING}").text) == [str(pay)]
    by_amount = client.get("/transactions?review=1&r_sort=amount").text                # biggest first
    assert shown(by_amount) == [str(diner), str(cafe), str(pay)]
    assert shown(client.get("/transactions?review=1&r_sort=amount&r_dir=asc").text) == [str(pay), str(cafe), str(diner)]
    assert "r_sort=amount&amp;r_dir=asc" in card(by_amount)                            # the heading again reverses it


def test_the_bulk_bar_sets_a_category_or_keeps_theirs(client, db):
    sub = categories.add_subcategory(db, "FOOD_AND_DRINK", "Fake Sub")
    a, b, c = add(db, "a", 1200), add(db, "b", 800), add(db, "c", 300)
    done = client.post("/transactions/category-review/bulk", headers=ORIGIN, follow_redirects=False,
                       data={"ids": [str(a), str(b)], "category": sub, "action": "set", "return_url": "/transactions?review=1"})
    assert done.headers["location"] == "/transactions?review=1" and listed(db) == [c]
    assert {r["category"] for r in db.execute(f"SELECT category FROM transactions_effective WHERE id IN ({a}, {b})")} == {sub}
    client.post("/transactions/category-review/bulk", headers=ORIGIN, data={"ids": [str(c)], "action": "accept"})
    assert listed(db) == []
    assert db.execute(f"SELECT category FROM transactions_effective WHERE id = {c}").fetchone()[0] == "FOOD_AND_DRINK"
