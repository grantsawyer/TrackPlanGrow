"""Your account types (account_types.py, migration 0032; user's request 2026-10-13) and the Balances account page's
details card (rename, type, a card's statement and paying account). All accounts and amounts are made up."""

from datetime import date, timedelta

import pytest

from fintrack import account_types, items, visuals
from tests.fakes import FAKE_ACCOUNTS

ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    items.upsert_accounts(conn, 1, FAKE_ACCOUNTS)       # a checking account and a credit card
    conn.commit()
    return conn


def account(conn, plaid_id):
    return conn.execute("SELECT * FROM accounts WHERE plaid_account_id = ?", (plaid_id,)).fetchone()


def node(conn, path):
    parent, _, child = path.partition(" › ")
    for t in account_types.tree(conn):
        if t.name == parent:
            return t.id if not child else next(c.id for c in t.children if c.name == child)
    raise KeyError(path)


def test_default_hierarchy(db):
    types = account_types.tree(db)
    assert [t.name for t in types][:7] == ["Cash", "Retirement", "Investment", "Other assets", "Property", "Credit card",
                                          "Loan"]
    retirement = next(t for t in types if t.name == "Retirement")
    assert retirement.kind == "retirement" and [c.name for c in retirement.children][:3] == ["401(k)", "403(b)", "IRA"]


def test_table_changes(db):
    new = account_types.add_type(db, "Fake crypto wallets", "investment")
    sub = account_types.add_subtype(db, new, "Cold storage")
    with pytest.raises(account_types.TypeError_):
        account_types.add_subtype(db, new, "cold STORAGE")                     # no duplicates (any case)
    with pytest.raises(account_types.TypeError_):
        account_types.add_subtype(db, sub, "Nested")                           # two levels only
    account_types.rename(db, sub, "Hardware wallet")
    account_types.move_to(db, sub, node(db, "Retirement"))
    assert account_types.path(db, sub) == "Retirement › Hardware wallet"
    first = account_types.tree(db)[0].id
    account_types.move(db, first, "down")
    assert account_types.tree(db)[1].id == first                               # reordered
    account_types.delete(db, new)
    assert all(t.name != "Fake crypto wallets" for t in account_types.tree(db))


def test_assigning_a_type_changes_what_the_account_is(db):
    checking = account(db, FAKE_ACCOUNTS[0]["account_id"])
    account_types.assign(db, checking["id"], node(db, "Retirement › IRA"))
    a = account(db, checking["plaid_account_id"])
    assert (a["type"], a["classification"], a["type_kind"], a["type_label"]) == ("investment", "asset", "retirement", "IRA")
    assert visuals.is_retirement_account(a) and visuals.investment_type_label(a) == "IRA"
    # a sync keeps your type (Plaid still says depository)
    items.upsert_accounts(db, 1, FAKE_ACCOUNTS)
    assert account(db, checking["plaid_account_id"])["type"] == "investment"
    # moving the subtype under Investment makes it an investment account; renaming follows
    account_types.move_to(db, node(db, "Retirement › IRA"), node(db, "Investment"))
    a = account(db, checking["plaid_account_id"])
    assert a["type_kind"] == "investment" and not visuals.is_retirement_account(a)
    # deleting it puts the account back to automatic: Plaid's type again
    assert account_types.delete(db, node(db, "Investment › IRA")) == 1
    a = account(db, checking["plaid_account_id"])
    assert (a["type"], a["classification"], a["type_node_id"], a["type_kind"]) == ("depository", "asset", None, None)


def test_a_liability_type_and_the_automatic_match(db):
    checking = account(db, FAKE_ACCOUNTS[0]["account_id"])
    assert account_types.path(db, account_types.automatic(db, checking)) == "Cash › Checking"
    card = account(db, FAKE_ACCOUNTS[1]["account_id"])
    assert account_types.path(db, account_types.automatic(db, card)) == "Credit card › Credit card"
    account_types.assign(db, checking["id"], node(db, "Loan"))
    assert account(db, checking["plaid_account_id"])["classification"] == "liability"


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_account_page_details_and_edits(client, db):
    checking = account(db, FAKE_ACCOUNTS[0]["account_id"])
    page = client.get(f"/accounts/{checking['id']}").text
    assert "Automatic · Cash › Checking" in page and 'href="/account-types"' in page
    r = client.post(f"/accounts/{checking['id']}/name", data={"name": "Fake everyday"}, headers=ORIGIN,
                    follow_redirects=False)
    assert r.status_code == 303 and account(db, checking["plaid_account_id"])["name"] == "Fake everyday"
    client.post(f"/accounts/{checking['id']}/type", data={"node": str(node(db, "Cash › Savings"))}, headers=ORIGIN)
    assert account(db, checking["plaid_account_id"])["type_label"] == "Savings"
    client.post(f"/accounts/{checking['id']}/type", data={"node": ""}, headers=ORIGIN)
    assert account(db, checking["plaid_account_id"])["type_node_id"] is None


def test_credit_card_statement_and_paying_account(client, db):
    card, checking = account(db, FAKE_ACCOUNTS[1]["account_id"]), account(db, FAKE_ACCOUNTS[0]["account_id"])
    due = (date.today() + timedelta(days=10)).isoformat()
    db.execute("INSERT INTO card_statements (account_id, statement_balance_cents, statement_date, due_date,"
               " minimum_payment_cents) VALUES (?, 41001, ?, ?, 2500)",
               (card["id"], (date.today() - timedelta(days=15)).isoformat(), due))
    db.commit()
    page = client.get(f"/accounts/{card['id']}").text
    assert "<dt>Minimum</dt><dd>$25.00</dd>" in page and "in 10 days" in page
    assert '<span class="status-text warning">◷ Due</span> · $410.01 owed' in page
    assert "Bills paid from" in page and f"Automatic · {checking['name']}" in page
    # a payment to the card since the statement: paid
    db.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key,"
               " plaid_category_detailed) VALUES ('pay1', ?, ?, -41001, 'FAKE PAYMENT', 'CREDIT_CARD_PAYMENT',"  # the app's key (set at sync)
               " 'LOAN_PAYMENTS_CREDIT_CARD_PAYMENT')", (card["id"], date.today().isoformat()))
    db.commit()
    assert '<span class="status-text good">✓ Paid</span>' in client.get(f"/accounts/{card['id']}").text
    client.post(f"/accounts/{card['id']}/pay-from", data={"pay_from": "none"}, headers=ORIGIN)
    assert db.execute("SELECT pay_from_account_id FROM card_payment_accounts WHERE card_account_id = ?",
                      (card["id"],)).fetchone()[0] is None                       # "don't forecast"
    assert "Bills paid from" not in client.get(f"/accounts/{checking['id']}").text   # cards only


def test_account_types_page(client, db):
    page = client.get("/account-types").text
    assert 'aria-current="page" class="active">Account types</a>' in page and 'value="Retirement"' in page
    r = client.post("/account-types/add", data={"name": "Fake collectibles", "kind": "other_asset"}, headers=ORIGIN,
                    follow_redirects=False)
    assert "msg=Type+added" in r.headers["location"]
    new = node(db, "Fake collectibles")
    client.post(f"/account-types/{new}/add", data={"name": "Fake coins"}, headers=ORIGIN)
    assert account_types.path(db, node(db, "Fake collectibles › Fake coins")) == "Fake collectibles › Fake coins"
    bad = client.post("/account-types/add", data={"name": "cash", "kind": "cash"}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in bad.headers["location"]
    client.post(f"/account-types/{new}/delete", headers=ORIGIN)
    assert "Fake collectibles" not in client.get("/account-types").text


def test_manual_account_institution_and_logo(client, db, gateway, home):
    from fintrack import logos, manual

    acct = manual.create_account(db, "Fake brokerage (manual)", "investment", "5000", date.today().isoformat())
    r = client.post(f"/accounts/{acct}/institution", data={"institution": "  Fake   Broker "}, headers=ORIGIN,
                    follow_redirects=False)
    assert r.status_code == 303 and "Institution+saved" in r.headers["location"]
    row = db.execute("SELECT manual_institution FROM accounts WHERE id = ?", (acct,)).fetchone()
    assert row[0] == "Fake Broker"
    # Plaid's search: an exact name match wins over the first result; its logo is saved and shown
    found = db.execute("SELECT institution_id, status FROM institution_searches WHERE query = 'fake broker'").fetchone()
    assert tuple(found) == ("ins_fake_8", "ok")
    assert logos.institution_logo_files(db)[acct].endswith(".png")
    page = client.get(f"/accounts/{acct}").text
    assert "<h2>Fake Broker · Fake brokerage (manual)</h2>" in page and 'src="/logos/' in page
    # each name is looked up once (another save or a sync doesn't search again)
    client.post(f"/accounts/{acct}/institution", data={"institution": "Fake Broker"}, headers=ORIGIN)
    assert logos.find_manual_institution_logos(db, home, gateway) == 0
    assert [c for c in gateway.calls if c[0] == "search_institutions"] == [("search_institutions", "fake broker")]


def test_manual_institution_not_found_or_failing(db, gateway, home):
    from fintrack import logos, manual
    from fintrack.plaid_client import PlaidError

    acct = manual.create_account(db, "Fake pension", "investment", "100", date.today().isoformat())
    db.execute("UPDATE accounts SET manual_institution = 'Fake Nowhere Trust' WHERE id = ?", (acct,))
    db.commit()
    gateway.institution_search_results = []
    assert logos.find_manual_institution_logos(db, home, gateway) == 0
    assert db.execute("SELECT status FROM institution_searches").fetchone()[0] == "none"
    assert logos.find_manual_institution_logos(db, home, gateway) == 0             # not again within 30 days
    assert len([c for c in gateway.calls if c[0] == "search_institutions"]) == 1
    db.execute("UPDATE institution_searches SET checked_at = datetime('now', '-31 days')")
    db.commit()

    def boom(_query):
        raise PlaidError("INTERNAL_SERVER_ERROR", "fake")
    gateway.search_institutions = boom                                             # never raises
    assert logos.find_manual_institution_logos(db, home, gateway) == 0
    assert db.execute("SELECT status FROM institution_searches").fetchone()[0] == "failed"
    assert acct not in logos.institution_logo_files(db)


def test_balances_rows_show_a_manual_institution(client, db):
    from fintrack import balances, manual

    acct = manual.create_account(db, "Fake brokerage (manual)", "investment", "5000", date.today().isoformat())
    client.post(f"/accounts/{acct}/institution", data={"institution": "Fake Broker"}, headers=ORIGIN)
    row = next(r for r in balances.account_rows(db, "investment") if r.id == acct)
    assert row.institution == "Fake Broker" and row.logo and row.is_manual


def test_counts_include_automatic_matches(client, db):
    # with nothing assigned, the checking account counts under Cash › Checking and the card under Credit card ›
    # Credit card, automatically (user's request 2026-10-13)
    types = {t.name: t for t in account_types.tree(db, with_automatic=True)}
    checking = next(c for c in types["Cash"].children if c.name == "Checking")
    assert (checking.shown, checking.automatic, checking.accounts) == (1, 1, 0)
    assert "1 account (1 automatic)" in client.get("/account-types").text
    acct = account(db, FAKE_ACCOUNTS[0]["account_id"])
    account_types.assign(db, acct["id"], node(db, "Cash › Savings"))
    types = {t.name: t for t in account_types.tree(db, with_automatic=True)}
    savings = next(c for c in types["Cash"].children if c.name == "Savings")
    checking = next(c for c in types["Cash"].children if c.name == "Checking")
    assert (savings.shown, savings.automatic, checking.shown) == (1, 0, 0)


def test_details_edit_in_place(client, db):
    """Name / Institution / Type edit in place (user's request 2026-10-13): no Save buttons; each field saves on change
    and carries its rules (the browser refuses an invalid value; the server refuses it too)."""
    from fintrack import manual

    acct = manual.create_account(db, "Fake brokerage (manual)", "investment", "5000", date.today().isoformat())
    page = client.get(f"/accounts/{acct}").text
    details = page[page.index(">Details</h3>"):page.index('class="acct-statement"') if 'class="acct-statement"' in page else None]
    assert "Edit details" not in page and "From the bank" not in page
    assert 'name="name" value="Fake brokerage (manual)" maxlength="60" data-autosubmit' in details and "required" in details
    assert 'name="institution"' in details and 'name="node" data-autosubmit' in details
    assert '<button type="submit" class="secondary">Save</button></noscript>' in details     # only without JavaScript
    bad = client.post(f"/accounts/{acct}/name", data={"name": "  "}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in bad.headers["location"]
    assert db.execute("SELECT name FROM accounts WHERE id = ?", (acct,)).fetchone()[0] == "Fake brokerage (manual)"


def test_overview_accounts_card_groups_by_type(client, db):
    """The Overview's Accounts card groups by type (user's request 2026-10-13; account categories were removed)."""
    import re as _re

    page = client.get("/").text
    groups = _re.findall(r'data-toggle-group="[^"]*" aria-expanded="false">([^<]*)</button>', page)
    assert groups[:2] == ["Cash", "Credit card"]                   # the automatic matches' types, in the table's order
    checking = account(db, FAKE_ACCOUNTS[0]["account_id"])
    account_types.assign(db, checking["id"], node(db, "Retirement › IRA"))
    groups = _re.findall(r'data-toggle-group="[^"]*" aria-expanded="false">([^<]*)</button>', client.get("/").text)
    assert "Retirement" in groups and "Cash" not in groups
