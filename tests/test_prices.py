"""Security price history and the Overview movers table (fake securities only)."""

from datetime import date

import pytest

from fintrack import prices

BROKERAGE, IRA = 1, 2


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Broker')")
    for acc_id, name in [(BROKERAGE, "Fake Brokerage"), (IRA, "Fake IRA")]:
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification)"
                     " VALUES (?, 1, ?, ?, 'investment', 'asset')", (acc_id, f"acc-{acc_id}", name))
    conn.commit()
    return conn


def hold(conn, account, sid, ticker, price, value_cents, cost_cents, is_cash=0, security_type="equity"):
    conn.execute("INSERT INTO holdings (account_id, security_id, name, ticker, security_type, is_cash, price,"
                 " value_cents, cost_basis_cents) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (account, sid, f"Fake {ticker}", ticker, security_type, is_cash, price, value_cents, cost_cents))
    conn.commit()


def test_record_one_price_per_date_and_skip_missing(db):
    prices.record(db, [("s1", "AAA", "Fake AAA", 10.0, "2026-10-02"), ("s2", "BBB", "Fake BBB", None, None)])
    prices.record(db, [("s1", "AAA", "Fake AAA", 10.5, "2026-10-02")])           # same price date: replaced
    prices.record(db, [("s1", "AAA", "Fake AAA", 11.0, None)], today=date(2026, 10, 5))   # no as-of: sync day
    rows = db.execute("SELECT price_date, price FROM security_prices ORDER BY price_date").fetchall()
    assert [tuple(r) for r in rows] == [("2026-10-02", 10.5), ("2026-10-05", 11.0)]


def test_movers_before_and_after_a_second_price_date(db):
    hold(db, BROKERAGE, "s1", "AAA", 12.0, 120000, 100000)     # +20% vs cost
    hold(db, IRA, "s1", "AAA", 12.0, 60000, 50000)             # same security, other account
    hold(db, BROKERAGE, "s2", "BBB", 5.0, 50000, 100000)       # -50% vs cost
    hold(db, BROKERAGE, "cash", None, 1.0, 9900, None, is_cash=1)   # cash: never a mover
    prices.record(db, [("s1", "AAA", "Fake AAA", 12.0, "2026-10-02"), ("s2", "BBB", "Fake BBB", 5.0, "2026-10-02")])

    first = prices.movers(db)
    assert first["by"] == "value" and first["count"] == 2
    assert [m["ticker"] for m in first["rows"]] == ["AAA", "BBB"]          # largest holding first
    aaa = first["rows"][0]
    assert aaa["value_cents"] == 180000 and aaa["change"] is None

    prices.record(db, [("s1", "AAA", "Fake AAA", 13.2, "2026-10-05"), ("s2", "BBB", "Fake BBB", 5.1, "2026-10-05")])
    later = prices.movers(db)
    assert later["by"] == "change"
    assert [m["ticker"] for m in later["rows"]] == ["AAA", "BBB"]          # +10% beats +2%
    assert later["rows"][0]["change"] == pytest.approx(0.10) and later["rows"][0]["previous_date"] == "2026-10-02"


def test_sync_records_prices_from_holdings(db):
    from fintrack.sync import store_holdings

    store_holdings(db, 1, {
        "securities": [{"security_id": "s1", "ticker_symbol": "AAA", "name": "Fake AAA", "type": "equity"},
                       {"security_id": "c1", "ticker_symbol": None, "name": "Fake Cash", "type": "cash",
                        "is_cash_equivalent": True}],
        "holdings": [{"account_id": "acc-1", "security_id": "s1", "quantity": 2, "institution_price": 50.0,
                      "institution_value": 100.0, "cost_basis": 80.0, "institution_price_as_of": "2026-10-02"},
                     {"account_id": "acc-1", "security_id": "c1", "quantity": 5, "institution_price": 1.0,
                      "institution_value": 5.0, "cost_basis": None, "institution_price_as_of": None}],
    })
    assert [tuple(r) for r in db.execute("SELECT security_id, price_date, price FROM security_prices")] == [
        ("s1", "2026-10-02", 50.0)]


def test_movers_sorted_by_size_of_percent_change_and_unlimited(db):
    from fintrack.quotes import Quote

    for k, (ticker, change) in enumerate([("AAA", 0.01), ("BBB", -0.03), ("CCC", 0.04), ("DDD", None)]):
        hold(db, BROKERAGE, f"s{k}", ticker, 10.0, 10000 * (k + 1), None)
    live = {t: Quote(price=10.0, change_pct=c, previous_close=10.0, as_of=None, fetched_at=1790000000.0 + k)
            for k, (t, c) in enumerate([("AAA", 0.01), ("BBB", -0.03), ("CCC", 0.04)])}
    m = prices.movers(db, limit=None, live=live)
    # the biggest moves first, up or down (user's request 2026-10-23); no change last
    assert [r["ticker"] for r in m["rows"]] == ["CCC", "BBB", "AAA", "DDD"]
    assert m["fetched_at"] == 1790000002.0                                     # newest fetch time


def test_only_stocks_etfs_and_funds_are_quoted_live(db):
    """A crypto "BTC" must never be valued at a same-named stock/ETF's live price."""
    from fintrack.quotes import Quote

    hold(db, BROKERAGE, "s1", "AAA", 10.0, 10000, None)
    hold(db, BROKERAGE, "s2", "FUNDX", 20.0, 20000, None, security_type="mutual fund")
    hold(db, BROKERAGE, "s3", "BTC", 60000.0, 30000, None, security_type="cryptocurrency")
    hold(db, BROKERAGE, "s4", "OPT", 1.0, 5000, None, security_type="derivative")
    hold(db, BROKERAGE, "s5", "UNK", 1.0, 5000, None, security_type=None)
    assert sorted(prices.held_tickers(db)) == ["AAA", "FUNDX"]

    live = {t: Quote(price=40.0, change_pct=0.01, previous_close=39.6, as_of=None) for t in ("AAA", "BTC", "OPT")}
    by_ticker = {r["ticker"]: r for r in prices.movers(db, limit=None, live=live)["rows"]}
    assert by_ticker["AAA"]["source"] == "live"
    assert by_ticker["BTC"]["source"] == "plaid" and by_ticker["BTC"]["price"] == 60000.0
    assert by_ticker["BTC"]["value_cents"] == 30000 and by_ticker["OPT"]["source"] == "plaid"


def test_movers_benchmark_row(db):
    """SPY, always the Overview's 6th mover (user's request 2026-10-23): live, else its last two recorded closes; your
    holding when you have one, kept out of the ranked rows."""
    from fintrack.quotes import Quote

    hold(db, BROKERAGE, "s1", "AAA", 10.0, 10000, None)
    assert prices.movers(db, benchmark="SPY")["benchmark"]["price"] is None            # nothing recorded yet
    db.executemany("INSERT INTO benchmark_prices (symbol, price_date, price) VALUES ('SPY', ?, ?)",
                   [("2026-10-21", 500.0), ("2026-10-22", 505.0)])
    db.commit()
    spy = prices.movers(db, benchmark="SPY")["benchmark"]
    assert (spy["price"], spy["change"], spy["held"], spy["value_cents"]) == (505.0, pytest.approx(0.01), False, None)
    live = {"SPY": Quote(price=510.0, change_pct=-0.02, previous_close=520.0, as_of=None)}
    spy = prices.movers(db, live=live, benchmark="SPY")["benchmark"]
    assert (spy["price"], spy["change"], spy["source"]) == (510.0, -0.02, "live")
    hold(db, BROKERAGE, "s2", "SPY", 505.0, 50500, None)                               # you hold it
    m = prices.movers(db, limit=None, benchmark="SPY")
    assert m["benchmark"]["held"] and m["benchmark"]["value_cents"] == 50500
    assert [r["ticker"] for r in m["rows"]] == ["AAA"]
