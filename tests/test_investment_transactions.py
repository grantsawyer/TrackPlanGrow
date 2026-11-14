"""Investment transactions (migration 0023): storing them, syncing them only when allowed, the Plaid call, and
counting contributions once when a transfer shows on both the bank side and the investment side."""

from dataclasses import replace
from datetime import date

import pytest

from fintrack import contributions, items, sync
from fintrack.config import load_settings
from fintrack.contributions import INTO_CODE, OUT_CODE
from fintrack.plaid_client import PlaidError, PlaidGateway
from tests.fakes import FakeGateway

TODAY = date(2026, 10, 9)
CHECKING, BROKERAGE, HIDDEN = 1, 2, 3


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name, products)"
                 " VALUES (1, 'i1', 'sandbox', 'Fake Bank', '[\"investments\", \"transactions\"]')")
    for acc_id, account_type, hidden in [(CHECKING, "depository", 0), (BROKERAGE, "investment", 0), (HIDDEN, "investment", 1)]:
        conn.execute(
            "INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification, is_hidden)"
            " VALUES (?, 1, ?, ?, ?, ?, ?)",
            (acc_id, f"acc-{acc_id}", f"Fake {account_type}", account_type, items.classify(account_type), hidden))
    conn.commit()
    return conn


def inv(txn_id, account, day, name, kind, subtype, amount):
    """An investment transaction as the SDK returns it (dates as date objects)."""
    return {"investment_transaction_id": txn_id, "account_id": f"acc-{account}", "security_id": "sec-1",
            "date": date.fromisoformat(day), "name": name, "quantity": amount, "price": 1.0, "fees": 0.0,
            "amount": amount, "type": kind, "subtype": subtype, "iso_currency_code": "USD"}


_ids = iter(range(1, 100_000))


def bank(conn, cents, day, code, user_category=None):
    tid = next(_ids)
    primary = "TRANSFER_OUT" if code == INTO_CODE else "TRANSFER_IN"
    conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_primary,"
        " plaid_category_detailed, plaid_category_key, user_category) VALUES (?, ?, ?, ?, 'FAKE', ?, ?, ?, ?)",
        (f"b{tid}", CHECKING, day, cents, primary, code, primary, user_category))
    conn.commit()


def store(conn, *transactions):
    return sync.store_investment_transactions(conn, 1, list(transactions))


def totals(conn):
    return {m.month: (m.into_cents, m.out_cents) for m in contributions.monthly(conn, 12, TODAY) if m.into_cents or m.out_cents}


# ---------------------------------------------------------------- storing

def test_store_keeps_plaids_sign_and_plain_text_types(db):
    assert store(db, inv("t1", BROKERAGE, "2026-09-18", "EMPLOYER PAYROLL CONTRIBUTION", "cash", "contribution", -1500.0),
                 inv("t2", BROKERAGE, "2026-09-19", "BUY FUND", "buy", "buy", 1500.0)) == 2
    rows = {r["plaid_investment_transaction_id"]: r for r in db.execute("SELECT * FROM investment_transactions")}
    assert (rows["t1"]["type"], rows["t1"]["subtype"], rows["t1"]["amount_cents"], rows["t1"]["date"]) == ("cash", "contribution", -150000, "2026-09-18")
    assert rows["t2"]["amount_cents"] == 150000 and rows["t2"]["fees_cents"] == 0


def test_store_updates_in_place_and_a_quiet_resync_changes_nothing(db):
    first = inv("t1", BROKERAGE, "2026-09-18", "CONTRIB", "cash", "contribution", -100.0)
    store(db, first)
    revision = db.execute("SELECT n FROM app_revision").fetchone()[0]
    store(db, first)                                                       # identical: no write, so open tabs don't reload
    assert db.execute("SELECT n FROM app_revision").fetchone()[0] == revision
    store(db, {**first, "amount": -250.0})                                 # a correction
    assert db.execute("SELECT COUNT(*), MAX(amount_cents) FROM investment_transactions").fetchone()[:] == (1, -25000)
    assert db.execute("SELECT n FROM app_revision").fetchone()[0] > revision


def test_store_skips_accounts_that_arent_this_items(db):
    assert store(db, inv("t1", 99, "2026-09-18", "NOPE", "cash", "contribution", -1.0)) == 0


def test_start_date_is_the_full_history_first_then_a_month_before_the_newest(db):
    assert sync.investment_transactions_start(db, 1, TODAY) == date(2024, 10, 9)          # 730 days back
    store(db, inv("t1", BROKERAGE, "2026-08-20", "C", "cash", "contribution", -1.0))
    assert sync.investment_transactions_start(db, 1, TODAY) == date(2026, 7, 21)           # 30 days before the newest


# ---------------------------------------------------------------- syncing only when allowed

class InvestmentGateway(FakeGateway):
    def __init__(self):
        super().__init__()
        self.products = ["investments"]
        self.investment_transactions_payload = [inv("t1", "x", "2026-09-18", "CONTRIB", "cash", "contribution", -500.0)]   # -> account "acc-x"

    def get_accounts(self, access_token):
        return [{"account_id": "acc-x", "name": "Fake IRA", "official_name": None, "mask": "0001", "type": "investment",
                 "subtype": "ira", "balances": {"current": 1000.0, "available": None, "iso_currency_code": "USD"}}]

    def get_holdings(self, access_token):
        return {"holdings": [], "securities": []}


@pytest.fixture
def sync_db(conn, cipher):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name, access_token_enc, products)"
                 " VALUES (1, 'i1', 'sandbox', 'Fake Bank', ?, '[\"investments\"]')", (cipher.encrypt("access-sandbox-fake"),))
    conn.commit()
    return conn


def called(gateway):
    return [c for c in gateway.calls if c[0] == "get_investment_transactions"]


def test_sync_reads_investment_transactions_only_when_told_to(sync_db, cipher):
    gateway = InvestmentGateway()
    off = sync.sync_item(sync_db, gateway, cipher, 1)                                       # default: off (Production's default)
    assert called(gateway) == [] and off.investment_transactions == 0
    on = sync.sync_item(sync_db, gateway, cipher, 1, investment_transactions=True)
    assert len(called(gateway)) == 1 and on.investment_transactions == 1 and not on.error
    assert sync_db.execute("SELECT COUNT(*) FROM investment_transactions").fetchone()[0] == 1


def test_sync_never_asks_an_item_without_the_investments_product(sync_db, cipher):
    sync_db.execute("UPDATE items SET products = '[\"transactions\"]'")
    sync_db.commit()
    gateway = InvestmentGateway()
    sync.sync_item(sync_db, gateway, cipher, 1, investment_transactions=True)
    assert called(gateway) == []                                   # asking would add a subscription it doesn't have


def test_a_plaid_error_on_investment_transactions_does_not_fail_the_item(sync_db, cipher):
    gateway = InvestmentGateway()
    gateway.investment_transactions_error = PlaidError("PRODUCTS_NOT_SUPPORTED", "nope")
    result = sync.sync_item(sync_db, gateway, cipher, 1, investment_transactions=True)
    assert result.error is None and "investment transactions unavailable (PRODUCTS_NOT_SUPPORTED)" in result.note
    gateway.investment_transactions_error = PlaidError("ITEM_LOGIN_REQUIRED", "log in")
    assert sync.sync_item(sync_db, gateway, cipher, 1, investment_transactions=True).error == "ITEM_LOGIN_REQUIRED"


def test_sync_all_passes_the_setting_through(sync_db, cipher, home):
    gateway = InvestmentGateway()
    sync.sync_all(sync_db, gateway, cipher, home)
    assert called(gateway) == []
    sync.sync_all(sync_db, gateway, cipher, home, investment_transactions=True)
    assert len(called(gateway)) == 1


# ---------------------------------------------------------------- the production opt-in

def test_investment_transactions_are_on_in_the_sandbox_and_opt_in_in_production(settings, prod_settings):
    assert settings.sync_investment_transactions is True                                    # Sandbox: free
    assert prod_settings.sync_investment_transactions is False                              # Production: a billed subscription
    assert replace(prod_settings, investment_transactions_opt_in=True).sync_investment_transactions is True


@pytest.mark.parametrize("value, expected", [("on", True), ("1", True), ("true", True), ("OFF", False), ("", False), ("no", False)])
def test_env_setting_parses(home, monkeypatch, value, expected):
    (home / ".env").write_text("PLAID_ENV=production\nPLAID_CLIENT_ID=c\nPLAID_SECRET_PRODUCTION=s\n"
                               + (f"INVESTMENT_TRANSACTIONS={value}\n" if value else ""))
    assert load_settings().sync_investment_transactions is expected


# ---------------------------------------------------------------- the Plaid call

class Page:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


class StubApi:
    def __init__(self, total):
        self.total, self.requests = total, []

    def investments_transactions_get(self, request):
        offset = request.options.offset
        self.requests.append((request.access_token, request.options.count, offset, request.start_date, request.end_date))
        size = min(500, self.total - offset)
        return Page({"investment_transactions": [{"investment_transaction_id": f"t{offset + i}"} for i in range(size)],
                     "total_investment_transactions": self.total})


def test_gateway_reads_every_page_of_investment_transactions():
    gateway = object.__new__(PlaidGateway)
    gateway._api = StubApi(total=1_130)
    found = gateway.get_investment_transactions("access-x", date(2024, 10, 9), date(2026, 10, 9))
    assert len(found) == 1_130 and found[-1]["investment_transaction_id"] == "t1129"
    assert [r[2] for r in gateway._api.requests] == [0, 500, 1000] and all(r[1] == 500 for r in gateway._api.requests)
    empty = object.__new__(PlaidGateway)
    empty._api = StubApi(total=0)
    assert empty.get_investment_transactions("access-x", date(2024, 10, 9), date(2026, 10, 9)) == []


# ---------------------------------------------------------------- counting a contribution once

def test_a_transfer_seen_on_both_sides_counts_once(db):
    bank(db, 185000, "2026-09-16", INTO_CODE)                                                 # leaves checking
    store(db, inv("t1", BROKERAGE, "2026-09-18", "DEPOSIT", "cash", "contribution", -1850.0))  # arrives two days later
    assert totals(db) == {"2026-09": (185000, 0)}


def test_investment_only_contributions_count_even_though_the_bank_never_saw_them(db):
    bank(db, 185000, "2026-09-16", INTO_CODE)
    store(db, inv("t1", BROKERAGE, "2026-09-18", "DEPOSIT", "cash", "contribution", -1850.0),
          inv("t2", BROKERAGE, "2026-09-15", "EMPLOYER PAYROLL CONTRIBUTION", "cash", "contribution", -400.0),
          inv("t3", BROKERAGE, "2026-10-01", "EMPLOYEE DEFERRAL", "cash", "deposit", -250.0))
    assert totals(db) == {"2026-09": (185000 + 40000, 0), "2026-10": (25000, 0)}


def test_a_bank_transfer_with_no_matching_deposit_still_counts(db):
    bank(db, 70000, "2026-09-09", INTO_CODE)                                                  # to an account we can't see inside
    store(db, inv("t1", BROKERAGE, "2026-09-18", "DEPOSIT", "cash", "contribution", -1850.0))  # a different amount
    assert totals(db) == {"2026-09": (70000 + 185000, 0)}


def test_pairs_need_the_same_amount_and_to_be_within_five_days(db):
    bank(db, 100000, "2026-09-01", INTO_CODE)
    store(db, inv("t1", BROKERAGE, "2026-09-07", "DEPOSIT", "cash", "contribution", -1000.0))        # six days: not the same event
    assert totals(db) == {"2026-09": (200000, 0)}


def test_one_deposit_pairs_with_only_one_bank_transfer(db):
    bank(db, 100000, "2026-09-01", INTO_CODE)
    bank(db, 100000, "2026-09-02", INTO_CODE)
    store(db, inv("t1", BROKERAGE, "2026-09-03", "DEPOSIT", "cash", "contribution", -1000.0))
    assert totals(db) == {"2026-09": (200000, 0)}                                              # two transfers, one deposit seen


def test_withdrawals_pair_too_and_are_dated_by_the_investment_side(db):
    bank(db, -300000, "2026-09-02", OUT_CODE)                                                 # arrives in checking
    store(db, inv("t1", BROKERAGE, "2026-08-31", "WITHDRAWAL", "cash", "withdrawal", 3000.0))   # left the account two days earlier
    assert totals(db) == {"2026-08": (0, 300000)}


def test_trades_dividends_interest_and_fees_are_not_new_money(db):
    store(db, inv("t1", BROKERAGE, "2026-09-02", "BUY", "buy", "buy", 5000.0), inv("t2", BROKERAGE, "2026-09-03", "SELL", "sell", "sell", -5000.0),
          inv("t3", BROKERAGE, "2026-09-04", "DIVIDEND", "cash", "dividend", -40.0), inv("t4", BROKERAGE, "2026-09-05", "INTEREST", "cash", "interest", -2.0),
          inv("t5", BROKERAGE, "2026-09-06", "FEE", "fee", "account fee", 3.0))
    assert totals(db) == {}


def test_hidden_accounts_investment_activity_is_left_out(db):
    store(db, inv("t1", HIDDEN, "2026-09-18", "DEPOSIT", "cash", "contribution", -1850.0))
    assert totals(db) == {}


def test_without_investment_transactions_the_bank_side_is_unchanged(db):
    bank(db, 185000, "2026-09-16", INTO_CODE)                                                 # Production's default: nothing synced
    assert totals(db) == {"2026-09": (185000, 0)}


def test_cash_sweeps_inside_an_account_are_not_money_in_or_out(db):
    # a sweep between the settlement fund and holdings comes back as deposit/withdrawal; only the real movements count
    store(db, inv("t1", BROKERAGE, "2026-09-10", "Sweep out", "cash", "deposit", -5000.0),
          inv("t2", BROKERAGE, "2026-09-10", "Withdrawal", "cash", "withdrawal", 5000.0),
          inv("t3", BROKERAGE, "2026-09-11", "SWEEP IN", "cash", "withdrawal", 123.45),
          inv("t4", BROKERAGE, "2026-09-12", "Funds Received", "cash", "deposit", -250.0))
    assert totals(db) == {"2026-09": (25000, 500000)}
