"""The Balances page (balances.py, user's request 2026-10-12): change in the total value of your accounts over time,
from the daily net-worth snapshots, by kind of account. All accounts and amounts are made up."""

import re
from datetime import date

import pytest

from fintrack import balances, visuals

TODAY = date(2026, 10, 9)
CHECKING, CARD, IRA, BROKERAGE, LENT, CAR, LOAN = 1, 2, 3, 4, 5, 6, 7


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name, products)"
                 " VALUES (1, 'i1', 'sandbox', 'Fake Broker', '[\"investments\"]')")
    for acc_id, name, kind, subtype, side, manual in [
            (CHECKING, "Fake checking", "depository", "checking", "asset", 0),
            (CARD, "Fake card", "credit", "credit card", "liability", 0),
            (IRA, "Fake IRA", "investment", "ira", "asset", 0),
            (BROKERAGE, "Fake brokerage", "investment", "brokerage", "asset", 0),
            (LENT, "Fake loan to a friend", "receivable", None, "asset", 1),
            (CAR, "Fake car", "property", None, "asset", 1),
            (LOAN, "Fake car loan", "loan", None, "liability", 1)]:
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, subtype, classification, is_manual)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (acc_id, None if manual else 1, None if manual else f"acc-{acc_id}", name, kind, subtype, side, manual))
    conn.commit()
    return conn


KINDS = {CHECKING: ("depository", "asset"), CARD: ("credit", "liability"), IRA: ("investment", "asset"),
         BROKERAGE: ("investment", "asset"), LENT: ("receivable", "asset"), CAR: ("property", "asset"),
         LOAN: ("loan", "liability")}


def snapshot(conn, day, **dollars):
    """One snapshot day: snapshot(db, "2026-08-31", checking=1000, card=200, ...) (balances as Plaid reports them)."""
    ids = {"checking": CHECKING, "card": CARD, "ira": IRA, "brokerage": BROKERAGE, "lent": LENT, "car": CAR, "loan": LOAN}
    for name, amount in dollars.items():
        kind, side = KINDS[ids[name]]
        conn.execute("INSERT INTO balance_snapshots (date, account_id, balance_cents, classification, account_type)"
                     " VALUES (?, ?, ?, ?, ?)", (day, ids[name], amount * 100, side, kind))
    conn.commit()


@pytest.fixture
def history(db):
    snapshot(db, "2026-08-31", checking=10000, card=2000, ira=50000, brokerage=30000, lent=0, car=20000, loan=15000)
    # September: paid $1,000 into the IRA, the market added $1,500 to it, the brokerage fell $500, $3,000 lent,
    # the card grew by $500, $400 of loan paid down, the car lost $200
    snapshot(db, "2026-09-30", checking=6000, card=2500, ira=52500, brokerage=29500, lent=3000, car=19800, loan=14600)
    # October so far: no snapshot on Oct 1-4 (carried), then checking +$2,000
    snapshot(db, "2026-10-05", checking=8000, card=2500, ira=52500, brokerage=29500, lent=3000, car=19800, loan=14600)
    return db


def test_change_by_kind_adds_up_to_net_worth(history):
    september = balances.range_changes(history, "2026-09-01", "2026-09-30", TODAY)
    assert [p.month for p in september] == ["2026-09"]
    m = september[0]
    assert m.change("retirement") == 250000                    # contributions and market growth alike
    assert m.change("investment") == -50000
    assert m.change("purposes") == 300000
    assert m.change("cash") == -400000                          # checking down $4,000
    assert m.change("credit_cards") == -50000                   # the card owes $500 more: net worth down
    assert m.change("property_loans") == -20000 + 40000         # the car lost $200, $400 less owed
    assert m.change() == sum(m.change(g) for g, _ in balances.GROUPS) == 250000 - 50000 + 300000 - 450000 + 20000
    assert m.pct("credit_cards") == pytest.approx(-0.25)        # -$2,000 -> -$2,500: 25% more owed (vs its size)
    start_net = (10000 - 2000 + 50000 + 30000 + 20000 - 15000) * 100
    assert m.value_before() == start_net and m.pct() == pytest.approx(m.change() / start_net)
    assert m.segment_changes("cash") == {"Fake checking": -400000}                # by account
    assert m.segment_changes("credit_cards") == {"Fake card": -50000}
    assert m.segment_changes("retirement") == {"IRA": 250000}                     # by account type
    assert m.segment_changes("investment") == {"Fake Broker": -50000}            # by institution


def test_months_carry_days_and_count_from_the_first_snapshot(history):
    series = balances.range_changes(history, "2026-07-15", "2026-10-09", TODAY)
    # July has no snapshot at all: left out; August starts before the first snapshot, so it counts from it (no change)
    assert [p.month for p in series] == ["2026-08", "2026-09", "2026-10"]
    assert series[0].change() == 0
    october = series[-1]
    assert october.partial and october.change() == october.change("cash") == 200000
    assert october.value_after() == series[1].value_after() + 200000
    # the whole range at once = the sum of its months
    whole = balances.summary(history, "2026-07-15", "2026-10-09", TODAY)
    assert whole.change() == sum(p.change() for p in series)


def test_by_day_and_no_starting_value(history):
    days = balances.range_changes(history, "2026-10-01", "2026-10-09", TODAY, by_day=True)
    assert [p.month for p in days if p.change()] == ["2026-10-05"]                  # carried days change nothing
    assert len(days) == 9
    # money lent started at $0: a change but no meaningful %
    september = balances.summary(history, "2026-09-01", "2026-09-30", TODAY)
    assert september.change("purposes") == 300000 and september.pct("purposes") is None


def test_cards_chart_data(history):
    series = balances.range_changes(history, "2026-09-01", "2026-10-09", TODAY)
    whole = balances.summary(history, "2026-09-01", "2026-10-09", TODAY)
    cash = visuals.balance_change_mix(series, "cash", whole)
    assert not cash["money"] and cash["total_cents"] == -200000           # % mode by default
    assert cash["value_cents"] == 800000                                  # Total value: at the end of the range
    # each bar = the month's % change ($ change in the tooltip): -$4,000 on $10,000, then +$2,000 on $6,000
    assert [(s["label"], s["values"], s["dollars"]) for s in cash["series"]] == [
        ("Fake checking", [-40.0, 33.33], [-4000.0, 2000.0])]
    cards = visuals.balance_change_mix(series, "credit_cards", whole)
    # the % follows the amount owed: $500 more on $2,000 = +25%, drawn with the opposite colours (invert)
    assert cards["value_cents"] == -250000 and cards["pct"] == 25.0 and cards["invert"] and cards["hue"] == "--series-7"
    assert not cash["invert"]
    assert [(s["label"], s["dollars"]) for s in cards["series"]] == [("Fake card", [-500.0, 0.0])]
    nw = visuals.networth_change_mix(series, [g for g, _ in balances.GROUPS], whole)
    assert nw["total_cents"] == whole.change()
    assert [s["label"] for s in nw["series"]] == [label for _, label in balances.GROUPS]
    assert sum(s["dollars"][0] for s in nw["series"]) == series[0].change() / 100


@pytest.fixture
def client(settings, history, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_pages_show_changes_not_rates(client):
    """Since 2026-10-13 (user's request): Balances = tiles + the Net worth card + the Accounts card; Net worth = the
    cards per kind of account + the Change in value table, for the same date range."""
    query = "?range=custom&from=2026-09-01&to=2026-09-30"
    bal, nw = client.get("/accounts" + query).text, client.get("/networth" + query + "&cards=compact").text
    assert "<h2>Net worth</h2>" in bal and 'class="cards n1"' in bal and "<h2>Summary</h2>" in bal
    assert "<h2>Change in value</h2>" not in bal and "<h2>Retirement</h2>" not in bal
    for card in ("Retirement", "Investment", "Other assets", "Cash", "Credit cards"):
        assert f"<h2>{card}</h2>" in nw
    assert "<h2>Change in value</h2>" in nw and ">Property &amp; loans</th>" in nw
    assert "<h2>Accounts</h2>" not in nw and 'id="bal-networth"' not in nw
    for page in (bal, nw):
        assert "Net savings rate" not in page and "% inc." not in page and "Net cash" not in page
        assert not re.search(r"<h2>[^<]*[Ss]avings[^<]*</h2>", page)             # no savings in the headers
    assert nw.count(">Total value<") == 5 and nw.count(">% change<") == 5 and 'class="cards n5 fit4"' in nw
    assert '"measure-value ">-$2,500</span>' in nw                           # the cards' Total value: owed, not red
    assert '<span class="delta-down">▲ 25.0%</span>' in nw          # $500 more owed on $2,000: red ▲ (opposite colours)
    assert '<span class="delta-up">▲ 5.0%</span>' in nw            # the IRA rose $2,500 on $50,000 (stock-style arrows)
    assert '<span class="delta-down">▼ 1.7%</span>' in nw          # the brokerage fell $500 on $30,000
    assert "Sep 2026" in nw and "+$2,500" in nw                     # the IRA's change in the table
    # the date range control is on both ribbons, each submitting to its own page
    assert 'id="tx-range"' in bal and 'id="tx-range"' in nw and 'action="/networth"' in nw


def test_page_without_snapshots(client, history):
    history.execute("DELETE FROM balance_snapshots")
    history.commit()
    assert "No balances in this range." in client.get("/networth").text
    assert client.get("/accounts").status_code == 200


def test_paying_a_card_down_is_a_green_down_arrow(client, history):
    snapshot(history, "2026-10-08", checking=8000, card=1500, ira=52500, brokerage=29500, lent=3000, car=19800, loan=14600)
    query = "?range=custom&from=2026-10-01&to=2026-10-09"
    assert '<span class="delta-up">▼ 40.0%</span>' in client.get("/networth" + query).text   # owed $2,500 -> $1,500: green ▼
    # assets keep their colours: net worth up $3,000 on $93,700, green ▲ (the Net worth card on Balances)
    assert '<span class="delta-up">▲ 3.2%</span>' in client.get("/accounts" + query).text


def test_dollar_and_percent_toggle(client, history):
    pct = client.get("/accounts?range=custom&from=2026-09-01&to=2026-10-09").text
    value = client.get("/accounts?range=custom&from=2026-09-01&to=2026-10-09&show=value").text
    # the toggle keeps the range; the current mode is marked; the range form carries the mode
    assert 'href="/accounts?range=custom&amp;from=2026-09-01&amp;to=2026-10-09&amp;show=value"' in pct
    assert 'title="% change" aria-current="page">%</a>' in pct
    assert 'title="Total value" aria-current="page">$</a>' in value
    assert '<input type="hidden" name="show" value="value">' in value
    # the measures are the same in both modes; only the bars change
    assert pct.count(">Total value<") == value.count(">Total value<") == 1
    assert "&#34;money&#34;: false" in pct or '"money": false' in pct
    assert client.get("/accounts?show=nonsense").status_code == 200
    # Net worth has the same toggle (2026-10-13), linking to itself with the range kept
    nw = client.get("/networth?range=custom&from=2026-09-01&to=2026-10-09&show=value").text
    assert 'href="/networth?range=custom&amp;from=2026-09-01&amp;to=2026-10-09&amp;show=pct"' in nw
    assert 'title="Total value" aria-current="page">$</a>' in nw and nw.count('data-chart="stackedarea"') == 5


def test_percent_and_value_bars(history):
    series = balances.range_changes(history, "2026-09-01", "2026-10-09", TODAY)
    whole = balances.summary(history, "2026-09-01", "2026-10-09", TODAY)
    retirement = visuals.balance_change_mix(series, "retirement", whole, "pct")
    assert not retirement["money"] and retirement["series"][0]["values"] == [5.0, 0.0]        # +$2,500 on $50,000
    assert visuals.balance_change_mix(series, "retirement", whole, "value")["series"][0]["values"] == [52500.0, 52500.0]
    # credit cards: the % follows the amount owed (+25% = owing more), the value is negative (owed)
    cards = visuals.balance_change_mix(series, "credit_cards", whole, "pct")
    assert cards["series"][0]["values"] == [25.0, 0.0]
    assert visuals.balance_change_mix(series, "credit_cards", whole, "value")["series"][0]["values"] == [-2500.0, -2500.0]
    # net worth: % parts add up to the month's % change; $ parts add up to net worth
    groups = [g for g, _ in balances.GROUPS]
    nw_pct = visuals.networth_change_mix(series, groups, whole, "pct")
    assert sum(s["values"][0] for s in nw_pct["series"]) == pytest.approx(series[0].pct() * 100, abs=0.05)
    nw_value = visuals.networth_change_mix(series, groups, whole, "value")
    assert sum(s["values"][1] for s in nw_value["series"]) == series[1].value_after() / 100


def test_first_row_sits_16px_below_the_ribbon(client):
    # the empty range form comes first in <main>; it's marked so the tiles after it lose their top margin (app.css)
    for path in ("/accounts", "/networth"):
        assert re.search(r'<main>\s*<form id="txn-filters" class="form-anchor"', client.get(path).text)
    css = client.get("/static/app.css").text
    assert "main > .form-anchor:first-child + * { margin-top: 0; }" in css


def test_over_time_follows_the_range_and_the_toggle(history):
    """Net worth's Over time chart (2026-10-13): every day of the range, empty before the first snapshot; % = change
    since the day before the range."""
    pct = visuals.networth_trend_range(history, "2026-08-30", "2026-10-09", "pct", TODAY)
    assert [p["d"] for p in pct][:3] == ["2026-08-30", "2026-08-31", "2026-09-01"] and pct[-1]["d"] == "2026-10-09"
    assert pct[0]["v"] is None                                  # before the first snapshot (Aug 31)
    assert pct[1]["v"] == 0.0                                   # the range's first data day is the base
    start = (10000 - 2000 + 50000 + 30000 + 0 + 20000 - 15000) * 100      # Aug 31's net worth
    sep30 = (6000 - 2500 + 52500 + 29500 + 3000 + 19800 - 14600) * 100
    point = next(p for p in pct if p["d"] == "2026-09-30")
    assert point["v"] == round((sep30 - start) / start * 100, 2) and point["net"] == sep30 / 100
    assert next(p for p in pct if p["d"] == "2026-10-02")["carried"]      # no sync: carried, drawn dashed
    value = visuals.networth_trend_range(history, "2026-09-01", "2026-09-30", "value", TODAY)
    assert value[0]["v"] == start / 100 and value[-1]["v"] == sep30 / 100  # Sep 1 carries Aug 31


def test_default_ranges(client):
    # Net worth opens on 90 days, Balances on 365 days (user's requests 2026-10-13); a chosen range still wins
    assert 'aria-label="Date range: 90 days"' in client.get("/networth").text
    assert 'aria-label="Date range: 365 days"' in client.get("/accounts").text
    assert 'aria-label="Date range: YTD"' in client.get("/networth?range=ytd").text


def test_over_time_card_has_no_helper_text(client):
    page = client.get("/networth").text
    card = page[page.index("<h2>Net worth, 90 days</h2>"):page.index('id="nw-trend"')]   # titled by the range
    assert "<p" not in card                                     # removed (user's request 2026-10-13)


def test_tiles_show_the_ranges_percent_change(client, history):
    from fintrack import networth

    change = networth.range_change(history, "2026-09-01", "2026-09-30", TODAY)
    assert change.before.date == "2026-08-31" and change.after.date == "2026-09-30"
    assert change.assets_pct == pytest.approx((110800 - 110000) / 110000)          # assets $110,000 -> $110,800
    assert change.liabilities_pct == pytest.approx((17100 - 17000) / 17000)        # owed $17,000 -> $17,100
    assert change.net_pct == pytest.approx((93700 - 93000) / 93000)
    assert networth.range_change(history, "2026-01-01", "2026-03-31", TODAY) is None  # before any history
    page = client.get("/networth?range=custom&from=2026-09-01&to=2026-09-30").text
    hero = page[page.index('class="hero"'):page.index("Assets</div>")]
    assert '<span class="delta-up">▲ 0.8%</span>' in hero
    tiles = page[page.index("Assets</div>"):page.index("</section>")]
    assert '<span class="delta-up">▲ 0.7%</span>' in tiles               # assets up: green ▲
    assert '<span class="delta-down">▲ 0.6%</span>' in tiles             # owed up: red ▲ (opposite colours)


def test_net_worth_cards_fit_four_per_row(client):
    # the Net worth page's cards opt in to four per row on wide screens (user's request 2026-10-13); Balances doesn't
    assert 'class="cards n5 fit4"' in client.get("/networth?range=custom&from=2026-09-01&to=2026-09-30&cards=compact").text
    assert "fit4" not in client.get("/accounts").text
    css = client.get("/static/app.css").text
    assert ".savings-cards .cards.fit4.n4, .savings-cards .cards.fit4.n5 { grid-template-columns: repeat(4" in css


def test_cash_comes_first(client):
    page = client.get("/networth?range=custom&from=2026-09-01&to=2026-09-30").text
    order = [page.index(f"<h2>{t}</h2>") for t in ("Cash", "Retirement", "Investment", "Other assets", "Credit cards")]
    assert order == sorted(order)                                     # user's request 2026-10-13
    head = page[page.index('id="balances-table"'):page.index("</thead>")]
    assert head.index(">Cash</th>") < head.index(">Retirement</th>")


def test_recent_activity_by_kind(history):
    """Detailed cards (2026-10-13): the newest movements inside the range on each kind's accounts, in the Transactions
    page's sign (+ = money out, - = money in)."""
    db = history
    rows = [(CHECKING, "2026-09-05", 120000, "FAKE RENT", "RENT_AND_UTILITIES"),
            (CHECKING, "2026-09-15", -500000, "FAKE PAYROLL", "INCOME"),
            (CHECKING, "2026-07-01", 999, "FAKE OLD", "FOOD_AND_DRINK"),             # before the range: left out
            (CARD, "2026-09-20", 4500, "FAKE DINER", "FOOD_AND_DRINK")]
    for n, (acc, day, cents, name, key) in enumerate(rows):
        db.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key)"
                   " VALUES (?, ?, ?, ?, ?, ?)", (f"t{n}", acc, day, cents, name, key))
    db.execute("INSERT INTO investment_transactions (plaid_investment_transaction_id, account_id, date, name, amount_cents,"
               " type, subtype) VALUES ('i1', ?, '2026-09-10', 'FAKE DIVIDEND', -2500, 'cash', 'dividend')", (IRA,))
    db.execute("INSERT INTO investment_transactions (plaid_investment_transaction_id, account_id, date, name, amount_cents,"
               " type, subtype) VALUES ('i2', ?, '2026-09-11', 'FAKE SWEEP OUT', 9999, 'cash', 'withdrawal')", (BROKERAGE,))
    db.commit()
    cash = balances.recent_activity(db, "cash", "2026-09-01", "2026-09-30")
    assert [(a.date, a.description, a.amount_cents) for a in cash] == [
        ("2026-09-15", "FAKE PAYROLL", -500000), ("2026-09-05", "FAKE RENT", 120000)]          # newest first
    assert cash[0].detail.startswith("Fake checking · ")
    assert [a.description for a in balances.recent_activity(db, "credit_cards", "2026-09-01", "2026-09-30")] == ["FAKE DINER"]
    retirement = balances.recent_activity(db, "retirement", "2026-09-01", "2026-09-30")
    assert [(a.description, a.amount_cents, a.detail) for a in retirement] == [("FAKE DIVIDEND", -2500, "Fake IRA · Dividend")]
    assert balances.recent_activity(db, "investment", "2026-09-01", "2026-09-30") == []       # sweeps are hidden
    assert len(balances.recent_activity(db, "cash", "2026-01-01", "2026-12-31", limit=1)) == 1


def test_compact_and_detailed_switch(client):
    query = "?range=custom&from=2026-09-01&to=2026-09-30"
    compact = client.get("/networth" + query + "&cards=compact").text
    detailed = client.get("/networth" + query).text                     # Detailed by default (2026-10-13)
    # icons, not words (2026-10-13): each link named by aria-label / title, the current one marked
    assert 'aria-label="Compact" title="Compact" aria-current="page"><svg class="icon"' in compact
    assert 'aria-label="Detailed" title="Detailed: with recent activity" aria-current="page"><svg' in detailed
    assert '>Compact</a>' not in compact
    assert "card-detailed" not in compact and "Recent activity" not in compact
    assert detailed.count('class="card card-detailed"') == 5 and detailed.count("Recent activity") == 5
    assert 'class="cards detailed"' in detailed and "No activity in this range." in detailed
    # each control keeps the others: $ / % links keep cards=detailed, the range form carries it
    assert "cards=detailed" in detailed.split('aria-label="Card layout"')[1].split("</nav>")[0] and '<input type="hidden" name="cards" value="detailed">' in detailed
    assert client.get("/networth?cards=nonsense").status_code == 200


def test_seven_rows_and_one_card_height(client, history):
    # the activity list holds 7 (user's request 2026-10-13); both views share one card height in app.css
    for n in range(9):
        history.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name,"
                        " plaid_category_key) VALUES (?, ?, ?, 100, 'FAKE COFFEE', 'FOOD_AND_DRINK')",
                        (f"c{n}", CHECKING, f"2026-09-{10 + n:02d}"))
    history.commit()
    assert len(balances.recent_activity(history, "cash", "2026-09-01", "2026-09-30")) == 7
    page = client.get("/networth?range=custom&from=2026-09-01&to=2026-09-30&cards=detailed").text
    cash = page[page.index("<h2>Cash</h2>"):page.index("<h2>Retirement</h2>")]
    assert cash.count('title="FAKE COFFEE"') == 7 and page.count('class="kind-body"') == 5
    css = client.get("/static/app.css").text
    assert "--kind-card-h: calc(30px + 7 * var(--activity-row))" in css
    assert ".cards.fit4 .kind-body, .cards.detailed .kind-body { display: flex; flex-direction: column; min-height: var(--kind-card-h); }" in css


def test_net_worth_chart_title_names_the_range(client):
    assert "<h2>Net worth, YTD</h2>" in client.get("/networth?range=ytd").text
    assert "<h2>Net worth, Sep 1 – Sep 30</h2>" in client.get("/networth?range=custom&from=2026-09-01&to=2026-09-30").text


def test_activity_dots_match_the_chart(client, history):
    # each row's dot = its segment's colour in the card's chart (user's request 2026-10-13): hue by kind, shade by
    # the segment's place among the chart's series
    history.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key)"
                    " VALUES ('d1', ?, '2026-09-10', 4500, 'FAKE DINER', 'FOOD_AND_DRINK')", (CARD,))
    history.commit()
    rows = balances.recent_activity(history, "credit_cards", "2026-09-01", "2026-09-30")
    assert rows[0].segment == "Fake card"                     # = the Credit cards chart's segment (by card)
    page = client.get("/networth?range=custom&from=2026-09-01&to=2026-09-30&cards=detailed").text
    cards = page[page.index("<h2>Credit cards</h2>"):]
    assert '<span class="seg-text hue-credit_cards shade-0">Fake card</span> · ' in cards
    css = client.get("/static/app.css").text
    assert ".seg-text.shade-1 { color: color-mix(in srgb, var(--hue) 60%, var(--surface)); }" in css


def test_net_worth_tiles_three_equal_columns(client):
    # three equal tiles on one row at every width, figures scaling with the row (user's request 2026-10-13)
    page = client.get("/networth").text
    assert re.search(r'<div class="nw-kpis-wrap">\s*<section class="kpis nw-kpis">\s*<div class="hero">', page)
    css = client.get("/static/app.css").text
    assert ".kpis.nw-kpis { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 0; }" in css
    assert "grid-column: span 2" not in css.split(".nw-kpis-wrap")[1].split(".tabbar-group")[0]


def test_account_cards_on_balances(client, history):
    """Balances (user's request 2026-10-13): no tiles; a card per kind listing its accounts (logo or letter, name,
    value, last refresh or Manual); the title = the kind's total + its $ and % change over the period."""
    for acc, cents in [(CHECKING, 800000), (CARD, 250000), (LENT, 300000)]:      # today's balances (rows show these)
        history.execute("UPDATE accounts SET current_balance_cents = ? WHERE id = ?", (cents, acc))
    history.commit()
    page = client.get("/accounts?range=custom&from=2026-09-01&to=2026-09-30").text
    assert 'class="kpis"' not in page and "This month so far" not in page
    assert page.count('class="card account-card"') == 5
    cash = page[page.index('<h2>Cash <span'):page.index('<h2>Retirement <span')]
    assert '<span class="account-card-total ">$6,000.00</span>' in cash          # total at the end of the period, with cents
    assert '<span class="delta-down">-$4,000</span>' in cash and "▼ 40.0%" in cash  # its $ and % change
    assert "Fake checking" in cash and "$8,000.00" in cash
    assert "never" not in cash and "manual-tag" not in page.split("<h2>Summary</h2>")[0]   # no refresh column (2026-10-13)
    cards = page[page.index('<h2>Credit cards <span'):]
    assert "-$2,500" in cards and '<span class="delta-down">▲ 25.0%</span>' in cards  # owing more: red ▲
    assert "-$2,500.00" in cards                                                   # the card's row: owed, negative
    lent = page[page.index('<h2>Other assets <span'):page.index('<h2>Credit cards <span')]
    assert f'href="/accounts/{LENT}"' in lent


def test_institution_logos_fetched_once(conn, home, gateway):
    from fintrack import logos

    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_id, institution_name)"
                 " VALUES (1, 'i1', 'sandbox', 'ins_1', 'Fake Bank')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification)"
                 " VALUES (1, 1, 'a1', 'Fake checking', 'depository', 'asset')")
    conn.commit()
    assert logos.fetch_institution_logos(conn, home, gateway) == (1, 0)
    files = logos.institution_logo_files(conn)
    assert (home / "logos" / files[1]).read_bytes().startswith(logos.PNG_SIGNATURE)
    assert oct((home / "logos" / files[1]).stat().st_mode)[-3:] == "600"
    assert logos.fetch_institution_logos(conn, home, gateway) == (0, 0)            # once per institution
    assert [c for c in gateway.calls if c[0] == "get_institution_logo"] == [("get_institution_logo", "ins_1")]


def test_institution_without_logo_or_failing_call(conn, home, gateway):
    from fintrack import logos
    from fintrack.plaid_client import PlaidError

    for n in (1, 2):
        conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_id) VALUES (?, ?, 'sandbox', ?)",
                     (n, f"i{n}", f"ins_{n}"))
    conn.commit()
    gateway.institution_logo = None                                                # Plaid has no logo
    assert logos.fetch_institution_logos(conn, home, gateway) == (0, 2)
    assert {r[0] for r in conn.execute("SELECT status FROM institution_logos")} == {"none"}
    conn.execute("UPDATE institution_logos SET fetched_at = datetime('now', '-31 days')")
    conn.commit()

    def boom(_institution_id):
        raise PlaidError("INSTITUTION_NOT_FOUND", "fake")
    gateway.get_institution_logo = boom                                            # a failing call never raises
    assert logos.fetch_institution_logos(conn, home, gateway) == (0, 2)
    assert {r[0] for r in conn.execute("SELECT status FROM institution_logos")} == {"failed"}
    gateway.get_institution_logo = lambda _i: "bm90IGEgcG5n"                         # "not a png": refused
    conn.execute("UPDATE institution_logos SET fetched_at = datetime('now', '-31 days')")
    conn.commit()
    assert logos.fetch_institution_logos(conn, home, gateway) == (0, 2)
    assert not (home / "logos").exists() or not list((home / "logos").iterdir())


def test_balances_uses_the_budgets_sizing(client):
    # account cards = the main column; the Accounts card = the summary's column from 1200px, hidden below
    # (user's request 2026-10-13)
    page = client.get("/accounts").text
    main, side = page.index('<div class="balances-main">'), page.index('<aside class="balances-side">')
    assert main < page.index('class="card account-card"') < side < page.index("<h2>Summary</h2>")
    css = client.get("/static/app.css").text
    assert ".balances-side { display: none; }" in css
    assert '<div class="account-cards">' in page and ".account-cards { display: grid; grid-template-columns: minmax(0, 1fr);" in css
    assert ".balances-layout { grid-template-columns: minmax(0, 1fr) minmax(325px, 400px); }" in css


def test_accounts_card_proportion_bars(client, history):
    """Balances' Accounts card (user's request 2026-10-13): a proportion bar per side, a segment per account type,
    coloured like the Net worth page's cards (kind hue, shaded by order within the kind)."""
    for acc, cents in [(CHECKING, 800000), (CARD, 250000), (IRA, 6000000), (BROKERAGE, 2000000), (LENT, 300000),
                       (CAR, 1900000), (LOAN, 1400000)]:
        history.execute("UPDATE accounts SET current_balance_cents = ? WHERE id = ?", (cents, acc))
    history.commit()
    kinds = balances.kind_of_accounts(history)
    assert (kinds[IRA], kinds[BROKERAGE], kinds[CHECKING], kinds[CARD], kinds[LENT]) == (
        "retirement", "investment", "cash", "credit_cards", "purposes")
    page = client.get("/accounts").text
    assert page.count('class="prop-bar"') == 2                              # assets, liabilities
    assets = page[page.index('aria-label="Assets by kind:'):page.index('aria-label="Liabilities by kind:')]
    assert 'class="meter-fill prop-seg hue-cash shade-0"' in assets
    assert 'data-share="0.0727"' in assets                                    # $8,000 of $110,000 assets
    assert 'class="prop-swatch hue-cash shade-0"' in page                     # the legend swatch on its type row
    css = client.get("/static/app.css").text
    assert ":is(.prop-seg, .prop-swatch).shade-1 { background: color-mix(in srgb, var(--hue) 60%, var(--surface)); }" in css
    overview = client.get("/").text                                         # the Overview's card has them too (2026-10-23)
    assert "prop-bar" in overview and overview.count("prop-swatch") == overview.count('class="type-row"')


def test_accounts_card_grouped_like_the_cards(client, history):
    # Balances' Accounts card: rows by kind in the cards' order (user's request 2026-10-13); the Overview keeps types
    for acc, cents in [(CHECKING, 800000), (CARD, 250000), (IRA, 6000000), (BROKERAGE, 2000000), (LENT, 300000),
                       (CAR, 1900000), (LOAN, 1400000)]:
        history.execute("UPDATE accounts SET current_balance_cents = ? WHERE id = ?", (cents, acc))
    history.commit()
    page = client.get("/accounts").text
    card = page[page.index("<h2>Summary</h2>"):]
    labels = [label.strip() for label in re.findall(r'prop-swatch [^"]*" aria-hidden="true"></span>([^<]*)<', card)]
    assert "data-toggle-group" not in card and "account-row" not in card       # no drop-down rows (2026-10-13)
    assert "Assets − liabilities" not in card                                  # removed (2026-10-13)
    assert labels == ["Cash", "Retirement", "Investment", "Other assets", "Property &amp; other",
                      "Credit cards", "Loans &amp; other"]                       # "&" is escaped in the HTML
    segs = re.findall(r'prop-seg hue-([a-z_]+) shade-(\d)', card)
    assert segs == [("cash", "0"), ("retirement", "0"), ("investment", "0"), ("purposes", "0"), ("property_loans", "0"),
                    ("credit_cards", "0"), ("property_loans", "0")]


def test_account_rows_open_the_account_page(client, history):
    history.execute("UPDATE accounts SET current_balance_cents = 800000 WHERE id = ?", (CHECKING,))
    history.commit()
    page = client.get("/accounts").text
    assert f'<tr class="row-link" data-href="/accounts/{CHECKING}">' in page
    sub = client.get(f"/accounts/{CHECKING}")
    assert sub.status_code == 200 and "<h2>Fake Broker · Fake checking</h2>" in sub.text    # source · account
    assert "<title>Fake checking · Track Plan Grow</title>" in sub.text                  # nothing else in the page title
    assert sub.text.count("<h2>") == len(set(re.findall(r"<h2>[^<]*</h2>", sub.text)))   # no card rendered twice
    assert 'aria-current="page" class="active">Accounts</a>' in sub.text          # Accounts section, Accounts tab (was Balances)
    assert "<h2>Manual · Fake car</h2>" in client.get(f"/accounts/{CAR}").text
    assert client.get("/accounts/9999").status_code == 404


def test_account_page_transactions(client, history):
    """An account page's Transactions card (user's request 2026-10-13): the Transactions page's rows without the
    account column, read-only, newest first; investment accounts list their investment activity."""
    for n, (day, cents) in enumerate([("2026-09-05", 120000), ("2026-09-15", -500000)]):
        history.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name,"
                        " plaid_category_key) VALUES (?, ?, ?, ?, ?, 'FOOD_AND_DRINK')", (f"p{n}", CHECKING, day, cents,
                                                                                        f"FAKE ROW {n}"))
    history.execute("INSERT INTO investment_transactions (plaid_investment_transaction_id, account_id, date, name,"
                    " amount_cents, type, subtype) VALUES ('i9', ?, '2026-09-10', 'FAKE DIVIDEND', -2500, 'cash', 'dividend')",
                    (IRA,))
    history.commit()
    page = client.get(f"/accounts/{CHECKING}").text
    card = page[page.index("<h2>Transactions</h2>"):]
    assert card.index("FAKE ROW 1") < card.index("FAKE ROW 0")                   # newest first
    head = card[card.index("<thead>"):card.index("</thead>")]
    assert ">Category</a></th>" in head and ">Account</a></th>" not in head and "select-col" not in head
    assert "row-select" not in card and "Fake checking</td>" not in card
    assert '/edit?view=account"' in card                                          # editable, in this table's format
    assert "+$5,000.00" in card                                                  # money in, as on Transactions
    inv = client.get(f"/accounts/{IRA}").text
    # investment activity in the same table, edited the same way (user's request 2026-10-31)
    assert "FAKE DIVIDEND" in inv and ">Category</a></th>" in inv and 'data-edit-url="/transactions/-1/edit?view=account"' in inv
    car = client.get(f"/accounts/{CAR}").text                       # a manual account: its History instead
    assert "<h2>Balances</h2>" in car and "<h2>Transactions</h2>" not in car
    # the Transactions page itself is unchanged: select boxes, edit panel, account column
    txns = client.get("/transactions?range=custom&from=2026-09-01&to=2026-09-30").text
    assert "row-select" in txns and "data-edit-url" in txns and ">Account</a></th>" in txns


def test_account_page_rows_edit_in_place(client, history):
    """Rows on an account page are edited like on Transactions (user's request 2026-10-13); every row the edits return
    keeps the account page's format (no select box, no account column)."""
    from tests.test_purposes import ORIGIN

    history.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name,"
                    " plaid_category_key) VALUES ('e1', ?, '2026-09-05', 120000, 'FAKE RENT CO', 'RENT_AND_UTILITIES')",
                    (CHECKING,))
    history.commit()
    t_id = history.execute("SELECT id FROM transactions WHERE plaid_transaction_id = 'e1'").fetchone()[0]
    panel = client.get(f"/transactions/{t_id}/edit?view=account").text
    assert 'colspan="5"' in panel and '<input type="hidden" name="view" value="account">' in panel
    assert f"/rules/new?from={t_id}" in panel
    saved = client.post(f"/transactions/{t_id}/edit", data={"description": "Fake landlord", "category": "",
                                                            "view": "account"}, headers=ORIGIN).text
    assert "Fake landlord" in saved and "row-select" not in saved and "Fake checking" not in saved
    assert '/edit?view=account"' in saved
    full = client.post(f"/transactions/{t_id}/edit", data={"description": "Fake landlord", "category": ""},
                       headers=ORIGIN).text                                      # from Transactions: the full row
    assert "row-select" in full and "Fake checking" in full
    flagged = client.post(f"/transactions/{t_id}/transfer", data={"ignore": "1", "view": "account"}, headers=ORIGIN).text
    assert "row-select" not in flagged


def test_back_returns_to_an_account_page():
    from fintrack.web.transaction_edits import _back

    assert _back("/accounts/7", msg="ok").headers["location"] == "/accounts/7?msg=ok"
    assert _back("/accounts/7x", msg="ok").headers["location"] == "/transactions?msg=ok"     # only these
    assert _back("https://evil.example/", msg="ok").headers["location"] == "/transactions?msg=ok"


def test_credit_card_rows_show_the_statement(client, history):
    from datetime import timedelta

    from fintrack.web.deps import date_label

    # Dates relative to today: the page counts down to a due date still ahead (a fixed date went stale once it passed)
    today = date.today()
    due, statement_day, paid_day = (str(today + timedelta(days=n)) for n in (10, -20, -15))
    history.execute("UPDATE accounts SET current_balance_cents = 250000 WHERE id = ?", (CARD,))
    history.execute("INSERT INTO card_statements (account_id, statement_balance_cents, due_date) VALUES (?, 145000, ?)",
                    (CARD, due))
    history.commit()
    page = client.get("/accounts").text
    cards = page[page.index("<h2>Credit cards <span"):page.index("<h2>Summary</h2>")]
    assert "Statement" not in cards and "$1,450.00 ·" in cards and f"due {date_label(due)} · " in cards   # a countdown while owed
    # paid since the statement: a paid indicator instead of the countdown
    history.execute("INSERT INTO card_statements (account_id, statement_balance_cents, due_date, statement_date)"
                    " VALUES (?, 145000, ?, ?) ON CONFLICT (account_id) DO UPDATE SET"
                    " statement_date = excluded.statement_date", (CARD, due, statement_day))
    history.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key)"
                    " VALUES ('ccpay', ?, ?, -145000, 'FAKE PAYMENT', 'CREDIT_CARD_PAYMENT')", (CARD, paid_day))
    history.commit()
    page = client.get("/accounts").text
    assert '<span class="status-text good">✓ Paid</span>' in page[page.index("<h2>Credit cards <span"):]


def test_investment_account_page_shows_holdings(client, history):
    """The investment account page's Holdings card on its Balances account page too (user's request 2026-10-13)."""
    history.execute("INSERT INTO holdings (account_id, security_id, name, ticker, quantity, price, value_cents,"
                    " cost_basis_cents, is_cash) VALUES (?, 'sec-1', 'Fake Index Fund', 'FAKEX', 10, 100.0, 100000, 90000, 0)",
                    (IRA,))
    history.commit()
    page = client.get(f"/accounts/{IRA}").text
    card = page[page.index("<h2>Holdings</h2>"):page.index("<h2>Transactions</h2>")]
    assert "Fake Index Fund" in card and "FAKEX" in card and "$1,000.00" in card and "$900.00" in card
    assert "<h2>Holdings</h2>" not in client.get(f"/accounts/{CHECKING}").text     # investment accounts only


def test_rules_page_is_its_own_tab(client, history):
    # category rules moved from Categories to Settings → Rules (user's request 2026-10-13); an old prefilled link forwards
    page = client.get("/rules").text
    assert 'aria-current="page" class="active">Rules</a>' in page and 'href="/rules/new"' in page
    tabs = page[page.index('aria-label="Pages"'):page.index("</nav>", page.index('aria-label="Pages"'))]
    # Setup is the first Settings tab and holds the old Accounts page (user's request 2026-10-14)
    assert tabs.index(">Setup</a>") < tabs.index(">Account types</a>") < tabs.index(">Categories</a>") < tabs.index(">Rules</a>")
    assert ">Accounts</a>" not in tabs
    old = client.get("/categories?pattern=Fake+Coffee&category=FOOD_AND_DRINK", follow_redirects=False)
    assert old.status_code == 303 and old.headers["location"].startswith("/rules/new?pattern=Fake+Coffee")
