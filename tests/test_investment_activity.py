"""Investment activity on the Transactions page and on a connected investment account's page (user's request
2026-10-12): sweeps hidden; in the Transactions list with your transactions (2026-10-30), never in totals
elsewhere; edited like transactions (description, category, tags; 2026-10-31). Made-up data only."""

from datetime import date

import pytest

from fintrack import items, sync

CHECKING, BROKERAGE = 1, 2
ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name, products)"
                 " VALUES (1, 'i1', 'sandbox', 'Fake Broker', '[\"investments\", \"transactions\"]')")
    for acc_id, account_type, subtype in [(CHECKING, "depository", "checking"), (BROKERAGE, "investment", "brokerage")]:
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, subtype, classification,"
                     " current_balance_cents) VALUES (?, 1, ?, ?, ?, ?, ?, 123400)",
                     (acc_id, f"acc-{acc_id}", f"Fake {subtype}", account_type, subtype, items.classify(account_type)))
    conn.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key)"
                 " VALUES ('b1', ?, '2026-09-03', 2500, 'FAKE CAFE', 'FOOD_AND_DRINK')", (CHECKING,))
    conn.execute("INSERT INTO security_prices (security_id, price_date, ticker, name, price) VALUES ('sec-1', '2026-09-01', 'FAKX', 'Fake Fund', 50)")
    conn.commit()

    def inv(txn_id, day, name, kind, subtype, amount, quantity=0.0, price=0.0):
        return {"investment_transaction_id": txn_id, "account_id": f"acc-{BROKERAGE}", "security_id": "sec-1",
                "date": date.fromisoformat(day), "name": name, "quantity": quantity, "price": price, "fees": 0.0,
                "amount": amount, "type": kind, "subtype": subtype, "iso_currency_code": "USD"}
    sync.store_investment_transactions(conn, 1, [
        inv("i1", "2026-09-05", "Fake dividend", "cash", "dividend", -12.34),
        inv("i2", "2026-09-06", "Buy", "buy", "buy", 500.0, quantity=10, price=50),
        inv("i3", "2026-09-07", "Sweep out", "cash", "deposit", -999.0),          # internal: hidden
        inv("i4", "2026-09-08", "Funds Received", "cash", "deposit", -300.0),
    ])
    return conn


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


SEPT = "/transactions?range=custom&from=2026-09-01&to=2026-09-30"


def test_the_transactions_list_includes_investment_activity(client):
    """User's request 2026-10-30: activity sits in the list with your transactions, in categories found by name (here the
    built-in fallbacks: no Investment Income / Investments categories), and counts on this page only."""
    page = client.get(SEPT).text
    rows = page.split('<table class="data txns">')[1].split("<tbody>")[1].split("</tbody>")[0]
    assert all(n in rows for n in ("FAKE CAFE", "Fake dividend", "Funds Received", ">Buy<")) and "Sweep out" not in rows
    # every row has its box and edit panel (2026-10-31); activity under its negative id
    assert rows.count('class="row-select"') == 4 and 'data-edit-url="/transactions/-1/edit"' in rows
    assert rows.count('data-edit-url="/transactions/-') == 3 and 'data-bulk-total="4"' in page
    assert "FAKX" in rows and "10 × $50.00" in rows                          # ticker; a trade's quantity x price
    assert client.get(SEPT + "&kind=investment").text.count('data-edit-url="/transactions/-') == 3   # old link: All

    def names(query):
        rows = client.get(SEPT + query).text.split('<table class="data txns">')[1].split("<tbody>")[1].split("</tbody>")[0]
        return sorted(n for n in ("FAKE CAFE", "Fake dividend", "Funds Received", ">Buy<") if n in rows)
    assert names("&kind=income") == ["Fake dividend"]                        # Income (the built-in) is income
    assert names("&kind=transfer") == [">Buy<", "Funds Received"]            # Transfer out / Transfer in
    assert names("&kind=expense") == ["FAKE CAFE"]
    assert names("&q=dividend") == ["Fake dividend"] and names(f"&account={CHECKING}") == ["FAKE CAFE"]
    assert names("&category=TRANSFER_IN") == ["Funds Received"]
    income = client.get(SEPT + "&kind=income").text
    assert "selected>Income by category</option>" in income and ">$12 · 1</span>" in income   # the pie counts it here


def test_activity_takes_your_similarly_named_categories(client, db):
    from fintrack import categories, investment_activity
    income = categories.add_category(db, "Investment Income", "income")
    dividend = categories.add_subcategory(db, income, "Dividend")
    invest = categories.add_category(db, "Investments", "transfer")
    buy = categories.add_subcategory(db, invest, "Buy")
    keys = investment_activity.category_keys(db)
    assert (keys["dividend"], keys["buy"], keys["sell"], keys["fee"]) == (dividend, buy, income, invest)
    assert keys["in"] == "TRANSFER_IN" and keys["out"] == "TRANSFER_OUT"
    rows = client.get(SEPT + "&kind=transfer").text.split('<table class="data txns">')[1].split("<tbody>")[1].split("</tbody>")[0]
    assert ">Buy<" in rows and "Funds Received" in rows
    assert client.get(SEPT + f"&category={income}").text.count('data-edit-url="/transactions/-') == 1   # the dividend


def test_investment_account_page_shows_its_activity(client, db):
    for security_id, name, ticker, is_cash, qty, price, value, basis in [
            ("sec-1", "Fake Fund", "FAKX", 0, 10.0, 50.0, 50000, 45000),
            ("sec-2", "Fake Bond Fund", "FBND", 0, 4.0, 25.0, 10000, None),           # no cost basis reported
            ("cash-1", "Fake Settlement Fund", None, 1, 30.0, 1.0, 3000, None)]:
        db.execute("INSERT INTO holdings (account_id, security_id, name, ticker, security_type, is_cash, quantity, price,"
                   " value_cents, cost_basis_cents) VALUES (?, ?, ?, ?, 'etf', ?, ?, ?, ?, ?)",
                   (BROKERAGE, security_id, name, ticker, is_cash, qty, price, value, basis))
    db.commit()
    # the activity page was folded into the account page (user's request 2026-10-15); its address redirects there
    moved = client.get(f"/accounts/{BROKERAGE}/activity", follow_redirects=False)
    assert moved.status_code == 307 and moved.headers["location"] == f"/accounts/{BROKERAGE}"
    page = client.get(f"/accounts/{BROKERAGE}").text
    holdings = page.split("<h2>Holdings</h2>")[1].split("<h2>Transactions</h2>")[0]
    assert holdings.index("Fake Fund") < holdings.index("Fake Bond Fund") < holdings.index("Fake Settlement Fund")
    assert "$500.00" in holdings and "$450.00" in holdings and "$50.00" in holdings              # value, basis, price
    assert "$630.00" in holdings                                                                 # total value
    assert holdings.count('class="sort"') == 5
    assert "Fake dividend" in page and "Sweep out" not in page
    # the table bank and card accounts have (2026-10-31): filters, headings that sort, rows that edit
    activity = page.split("<h2>Transactions</h2>")[1]
    assert 'id="acct-filters"' in activity and ">Category</a></th>" in activity and "data-sortable" not in activity
    assert 'data-edit-url="/transactions/-1/edit?view=account"' in activity and "+$12.34" in activity   # dividend: in
    assert "FAKX" in activity and "Funds Received" in activity and "FAKE CAFE" not in activity
    oldest = client.get(f"/accounts/{BROKERAGE}?sort=date&dir=asc").text.split("<h2>Transactions</h2>")[1]
    assert oldest.index("Fake dividend") < oldest.index("Funds Received")
    accounts = client.get("/setup").text
    # Settings → Accounts names link to the Balances pages (2026-10-14), which carry the holdings and activity
    assert f'href="/accounts/{BROKERAGE}"' in accounts and f'href="/accounts/{CHECKING}/activity"' not in accounts


def _rows(client, query=""):
    """The list's rows ("" when nothing matches: no table)."""
    page = client.get(SEPT + query).text
    return page.split('<table class="data txns">')[1].split("<tbody>")[1].split("</tbody>")[0] if '<table class="data txns">' in page else ""


def test_activity_is_edited_like_a_transaction(client, db):
    """User's request 2026-10-31: description, category (its kind follows) and tags, from the row's panel and by group
    edit; the panel leaves out what doesn't apply (rules, recurring, bonus, matching). Ids in URLs are negative."""
    from fintrack import categories, tags
    dividend = db.execute("SELECT id FROM investment_transactions WHERE plaid_investment_transaction_id = 'i1'").fetchone()[0]
    row_id = -dividend
    panel = client.get(f"/transactions/{row_id}/edit").text
    assert "From Plaid: Fake dividend · Dividend · FAKX" in panel and 'name="category"' in panel
    assert "Create a rule" not in panel and "recurring" not in panel.lower() and "From the bank" not in panel
    # your description and category: the dividend becomes spending (kind follows the category)
    row = client.post(f"/transactions/{row_id}/edit", data={"description": "My fake payout", "category": "FOOD_AND_DRINK",
                                                            "new_tag": "🧪 fake"}, headers=ORIGIN).text
    assert "My fake payout" in row and "Food &amp; drink" in row and "🧪 fake" in row
    assert "My fake payout" in _rows(client, "&kind=expense") and "My fake payout" not in _rows(client, "&kind=income")
    tag = tags.tag_by_name(db, "🧪 fake")
    assert "My fake payout" in _rows(client, f"&tag={tag.id}") and "My fake payout" not in _rows(client, "&tag=none")
    # Plaid's wording back = automatic again; a blank category = the one for its type
    client.post(f"/transactions/{row_id}/edit", data={"description": "Fake dividend", "category": ""}, headers=ORIGIN)
    assert tuple(db.execute("SELECT user_description, user_category FROM investment_transactions WHERE id = ?",
                            (dividend,)).fetchone()) == (None, None)
    # the Tags page sums tagged activity (Income here)
    summary = next(s for s in tags.summaries(db) if s.tag.id == tag.id)
    assert summary.count == 1 and summary.received_cents == 1234
    # tag remove from the panel
    client.post(f"/transactions/{row_id}/tags/remove", data={"tag_id": tag.id}, headers=ORIGIN)
    assert tags.tags_of(db, [row_id]) == {}
    # group edit: checked rows (a transaction and activity) and "all matching"
    cafe = db.execute("SELECT id FROM transactions WHERE plaid_transaction_id = 'b1'").fetchone()[0]
    done = client.post("/transactions/bulk-category", data={"ids": [str(cafe), str(row_id)], "apply_category": "TRAVEL",
                                                            "return_url": SEPT}, headers=ORIGIN, follow_redirects=False)
    assert "2+transactions+recategorized" in done.headers["location"]
    assert [r[0] for r in db.execute("SELECT user_category FROM investment_transactions WHERE id = ?", (dividend,))] == ["TRAVEL"]
    client.post("/transactions/bulk-tag", data={"all": "1", "tag_name": "fake trip", "range": "custom", "from": "2026-09-01",
                                                "to": "2026-09-30", "return_url": SEPT}, headers=ORIGIN)
    assert len(tags.tags_of(db, [cafe, -1, -2, -3, -4])) == 4             # the cafe and the 3 activity rows shown
    # a deleted category's choices move on, as for transactions; a later sync keeps your edits
    categories.delete_category(db, "TRAVEL", "ENTERTAINMENT")
    sync.store_investment_transactions(db, 1, [
        {"investment_transaction_id": "i1", "account_id": f"acc-{BROKERAGE}", "security_id": "sec-1",
         "date": date(2026, 9, 5), "name": "Fake dividend", "quantity": 0.0, "price": 0.0, "fees": 0.0,
         "amount": -12.35, "type": "cash", "subtype": "dividend", "iso_currency_code": "USD"}])
    assert tuple(db.execute("SELECT user_category, amount_cents FROM investment_transactions WHERE id = ?",
                            (dividend,)).fetchone()) == ("ENTERTAINMENT", -1235)
