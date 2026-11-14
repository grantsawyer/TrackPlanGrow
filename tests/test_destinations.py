"""Destination rules and the raw bank text (migration 0030; user's request 2026-10-12): bank transfers that can't be paired
with an investment account's own activity are assigned by the text in their raw description. Made-up data only."""

from datetime import date

import pytest

from fintrack import contributions, items, savings, sync, visuals
from fintrack.contributions import INTO_CODE

TODAY = date(2026, 10, 9)
CHECKING, BROKERAGE, IRA = 1, 2, 3
ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name, products, access_token_enc)"
                 " VALUES (1, 'i1', 'sandbox', 'Fake Bank', '[\"investments\", \"transactions\"]', 'enc')")
    for acc_id, account_type, subtype in [(CHECKING, "depository", "checking"), (BROKERAGE, "investment", "brokerage"),
                                          (IRA, "investment", "ira")]:
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, subtype, classification)"
                     " VALUES (?, 1, ?, ?, ?, ?, ?)",
                     (acc_id, f"acc-{acc_id}", f"Fake {subtype}", account_type, subtype, items.classify(account_type)))
    conn.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key,"
                 " plaid_category_detailed, original_description) VALUES ('t1', ?, '2026-09-05', 60000,"
                 " 'FAKE BUY INVESTMENT', 'TRANSFER_OUT', ?, 'FAKE BUY INVESTMENT ***********9999')", (CHECKING, INTO_CODE))
    conn.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key)"
                 " VALUES ('t2', ?, '2026-09-15', -500000, 'FAKE PAYROLL', 'INCOME')", (CHECKING,))
    conn.commit()
    return conn


def september(conn):
    series = savings.range_series(conn, "2026-09-01", "2026-09-30", TODAY)
    return series[0], visuals.savings_mix(conn, series, "2026-09-01", "2026-09-30", "investment")


def test_a_rule_names_the_account_an_unpaired_transfer_went_to(db):
    m, chart = september(db)
    assert [(s["label"], s["dollars"]) for s in chart["series"]] == [("Account unknown", [600.0])]
    contributions.add_destination_rule(db, BROKERAGE, "9999")
    m2, chart = september(db)
    assert [(s["label"], s["dollars"]) for s in chart["series"]] == [("Fake Bank", [600.0])]   # by institution
    assert (m2.investment_cents, m2.retirement_cents) == (m.investment_cents, m.retirement_cents) == (60000, 0)
    assert contributions.destination_rules(db, BROKERAGE, TODAY) == [{"id": 1, "pattern": "9999", "count": 1}]


def test_a_rule_naming_a_retirement_account_moves_the_money_to_retirement(db):
    contributions.add_destination_rule(db, IRA, "***9999")
    m, chart = september(db)
    assert (m.investment_cents, m.retirement_cents) == (0, 60000) and chart["series"] == []
    assert m.saved_cents == 500000                                         # the rate itself is unchanged


def test_a_paired_transfer_ignores_the_rules(db):
    sync.store_investment_transactions(db, 1, [{
        "investment_transaction_id": "i1", "account_id": f"acc-{IRA}", "security_id": None, "date": date(2026, 9, 6),
        "name": "Funds Received", "quantity": 0, "price": 0, "fees": 0, "amount": -600.0, "type": "cash",
        "subtype": "deposit", "iso_currency_code": "USD"}])
    contributions.add_destination_rule(db, BROKERAGE, "9999")              # would say Brokerage...
    m, _chart = september(db)
    assert m.retirement_cents == 60000 and m.investment_cents == 0         # ...but the IRA's own row says IRA
    assert contributions.destination_rules(db, BROKERAGE, TODAY)[0]["count"] == 0


def test_rules_are_for_investment_accounts_and_need_some_text(db):
    with pytest.raises(ValueError):
        contributions.add_destination_rule(db, CHECKING, "9999")
    with pytest.raises(ValueError):
        contributions.add_destination_rule(db, BROKERAGE, "99")


class _Gateway:
    def get_transactions(self, token, start, end, logos=True, original_description=False):
        assert original_description and not logos
        return [{"transaction_id": "t2", "original_description": "FAKE PAYROLL DIRECT DEP 1234"},
                {"transaction_id": "t1", "original_description": "FAKE BUY INVESTMENT ***********9999"},   # unchanged
                {"transaction_id": "unknown", "original_description": "NOT HERE"}]


class _Cipher:
    def decrypt(self, value):
        return "token"


def test_backfill_fills_in_only_missing_or_changed_raw_text(db):
    dry = sync.backfill_original_descriptions(db, _Gateway(), _Cipher(), dry_run=True, today=TODAY)
    assert dry[0]["updated"] == 0 and dry[0]["with_text"] == 3
    assert db.execute("SELECT original_description FROM transactions WHERE plaid_transaction_id = 't2'").fetchone()[0] is None
    report = sync.backfill_original_descriptions(db, _Gateway(), _Cipher(), today=TODAY)
    assert report[0]["updated"] == 1
    assert db.execute("SELECT original_description FROM transactions WHERE plaid_transaction_id = 't2'").fetchone()[0] \
        == "FAKE PAYROLL DIRECT DEP 1234"


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_the_account_page_manages_rules(client, db):
    response = client.post(f"/accounts/{BROKERAGE}/destination-rules", data={"pattern": "9999"}, headers=ORIGIN,
                           follow_redirects=False)
    assert response.status_code == 303 and "msg=" in response.headers["location"]
    # the rules live on the account's Balances page since 2026-10-13 (user's request), and the form returns there
    assert response.headers["location"].startswith(f"/accounts/{BROKERAGE}?")
    page = client.get(f"/accounts/{BROKERAGE}").text
    assert "<h2>Destination rules</h2>" in page and "<strong>9999</strong> · 1 transfer" in page
    assert client.get(f"/accounts/{BROKERAGE}/activity", follow_redirects=False).status_code == 307   # folded in
    t1 = db.execute("SELECT id FROM transactions WHERE plaid_transaction_id = 't1'").fetchone()[0]
    assert "raw FAKE BUY INVESTMENT ***********9999" in client.get(f"/transactions/{t1}/edit").text


# ---------------------------------------------------------------- shown as transfers between accounts (2026-10-30)

def test_a_bank_transfer_matched_with_investment_activity_is_a_matched_transfer(client, db):
    """User's request 2026-10-31 (was its own Transfers card, 2026-10-30): a bank transfer paired with an investment
    account's own deposit is a matched transfer like any other (both rows, the Matched transfers list, the flow chart,
    Remove match); one only a destination rule places isn't matched. Savings don't change."""
    from fintrack import investment_activity, transfers
    db.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key,"
               " plaid_category_detailed) VALUES ('t3', ?, '2026-09-20', 40000, 'FAKE FUNDS', 'TRANSFER_OUT', ?)",
               (CHECKING, INTO_CODE))
    db.commit()
    sync.store_investment_transactions(db, 1, [{
        "investment_transaction_id": "i9", "account_id": f"acc-{BROKERAGE}", "security_id": None, "date": date(2026, 9, 20),
        "name": "Funds Received", "quantity": 0, "price": 0, "fees": 0, "amount": -400.0, "type": "cash",
        "subtype": "deposit", "iso_currency_code": "USD"}])
    contributions.add_destination_rule(db, BROKERAGE, "9999")              # t1: no Brokerage row of its own
    t1, t3 = (db.execute("SELECT id FROM transactions WHERE plaid_transaction_id = ?", (p,)).fetchone()[0] for p in ("t1", "t3"))
    deposit = db.execute("SELECT id FROM investment_transactions WHERE plaid_investment_transaction_id = 'i9'").fetchone()[0]
    links = contributions.investment_links(db)
    assert links[t1] == contributions.InvestmentLink(t1, BROKERAGE, "rule", None, "9999")
    assert links[t3] == contributions.InvestmentLink(t3, BROKERAGE, "activity", deposit)
    assert contributions.transfer_accounts(db) == {t1: BROKERAGE, t3: BROKERAGE}      # the logos' view, unchanged

    before = september(db)
    assert transfers.detect_transfers(db) == 1
    pair = lambda tid: tuple(db.execute("SELECT is_transfer, transfer_pair_id FROM transactions WHERE id = ?",  # noqa: E731
                                        (tid,)).fetchone())
    assert pair(t3) == (1, -deposit) and pair(t1) == (0, None)
    assert september(db) == before                                         # Savings: the same figures

    transfers_only = "account_hidden = 0 AND kind IN ('transfer', 'excluded')"
    source = investment_activity.as_transactions(db)
    flows = visuals.transfer_flows(db, transfers_only, [], source=source)["flows"]
    assert {"from": f"L:a{CHECKING}", "to": f"R:a{BROKERAGE}", "flow": 400.0, "count": 1} in flows   # drawn once
    assert [f["to"] for f in flows if f["flow"] == 600.0] != [f"R:a{BROKERAGE}"]     # the rule's: to its category
    alone = visuals.transfer_flows(db, transfers_only + " AND account_id = ?", [BROKERAGE], source=source)
    assert alone["flows"] == [{"from": f"L:a{CHECKING}", "to": f"R:a{BROKERAGE}", "flow": 400.0, "count": 1}]

    sept = "/transfers?range=custom&from=2026-09-01&to=2026-09-30"
    page = client.get(sept).text
    matched = page.split("<h2>Matched transfers</h2>")[1].split("</section>")[0]
    assert "1 transfer<" in matched and "Funds Received" in matched and "FAKE BUY" not in matched
    assert f'action="/transfers/{t3}/unmatch"' in matched and "Investment account transfers" not in page
    listing = client.get("/transactions?range=custom&from=2026-09-01&to=2026-09-30").text
    def row(tid):
        return listing.split(f'id="txn-{tid}"')[1].split("</tr>")[0]
    assert 'aria-label="Matched transfer"' in row(t3) and 'aria-label="Matched transfer"' in row(-deposit)
    assert 'aria-label="Matched transfer"' not in row(t1) and 'aria-label="Not counted"' in row(t1)

    # picking the flow on the chart (2026-10-31) lists both halves, the activity one included
    picked = client.get("/transactions?range=custom&from=2026-09-01&to=2026-09-30&kind=transfer"
                        f"&flow=a{CHECKING}%3Ea{BROKERAGE}").text.split("<tbody>")[1].split("</tbody>")[0]
    assert f'id="txn-{t3}"' in picked and f'id="txn-{-deposit}"' in picked and f'id="txn-{t1}"' not in picked

    # Remove match, by either half: the bank row is "Not a transfer", so it doesn't match again
    client.post(f"/transfers/{-deposit}/unmatch", data={"range": "custom", "from": "2026-09-01", "to": "2026-09-30"},
                headers=ORIGIN)
    assert pair(t3) == (0, None)
    transfers.detect_transfers(db)
    assert pair(t3) == (0, None)
