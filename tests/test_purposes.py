"""Purpose accounts (migration 0028, user's request 2026-10-12): transfers out of a bank account toward a defined
purpose (e.g. money lent for school) linked to a manual "Money lent" account. All names and amounts are made up."""

from datetime import date

import pytest

from fintrack import items, manual, networth, purposes, savings

TODAY = date(2026, 10, 9)
CHECKING = 1
ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification)"
                 " VALUES (?, 1, 'acc-1', 'Fake checking', 'depository', ?)", (CHECKING, items.classify("depository")))
    conn.commit()
    return conn


_ids = iter(range(1, 100_000))


def txn(conn, cents, day, name, key="TRANSFER_OUT", detailed="TRANSFER_OUT_ACCOUNT_TRANSFER"):
    tid = conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key,"
        " plaid_category_detailed) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (f"p{next(_ids)}", CHECKING, day, cents, name, key, detailed)).lastrowid
    conn.commit()
    return tid


@pytest.fixture
def lent(db):
    """A fake purpose account and a rule for its transfers."""
    account_id = manual.create_account(db, "Fake loan to a friend", "lent", "0", "2026-08-01")
    purposes.adopt_opening_value(db, account_id)
    return account_id


def balance(conn, account_id):
    return manual.get_account(conn, account_id)["current_balance_cents"]


def test_rules_link_transfers_and_build_the_balance(db, lent):
    t1 = txn(db, 500000, "2026-08-10", "FAKE FUNDS TRANSFER DB")              # $5,000 lent
    txn(db, 300000, "2026-09-10", "FAKE FUNDS TRANSFER DB")                   # $3,000 lent
    txn(db, -100000, "2026-09-20", "FAKE FUNDS TRANSFER CR", key="TRANSFER_IN",
        detailed="TRANSFER_IN_ACCOUNT_TRANSFER")                              # $1,000 paid back
    txn(db, 4000, "2026-09-12", "FAKE FUNDS TRANSFER COFFEE", key="FOOD_AND_DRINK", detailed=None)   # spending: never
    purposes.add_rule(db, lent, "fake funds transfer")
    assert balance(db, lent) == 700000
    assert [e.amount_cents for e in purposes.entries(db, lent)] == [-100000, 300000, 500000]
    # your unlink wins over the rule, also after the next update (e.g. a sync)
    purposes.set_link(db, t1, None)
    purposes.update(db)
    assert balance(db, lent) == 200000
    # an adjustment (written off) changes the balance
    purposes.add_adjustment(db, lent, "-500", "2026-09-30", "written off")
    assert balance(db, lent) == 150000
    assert purposes.update(db) is False                                       # nothing changed: nothing written


def test_purpose_accounts_count_in_net_worth_and_refuse_typed_values(db, lent):
    txn(db, 250000, "2026-08-10", "FAKE FUNDS TRANSFER DB")
    purposes.add_rule(db, lent, "FAKE FUNDS")
    assert manual.get_account(db, lent)["classification"] == "asset" and balance(db, lent) == 250000
    with pytest.raises(manual.ManualError, match="linked transfers"):
        manual.add_valuation(db, lent, "1000", "2026-09-01")
    rows = {t.label: t for t in networth.current_net_worth(db).by_type}
    assert rows["Money lent"].total_cents == 250000                             # its own asset row in net worth


def test_savings_leaves_linked_transfers_out(db, lent):
    txn(db, -500000, "2026-09-15", "FAKE PAYROLL", key="INCOME", detailed="INCOME_SALARY")   # $5,000 income
    txn(db, 300000, "2026-09-10", "FAKE FUNDS TRANSFER DB")                                  # $3,000 lent
    txn(db, 20000, "2026-09-11", "FAKE APP TRANSFER", detailed="TRANSFER_OUT_TRANSFER_OUT_FROM_APPS")  # $200 elsewhere
    purposes.add_rule(db, lent, "FAKE FUNDS")
    purposes.add_adjustment(db, lent, "-1000", "2026-09-20")                                  # $10 written off
    saving = savings.range_series(db, "2026-09-01", "2026-09-30", TODAY)[0]   # lending and its adjustments left out
    assert (saving.other_transfers_cents, saving.net_cash_cents) == (20000, 500000 - 20000)
    assert saving.saved_cents == 480000


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_pages_link_from_the_transaction_panel_and_show_the_account(client, db, lent):
    t = txn(db, 120000, "2026-09-10", "FAKE ONE-OFF TRANSFER")
    panel = client.get(f"/transactions/{t}/edit").text
    assert 'name="purpose"' in panel and "Fake loan to a friend" in panel
    client.post(f"/transactions/{t}/edit", data={"description": "", "category": "", "purpose": str(lent)}, headers=ORIGIN)
    assert balance(db, lent) == 120000
    # History (linked transfers + adjustments) and Rules are on the account's page (2026-10-13; /manual/{id} redirects
    # there since 2026-10-15)
    page = client.get(f"/accounts/{lent}").text
    assert "<h2>History</h2>" in page and "FAKE ONE-OFF TRANSFER" in page and "Add a value" not in page
    assert "Add adjustment" in page or "adjustment" in page
    assert "<h2>Rules</h2>" in page and "<h2>Manage</h2>" in page
    assert client.get(f"/manual/{lent}", follow_redirects=False).headers["location"] == f"/accounts/{lent}"
    client.post(f"/purpose-links/{t}/unlink", data={"account_id": lent}, headers=ORIGIN)
    assert balance(db, lent) == 0


def test_group_edit_links_checked_transfers_and_skips_spending(client, db, lent):
    t1 = txn(db, 100000, "2026-09-01", "FAKE TRANSFER A")
    t2 = txn(db, 200000, "2026-09-02", "FAKE TRANSFER B")
    spent = txn(db, 3000, "2026-09-03", "FAKE CAFE", key="FOOD_AND_DRINK", detailed=None)
    page = client.get("/transactions").text
    assert 'formaction="/transactions/bulk-purpose"' in page and "Fake loan to a friend" in page
    response = client.post("/transactions/bulk-purpose", data={"ids": [t1, t2, spent], "apply_purpose": str(lent),
                                                              "return_url": "/transactions"},
                           headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303
    assert "2+transactions+linked+to+Fake+loan+to+a+friend" in response.headers["location"]
    assert "1+skipped" in response.headers["location"]
    assert balance(db, lent) == 300000
    client.post("/transactions/bulk-purpose", data={"ids": [t2], "apply_purpose": "none", "return_url": "/transactions"},
                headers=ORIGIN)
    assert balance(db, lent) == 100000
    response = client.post("/transactions/bulk-purpose", data={"ids": [t1], "apply_purpose": "",
                                                              "return_url": "/transactions"}, headers=ORIGIN,
                           follow_redirects=False)
    assert "error=" in response.headers["location"]


def test_other_assets_show_on_balances_not_on_savings(client, db, lent):
    txn(db, -500000, "2026-09-15", "FAKE PAYROLL", key="INCOME", detailed="INCOME_SALARY")
    purposes.set_link(db, txn(db, 300000, "2026-09-10", "FAKE FUNDS TRANSFER DB"), lent)
    on_savings = client.get("/savings?range=custom&from=2026-09-01&to=2026-09-30").text
    # Balances shows the change in the account's value (balances.py, 2026-10-12), from the snapshots: take today's (as
    # a sync does); the default range (365 days) includes it
    from fintrack import networth

    purposes.update(db)                               # the account's balance, as after a sync
    networth.take_snapshot(db)
    on_balances = client.get("/networth").text     # the cards per kind + the table: on Net worth since 2026-10-13
    assert "<h2>Other assets</h2>" not in on_savings and ">Other assets</th>" not in on_savings
    assert "<h2>Other assets</h2>" in on_balances and ">Other assets</th>" in on_balances
