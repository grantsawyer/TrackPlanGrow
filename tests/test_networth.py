"""Net worth signs, snapshots, history, and holdings."""

import re
from datetime import date

import pytest

from fintrack import items, networth, sync
from fintrack.plaid_client import PlaidError


def add_item(conn, n=1):
    conn.execute(
        "INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (?, ?, 'sandbox', 'Fake Bank')",
        (n, f"item-{n}"),
    )


def add_account(conn, acc_id, account_type, balance_cents, *, item_id=1, hidden=0, currency="USD", name=None):
    conn.execute(
        "INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification,"
        " current_balance_cents, iso_currency_code, is_hidden) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (acc_id, item_id, f"acc-{acc_id}", name or f"Account {acc_id}", account_type,
         items.classify(account_type), balance_cents, currency, hidden),
    )


@pytest.fixture
def mixed(conn):
    """Assets: 1000.00 checking, -50.00 overdrawn checking, 500.00 brokerage.
    Liabilities: 300.00 owed on a card, -20.00 card credit (issuer owes you), 1000.00 loan."""
    add_item(conn)
    add_account(conn, 1, "depository", 100000)
    add_account(conn, 2, "depository", -5000)
    add_account(conn, 3, "investment", 50000)
    add_account(conn, 4, "credit", 30000)
    add_account(conn, 5, "credit", -2000)
    add_account(conn, 6, "loan", 100000)
    conn.commit()
    return conn


# ---------------------------------------------------------------- signs

def test_net_worth_signs(mixed):
    nw = networth.current_net_worth(mixed)
    assert nw.assets_cents == 145000         # 1000 - 50 + 500
    assert nw.liabilities_cents == 128000    # 300 - 20 + 1000
    assert nw.net_cents == 17000             # 1450 - 1280


def test_signed_contribution():
    assert networth.signed(30000, "liability") == -30000   # card debt subtracts
    assert networth.signed(-2000, "liability") == 2000     # card credit adds
    assert networth.signed(-5000, "asset") == -5000        # overdraft subtracts


def test_classification_of_plaid_types():
    assert [items.classify(t) for t in ("depository", "investment", "other", "credit", "loan")] == [
        "asset", "asset", "asset", "liability", "liability"]


def test_breakdown_and_shares(mixed):
    rows = {(t.label): t for t in networth.current_net_worth(mixed).by_type}
    assert list(rows) == ["Cash", "Investments", "Credit cards", "Loans"]
    assert rows["Cash"].total_cents == 95000
    assert rows["Investments"].share == pytest.approx(50000 / 145000)
    assert rows["Credit cards"].total_cents == 28000
    assert rows["Loans"].share == pytest.approx(100000 / 128000)


def test_other_liability_never_lands_in_assets(conn):
    add_item(conn)
    add_account(conn, 1, "depository", 1000)
    conn.execute(
        "INSERT INTO accounts (id, name, type, classification, current_balance_cents, is_manual)"
        " VALUES (2, 'IOU to family', 'other', 'liability', 400, 1)"
    )
    labels = {t.label: t.classification for t in networth.current_net_worth(conn).by_type}
    assert labels == {"Cash": "asset", "Other liabilities": "liability"}


def test_hidden_non_usd_and_missing_balances_excluded(conn):
    add_item(conn)
    add_account(conn, 1, "depository", 1000)
    add_account(conn, 2, "depository", 999999, hidden=1)           # removed Item
    add_account(conn, 3, "depository", 50000, currency="EUR", name="Euro account")
    add_account(conn, 4, "investment", None, name="No balance yet")
    nw = networth.current_net_worth(conn)
    assert nw.net_cents == 1000
    assert {a["name"] for a in nw.excluded} == {"Euro account", "No balance yet"}


def test_null_currency_counted_as_usd(conn):
    add_item(conn)
    add_account(conn, 1, "depository", 1234, currency=None)
    assert networth.current_net_worth(conn).net_cents == 1234


# ---------------------------------------------------------------- snapshots and history

def test_snapshot_same_day_overwrites(mixed):
    day = date(2026, 10, 1)
    networth.take_snapshot(mixed, day)
    mixed.execute("UPDATE accounts SET current_balance_cents = 110000 WHERE id = 1")  # 1000.00 -> 1100.00
    mixed.commit()
    assert networth.take_snapshot(mixed, day) == 6
    rows = mixed.execute("SELECT COUNT(*), SUM(balance_cents) FROM balance_snapshots WHERE date = '2026-10-01'").fetchone()
    assert rows[0] == 6
    assert networth.history(mixed)[0].net_cents == 27000  # 17000 + 10000 from the updated checking


def test_snapshot_drops_account_hidden_later_same_day(mixed):
    day = date(2026, 10, 1)
    networth.take_snapshot(mixed, day)
    mixed.execute("UPDATE accounts SET is_hidden = 1 WHERE id = 6")  # loan's Item removed
    mixed.commit()
    networth.take_snapshot(mixed, day)
    ids = {r[0] for r in mixed.execute("SELECT account_id FROM balance_snapshots")}
    assert 6 not in ids


def test_snapshot_keeps_classification_for_history(mixed):
    networth.take_snapshot(mixed, date(2026, 10, 1))
    row = mixed.execute("SELECT classification, account_type FROM balance_snapshots WHERE account_id = 4").fetchone()
    assert tuple(row) == ("liability", "credit")


def test_history_fills_gaps_and_flags_them(mixed):
    networth.take_snapshot(mixed, date(2026, 10, 1))
    mixed.execute("UPDATE accounts SET current_balance_cents = 110000 WHERE id = 1")
    mixed.commit()
    networth.take_snapshot(mixed, date(2026, 10, 4))
    points = networth.history(mixed)
    assert [p.date for p in points] == ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]
    assert [p.carried for p in points] == [False, True, True, False]
    assert [p.net_cents for p in points] == [17000, 17000, 17000, 27000]


def test_history_removed_account_does_not_linger(mixed):
    networth.take_snapshot(mixed, date(2026, 10, 1))
    mixed.execute("UPDATE accounts SET is_hidden = 1 WHERE id = 3")  # brokerage removed
    mixed.commit()
    networth.take_snapshot(mixed, date(2026, 10, 3))
    points = networth.history(mixed)
    assert points[0].assets_cents == 145000
    assert points[1].assets_cents == 145000 and points[1].carried  # gap repeats the 1st as a whole day
    assert points[2].assets_cents == 95000                            # brokerage gone from the 3rd


def test_history_empty():
    import sqlite3

    from fintrack import db
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db.migrate(c)
    assert networth.history(c) == []


@pytest.mark.parametrize("day, expected", [
    (date(2026, 3, 31), date(2026, 2, 28)),
    (date(2028, 3, 31), date(2028, 2, 29)),   # leap year
    (date(2026, 1, 15), date(2025, 12, 15)),
    (date(2026, 5, 31), date(2026, 4, 30)),
    (date(2026, 10, 3), date(2026, 9, 3)),
])
def test_one_month_before(day, expected):
    assert networth.one_month_before(day) == expected


def test_change_since_last_month(mixed):
    assert networth.change_since_last_month(mixed, 17000, today=date(2026, 10, 3)) is None  # no history
    networth.take_snapshot(mixed, date(2026, 8, 30))
    networth.take_snapshot(mixed, date(2026, 9, 2))  # latest on or before Sep 3
    mixed.execute("UPDATE accounts SET current_balance_cents = 150000 WHERE id = 1")
    mixed.commit()
    networth.take_snapshot(mixed, date(2026, 9, 20))  # after Sep 3: ignored as baseline
    change = networth.change_since_last_month(mixed, 50000, today=date(2026, 10, 3))
    assert (change.since_date, change.delta_cents) == ("2026-09-02", 50000 - 17000)


# ---------------------------------------------------------------- holdings + sync integration

def connect(conn, settings, gateway, cipher):
    session_id, _ = items.start_create_session(conn, settings, gateway, "banking")
    return items.complete_create(conn, settings, gateway, cipher, session_id, "public-sandbox-fake")["item_id"]


BROKERAGE = {"account_id": "acc-brokerage", "name": "Fake Brokerage", "mask": "5555", "type": "investment",
             "subtype": "brokerage", "balances": {"current": 1500.0, "available": 100.0, "iso_currency_code": "USD"}}


def holdings_payload(*positions):
    securities = [
        {"security_id": "sec-fake", "name": "Fake Index Fund", "ticker_symbol": "FAKE", "type": "etf",
         "is_cash_equivalent": False},
        {"security_id": "sec-cash", "name": "Cash", "ticker_symbol": None, "type": "cash", "is_cash_equivalent": True},
    ]
    holdings = [{"account_id": "acc-brokerage", "security_id": s, "quantity": q, "institution_price": 10.0,
                 "institution_value": v, "cost_basis": c, "institution_price_as_of": None} for s, q, v, c in positions]
    return {"holdings": holdings, "securities": securities}


@pytest.fixture
def invest_gateway(gateway):
    from tests.fakes import FAKE_ACCOUNTS

    gateway.products = ["investments", "transactions"]
    gateway.accounts_override = FAKE_ACCOUNTS + [BROKERAGE]
    original = gateway.get_accounts
    gateway.get_accounts = lambda token: (original(token), gateway.accounts_override)[1]
    gateway.holdings_payload = holdings_payload(("sec-fake", 140.0, 1400.0, 1200.0), ("sec-cash", 100.0, 100.0, None))
    gateway.holdings_error = None

    def get_holdings(token):
        gateway.calls.append(("get_holdings",))
        if gateway.holdings_error:
            raise gateway.holdings_error
        return gateway.holdings_payload

    gateway.get_holdings = get_holdings
    return gateway


def holdings_rows(conn):
    return {r["security_id"]: r for r in conn.execute("SELECT * FROM holdings")}


def test_holdings_stored_and_replaced(conn, settings, invest_gateway, cipher):
    connect(conn, settings, invest_gateway, cipher)
    sync.sync_all(conn, invest_gateway, cipher, settings.home)
    rows = holdings_rows(conn)
    assert rows["sec-fake"]["value_cents"] == 140000 and rows["sec-fake"]["ticker"] == "FAKE"
    assert rows["sec-cash"]["is_cash"] == 1

    invest_gateway.holdings_payload = holdings_payload(("sec-fake", 150.0, 1500.0, 1300.0))  # cash moved out
    sync.sync_all(conn, invest_gateway, cipher, settings.home)
    assert set(holdings_rows(conn)) == {"sec-fake"}


def test_duplicate_lots_combined(conn, settings, invest_gateway, cipher):
    connect(conn, settings, invest_gateway, cipher)
    invest_gateway.holdings_payload = holdings_payload(("sec-fake", 10.0, 100.0, 90.0), ("sec-fake", 5.0, 50.0, 40.0))
    sync.sync_all(conn, invest_gateway, cipher, settings.home)
    row = holdings_rows(conn)["sec-fake"]
    assert (row["quantity"], row["value_cents"], row["cost_basis_cents"]) == (15.0, 15000, 13000)


def test_lot_without_basis_makes_the_basis_unknown(conn, settings, invest_gateway, cipher):
    connect(conn, settings, invest_gateway, cipher)
    invest_gateway.holdings_payload = holdings_payload(("sec-fake", 10.0, 100.0, 90.0), ("sec-fake", 5.0, 50.0, None))
    sync.sync_all(conn, invest_gateway, cipher, settings.home)
    row = holdings_rows(conn)["sec-fake"]
    assert (row["quantity"], row["value_cents"], row["cost_basis_cents"]) == (15.0, 15000, None)


def test_holdings_on_a_non_investment_account_are_replaced_not_added(conn):
    """Some providers report an HSA as depository; its holdings must not pile up each sync."""
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env) VALUES (1, 'i1', 'sandbox')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification)"
                 " VALUES (1, 1, 'acc-hsa', 'Fake HSA', 'depository', 'asset')")
    conn.commit()
    payload = {"securities": [{"security_id": "s1", "ticker_symbol": "FAKE", "name": "Fake Fund", "type": "etf"}],
               "holdings": [{"account_id": "acc-hsa", "security_id": "s1", "quantity": 3, "institution_price": 10.0,
                             "institution_value": 30.0, "cost_basis": 25.0, "institution_price_as_of": None}]}
    sync.store_holdings(conn, 1, payload)
    sync.store_holdings(conn, 1, payload)
    row = holdings_rows(conn)["s1"]
    assert (row["quantity"], row["value_cents"]) == (3.0, 3000)


def test_holdings_not_fetched_without_investments_product(conn, settings, invest_gateway, cipher):
    invest_gateway.products = ["transactions"]
    connect(conn, settings, invest_gateway, cipher)
    sync.sync_all(conn, invest_gateway, cipher, settings.home)
    assert ("get_holdings",) not in invest_gateway.calls


def test_holdings_error_does_not_fail_sync(conn, settings, invest_gateway, cipher):
    item_id = connect(conn, settings, invest_gateway, cipher)
    invest_gateway.holdings_error = PlaidError("PRODUCT_NOT_READY", "not ready")
    result = sync.sync_all(conn, invest_gateway, cipher, settings.home).results[0]
    assert result.error is None and "holdings unavailable (PRODUCT_NOT_READY)" in result.note
    assert items.get_item(conn, item_id)["status"] == "ok"


def test_holdings_login_error_marks_item(conn, settings, invest_gateway, cipher):
    item_id = connect(conn, settings, invest_gateway, cipher)
    invest_gateway.holdings_error = PlaidError("ITEM_LOGIN_REQUIRED", "login")
    result = sync.sync_all(conn, invest_gateway, cipher, settings.home).results[0]
    assert result.error == "ITEM_LOGIN_REQUIRED"
    assert items.get_item(conn, item_id)["status"] == "login_required"


def test_sync_takes_daily_snapshot(conn, settings, invest_gateway, cipher):
    connect(conn, settings, invest_gateway, cipher)
    sync.sync_all(conn, invest_gateway, cipher, settings.home)
    points = networth.history(conn)
    assert len(points) == 1 and points[0].date == date.today().isoformat()
    # FAKE_ACCOUNTS: checking 110.00 (asset), card 410.01 (liability); plus brokerage 1500.00
    assert points[0].net_cents == 11000 + 150000 - 41001


def test_holdings_live_on_investments_and_net_worth_sits_under_accounts(conn, settings, invest_gateway, cipher):
    """User's layout (2026-10-07): Accounts section -> Net worth tab; Investments section -> mix + holdings."""
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    connect(conn, settings, invest_gateway, cipher)
    sync.sync_all(conn, invest_gateway, cipher, settings.home)
    client = TestClient(create_app(settings, gateway_factory=lambda s: invest_gateway, cipher_factory=TokenCipher),
                        base_url="https://localhost:8443")

    def ribbon(html):
        return html.split('<header class="tabbar">')[1].split("</header>")[0]

    inv = client.get("/investments").text
    assert "Investment holdings" not in inv and "Investment mix" not in inv    # both on Portfolio
    assert 'id="inv-trend-data"' in inv and 'data-range-for="inv-trend"' in ribbon(inv)   # ribbon, top right
    # The mix moved to Portfolio, the Investments section's second tab (user's request 2026-10-07).
    port = client.get("/portfolio").text
    assert "Total investments" in port and 'id="port-types-data"' in port and "Investment mix" not in port
    assert [b.split('"')[0] for b in port.split('data-range-for="port-types"')[1].split("</div>")[0]
            .split('data-range="')[1:]] == ["1", "ytd", "365", "1095", "1825", "3650"]    # user's request 2026-11-13
    assert 'data-range-for="port-types"' in ribbon(port)                      # in the ribbon, top right
    assert "Investment holdings" in port and "Fake Index Fund" in port
    holdings = port.split('<table class="data holdings"')[1].split("</table>")[0]
    heads = [re.sub(r"<span class=\"w-short\">.*?</span>|<[^>]+>", "", h.split("</th>")[0])
             for h in holdings.split("</thead>")[0].split('<th class="num">')[1:]]     # the long labels (phones: 90D)
    assert heads == ["Value", "Cost basis", "1 day", "YTD", "1 year", "3 years", "5 years", "10 years"]   # no Quantity
    assert "$1,400<" in holdings and "$1,200<" in holdings and ".00" not in holdings      # whole dollars
    assert "style=" not in port.split("<main>")[1]
    tabs = ribbon(port)
    assert tabs.index(">Investments<") < tabs.index(">Portfolio<")
    assert "style=" not in inv.split("<main>")[1]                         # no inline styles (CSP)

    nw = client.get("/networth").text
    assert "Breakdown" not in nw                                          # removed (user's request 2026-10-07)
    assert "What you own and owe" not in nw and "nw-comp" not in nw       # removed (user's request 2026-10-12)
    assert "Investment holdings" not in nw and "Investment mix" not in nw
    # The Overview's Accounts card is on Net worth too (one shared partial).
    # The Overview's Accounts card moved from Net worth to Balances (user's request 2026-10-13); one shared partial.
    assert 'class="data compact accounts-tree"' not in nw
    bal = client.get("/accounts").text               # its Summary card (2026-10-13) + the accounts on the account cards
    assert "<h2>Summary</h2>" in bal and "Fake Brokerage" in bal
    assert 'class="data compact accounts-tree"' in client.get("/").text

    rail = nw.split('<aside class="rail"')[1].split("</aside>")[0]
    assert rail.index(">Accounts<") < rail.index(">Investments<") < rail.index(">Planning<")
    assert "Net worth" in ribbon(nw) and "Investments" in ribbon(inv)
    # Settings → Accounts was folded into Setup (user's request 2026-10-14); the old address redirects there
    assert ">Setup<" in ribbon(client.get("/setup").text) and ">Accounts<" not in ribbon(client.get("/setup").text)
    # Balances became Accounts at /accounts (user's request 2026-10-14); old addresses redirect, query kept
    moved = client.get("/balances?range=90", follow_redirects=False)
    assert moved.status_code == 307 and moved.headers["location"] == "/accounts?range=90"
    page = client.get("/balances/accounts/5?msg=Hi", follow_redirects=False)
    assert page.status_code == 307 and page.headers["location"] == "/accounts/5?msg=Hi"


def test_history_by_account_type_counts_only_that_type(conn):
    """networth.history(account_type=...): other types left out, gaps carried, a sync day with none = 0."""
    from datetime import date, timedelta

    d = lambda n: (date(2026, 1, 1) + timedelta(days=n)).isoformat()   # noqa: E731
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env) VALUES (1, 'i1', 'sandbox')")
    for acc_id, kind, cls in [(1, "investment", "asset"), (2, "depository", "asset"), (3, "credit", "liability")]:
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification)"
                     " VALUES (?, 1, ?, 'Fake', ?, ?)", (acc_id, f"a{acc_id}", kind, cls))
    rows = [(d(0), 1, 100000, "asset", "investment"), (d(0), 2, 5000, "asset", "depository"),
            (d(0), 3, 2000, "liability", "credit"),
            (d(2), 1, 110000, "asset", "investment"),                 # day 1: no sync -> carried
            (d(3), 2, 5000, "asset", "depository")]                   # investments gone that day -> 0
    conn.executemany("INSERT INTO balance_snapshots (date, account_id, balance_cents, classification, account_type)"
                     " VALUES (?, ?, ?, ?, ?)", rows)
    conn.commit()
    points = networth.history(conn, account_type="investment")
    assert [(p.date, p.assets_cents, p.carried) for p in points] == [
        (d(0), 100000, False), (d(1), 100000, True), (d(2), 110000, False), (d(3), 0, False)]


def test_day_changes_compare_with_the_latest_snapshot_before_today(mixed):
    """The Overview's Accounts card, 1-day change (user's request 2026-10-23)."""
    today = date(2026, 10, 23)
    networth.take_snapshot(mixed, date(2026, 10, 20))          # older: ignored when a later one exists
    mixed.execute("UPDATE accounts SET current_balance_cents = 90000 WHERE id = 1")
    networth.take_snapshot(mixed, date(2026, 10, 22))          # yesterday
    networth.take_snapshot(mixed, today)                       # today's own snapshot: never the baseline
    mixed.execute("UPDATE accounts SET current_balance_cents = 95000 WHERE id = 1")
    mixed.execute("UPDATE accounts SET current_balance_cents = 35000 WHERE id = 4")      # owe $50 more on the card
    add_account(mixed, 7, "depository", 1000)                  # new today: no baseline
    changes = networth.day_changes(mixed, today)
    assert changes[1] == 5000 and changes[4] == 5000 and changes[3] == 0 and 7 not in changes


def test_the_accounts_card_shows_day_changes(mixed):
    from fintrack.web.dashboard import account_groups

    institutions = [{"item": {"institution_name": "Fake Bank", "status": "ok"},
                     "accounts": mixed.execute("SELECT * FROM accounts ORDER BY id").fetchall()}]
    sides = account_groups(institutions, [], changes={1: 5000, 2: -1000, 4: 5000})
    assets, liabilities = sides
    cash = next(g for g in assets["groups"] if g["label"] == "Cash")
    assert cash["change_cents"] == 4000 and assets["change_cents"] == 4000 and liabilities["change_cents"] == 5000
    assert next(g for g in assets["groups"] if g["label"] == "Investments")["change_cents"] is None   # none known


def test_the_overview_range_toggle_is_on_its_card(mixed, settings, cipher, gateway):
    """Net worth over time's 30/90/180/365 days, top right of its card (user's request 2026-11-06; in the ribbon from
    2026-10-23), so the Overview has no ribbon controls."""
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    client = TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                        base_url="https://localhost:8443")
    networth.take_snapshot(mixed, date(2026, 10, 1))
    networth.take_snapshot(mixed, date(2026, 10, 2))
    page = client.get("/").text
    header, main = page.split("</header>")[0], page.split("</header>")[1]
    assert 'data-range-for="ov-nw"' not in header and 'class="tabs has-controls"' not in header
    card = main.split("Net worth over time</a></h2>")[1].split("</div>")[0]
    assert card.count('data-range-for="ov-nw"') == 1 and main.count('data-range-for="ov-nw"') == 1
    toggle = card.split('data-range-for="ov-nw"')[1]
    assert [f'data-days="{d}"' in toggle for d in (30, 90, 180, 365)] == [True] * 4
    assert 'data-days="90" aria-pressed="true"' in toggle
