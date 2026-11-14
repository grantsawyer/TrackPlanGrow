"""Cash-flow projection and month-end projection (fixed dates: deterministic)."""
import re

import json
from datetime import date, timedelta

import pytest

from fintrack import forecast, items, money, recurring

TODAY = date(2026, 10, 3)
CHECKING, SAVINGS = 1, 2


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    for acc_id, name, balance in [(CHECKING, "Fake Checking", 200000), (SAVINGS, "Fake Savings", 1000000)]:
        conn.execute(
            "INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification, current_balance_cents)"
            " VALUES (?, 1, ?, ?, 'depository', 'asset', ?)",
            (acc_id, f"acc-{acc_id}", name, balance),
        )
    conn.commit()
    return conn


_ids = iter(range(1, 1_000_000))


def add(conn, day, cents, merchant, account=CHECKING, key="FOOD_AND_DRINK"):
    tid = next(_ids)
    conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, merchant_name, plaid_category_key)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (f"f{tid}", account, day.isoformat(), cents, merchant.upper(), merchant, key),
    )
    conn.commit()


def item(conn, name, cents, frequency, next_date, account=CHECKING, category=None, dom=None, merchant=None):
    conn.execute(
        """INSERT INTO recurring_items (name, merchant_key, account_id, category_key, amount_cents, frequency,
               days_of_month, next_expected_date, type, status, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?)""",
        (name, recurring.merchant_key(merchant, None) if merchant else None, account, category, cents, frequency, dom,
         next_date.isoformat(), "income" if cents < 0 else "bill", "local" if merchant else "manual"),
    )
    conn.commit()


def steady_history(conn, daily_cents=1000, days=90, merchant="Fake Grocer", account=CHECKING, key="FOOD_AND_DRINK"):
    """`days` days of identical daily spending ending yesterday."""
    for k in range(1, days + 1):
        add(conn, TODAY - timedelta(days=k), daily_cents, merchant, account, key)


# ---------------------------------------------------------------- cash flow

def test_only_known_events_move_the_balance(db):
    steady_history(db, daily_cents=1000)          # lots of past spending: NOT projected (no trend)
    item(db, "Paycheck", -450000, "semimonthly", date(2026, 10, 15), dom="1,15")
    item(db, "Rent", 145000, "monthly", date(2026, 11, 1), dom="1")
    flow = forecast.cash_flow(db, TODAY)
    checking = next(a for a in flow.accounts if a.account_id == CHECKING)
    assert checking.at(0) == 200000
    assert checking.at(11) == 200000                                            # Oct 14: flat until payday
    assert checking.at(12) == 200000 + 450000                                   # Oct 15: paid
    # Day 29 = Nov 1: both a payday and rent day (paid Oct 15 and Nov 1).
    assert checking.at(29) == 200000 + 450000 + 450000 - 145000
    savings = next(a for a in flow.accounts if a.account_id == SAVINGS)
    assert {cents for _, cents in savings.points} == {1000000}                 # no events: flat line


def test_overdue_bill_hits_today_but_late_paycheck_not_assumed(db):
    item(db, "Late bill", 30000, "monthly", date(2026, 10, 1), dom="1")
    item(db, "Late pay", -100000, "monthly", date(2026, 10, 2), dom="2")
    checking = next(a for a in forecast.cash_flow(db, TODAY).accounts if a.account_id == CHECKING)
    assert checking.at(0) == 200000                                             # today: the actual balance
    assert checking.at(1) == 200000 - 30000                                     # late bill assumed by tomorrow
    assert checking.at(28) == 200000 - 30000                                    # Oct 31: late pay NOT assumed
    assert checking.at(29) == 200000 - 30000 - 30000                            # Nov 1: next bill
    assert checking.at(30) == 200000 - 30000 - 30000 + 100000                   # Nov 2: next paycheck


def test_low_balance_warning(db):
    item(db, "Rent", 120000, "monthly", date(2026, 10, 20), dom="20")          # $1,200 from $2,000
    forecast.set_low_balance_threshold(db, "$1,000")
    flow = forecast.cash_flow(db, TODAY)
    checking = next(a for a in flow.accounts if a.account_id == CHECKING)
    assert checking.first_below == date(2026, 10, 20)
    assert checking.lowest == (date(2026, 12, 20), 200000 - 3 * 120000)        # Oct 20, Nov 20, Dec 20
    assert [a.account_id for a in flow.warnings] == [CHECKING]                 # savings fine
    assert flow.combined.at(90) == checking.at(90) + 1000000


def test_threshold_validation(db):
    assert forecast.low_balance_threshold(db) == 50000
    with pytest.raises(forecast.ForecastError):
        forecast.set_low_balance_threshold(db, "-5")


# ---------------------------------------------------------------- pages

ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_recurring_review_flow_and_forecast_pages(client, db):
    real_today = date.today()
    for k in range(4):   # a monthly bill ending this month, relative to the real date
        add(db, recurring._add_months(real_today, -k, real_today.day) - timedelta(days=1), 5000, "Fake Gym", key="PERSONAL_CARE")
    client.post("/recurring/refresh", headers=ORIGIN)
    assert "Needs review (1)" in client.get("/recurring").text
    assert "1 recurring item to review" in client.get("/").text

    item_id = db.execute("SELECT id FROM recurring_items").fetchone()[0]
    client.post(f"/recurring/{item_id}/status", data={"status": "confirmed"}, headers=ORIGIN)
    page = client.get("/recurring").text
    assert "Confirmed (1)" in page and "Fake Gym" in page
    bills = page.split("<h2>Recurring expenses this month</h2>")[1].split("</section>")[0]    # top charts (2026-10-16)
    assert 'id="rc-bills" data-chart="daybars"' in bills and 'data-direction="out"' in bills and "Fake Gym" in bills
    assert '<span class="measure-value">$50</span>\n        <span class="kpi-label">Total expenses</span>' in bills
    income = page.split("<h2>Recurring income this month</h2>")[1].split("</section>")[0]
    assert '<span class="measure-value">$0</span>' in income and "Total income" in income
    assert "No confirmed recurring income this month." in income

    client.post(f"/recurring/{item_id}/status", data={"status": "dismissed"}, headers=ORIGIN)
    assert "Dismissed (1)" in client.get("/recurring").text


def test_add_manual_item_via_form(client, db):
    bad = client.post("/recurring", data={"name": "Rent", "amount": "1800", "direction": "out",
                                          "frequency": "semimonthly", "next_date": "2026-11-01"},
                      headers=ORIGIN, follow_redirects=False)
    assert "two+days" in bad.headers["location"]
    client.post("/recurring", data={"name": "Rent", "amount": "1,450", "direction": "out", "frequency": "monthly",
                                    "next_date": "2026-11-01", "account_id": str(CHECKING),
                                    "category_key": "RENT_AND_UTILITIES"}, headers=ORIGIN)
    assert "added by you" in client.get("/recurring").text


def test_threshold_on_cash_flow_and_next_30_days_on_recurring(client, db):
    """Recurring's cash-flow section was removed (user's request 2026-10-16): its "Next 30 days" table stays on
    Recurring, the low-balance threshold moved to the Cash flow page's ribbon, /forecast lands on Cash flow."""
    real_today = date.today()
    soon = real_today + timedelta(days=5)
    item(db, "Fake Rent", 150000, "monthly", soon, dom=str(soon.day))   # $1,500 bill: $2,000 -> $500
    saved = client.post("/forecast/threshold", data={"amount": "1000"}, headers=ORIGIN, follow_redirects=False)
    assert saved.headers["location"].startswith("/cash-flow?msg=")    # back to where the form lives
    cash_flow = client.get("/cash-flow").text
    ribbon, main = cash_flow.split('<header class="tabbar">')[1].split("</header>")
    # the threshold is in the chart's header; the ribbon holds the 30 / 90 / 180-day range (user's request 2026-10-23)
    chart_head = main.split("<h2>Projected balance</h2>")[1].split('id="cf-balance"')[0]     # history is first (2026-10-24)
    assert 'action="/forecast/threshold"' in chart_head and 'value="1000"' in chart_head
    # saves on change, no Save button outside <noscript>; no legends on this page's charts (2026-10-24)
    assert "data-autosubmit" in chart_head and ">Save</button>" not in chart_head.split("<noscript>")[0]
    assert '"legend": false' in main.split('id="cf-balance-data">')[1].split("</script>")[0]
    # the ribbon: the cash account and 30 / 90 / 180-day links (user's requests 2026-10-23/24)
    assert 'action="/forecast/threshold"' not in ribbon and 'name="account"' in ribbon
    assert [f'days={d}"' in ribbon for d in (30, 90, 180)] == [True] * 3 and 'days=30" aria-current="true"' in ribbon
    page = client.get("/recurring").text
    upcoming = page.split("<h2>Next 30 days</h2>")[1].split("</section>")[0]
    assert "Fake Rent" in upcoming and "-$1,500.00" in upcoming
    assert "Cash flow: next 90 days" not in page and "Warn below" not in page and "forecast.js" not in page
    assert "<h2>Next 60 days</h2>" not in page and "Bills per month by category" not in page
    old = client.get("/forecast", params={"account": "all"}, follow_redirects=False)
    assert old.status_code == 307 and old.headers["location"] == "/cash-flow"
    dash = " ".join(client.get("/").text.split())
    assert "Fake Checking below $1,000.00 on" in dash



def test_cash_flow_page_has_the_overview_balance_chart(client, db):
    """Cash flow (Analysis section since 2026-10-25; user's requests 2026-10-14): the Overview's projected balance chart (same data)
    and its "Coming up" list below it; no "Show data" table on either page, the list shows the same events."""
    soon = date.today() + timedelta(days=5)
    item(db, "Fake Rent", 150000, "monthly", soon, dom=str(soon.day))
    page = client.get("/cash-flow").text
    tabs = page[page.index('aria-label="Pages"'):page.index("</nav>", page.index('aria-label="Pages"'))]
    # the current tab (a page with ribbon controls: its tab also opens them on phones, so more attributes + a chevron)
    assert re.search(r'aria-current="page" class="active"[^>]*>Cash flow<', tabs)
    assert tabs.index(">Savings<") < tabs.index(">Cash flow<") < tabs.index(">Credit cards<")   # Analysis
    dash = client.get("/").text
    for html, chart_id in ((page, "cf-balance"), (dash, "ov-balance")):
        assert f'id="{chart_id}" data-chart="balance"' in html
        assert 'data-no-table="1"' in html.split(f'id="{chart_id}"')[1][:300]
    data = page.split('id="cf-balance-data">')[1].split("</script>")[0]
    overview = dash.split('id="ov-balance-data">')[1].split("</script>")[0]
    assert "Fake Rent" in data
    # the same projection for the page's account (30 days by default; the Overview lists every account)
    page_accounts, ov_accounts = json.loads(data)["accounts"], json.loads(overview)["accounts"]
    assert len(page_accounts) == 1 and page_accounts[0] in ov_accounts and len(page_accounts[0]["labels"]) == 31
    # the Coming up list, after the chart, the same as the Overview's
    assert page.index('id="cf-balance"') < page.index("Coming up in the next 30 days")
    rows = page.split("Coming up in the next 30 days")[1]
    assert "Fake Rent" in rows and "$1,500.00" in rows


# ---------------------------------------------------------------- recurring from the Transactions page

def tid(conn, day, cents):
    return conn.execute("SELECT id FROM transactions WHERE date = ? AND amount_cents = ?", (day, cents)).fetchone()[0]


def test_recurring_icon_make_add_and_not_part_of_it(client, db):
    """User's request 2026-10-16: a transaction in a confirmed recurring item has an icon; its edit panel makes a new
    item from it, adds it to an existing one, or says it isn't part of one."""
    for month in (8, 9, 10):                          # rent and savings transfers share the bank's text
        add(db, date(2026, month, 1), 145000, "Fake Bank Transfer", key="TRANSFER_OUT")
    for month in (8, 9):
        add(db, date(2026, month, 15), 450000, "Fake Bank Transfer", key="TRANSFER_OUT")
    add(db, date(2026, 9, 20), 120000, "Fake Bank Transfer", key="TRANSFER_OUT")   # 17% under rent: another payment
    listing = "/transactions?range=custom&from=2026-07-01&to=2026-10-31"
    assert 'aria-label="Recurring"' not in client.get(listing).text
    rent = tid(db, "2026-10-01", 145000)
    panel = client.get(f"/transactions/{rent}/edit").text
    assert 'value="new:monthly"' in panel and 'name="recurring"' in panel and 'label="Add to"' not in panel   # no items yet

    row = client.post(f"/transactions/{rent}/recurring", data={"action": "create", "frequency": "monthly"}, headers=ORIGIN).text
    assert 'title="Recurring: Fake Bank Transfer"' in row
    made = db.execute("SELECT * FROM recurring_items").fetchone()
    assert (made["status"], made["amount_cents"], made["frequency"], made["days_of_month"]) == ("confirmed", 145000, "monthly", "1")
    assert made["merchant_key"].endswith(" @145000") and made["type"] == "transfer"
    assert (made["last_seen_date"], made["next_expected_date"]) == ("2026-10-01", "2026-11-01")
    page = client.get(listing).text
    assert page.count('aria-label="Recurring"') == 3                  # the earlier rent too, not the $4,500 / $1,200
    assert 'value="exclude">Not recurring' in client.get(f"/transactions/{rent}/edit").text    # it's in an item

    # Not part of it, then Undo
    august = tid(db, "2026-08-01", 145000)
    row = client.post(f"/transactions/{august}/recurring", data={"action": "exclude"}, headers=ORIGIN).text
    assert 'aria-label="Recurring"' not in row
    assert 'value="undo">Automatic' in client.get(f"/transactions/{august}/edit").text
    assert 'aria-label="Recurring"' in client.post(f"/transactions/{august}/recurring", data={"action": "undo"},
                                                   headers=ORIGIN).text

    # Add to an existing item: one you added by hand (no description to match), which then advances
    item(db, "Fake Gym", 4000, "monthly", date(2026, 9, 5), category="PERSONAL_CARE", dom="5")
    add(db, date(2026, 10, 6), 4000, "Fake Gym Club")
    gym_txn = tid(db, "2026-10-06", 4000)
    panel = client.get(f"/transactions/{gym_txn}/edit").text
    assert "Add to" in panel and "Fake Gym · $40.00 monthly" in panel and "Fake Bank Transfer" in panel
    gym = db.execute("SELECT id FROM recurring_items WHERE name = 'Fake Gym'").fetchone()[0]
    row = client.post(f"/transactions/{gym_txn}/recurring", data={"action": "add", "item_id": str(gym)}, headers=ORIGIN).text
    assert 'title="Recurring: Fake Gym"' in row
    assert db.execute("SELECT last_seen_date, next_expected_date FROM recurring_items WHERE id = ?", (gym,)).fetchone()[:] \
        == ("2026-10-06", "2026-11-05")
    # ... and takes the item's category, as your choice (user's request 2026-10-29); the row shows it
    assert db.execute("SELECT user_category FROM transactions WHERE id = ?", (gym_txn,)).fetchone()[0] == "PERSONAL_CARE"
    assert "Personal care" in row
    # an item with the transaction's own category: it stays automatic
    item(db, "Fake Diner", 2500, "monthly", date(2026, 9, 7), category="FOOD_AND_DRINK", dom="7")
    add(db, date(2026, 10, 7), 2500, "Fake Diner Place")
    diner_txn = tid(db, "2026-10-07", 2500)
    diner = db.execute("SELECT id FROM recurring_items WHERE name = 'Fake Diner'").fetchone()[0]
    client.post(f"/transactions/{diner_txn}/recurring", data={"action": "add", "item_id": str(diner)}, headers=ORIGIN)
    assert db.execute("SELECT user_category FROM transactions WHERE id = ?", (diner_txn,)).fetchone()[0] is None
    # the account's page shows the icon too
    assert 'title="Recurring: Fake Gym"' in client.get(f"/accounts/{CHECKING}").text



# ---------------------------------------------------------------- Cash flow page: one account (user's request 2026-10-24)

def add_txn(conn, account, day, cents, pending=0):
    conn.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key,"
                 " pending) VALUES (?, ?, ?, ?, 'FAKE', 'GENERAL_MERCHANDISE', ?)",
                 (f"h{account}-{day}-{cents}-{pending}", account, day.isoformat(), cents, pending))
    conn.commit()


def test_balance_history_works_back_from_todays_balance(db):
    add_txn(db, CHECKING, TODAY, 5000)                              # today: already in the $2,000 balance
    add_txn(db, CHECKING, TODAY - timedelta(days=2), -30000)        # $300 came in two days ago
    add_txn(db, CHECKING, TODAY - timedelta(days=2), 2500, pending=1)   # pending: not counted
    add_txn(db, CHECKING, TODAY - timedelta(days=9), 99900)         # before the range: no effect
    history = forecast.balance_history(db, CHECKING, 5, TODAY)
    assert [d for d, _ in history] == [TODAY - timedelta(days=k) for k in range(5, -1, -1)]
    assert [c for _, c in history] == [175000, 175000, 175000, 205000, 205000, 200000]


def test_cash_measures(db):
    item(db, "Fake Rent", 150000, "monthly", TODAY + timedelta(days=10))
    item(db, "Fake Pay", -50000, "monthly", TODAY + timedelta(days=20))
    add_txn(db, CHECKING, TODAY - timedelta(days=3), 20000)
    forecast.set_low_balance_threshold(db, "1000")                  # the $500 low point is under it
    flow = forecast.cash_flow(db, today=TODAY, horizon=30)
    checking = next(a for a in flow.accounts if a.account_id == CHECKING)
    m = forecast.cash_measures(checking, forecast.balance_history(db, CHECKING, 30, TODAY), 30)
    assert (m.current_cents, m.change_cents, m.since) == (200000, -20000, TODAY - timedelta(days=30))
    assert (m.end_cents, m.lowest_cents, m.lowest_day) == (100000, 50000, TODAY + timedelta(days=10))
    assert m.monthly_cents == round(-100000 / 30 * forecast.AVERAGE_MONTH_DAYS) and m.below_threshold


def test_cash_flow_page_follows_the_account_and_range(client, db):
    page = client.get("/cash-flow").text
    ribbon = page.split('<header class="tabbar">')[1].split("</header>")[0]
    assert "Fake Checking</option>" in ribbon and "Fake Savings</option>" in ribbon and "disabled" not in ribbon
    assert '<div class="kpi-label">Balance</div>' in page
    for label in ("Projected change per month", "Lowest projected balance"):                # (short ones on a phone)
        assert f'<div class="kpi-label"><span class="w-long">{label}</span>' in page
    assert 'id="cf-history"' in page and "<h2>Balance history</h2>" in page
    page = client.get("/cash-flow", params={"account": SAVINGS, "days": 90}).text
    history_data = json.loads(page.split('id="cf-history-data">')[1].split("</script>")[0])
    projected_data = json.loads(page.split('id="cf-balance-data">')[1].split("</script>")[0])
    history, projected = history_data["accounts"][0], projected_data["accounts"]
    assert len(history["labels"]) == 91 and history["balance"][-1] == 10000.0        # Fake Savings, 90 days
    # one y-axis and the warning line on both charts (user's request 2026-10-24)
    assert history_data["range"] == projected_data["range"] and history_data["threshold"] == projected_data["threshold"]
    assert 'id="cf-history" data-chart="balance"' in page
    assert [a["label"] for a in projected] == ["Fake Savings"] and len(projected[0]["labels"]) == 91
    assert 'days=90" aria-current="true"' in page and '<option value="2" selected>' in page
    assert "days=30" in client.get("/cash-flow", params={"days": 45}).text            # not a choice: 30
    db.execute("UPDATE accounts SET is_hidden = 1 WHERE id = ?", (SAVINGS,))
    db.commit()
    ribbon = client.get("/cash-flow").text.split('<header class="tabbar">')[1].split("</header>")[0]
    assert "disabled" in ribbon                                                        # one cash account: greyed out
    saved = client.post("/forecast/threshold", data={"amount": "500", "account": "1", "days": "90"}, headers=ORIGIN,
                        follow_redirects=False)
    assert "account=1" in saved.headers["location"] and "days=90" in saved.headers["location"]


def test_coming_up_follows_the_selected_account(client, db):
    """The Cash flow page's Coming up lists the selected account's events only (user's request 2026-10-24)."""
    soon = date.today() + timedelta(days=5)
    item(db, "Fake Rent", 150000, "monthly", soon, account=CHECKING)
    item(db, "Fake Gym", 4000, "monthly", soon, account=SAVINGS)

    def coming(account):
        page = client.get("/cash-flow", params={"account": account}).text
        return page.split("<h2>Coming up in the next 30 days</h2>")[1].split("</section>")[0]

    assert "Fake Rent" in coming(CHECKING) and "Fake Gym" not in coming(CHECKING)
    assert "Fake Gym" in coming(SAVINGS) and "Fake Rent" not in coming(SAVINGS)
    overview = client.get("/").text.split("Coming up in the next 30 days")[1]
    assert "Fake Rent" in overview and "Fake Gym" in overview                         # the Overview: every account


def test_coming_up_shows_the_balance_after_each_row(client, db):
    """One account's Coming up carries the projected balance after each row (user's request 2026-10-24)."""
    item(db, "Fake Rent", 150000, "monthly", TODAY + timedelta(days=10))
    item(db, "Fake Pay", -50000, "monthly", TODAY + timedelta(days=10))      # same day: income listed first
    item(db, "Fake Phone", 6000, "monthly", TODAY + timedelta(days=12))
    flow = forecast.cash_flow(db, today=TODAY, horizon=30)
    checking = next(a for a in flow.accounts if a.account_id == CHECKING)
    rows = forecast.upcoming_for(checking, TODAY)
    assert [(r["name"], r["balance_after"]) for r in rows] == [
        ("Fake Pay", 250000), ("Fake Rent", 100000), ("Fake Phone", 94000)]
    assert rows[-1]["balance_after"] == checking.at(12)                         # agrees with the chart
    page = client.get("/cash-flow").text.split("<h2>Coming up in the next 30 days</h2>")[1].split("</section>")[0]
    assert '<span class="w-long">Balance after</span>' in page                  # "Ending Balance" on a phone
    live = forecast.upcoming_for(next(a for a in forecast.cash_flow(db).accounts if a.account_id == CHECKING), date.today())
    assert f'<span class="w-long">{money.format_cents(live[-1]["balance_after"])}</span>' in page     # the last row's (whole dollars on a phone)
    assert "Balance after" not in client.get("/").text                                         # the Overview: every account


def test_coming_up_follows_the_selected_range(client, db):
    """The Cash flow page's Coming up covers the selected 30 / 90 / 180 days (user's request 2026-10-24)."""
    item(db, "Fake Insurance", 30000, "monthly", date.today() + timedelta(days=60))
    page = client.get("/cash-flow").text
    assert "<h2>Coming up in the next 30 days</h2>" in page and "Fake Insurance" not in page.split("Coming up")[1]
    page = client.get("/cash-flow", params={"days": 90}).text
    coming = page.split("<h2>Coming up in the next 90 days</h2>")[1].split("</section>")[0]
    assert "Fake Insurance" in coming
    assert "<h3>Coming up in the next 30 days</h3>" in client.get("/").text                 # the Overview: 30 days



def test_past_transactions_card_beside_coming_up(client, db):
    """The Cash flow page: the selected account's transactions in the period, newest first with the balance after each,
    beside Coming up; neither lists the account (user's request 2026-10-24)."""
    today = date.today()
    add_txn(db, CHECKING, today - timedelta(days=3), 12000)
    add_txn(db, CHECKING, today - timedelta(days=10), -50000)
    add_txn(db, CHECKING, today - timedelta(days=40), 7000)                   # outside 30 days
    rows = forecast.past_for(db, CHECKING, 30, today)
    assert [(r["amount_cents"], r["balance_after"]) for r in rows] == [(12000, 200000), (-50000, 212000)]
    page = client.get("/cash-flow").text
    pair = page.split('class="chart-pair list-pair')[1]
    past, coming = pair.split("<h2>In the last 30 days</h2>")[1], pair.split("<h2>Coming up in the next 30 days</h2>")[1]
    assert pair.index("In the last 30 days") < pair.index("Coming up in the next 30 days")      # past on the left
    assert "$2,120.00" in past.split("</section>")[0] and "<th>Account</th>" not in pair
    item(db, "Fake Rent", 150000, "monthly", today + timedelta(days=5))
    assert "<th>Account</th>" in client.get("/").text                                          # the Overview keeps it
    # both cards call the name column "Transaction"; the Overview keeps "What" (user's request 2026-10-24)
    assert client.get("/cash-flow").text.count("<th>Transaction</th>") == 2 and "<th>What</th>" in client.get("/").text


def test_lists_widen_the_date_column_for_another_year(client, db):
    """A date in another year ("Jan 4, 2027") would overlap the name: both lists get a wider Date column
    (user's request 2026-10-24)."""
    today = date.today()
    add_txn(db, CHECKING, today - timedelta(days=3), 12000)
    page = client.get("/cash-flow").text
    assert ("long-dates" in page) == ((today - timedelta(days=3)).year != today.year)
    next_year = date(today.year + 1, 1, 15)
    item(db, "Fake Insurance", 30000, "monthly", next_year, dom="15")
    page = client.get("/cash-flow", params={"days": 180}).text
    assert 'class="chart-pair list-pair long-dates ' in page and f"Jan 15, {today.year + 1}" in page
