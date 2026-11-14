"""Transfers into and out of investment and retirement accounts (contributions.py, the Portfolio page's chart)."""

from datetime import date

import pytest

from fintrack import contributions, items, visuals
from fintrack.contributions import INTO_CODE, OUT_CODE

TODAY = date(2026, 10, 9)
CHECKING, CARD, BROKERAGE, HIDDEN = 1, 2, 3, 4


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    for acc_id, account_type, hidden in [(CHECKING, "depository", 0), (CARD, "credit", 0), (BROKERAGE, "investment", 0),
                                         (HIDDEN, "depository", 1)]:
        conn.execute(
            "INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification, is_hidden)"
            " VALUES (?, 1, ?, ?, ?, ?, ?)",
            (acc_id, f"acc-{acc_id}", f"Fake {account_type}", account_type, items.classify(account_type), hidden))
    conn.commit()
    return conn


_ids = iter(range(1, 100_000))


def add(conn, account, cents, day, detailed, primary=None, user_category=None):
    tid = next(_ids)
    primary = primary or detailed.split("_INVESTMENT")[0]          # TRANSFER_OUT / TRANSFER_IN
    conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_primary,"
        " plaid_category_detailed, plaid_category_key, user_category) VALUES (?, ?, ?, ?, 'FAKE', ?, ?, ?, ?)",
        (f"c{tid}", account, day, cents, primary, detailed, primary, user_category))
    conn.commit()


def test_monthly_sums_both_directions_and_fills_empty_months(db):
    add(db, CHECKING, 150000, "2026-08-16", INTO_CODE)
    add(db, CHECKING, 50000, "2026-08-20", INTO_CODE)
    add(db, CHECKING, 150000, "2026-09-16", INTO_CODE)
    add(db, CHECKING, -300000, "2026-09-12", OUT_CODE)          # money in to the bank = out of investments
    add(db, CHECKING, 999, "2025-10-05", INTO_CODE)             # before the 12-month window (Nov 2025 - Oct 2026)
    series = contributions.monthly(db, 12, TODAY)
    assert [m.month for m in series][0] == "2025-11" and series[-1].month == "2026-10" and len(series) == 12
    by_month = {m.month: m for m in series}
    assert (by_month["2026-08"].into_cents, by_month["2026-08"].out_cents) == (200000, 0)
    assert (by_month["2026-09"].into_cents, by_month["2026-09"].out_cents) == (150000, 300000)
    assert by_month["2026-09"].net_cents == -150000
    assert all(m.into_cents == m.out_cents == 0 for m in series if m.month not in ("2026-08", "2026-09"))


def test_only_plaids_investment_transfer_categories_count(db):
    add(db, CHECKING, 777700, "2026-09-05", "TRANSFER_OUT_ACCOUNT_TRANSFER", primary="TRANSFER_OUT")
    add(db, CHECKING, 777700, "2026-09-06", "TRANSFER_OUT_TRANSFER_OUT_FROM_APPS", primary="TRANSFER_OUT")
    add(db, CHECKING, 100000, "2026-09-07", INTO_CODE)
    assert {m.month: m.into_cents for m in contributions.monthly(db, 12, TODAY)}["2026-09"] == 100000


def test_reversals_net_out(db):
    add(db, CHECKING, 100000, "2026-09-07", INTO_CODE)
    add(db, CHECKING, -100000, "2026-09-09", INTO_CODE)          # sent back
    add(db, CHECKING, 100000, "2026-09-10", INTO_CODE)
    assert {m.month: m.into_cents for m in contributions.monthly(db, 12, TODAY)}["2026-09"] == 100000


def test_investment_accounts_hidden_accounts_and_your_recategorizing(db):
    add(db, BROKERAGE, -100000, "2026-09-07", INTO_CODE)                   # the investment side of a transfer: not counted again
    add(db, HIDDEN, 100000, "2026-09-07", INTO_CODE)                       # a removed connection
    add(db, CHECKING, 100000, "2026-09-08", INTO_CODE, user_category="FOOD_AND_DRINK")   # you said it's not a transfer
    add(db, CHECKING, 100000, "2026-09-09", INTO_CODE, user_category="TRANSFER_OUT")     # still a transfer category: counts
    add(db, CARD, 50000, "2026-09-10", INTO_CODE)                          # money from a card account does count (not an investment account)
    assert {m.month: m.into_cents for m in contributions.monthly(db, 12, TODAY)}["2026-09"] == 150000


def test_chart_data_is_empty_without_transfers_and_in_dollars_otherwise(db):
    assert visuals.investment_transfers(db, 12, TODAY) == {"labels": [], "into": {}, "out": {}, "has_into": False, "has_out": False}
    add(db, CHECKING, 123456, "2026-09-16", INTO_CODE)
    add(db, CHECKING, -50000, "2026-10-02", OUT_CODE)
    chart = visuals.investment_transfers(db, 12, TODAY)
    assert len(chart["labels"]) == 12 and chart["labels"][-2:] == ["2026-09", "2026-10"]
    # no description says "retirement", so both are investment-account transfers; the retirement lists exist and are zero
    assert chart["into"]["investment"][-2:] == [1234.56, 0.0] and chart["out"]["investment"][-2:] == [0.0, 500.0]
    assert chart["into"]["retirement"] == [0.0] * 12 and chart["out"]["retirement"] == [0.0] * 12
    assert chart["has_into"] and chart["has_out"]


# ---------------------------------------------------------------- the page

@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


@pytest.mark.parametrize("path", ["/portfolio"])           # removed from Savings 2026-10-12 (user's request)
def test_portfolio_and_savings_pages_show_the_chart_or_a_plain_note(client, db, path):
    page = client.get(path).text
    assert "Transfers in" in page and "Transfers out" in page and 'data-chart="transfers"' not in page
    assert "No transfers into investment" in page and "No transfers out of investment" in page
    add(db, CHECKING, 150000, date.today().isoformat(), INTO_CODE)
    page = client.get(path).text
    # money in only: its own chart; the out card keeps its note
    assert 'id="inv-transfers-in"' in page and 'data-direction="into"' in page
    assert 'id="inv-transfers-out"' not in page and "No transfers out of investment" in page


# ---------------------------------------------------------------- retirement accounts vs other investment accounts

@pytest.mark.parametrize("text, group", [
    ("TRANSFER TO DEMO ROTH IRA", "retirement"), ("Contribution - Traditional IRA", "retirement"), ("401(k) CONTRIBUTION", "retirement"),
    ("401K DEFERRAL", "retirement"), ("403(b) payroll", "retirement"), ("457 (b) plan", "retirement"), ("HSA FUNDING", "retirement"),
    ("ROLLOVER FROM OLD EMPLOYER", "retirement"), ("RETIREMENT SAVINGS", "retirement"),
    ("TRANSFER TO DEMO BROKERAGE", "investment"), ("TRANSFER TO INVESTMENT ACCOUNT", "investment"), ("SIMPLE SAVINGS TRANSFER", "investment"),
    ("SEPTEMBER TRANSFER", "investment"), ("REF 457 TRANSFER", "investment"), ("IRATE CUSTOMER REFUND", "investment"), ("", "investment"),
])
def test_a_bank_transfer_is_assigned_by_its_description(text, group):
    assert contributions.group_of_text(text, None) == group
    assert contributions.group_of_text("TRANSFER", text) == group          # the merchant name counts too


def bank_named(conn, cents, day, name, code=INTO_CODE):
    tid = next(_ids)
    primary = "TRANSFER_OUT" if code == INTO_CODE else "TRANSFER_IN"
    conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_primary,"
        " plaid_category_detailed, plaid_category_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (f"n{tid}", CHECKING, day, cents, name, primary, code, primary))
    conn.commit()


def month_flow(conn, month):
    return next(m for m in contributions.monthly(conn, 12, TODAY) if m.month == month)


def test_unpaired_bank_transfers_are_split_by_description(db):
    bank_named(db, 700000, "2026-09-09", "TRANSFER TO ROTH IRA")
    bank_named(db, 185000, "2026-09-16", "TRANSFER TO BROKERAGE")
    bank_named(db, -120000, "2026-09-20", "TRANSFER FROM 401K", OUT_CODE)
    m = month_flow(db, "2026-09")
    assert m.into == {"retirement": 700000, "investment": 185000} and m.out == {"retirement": 120000, "investment": 0}
    assert (m.into_cents, m.out_cents, m.net_cents) == (885000, 120000, 765000)


@pytest.fixture
def two_kinds(conn):
    """A brokerage and an IRA (both visible), plus a Plaid 'other' account you gave the type Retirement › 401(k)."""
    from fintrack import account_types

    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    for acc_id, account_type, subtype in [(1, "depository", "checking"), (2, "investment", "brokerage"), (3, "investment", "ira"),
                                          (4, "investment", "other")]:
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, subtype, classification)"
                     " VALUES (?, 1, ?, ?, ?, ?, 'asset')", (acc_id, f"acc-{acc_id}", f"Fake {subtype}", account_type, subtype))
    conn.commit()
    node = conn.execute("SELECT n.id FROM account_type_nodes n JOIN account_type_nodes p ON p.id = n.parent_id"
                        " WHERE p.name = 'Retirement' AND n.name = ?", ('401(k)',)).fetchone()[0]
    account_types.assign(conn, 4, node)                    # your type decides (account categories were removed)
    return conn


def test_deposits_inside_accounts_are_assigned_by_the_account(two_kinds):
    from tests.test_investment_transactions import inv, store

    store(two_kinds, inv("t1", 2, "2026-09-18", "DEPOSIT", "cash", "contribution", -1850.0),                # brokerage
          inv("t2", 3, "2026-09-15", "EMPLOYER MATCH", "cash", "contribution", -400.0),                    # IRA: a retirement type
          inv("t3", 4, "2026-09-10", "PAYROLL", "cash", "contribution", -250.0),                           # your Retirement › 401(k) type wins
          inv("t4", 2, "2026-09-25", "WITHDRAWAL", "cash", "withdrawal", 300.0))
    m = month_flow(two_kinds, "2026-09")
    assert m.into == {"retirement": 40000 + 25000, "investment": 185000} and m.out == {"retirement": 0, "investment": 30000}


def test_a_pair_takes_the_investment_accounts_kind_not_the_descriptions(two_kinds):
    from tests.test_investment_transactions import inv, store

    # the bank text says nothing about retirement, but the deposit landed in the IRA, so that is where it went
    two_kinds.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_primary,"
                      " plaid_category_detailed, plaid_category_key) VALUES ('x', 1, '2026-09-16', 50000, 'TRANSFER 7734', 'TRANSFER_OUT', ?, 'TRANSFER_OUT')",
                      (INTO_CODE,))
    two_kinds.commit()
    store(two_kinds, inv("t1", 3, "2026-09-17", "DEPOSIT", "cash", "contribution", -500.0))
    m = month_flow(two_kinds, "2026-09")
    assert m.into == {"retirement": 50000, "investment": 0}                      # counted once, as retirement


def test_a_date_range_counts_only_transfers_inside_it(db):
    add(db, CHECKING, 100000, "2026-08-31", INTO_CODE)                 # the day before the range
    add(db, CHECKING, 200000, "2026-09-01", INTO_CODE)
    add(db, CHECKING, -50000, "2026-09-20", OUT_CODE)                  # the day after the range
    flows = contributions.between(db, date(2026, 9, 1), date(2026, 9, 19))
    assert [f.month for f in flows] == ["2026-09"]
    assert (flows[0].into_cents, flows[0].out_cents) == (200000, 0)
    chart = visuals.investment_transfers(db, start="2026-08-15", end="2026-09-30")
    assert chart["labels"] == ["2026-08", "2026-09"]
    assert chart["into"]["investment"] == [1000.0, 2000.0] and chart["out"]["investment"] == [0.0, 500.0]


def test_the_savings_page_no_longer_shows_the_transfer_cards(client):
    page = client.get("/savings").text
    assert "Transfers in" not in page and "Transfers out" not in page
