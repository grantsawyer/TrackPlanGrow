"""Your watchlist (user's request 2026-11-04; watchlist.py, performance.watch_rows) with a fake Alpaca: no network."""

from datetime import date, timedelta

import pytest

from fintrack import performance, price_history, watchlist
from fintrack.quotes import QuoteGateway
from tests.fakes import alpaca_fetch

TODAY = date.today()
D = lambda n: (TODAY - timedelta(days=n)).isoformat()   # noqa: E731
ORIGIN = {"origin": "https://localhost:8443"}


def gateway(prices, bars=None):
    return QuoteGateway("fake-key", "fake-secret", fetch=alpaca_fetch(prices, bars=bars))


def test_add_needs_a_price_and_fills_history(conn):
    quotes = gateway({"FAKEW": 110.0}, bars={"FAKEW": [(D(n), 100.0) for n in range(1, 200)]})
    assert watchlist.add(conn, quotes, " fakew ", "Fake Widgets") == "FAKEW"
    assert [(w.ticker, w.name) for w in watchlist.items(conn)] == [("FAKEW", "Fake Widgets")]
    days = conn.execute("SELECT COUNT(*), MIN(source) FROM ticker_prices WHERE ticker = 'FAKEW'").fetchone()
    assert tuple(days) == (199, "alpaca")                                # its history at once, today left to the quote
    for text, message in [("FAKEW", "already"), ("NOPE", "no price"), ("not a ticker!", "Enter a ticker")]:
        with pytest.raises(watchlist.WatchlistError, match=message):
            watchlist.add(conn, quotes, text)
    with pytest.raises(watchlist.WatchlistError, match="Alpaca keys"):
        watchlist.add(conn, None, "OTHER")
    assert "FAKEW" in price_history.tickers(conn)                         # priced at every sync too


def test_watch_rows_and_series_like_the_holdings(conn):
    quotes = gateway({"FAKEW": 110.0}, bars={"FAKEW": [(D(n), 100.0) for n in range(1, 200)]})
    watchlist.add(conn, quotes, "FAKEW")
    live = quotes.quotes(["FAKEW"])
    [row] = performance.watch_rows(conn, live)
    assert (row.name, row.ticker, row.price, row.quantity, row.value_cents) == ("FAKEW", "FAKEW", 110.0, None, None)
    change, usd, since = row.periods["90"]
    assert change == pytest.approx(0.10) and usd == 1000                 # one share: +$10.00
    series = performance.watch_series(conn, [row])
    assert list(series) == ["tick:FAKEW"] and len(series["tick:FAKEW"]["prices"]) >= 199   # its daily closes
    watchlist.remove(conn, "fakew")
    assert watchlist.items(conn) == []


def test_the_investments_card_has_a_watchlist_tab(settings, conn, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    fake = QuoteGateway("fake-key", "fake-secret", fetch=alpaca_fetch(
        {"FAKEW": 110.0, "AAA": 50.0}, bars={"FAKEW": [(D(n), 100.0) for n in range(1, 30)],
                                             "AAA": [(D(n), 40.0) for n in range(1, 30)]}))
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env) VALUES (1, 'i1', 'sandbox')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification, current_balance_cents)"
                 " VALUES (1, 1, 'a1', 'Fake Brokerage', 'investment', 'asset', 5000)")
    conn.execute("INSERT INTO holdings (account_id, security_id, ticker, name, security_type, quantity, value_cents)"
                 " VALUES (1, 's1', 'AAA', 'Fake AAA', 'equity', 1, 5000)")
    conn.commit()
    client = TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher,
                                   quotes_factory=lambda s: fake), base_url="https://localhost:8443")
    added = client.post("/investments/watchlist", data={"ticker": "fakew", "name": ""}, headers=ORIGIN, follow_redirects=False)
    assert added.headers["location"].startswith("/investments?tab=watchlist&msg=")
    page = client.get("/investments?tab=watchlist").text
    assert 'data-tab="watchlist" aria-selected="true"' in page and 'data-tab-panel="investments" hidden' in page
    panel = page.split('data-tab-panel="watchlist"')[1].split("</section>")[0]
    assert 'data-compare="tick:FAKEW"' in panel and ">Quantity<" not in panel and ">Value<" not in panel
    assert '"tick:FAKEW"' in page.split('id="inv-trend-data"')[1].split("</script>")[0]     # its line for the chart
    bad = client.post("/investments/watchlist", data={"ticker": "NOPE"}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in bad.headers["location"]
    client.post("/investments/watchlist/FAKEW/delete", headers=ORIGIN)
    assert watchlist.items(conn) == []


# ---------------------------------------------------------------- finding a ticker by company (2026-11-04)

ASSETS = [{"symbol": "FAKEW", "name": "Fake Widgets Inc. Common Stock", "exchange": "NASDAQ", "tradable": True, "status": "active"},
          {"symbol": "WIDG", "name": "Widgetworks Corp", "exchange": "NYSE", "tradable": True, "status": "active"},
          {"symbol": "OTCW", "name": "Other Widget Holdings", "exchange": "OTC", "tradable": True, "status": "active"},
          {"symbol": "GONE", "name": "Fake Widgets Old", "exchange": "NYSE", "tradable": False, "status": "active"}]


def asset_gateway(calls=None):
    base = alpaca_fetch({"FAKEW": 110.0, "WIDG": 20.0})

    def fetch(url, headers):
        if url.startswith("https://paper-api.alpaca.markets/v2/assets?"):
            if calls is not None:
                calls.append(url)
            return ASSETS
        return base(url, headers)
    return QuoteGateway("fake-key", "fake-secret", fetch=fetch)


def test_the_stock_list_is_fetched_weekly_and_searched_here(conn):
    calls = []
    quotes = asset_gateway(calls)
    assert watchlist.refresh_assets(conn, quotes, now=1_000_000) is True
    assert "status=active" in calls[0] and "asset_class=us_equity" in calls[0]
    names = dict(conn.execute("SELECT symbol, name FROM alpaca_assets").fetchall())
    assert names == {"FAKEW": "Fake Widgets Inc.", "WIDG": "Widgetworks Corp", "OTCW": "Other Widget Holdings"}  # no untradable
    assert watchlist.refresh_assets(conn, quotes, now=1_000_000 + 6 * 86400) is False and len(calls) == 1      # a week
    assert watchlist.refresh_assets(conn, quotes, now=1_000_000 + 8 * 86400) is True and len(calls) == 2
    found = [f["symbol"] for f in watchlist.search(conn, "widg")]
    assert found == ["WIDG", "FAKEW", "OTCW"]                 # ticker start, then a word starting so, OTC after
    assert [f["symbol"] for f in watchlist.search(conn, "fake w")] == ["FAKEW"] and watchlist.search(conn, " ") == []
    watchlist.add(conn, quotes, "FAKEW")                      # blank name: Alpaca's company name
    assert [(w.ticker, w.name) for w in watchlist.items(conn)] == [("FAKEW", "Fake Widgets Inc.")]
    assert [f["symbol"] for f in watchlist.search(conn, "widg")] == ["WIDG", "OTCW"]   # watched ones left out


def test_the_box_shows_matches_to_add(settings, conn, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    client = TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher,
                                   quotes_factory=lambda s: asset_gateway()), base_url="https://localhost:8443")
    page = client.get("/investments?tab=watchlist").text
    assert 'hx-get="/investments/watchlist/search"' in page and 'id="watch-results"' in page
    head = page.split('class="card-head card-tabs"')[1].split('data-tab-panel="investments"')[0]
    assert 'class="pop-button watch-pop" data-popover data-tab-only="watchlist"' in head    # the + in the card's head
    found = client.get("/investments/watchlist/search?ticker=widget").text      # the list is fetched on first use
    assert '<input type="hidden" name="ticker" value="WIDG"><input type="hidden" name="name" value="Widgetworks Corp">' in found
    assert client.get("/investments/watchlist/search?ticker=").text.strip() == ""


def test_a_brand_finds_the_company_by_its_legal_name_ahead_of_funds(conn):
    """User's request 2026-11-04: "spacex" finds Space Exploration Technologies (a loose match: a whole word, then the rest
    later), ahead of the funds named after it; a few letters in order don't match."""
    conn.executemany("INSERT INTO alpaca_assets (symbol, name, exchange) VALUES (?, ?, ?)", [
        ("SPCX", "Space Exploration Technologies Corp. Class A", "NASDAQ"),
        ("SPCF", "ProShares Ultra SpaceX", "ARCA"),
        ("LOFF", "Direxion Daily SpaceX Bull 2X ETF", "ARCA"),
        ("VNGD", "Vanguard Fake Index ETF", "ARCA"),
        ("EVAN", "Evangeline Fake Corp", "NYSE"),
        ("NOPE", "Sparkle Pace Excavation", "NYSE")])               # S..P..A..C..E..X in order, but no whole word
    conn.commit()
    assert [f["symbol"] for f in watchlist.search(conn, "spacex")] == ["SPCX", "SPCF", "LOFF"]
    assert [f["symbol"] for f in watchlist.search(conn, "vang")] == ["VNGD", "EVAN"]   # a word start beats mid-word


def test_the_overview_card_switches_to_the_watchlist(settings, conn, cipher, gateway):
    """User's request 2026-11-04: the Overview's Investments card (was Investment movers) has a title dropdown; Watchlist
    shows your watched tickers in the same table, without Value."""
    from fastapi.testclient import TestClient

    from fintrack import prices
    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    quotes = asset_gateway()
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env) VALUES (1, 'i1', 'sandbox')")
    conn.commit()
    watchlist.add(conn, quotes, "WIDG")
    client = TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher,
                                   quotes_factory=lambda s: quotes), base_url="https://localhost:8443")
    rows = prices.watch_movers(conn, live=quotes.quotes(["WIDG", "SPY"]), benchmark="SPY")
    assert [m["ticker"] for m in rows["rows"]] == ["WIDG"] and rows["rows"][0]["value_cents"] is None
    saved = client.post("/overview/movers", data={"view": "watchlist"}, headers=ORIGIN, follow_redirects=False)
    assert saved.headers["location"] == "/" and prices.movers_view(conn) == "watchlist"
    page = client.get("/").text
    card = page.split('action="/overview/movers"')[1].split("</section>")[0]
    assert '<option value="watchlist" selected>Watchlist</option>' in card and "WIDG" in card
    assert '<th class="num">Value</th>' not in card
    client.post("/overview/movers", data={"view": "investments"}, headers=ORIGIN)
    assert prices.movers_view(conn) == "investments"
