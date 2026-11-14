"""Savings math, month-to-date comparison, and the dashboard/savings pages."""

from datetime import date

import pytest

from fintrack import items, savings
from fintrack.web.deps import localtime

CHECKING, CARD = 1, 2


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    for acc_id, account_type in [(CHECKING, "depository"), (CARD, "credit")]:
        conn.execute(
            "INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification) VALUES (?, 1, ?, ?, ?, ?)",
            (acc_id, f"acc-{acc_id}", f"Fake {account_type}", account_type, items.classify(account_type)),
        )
    conn.commit()
    return conn


_ids = iter(range(1, 10_000))


def add(conn, account, amount, day, key):
    tid = next(_ids)
    conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key)"
        " VALUES (?, ?, ?, ?, 'Fake', ?)",
        (f"s{tid}", account, day, amount, key),
    )
    conn.commit()


# ---------------------------------------------------------------- math

def test_rate_and_saved():
    m = savings.MonthSavings("2026-09", income_cents=500000, spending_cents=350000)
    assert (m.saved_cents, m.rate) == (150000, 0.3)
    overspent = savings.MonthSavings("2026-09", income_cents=100000, spending_cents=150000)
    assert (overspent.saved_cents, overspent.rate) == (-50000, -0.5)
    assert savings.MonthSavings("2026-09", 0, 1000).rate is None   # no income: no meaningful %


def test_combined_rate_is_total_saved_over_total_income():
    months = [savings.MonthSavings("a", 1000, 0), savings.MonthSavings("b", 100000, 90000)]
    # Averaging monthly rates would give (100% + 10%) / 2 = 55%; the real rate is 11000 / 101000.
    assert savings.combined_rate(months) == pytest.approx(11000 / 101000)
    assert savings.combined_rate([savings.MonthSavings("a", 0, 5)]) is None


def test_monthly_series(db):
    add(db, CHECKING, -500000, "2026-08-01", "INCOME")
    add(db, CARD, 120000, "2026-08-15", "FOOD_AND_DRINK")
    add(db, CHECKING, 50000, "2026-08-20", "CREDIT_CARD_PAYMENT")  # not counted
    add(db, CHECKING, -500000, "2026-10-01", "INCOME")
    add(db, CARD, 20000, "2026-10-02", "TRAVEL")

    series = savings.monthly_series(db, months=4, today=date(2026, 10, 3))
    assert [m.month for m in series] == ["2026-07", "2026-08", "2026-09", "2026-10"]
    assert [(m.income_cents, m.spending_cents) for m in series] == [(0, 0), (500000, 120000), (0, 0), (500000, 20000)]
    assert [m.partial for m in series] == [False, False, False, True]
    trimmed = savings.trim_leading_empty(series)
    assert [m.month for m in trimmed] == ["2026-08", "2026-09", "2026-10"]   # empty Sep kept: it's real


def test_trim_keeps_current_month_when_no_data(db):
    series = savings.monthly_series(db, months=3, today=date(2026, 10, 3))
    assert [m.month for m in savings.trim_leading_empty(series)] == ["2026-10"]


# ---------------------------------------------------------------- month to date

def test_month_to_date_compares_same_days(db):
    add(db, CARD, 1000, "2026-09-02", "FOOD_AND_DRINK")
    add(db, CARD, 2000, "2026-09-03", "FOOD_AND_DRINK")
    add(db, CARD, 9000, "2026-09-04", "FOOD_AND_DRINK")   # after day 3: not in the comparison
    add(db, CARD, 4000, "2026-10-01", "FOOD_AND_DRINK")
    mtd = savings.month_to_date(db, today=date(2026, 10, 3))
    assert (mtd.day, mtd.spending_cents, mtd.last_month_same_period_cents, mtd.last_month_total_cents) == (
        3, 4000, 3000, 12000)


def test_month_to_date_clamps_to_shorter_month(db):
    add(db, CARD, 1000, "2026-02-28", "FOOD_AND_DRINK")
    add(db, CARD, 5000, "2026-03-31", "FOOD_AND_DRINK")
    mtd = savings.month_to_date(db, today=date(2026, 3, 31))
    assert (mtd.spending_cents, mtd.last_month_same_period_cents, mtd.last_month) == (5000, 1000, "2026-02")
    assert mtd.last_day == 28 and mtd.last_month_same_period_rate is None            # no February income


def test_month_to_date_savings_rate_over_the_same_days(db):
    """The Overview's Savings rate tile compares with the same days of last month (user's request 2026-10-23)."""
    add(db, CHECKING, -100000, "2026-09-01", "INCOME")
    add(db, CARD, 40000, "2026-09-02", "FOOD_AND_DRINK")
    add(db, CARD, 50000, "2026-09-20", "FOOD_AND_DRINK")   # after day 3: not in the comparison
    mtd = savings.month_to_date(db, today=date(2026, 10, 3))
    assert mtd.last_day == 3 and mtd.last_month_same_period_rate == pytest.approx(0.6)   # $600 of $1,000 saved


# ---------------------------------------------------------------- display helpers

def test_localtime_formats_utc_as_local(monkeypatch):
    import os
    import time

    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        assert localtime("2026-10-03 15:50:00") == "Oct 3, 11:50 AM"
        assert localtime(None) == "never"
        assert localtime("not a time") == "not a time"
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


# ---------------------------------------------------------------- pages

ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def client(settings, conn, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    app = create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher)
    return TestClient(app, base_url="https://localhost:8443")


def connect(client):
    token = client.post("/link/token/create", json={"kind": "banking"}, headers=ORIGIN).json()
    client.post("/link/complete", json={**token, "purpose": "create", "public_token": "p"}, headers=ORIGIN)


def test_dashboard_empty_state(client):
    html = client.get("/").text
    assert "Get started" in html and 'href="/connect"' in html


def test_dashboard_with_data_and_broken_connection(client, gateway):
    from tests.fakes import page, txn

    connect(client)
    gateway.sync_queue = [page(added=[txn("t1", 4.5, day=date.today().isoformat())])]
    client.post("/sync", headers=ORIGIN)
    html = client.get("/").text
    assert '<div class="hero-value">-$300</div>' in html   # net worth from the fake accounts (-$300.01, whole dollars)
    assert "Spent this month" in html and "$4.50" in html
    assert "No budgets yet" in html and "Cash flow" in html   # both cards live since M8/M9
    assert "Needs attention" not in html
    assert "link-initialize.js" not in html        # Plaid script only when something needs reconnecting

    client.post("/items/1/simulate-login-required", headers=ORIGIN)
    html = client.get("/").text
    assert "Needs attention" in html and "data-link-update" in html and "Login required" in html
    assert "link-initialize.js" in html


def test_savings_page(client, gateway):
    from tests.fakes import page, txn

    connect(client)
    today = date.today().isoformat()
    gateway.sync_queue = [page(added=[
        txn("t1", -1000.0, day=today, name="Fake Payroll", merchant=None, primary="INCOME"),
        txn("t2", 250.0, day=today),
    ])]
    client.post("/sync", headers=ORIGIN)
    html = client.get("/savings").text
    assert "75%" in html                           # (1000 - 250) / 1000
    assert "$750.00" in html and "(so far)" in html
    assert "Net savings rate" in html and "Income vs spending" not in html
    assert 'data-chart="ratebars"' not in html                       # the rate bars beside the table were removed
    assert "Where income went" not in html and "Saved vs invested" not in html     # removed 2026-10-11


# ---------------------------------------------------------------- the date range (user's request 2026-10-11)

def test_range_series_clips_the_end_months_to_the_range(db):
    add(db, CHECKING, -300000, "2026-08-05", "INCOME")                 # before the range starts: left out
    add(db, CHECKING, -100000, "2026-08-25", "INCOME")
    add(db, CARD, 20000, "2026-09-10", "FOOD_AND_DRINK")
    add(db, CHECKING, -100000, "2026-10-01", "INCOME")
    add(db, CARD, 50000, "2026-10-15", "FOOD_AND_DRINK")              # after "today": left out
    series = savings.range_series(db, "2026-08-20", "2026-10-11", today=date(2026, 10, 11))
    assert [m.month for m in series] == ["2026-08", "2026-09", "2026-10"]
    assert (series[0].income_cents, series[0].start, series[0].end) == (100000, "2026-08-20", "")   # Aug 20-31 only
    assert series[0].clipped_days == (20, 31) and series[1].clipped_days is None
    assert (series[1].spending_cents, series[1].start, series[1].end) == (20000, "", "")            # a whole month
    assert series[2].partial and series[2].end == "" and series[2].spending_cents == 0              # in progress: "so far"
    # a range ending inside a past month marks that month's last day counted
    past = savings.range_series(db, "2026-09-01", "2026-09-15", today=date(2026, 10, 11))
    assert len(past) == 1 and past[0].end == "2026-09-15" and not past[0].partial and past[0].clipped_days == (1, 15)


def test_savings_page_follows_the_date_range(client, gateway):
    from tests.fakes import page, txn

    connect(client)
    today = date.today()
    gateway.sync_queue = [page(added=[txn("t1", -1000.0, day=today.isoformat(), name="Fake Payroll", merchant=None,
                                          primary="INCOME")])]
    client.post("/sync", headers=ORIGIN)
    html = client.get("/savings").text                                  # default: 365 days
    assert 'id="tx-range"' in html and 'name="range" form="txn-filters" value="365"' in html
    assert 'action="/savings"' in html and ">365 days<" in html
    html = client.get("/savings?range=last-year").text                  # nothing then: the tile has no rate
    assert 'value="last-year"' in html and f'value="{today.year - 1}-01-01"' in html and '$0.00 <span class="w-long">saved in </span>total' in html
    html = client.get(f"/savings?range=custom&from={today.isoformat()}&to={today.isoformat()}").text
    assert 'value="custom"' in html and '$1,000.00 <span class="w-long">saved in </span>total' in html
