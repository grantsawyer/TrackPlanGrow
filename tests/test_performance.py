"""Investments performance cards: your stocks vs benchmark ETFs (fake prices only)."""

import re
from datetime import date, datetime, timedelta, timezone

import pytest

from fintrack import db, performance
from fintrack.quotes import Quote

TODAY = date(2026, 10, 7)
D = lambda n: (TODAY - timedelta(days=n)).isoformat()   # noqa: E731


def quote(price, change_pct=None, day=TODAY):
    """A live quote timed at noon on `day` (local)."""
    return Quote(price=price, change_pct=change_pct, previous_close=None,
                 as_of=int(datetime(day.year, day.month, day.day, 12).timestamp()))


def bench(conn, symbol, *prices):
    conn.executemany("INSERT INTO benchmark_prices (symbol, price_date, price) VALUES (?, ?, ?)",
                     [(symbol, D(n), p) for n, p in prices])
    conn.commit()


def by_key(conn, live):
    return {m.key: m for m in performance.metrics(conn, live, today=TODAY)}


def test_record_uses_the_quote_date_and_skips_unchanged(conn):
    friday = TODAY - timedelta(days=2)
    performance.record_benchmarks(conn, {"SPY": quote(500.0, day=friday), "AAA": quote(1.0)})
    rows = [tuple(r) for r in conn.execute("SELECT symbol, price_date, price FROM benchmark_prices")]
    assert rows == [("SPY", friday.isoformat(), 500.0)]          # only benchmarks; the quote's own day
    before = db.revision(conn)
    performance.record_benchmarks(conn, {"SPY": quote(500.0, day=friday)})
    assert db.revision(conn) == before                           # same price: nothing written


def test_benchmark_change_per_range(conn):
    bench(conn, "SPY", (100, 400.0), (10, 420.0))
    spy = by_key(conn, {"SPY": quote(440.0, change_pct=-0.003)})["SPY"]
    p90, p365 = spy.periods["90"], spy.periods["365"]
    assert p90.change == pytest.approx(440 / 400 - 1) and p90.since == D(100) and not p90.since > p90.start
    assert p365.change == pytest.approx(440 / 400 - 1) and p365.since > p365.start   # shorter history: "since"
    assert spy.today == pytest.approx(-0.003)
    iwm = by_key(conn, {})["IWM"]                                # nothing recorded, no quote
    assert iwm.periods["90"].change is None and iwm.today is None


def test_only_todays_price_is_not_a_change(conn):
    bench(conn, "URTH", (0, 150.0))
    assert by_key(conn, {"URTH": quote(151.0)})["URTH"].periods["90"].change is None


@pytest.fixture
def holdings(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env) VALUES (1, 'i1', 'sandbox')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification)"
                 " VALUES (1, 1, 'a1', 'Fake Brokerage', 'investment', 'asset')")
    for sid, ticker, kind, qty, value in [("s1", "AAA", "equity", 30, 330000), ("s2", "FUNDX", "mutual fund", 10, 90000),
                                          ("c1", None, "cash", 500, 50000)]:
        conn.execute("INSERT INTO holdings (account_id, security_id, ticker, security_type, is_cash, quantity, value_cents)"
                     " VALUES (1, ?, ?, ?, ?, ?, ?)", (sid, ticker, kind, 1 if kind == "cash" else 0, qty, value))
    conn.executemany("INSERT INTO security_prices (security_id, price_date, ticker, price) VALUES (?, ?, ?, ?)",
                     [("s1", D(30), "AAA", 100.0), ("s2", D(30), "FUNDX", 100.0), ("s2", D(1), "FUNDX", 90.0)])
    conn.commit()
    return conn


def test_your_stocks_weighted_by_value(holdings):
    # AAA: live $110 (+10%), 30 shares -> $3,300; FUNDX: no quote, recorded $90 (-10%), $900. Cash ignored.
    mine = by_key(holdings, {"AAA": quote(110.0, change_pct=0.02)})["portfolio"]
    expected = (3300 * 0.10 + 900 * -0.10) / (3300 + 900)
    assert mine.periods["90"].change == pytest.approx(expected) and mine.periods["90"].since == D(30)
    assert mine.today == pytest.approx(0.02)                     # only live-priced holdings have "today"
    assert by_key(holdings, {})["portfolio"].periods["90"].change == pytest.approx(-0.10 * 900 / 900)


def test_page_shows_four_tiles_and_sync_records_benchmarks(settings, conn, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.quotes import QuoteGateway
    from fintrack.web.app import create_app

    QuoteGateway.clear_cache()
    from tests.fakes import alpaca_fetch
    when = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    when = when[:-2] + ":" + when[-2:]                               # a quote from now, local zone
    fake = QuoteGateway("fake-key", "fake-secret", fetch=alpaca_fetch(
        {s: 500.0 for s in ("SPY", "IWM", "URTH")}, previous={s: 500.0 / 1.01 for s in ("SPY", "IWM", "URTH")}, when=when))
    client = TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher,
                                   quotes_factory=lambda s: fake), base_url="https://localhost:8443")
    page = client.get("/investments").text
    assert '<section class="kpis one-row perf-tiles">' in page                   # one row at any width
    for label in ("Your stocks", "S&amp;P 500", "Russell 2000", "MSCI World", "SPY", "IWM", "URTH"):
        assert label in page, label
    assert page.count('data-range-of="inv-trend"') == 4 * len(performance.ranges())
    assert '<span class="w-long">today</span><span class="w-short">1D</span> <span class="delta-up">▲ 1.0%</span>' in page   # 1D on phones
    assert "style=" not in page.split("<main>")[1]                # no inline styles (CSP)
    assert conn.execute("SELECT COUNT(*) FROM benchmark_prices").fetchone()[0] == 3

    conn.execute("DELETE FROM benchmark_prices")
    conn.commit()
    client.post("/sync", headers={"origin": "https://localhost:8443"})
    assert conn.execute("SELECT COUNT(*) FROM benchmark_prices").fetchone()[0] == 3
    QuoteGateway.clear_cache()


def test_ranges_include_day_week_and_ytd():
    rs = performance.ranges(TODAY)
    assert [(r.key, r.label) for r in rs] == [("1", "1 day"), ("7", "1 week"), ("ytd", "YTD"),
                                              ("90", "90 days"), ("180", "180 days"), ("365", "365 days")]
    ytd = rs[2]
    assert ytd.lookback == 280 and ytd.chart_days == 281        # Dec 31, 2025 -> Oct 7, 2026
    assert [r.key for r in rs if r.default] == ["90"]


def test_ytd_runs_from_the_last_december_close_and_1_day_from_yesterday(conn):
    conn.executemany("INSERT INTO benchmark_prices (symbol, price_date, price) VALUES ('SPY', ?, ?)",
                     [("2025-12-30", 380.0), ("2025-12-31", 400.0), (D(1), 430.0)])
    conn.commit()
    spy = by_key(conn, {"SPY": quote(440.0)})["SPY"]
    assert spy.periods["ytd"].change == pytest.approx(440 / 400 - 1) and spy.periods["ytd"].since == "2025-12-31"
    assert spy.periods["1"].change == pytest.approx(440 / 430 - 1) and spy.periods["1"].since == D(1)


def test_page_has_the_six_range_buttons(settings, conn, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    client = TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                        base_url="https://localhost:8443")
    seg = client.get("/investments").text.split('data-range-for="inv-trend"')[1].split("</div>")[0]
    assert [b.split('data-range="')[1].split('"')[0] for b in seg.split("<button")[1:]] == \
        ["1", "7", "ytd", "90", "180", "365"]
    assert ">1 day<" in seg and ">1 week<" in seg and ">YTD<" in seg
    assert 'data-range="90" aria-pressed="true"' in seg


def test_benchmark_cards_toggle_a_comparison_line(settings, conn, cipher, gateway):
    """The three benchmark cards are toggle buttons for the chart; prices reach it as JSON, oldest first."""
    import json

    from fastapi.testclient import TestClient

    from fintrack import networth
    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env) VALUES (1, 'i1', 'sandbox')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification, current_balance_cents)"
                 " VALUES (1, 1, 'a1', 'Fake Brokerage', 'investment', 'asset', 100000)")
    conn.execute("INSERT INTO holdings (account_id, security_id, ticker, name, security_type, quantity, value_cents)"
                 " VALUES (1, 's1', 'AAA', 'Fake AAA', 'equity', 1, 100000)")   # the chart needs a priced holding
    conn.execute("INSERT INTO security_prices (security_id, price_date, ticker, price) VALUES ('s1', ?, 'AAA', 1000.0)", (D(2),))
    conn.commit()
    networth.take_snapshot(conn)
    bench(conn, "SPY", (1, 410.0), (3, 400.0))
    assert performance.benchmark_series(conn)["SPY"]["prices"] == [[D(3), 400.0], [D(1), 410.0]]

    client = TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                        base_url="https://localhost:8443")
    page = client.get("/investments").text
    toggles = [t.split('data-compare="')[1].split('"')[0] for t in page.split('data-compare-for="inv-trend"')[1:]]
    assert toggles == ["SPY", "IWM", "URTH", "sec:s1"]             # the 3 benchmark cards (not "Your stocks"), then the row
    assert page.count('role="button" tabindex="0" aria-pressed="false"') == 3
    assert 'data-chart="performance"' in page
    title = page.split('<div class="card-title">')[1].split("</div>")[0]
    assert "Performance" in title and "not synced yet" in title              # no sync, no live prices
    conn.execute("UPDATE items SET last_synced_at = '2026-10-07 13:05:00' WHERE id = 1")
    conn.commit()
    title = client.get("/investments").text.split('<div class="card-title">')[1].split("</div>")[0]
    from fintrack.web.deps import localtime
    assert f"as of {localtime('2026-10-07 13:05:00')}" in title              # e.g. "as of Oct 7, 9:05 AM"
    data = json.loads(page.split('<script type="application/json" id="inv-trend-data">')[1].split("</script>")[0])
    assert data["series"]["SPY"]["label"] == "S&P 500" and data["mine"] == ["sec:s1"]


def test_holding_rows_cover_every_non_cash_holding_with_changes_per_range(holdings):
    """holdings: AAA (stock, 30 sh, $100 thirty days ago), FUNDX (fund, 10 sh, $100 -> $90 yesterday), cash."""
    holdings.execute("INSERT INTO holdings (account_id, security_id, ticker, name, security_type, quantity, value_cents)"
                     " VALUES (1, 's3', 'ETFZ', 'Fake ETF', 'etf', 4, 20000)")
    holdings.executemany("INSERT INTO security_prices (security_id, price_date, ticker, price) VALUES (?, ?, 'ETFZ', ?)",
                         [("s3", D(10), 40.0), ("s3", D(1), 50.0)])
    holdings.commit()
    rows = performance.holding_rows(holdings, {"ETFZ": quote(55.0), "AAA": quote(110.0)}, today=TODAY)
    assert [r.ticker for r in rows] == ["ETFZ", "AAA", "FUNDX"]    # stocks too, no cash; % change order
    etf, stock, fund = rows
    assert stock.periods["90"][:2] == (pytest.approx(0.10), 30 * 10 * 100)
    assert (etf.value_cents, stock.value_cents, fund.value_cents) == (4 * 5500, 30 * 11000, 90000)  # live x shares / recorded
    assert etf.price == 55.0 and etf.source == "live" and fund.price == 90.0 and fund.source == "recorded"
    change, usd, since = etf.periods["90"]
    assert change == pytest.approx(55 / 40 - 1) and usd == 4 * 15 * 100 and since == D(10)
    change, usd, since = etf.periods["1"]
    assert change == pytest.approx(55 / 50 - 1) and usd == 4 * 5 * 100
    assert fund.periods["1"][0] is None                             # only yesterday's price, no live: no change
    # The table's "1 day" column: a live quote's own change vs the previous close, else the 1 day range.
    assert etf.day_change == pytest.approx(55 / 50 - 1) and fund.day_change is None
    live = performance.holding_rows(holdings, {"ETFZ": quote(55.0, change_pct=0.03)}, today=TODAY)
    assert next(r for r in live if r.ticker == "ETFZ").day_change == pytest.approx(0.03)
    assert fund.periods["90"][:2] == (pytest.approx(-0.10), -10 * 10 * 100)


def test_investments_page_has_the_sortable_holdings_table(settings, conn, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env) VALUES (1, 'i1', 'sandbox')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification)"
                 " VALUES (1, 1, 'a1', 'Fake Brokerage', 'investment', 'asset')")
    conn.execute("INSERT INTO holdings (account_id, security_id, ticker, name, security_type, quantity, value_cents)"
                 " VALUES (1, 's3', 'ETFZ', 'Fake ETF', 'etf', 4, 20000)")
    conn.execute("INSERT INTO security_prices (security_id, price_date, ticker, price) VALUES ('s3', ?, 'ETFZ', 40.0)",
                 ((date.today() - timedelta(days=10)).isoformat(),))
    conn.execute("INSERT INTO security_prices (security_id, price_date, ticker, price) VALUES ('s3', ?, 'ETFZ', 50.0)",
                 (date.today().isoformat(),))
    conn.commit()
    client = TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                        base_url="https://localhost:8443")
    page = client.get("/investments").text
    table = page.split('data-tab-panel="investments"')[1].split("</table>")[0]
    assert 'data-sortable data-range-table="inv-trend" data-range="90"' in table
    assert '<th class="num" aria-sort="descending"><button type="button" class="sort" data-sort="pct" data-sort-ranged>' in table
    for key in ("1", "7", "90", "180", "ytd", "365"):
        assert f'data-sort-pct-{key}=' in table and f'data-sort-usd-{key}=' in table
    assert 'data-sort-pct-90="0.25"' in table and 'data-sort-usd-90="4000"' in table   # $40 -> $50, 4 shares
    assert "+$40<" in table and "▲ 25.0%" in table                            # whole dollars
    heads = [h.split('"')[0] for h in table.split('data-sort="')[1:]]
    assert heads == ["name", "ticker", "quantity", "price", "value", "day", "usd", "pct"]   # 1 day after Value
    group = table.split('<th class="th-group" colspan="2">')[1].split("</th>")[0]          # over $ / % change
    assert '<th colspan="6"></th><th class="th-group" colspan="2">' in table
    assert 'data-range-show="7" hidden>5 day Period<' in group and 'data-range-show="90" >90 day Period<' in group
    assert 'data-range-show="1" hidden>1 day Period<' in group
    assert 'data-sort-day="0.25"' in table                                    # no live quote: the recorded 1 day
    assert 'data-sort-value="20000"' in table and ">$200<" in table
    assert '<td class="num">4</td>' in table                                   # whole shares
    assert "style=" not in page.split("<main>")[1]


def test_portfolio_measures_split_retirement_and_investment_accounts(conn):
    """401(k) (Plaid) + HSA (your type Retirement › HSA, manual, no holdings) = Retirement; brokerage = Investment
    accounts. Totals are balances; % change = the group's own holdings only."""
    from fintrack import account_types

    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env) VALUES (1, 'i1', 'sandbox')")
    for acc_id, subtype, balance in [(1, "401k", 300000), (2, "brokerage", 100000)]:
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, subtype, classification,"
                     " current_balance_cents) VALUES (?, 1, ?, 'Fake', 'investment', ?, 'asset', ?)",
                     (acc_id, f"a{acc_id}", subtype, balance))
    conn.execute("INSERT INTO accounts (id, name, type, classification, is_manual, current_balance_cents)"
                 " VALUES (3, 'Fake HSA', 'investment', 'asset', 1, 50000)")
    node = conn.execute("SELECT n.id FROM account_type_nodes n JOIN account_type_nodes p ON p.id = n.parent_id"
                        " WHERE p.name = 'Retirement' AND n.name = ?", ('HSA',)).fetchone()[0]
    account_types.assign(conn, 3, node)
    for acc_id, sid, ticker, value in [(1, "s1", "RET", 300000), (2, "s2", "BRK", 100000)]:
        conn.execute("INSERT INTO holdings (account_id, security_id, ticker, security_type, quantity, value_cents)"
                     " VALUES (?, ?, ?, 'etf', 10, ?)", (acc_id, sid, ticker, value))
    conn.executemany("INSERT INTO security_prices (security_id, price_date, ticker, price) VALUES (?, ?, ?, ?)",
                     [("s1", D(100), "RET", 100.0), ("s1", D(1), "RET", 110.0),     # +10%
                      ("s2", D(100), "BRK", 100.0), ("s2", D(1), "BRK", 80.0)])     # -20%
    conn.commit()
    total, retirement, investment = performance.portfolio_measures(conn, {}, today=TODAY)
    assert [m.label for m in (total, retirement, investment)] == ["Total investments", "Retirement", "Investment accounts"]
    assert (total.total_cents, retirement.total_cents, investment.total_cents) == (450000, 350000, 100000)
    assert retirement.metric.periods["90"].change == pytest.approx(0.10)
    assert investment.metric.periods["90"].change == pytest.approx(-0.20)
    assert total.metric.periods["90"].change == pytest.approx((3000 * 0.10 + 1000 * -0.20) / 4000)


def test_portfolio_page_shows_three_measures_following_the_range(settings, conn, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    client = TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                        base_url="https://localhost:8443")
    page = client.get("/portfolio").text
    kpis = page.split('<section class="kpis one-row large">')[1].split("</section>")[0]   # always one row of three
    assert [re.sub(r"<[^>]+>", "", x.split("</div>")[0]) for x in kpis.split('<div class="kpi-label">')[1:]] == \
        ["Total investments", "Retirement", "Investment accounts"]      # "Investment" on a phone (the rest is .w-long)
    assert kpis.count('data-range-of="port-types"') == 3 * 6                  # 1 day, YTD, 1 / 3 / 5 / 10 years
    assert kpis.count('data-range-show="ytd" >') + kpis.count('data-range-show="ytd">') == 3   # YTD shown first
    assert "style=" not in page.split("<main>")[1]


def test_portfolio_holdings_show_whole_percent_changes(settings, conn, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env) VALUES (1, 'i1', 'sandbox')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification, current_balance_cents)"
                 " VALUES (1, 1, 'a1', 'Fake Brokerage', 'investment', 'asset', 100000)")
    conn.execute("INSERT INTO holdings (account_id, security_id, ticker, name, security_type, quantity, value_cents)"
                 " VALUES (1, 's1', 'AAA', 'Fake AAA', 'equity', 10, 112500)")
    conn.executemany("INSERT INTO security_prices (security_id, price_date, ticker, price) VALUES ('s1', ?, 'AAA', ?)",
                     [((date.today() - timedelta(days=100)).isoformat(), 100.0),
                      (date.today().isoformat(), 112.5)])                     # +12.5% -> "13%" (halves up)
    conn.commit()
    client = TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                        base_url="https://localhost:8443")
    table = client.get("/portfolio").text.split('<table class="data holdings"')[1].split("</table>")[0]
    row = table.split("Fake AAA")[-1].split("</tr>")[0]                     # [-1]: the name is in its cell's title too
    assert row.count("▲ 13%") == 6                  # 1 day, YTD, 1 / 3 / 5 / 10 years (all from day -100, the only earlier price)
    assert "$1,125<" in row


def test_portfolio_holdings_have_filters(settings, conn, cipher, gateway):
    """Filter controls (ui.js) + the data each row needs: account, kind, search text, value in cents."""
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env) VALUES (1, 'i1', 'sandbox')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification, current_balance_cents)"
                 " VALUES (1, 1, 'a1', 'Fake Brokerage', 'investment', 'asset', 100000)")
    for sid, ticker, name, kind, cash, value in [("s1", "AAA", "Fake AAA Corp", "equity", 0, 60000),
                                                 ("s2", "ETFZ", "Fake ETF", "etf", 0, 30000),
                                                 ("c1", None, "Fake Cash", "cash", 1, 10000)]:
        conn.execute("INSERT INTO holdings (account_id, security_id, ticker, name, security_type, is_cash, quantity,"
                     " value_cents) VALUES (1, ?, ?, ?, ?, ?, 1, ?)", (sid, ticker, name, kind, cash, value))
    conn.commit()
    client = TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                        base_url="https://localhost:8443")
    page = client.get("/portfolio").text
    filters = page.split('<div class="table-filters" data-phone-controls>')[1].split("</div>")[0]   # in the panel on a phone
    assert 'data-filter="text"' in filters and 'data-filter="account"' in filters and 'data-filter="kind"' in filters
    assert [o.split("<")[0] for o in filters.split('data-filter="kind"')[1].split('<option value="')[2:]] == \
        ['Stocks">Stocks', 'ETFs">ETFs', 'Cash">Cash']                    # only kinds you hold, in order
    table = page.split('id="port-holdings" data-filterable>')[1].split("</table>")[0]
    assert 'data-kind="Stocks" data-value="60000"' in table and 'data-search="fake aaa corp aaa"' in table
    assert '<th class="num" data-subtotal>$1,000</th>' in table
    assert '<tr class="filter-empty" hidden>' in table


def test_holding_rows_toggle_comparison_lines(settings, conn, cipher, gateway):
    """Each priced holding's row toggles a chart line (charts.js); its prices (+ today's live price)
    reach the chart with the benchmarks'. Cash has no prices, so its row isn't clickable."""
    import json

    from fastapi.testclient import TestClient

    from fintrack import networth
    from fintrack.crypto import TokenCipher
    from fintrack.quotes import QuoteGateway
    from fintrack.web.app import create_app
    from tests.fakes import alpaca_fetch

    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env) VALUES (1, 'i1', 'sandbox')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification, current_balance_cents)"
                 " VALUES (1, 1, 'a1', 'Fake Brokerage', 'investment', 'asset', 100000)")
    for sid, ticker, kind, cash in [("s1", "AAA", "equity", 0), ("c1", None, "cash", 1), ("x1", None, "other", 0)]:
        conn.execute("INSERT INTO holdings (account_id, security_id, ticker, name, security_type, is_cash, quantity,"
                     " value_cents) VALUES (1, ?, ?, ?, ?, ?, 1, 5000)", (sid, ticker, f"Fake {sid}", kind, cash))
    conn.executemany("INSERT INTO security_prices (security_id, price_date, ticker, price) VALUES ('s1', ?, 'AAA', ?)",
                     [((date.today() - timedelta(days=3)).isoformat(), 40.0), ((date.today() - timedelta(days=1)).isoformat(), 42.0)])
    conn.commit()
    networth.take_snapshot(conn)
    when = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    fake = QuoteGateway("k", "s", fetch=alpaca_fetch({"AAA": 44.0}, when=when[:-2] + ":" + when[-2:]))   # a quote from now
    client = TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher,
                                   quotes_factory=lambda s: fake), base_url="https://localhost:8443")
    page = client.get("/investments").text
    table = page.split('data-tab-panel="investments"')[1].split("</table>")[0]
    assert table.count("data-row-toggle") == 1 and 'data-compare="sec:s1"' in table   # AAA only
    block = json.loads(page.split('id="inv-trend-data">')[1].split("</script>")[0])["series"]
    assert block["sec:s1"]["label"] == "AAA" and block["sec:s1"]["prices"][-1] == [date.today().isoformat(), 44.0]
    assert len(block["sec:s1"]["prices"]) == 3 and "sec:x1" not in block and "SPY" in block
    assert block["sec:s1"]["value_cents"] == 4400                             # its weight: 1 share x $44 live


def test_ranges_end_on_the_last_trading_day_before_the_open(holdings):
    """Just after midnight (or at a weekend) the live quote is the last session's close: "1 day"
    must be that session's move, the same as "today" (user's report 2026-10-08)."""
    yesterday = TODAY - timedelta(days=1)
    holdings.executemany("INSERT INTO security_prices (security_id, price_date, ticker, price) VALUES ('s1', ?, 'AAA', ?)",
                         [(D(2), 100.0), (D(1), 99.0)])                    # closes: 100 two days ago, 99 yesterday
    holdings.commit()
    live = {"AAA": Quote(price=99.0, change_pct=-0.01, previous_close=100.0,
                         as_of=int(datetime(yesterday.year, yesterday.month, yesterday.day, 16).timestamp()))}
    assert performance.market_date(live, TODAY) == yesterday
    aaa = next(r for r in performance.holding_rows(holdings, live, today=TODAY) if r.ticker == "AAA")
    assert aaa.periods["1"][0] == pytest.approx(-0.01) and aaa.price_date == D(1)
    mine = by_key(holdings, live)["portfolio"]
    assert mine.today == pytest.approx(-0.01)


def test_intraday_series_keys_minutes_like_the_daily_lines_from_the_previous_close(holdings):
    """The chart's "1 day" (user's request 2026-10-27): minute prices for live-quoted holdings and benchmarks,
    keyed "sec:<id>" / symbol, each with its previous close; the live quote ends the line."""
    noon = int(datetime(2026, 10, 7, 12).timestamp())

    class Minutes:
        asked = None

        def intraday(self, tickers, day):
            Minutes.asked = (tickers, day)
            return {"AAA": [(noon - 120, 101.0), (noon - 60, 102.0)], "SPY": [(noon - 60, 500.0)]}

    live = {"AAA": Quote(103.0, 0.03, 100.0, noon), "SPY": Quote(501.0, 0.002, 499.0, noon - 60),
            "IWM": Quote(200.0, 0.0, 200.0, noon)}                     # IWM: no minutes from Alpaca
    rows = performance.holding_rows(holdings, live, today=TODAY)
    found = performance.intraday_series(Minutes(), live, rows, TODAY)
    assert Minutes.asked == (["AAA", "IWM", "SPY"], TODAY)             # FUNDX has no live quote: not asked
    assert found == {"series": {"sec:s1": [[noon - 120, 101.0], [noon - 60, 102.0], [noon, 103.0]],
                                "SPY": [[noon - 60, 500.0]]},           # the quote is no newer than its last minute
                     "base": {"sec:s1": 100.0, "SPY": 499.0}}
    assert performance.intraday_series(None, live, rows, TODAY) == {"series": {}, "base": {}}
    # An after-hours quote is drawn at 4 PM (New York): after a 3:59 bar it's a new point, on a 4 PM bar it replaces it.
    close = int(datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc).timestamp())
    late = {"AAA": Quote(104.0, 0.04, 100.0, close + 3 * 3600)}

    class Session:
        def __init__(self, last):
            self.last = last

        def intraday(self, tickers, day):
            return {"AAA": [(close - 120, 101.0), (self.last, 102.0)]}

    rows = performance.holding_rows(holdings, late, today=TODAY)
    assert performance.intraday_series(Session(close - 60), late, rows, TODAY)["series"]["sec:s1"][-1] == [close, 104.0]
    assert performance.intraday_series(Session(close), late, rows, TODAY)["series"]["sec:s1"] == [
        [close - 120, 101.0], [close, 104.0]]


def test_ten_years_of_closes_are_fetched_once_per_ticker(conn):
    """Portfolio's 10-year range (user's request 2026-11-13): a new ticker's history goes back DEEP_DAYS once."""
    from fintrack import price_history

    asked = []

    class Gateway:
        def history(self, tickers, start):
            asked.append((tuple(tickers), (date.today() - start).days))
            return {t: [((date.today() - timedelta(days=5)).isoformat(), 10.0)] for t in tickers}

    price_history.backfill(conn, Gateway())
    price_history.backfill(conn, Gateway())
    first, second = asked[0], asked[1]
    assert first[1] == price_history.DEEP_DAYS and second[1] == price_history.HISTORY_DAYS
    assert set(first[0]) == set(second[0])                                  # the same tickers, deep only once
