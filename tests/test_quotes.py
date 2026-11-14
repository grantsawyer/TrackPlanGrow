"""Alpaca prices (quotes.py) with a fake fetcher: tests never touch the network."""

import logging
import threading

import pytest

from fintrack import prices
from fintrack.logging_setup import RedactingFilter
from fintrack.quotes import QuoteGateway, _quote
from tests.fakes import alpaca_fetch

KEY_ID, SECRET = "fake-alpaca-key-1234", "fake-alpaca-secret-5678"


@pytest.fixture(autouse=True)
def fresh_cache():
    QuoteGateway.clear_cache()
    yield
    QuoteGateway.clear_cache()


def test_quotes_price_many_tickers_in_two_calls_and_cache():
    calls = []
    gw = QuoteGateway(KEY_ID, SECRET, fetch=alpaca_fetch({"AAA": 12.5, "BBB": 40.0}, previous={"AAA": 12.0}, calls=calls))
    q = gw.quotes(["AAA", "FUNDX", "BBB", "AAA", None])          # FUNDX: a fund Alpaca can't price
    assert set(q) == {"AAA", "BBB"}
    assert q["AAA"].price == 12.5 and q["AAA"].change_pct == pytest.approx(12.5 / 12 - 1) and q["AAA"].previous_close == 12.0
    feeds = sorted(url.split("feed=")[1] for url, _ in calls)
    assert feeds == ["delayed_sip", "iex"]                         # one call per feed, all symbols in each
    assert all(url.startswith("https://data.alpaca.markets/v2/stocks/snapshots?") for url, _ in calls)
    gw.quotes(["AAA", "FUNDX", "BBB"])                              # within 5 minutes: from the cache
    assert len(calls) == 2


def test_keys_travel_in_headers_never_in_urls():
    calls = []
    QuoteGateway(KEY_ID, SECRET, fetch=alpaca_fetch({"AAA": 1.0}, calls=calls)).quotes(["AAA"])
    for url, headers in calls:
        assert KEY_ID not in url and SECRET not in url
        assert headers == {"APCA-API-KEY-ID": KEY_ID, "APCA-API-SECRET-KEY": SECRET}


def test_newest_price_wins_and_the_official_close_wins_a_tie():
    sip = {"dailyBar": {"c": 100.0}, "minuteBar": {"t": "2026-10-07T19:59:00Z"}, "prevDailyBar": {"c": 98.0}}
    # After the close: IEX's last trade at 16:00 ties the all-exchange close -> the official close.
    assert _quote({"latestTrade": {"p": 100.2, "t": "2026-10-07T20:00:00.123456789Z"}}, sip) is not None  # nanoseconds parse
    q = _quote({"latestTrade": {"p": 100.2, "t": "2026-10-07T20:00:00Z"}}, sip)
    assert q.price == 100.0 and q.change_pct == pytest.approx(100 / 98 - 1)
    # Mid-day: IEX real time (10:30) is newer than the 15-minute-delayed all-exchange price (10:14).
    mid = {"dailyBar": {"c": 99.0}, "minuteBar": {"t": "2026-10-07T14:14:00Z"}, "prevDailyBar": {"c": 98.0}}
    assert _quote({"latestTrade": {"p": 99.5, "t": "2026-10-07T14:30:00Z"}}, mid).price == 99.5
    # A thinly traded stock: IEX's last trade (09:45) is older -> the all-exchange price.
    assert _quote({"latestTrade": {"p": 97.0, "t": "2026-10-07T13:45:00Z"}}, mid).price == 99.0
    assert _quote(None, None) is None


def test_network_error_is_no_quote_and_never_logs_the_keys(caplog):
    def broken(url, headers):
        raise OSError("boom")
    gw = QuoteGateway(KEY_ID, SECRET, fetch=broken)
    with caplog.at_level(logging.WARNING, logger="fintrack.quotes"):
        assert gw.quotes(["AAA"]) == {}
    assert "Alpaca" in caplog.text and KEY_ID not in caplog.text and SECRET not in caplog.text


def test_a_hung_alpaca_is_cut_off_and_cached_as_no_quote(caplog):
    """The page waits at most total_seconds; a hung feed counts as no data for 5 minutes."""
    release, calls = threading.Event(), []
    healthy = alpaca_fetch({"AAA": 10.0})

    def fetch(url, headers):
        calls.append(url)
        if "feed=delayed_sip" in url:
            release.wait(timeout=5)            # "hangs" until the test lets go
        return healthy(url, headers)

    gw = QuoteGateway(KEY_ID, SECRET, fetch=fetch, total_seconds=0.2)
    try:
        with caplog.at_level(logging.WARNING, logger="fintrack.quotes"):
            assert gw.quotes(["AAA"])["AAA"].price == 10.0       # IEX alone still prices it
        assert "took too long" in caplog.text
        gw.quotes(["AAA"])                                       # cached: no new calls
        assert len(calls) == 2
    finally:
        release.set()


def test_history_is_daily_split_adjusted_all_exchange_and_ends_16_minutes_ago():
    from datetime import date, datetime, timedelta, timezone
    from urllib.parse import parse_qs, urlparse

    calls = []
    gw = QuoteGateway(KEY_ID, SECRET, fetch=alpaca_fetch(
        {}, bars={"SPY": [("2026-10-05", 500.0), ("2026-10-06", 505.0)]}, calls=calls))
    assert gw.history(["SPY", "FUNDX"], date(2025, 9, 1)) == {"SPY": [("2026-10-05", 500.0), ("2026-10-06", 505.0)]}
    query = {k: v[0] for k, v in parse_qs(urlparse(calls[0][0]).query).items()}
    assert (query["timeframe"], query["feed"], query["adjustment"], query["start"]) == ("1Day", "sip", "split", "2025-09-01")
    end = datetime.strptime(query["end"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert datetime.now(timezone.utc) - end >= timedelta(minutes=15)   # free plan: no SIP from the last 15 min


def test_intraday_is_the_session_by_the_minute_all_exchange_then_iex_and_cached():
    """Minute prices for the Investments chart's "1 day" (user's request 2026-10-27): SIP up to 16 minutes ago,
    IEX for the rest, regular hours only (9:30 ET = 13:30Z in October), cached like quotes."""
    from datetime import date, datetime, timezone
    from urllib.parse import parse_qs, urlparse

    day = date(2026, 10, 7)
    now = datetime(2026, 10, 7, 18, 0, tzinfo=timezone.utc)                  # 2 pm in New York
    stamp = lambda t: int(datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp())   # noqa: E731
    minutes = {"SPY": [("2026-10-07T13:00:00Z", 499.0),                       # pre-market: left out
                       ("2026-10-07T13:30:00Z", 500.0), ("2026-10-07T17:40:00Z", 501.0),
                       ("2026-10-07T17:50:00Z", 502.0)]}
    calls = []
    gw = QuoteGateway(KEY_ID, SECRET, fetch=alpaca_fetch({}, minutes=minutes, calls=calls))
    assert gw.intraday(["SPY", "FUNDX"], day, now=now) == {
        "SPY": [(stamp("2026-10-07T13:30:00Z"), 500.0), (stamp("2026-10-07T17:40:00Z"), 501.0),
                (stamp("2026-10-07T17:50:00Z"), 502.0)]}
    windows = {q["feed"]: (q["start"], q["end"], q["timeframe"])
               for q in ({k: v[0] for k, v in parse_qs(urlparse(url).query).items()} for url, _ in calls)}
    assert windows == {"sip": ("2026-10-07T13:30:00Z", "2026-10-07T17:44:00Z", "1Min"),
                       "iex": ("2026-10-07T17:44:00Z", "2026-10-07T18:00:00Z", "1Min")}
    gw.intraday(["SPY", "FUNDX"], day, now=now)
    assert len(calls) == 2                                                   # cached
    before_open = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    assert gw.intraday(["SPY"], date(2026, 10, 8), now=before_open) == {} and len(calls) == 2   # nothing to ask


def test_redaction_covers_the_keys_and_token_parameters():
    f = RedactingFilter([KEY_ID, SECRET])
    assert KEY_ID not in f.redact(f"key {KEY_ID}") and SECRET not in f.redact(f"secret {SECRET}")
    assert f.redact("GET /quote?symbol=AAA&token=abcdef123456") == "GET /quote?symbol=AAA&token=[REDACTED]"


def test_movers_use_live_price_change_and_value(conn):
    from fintrack.quotes import Quote

    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env) VALUES (1, 'i1', 'sandbox')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification)"
                 " VALUES (1, 1, 'a1', 'Fake Brokerage', 'investment', 'asset')")
    for sid, ticker, qty, price, value in [("s1", "AAA", 10, 12.0, 12000), ("s2", "FUNDX", 4, 50.0, 20000)]:
        conn.execute("INSERT INTO holdings (account_id, security_id, ticker, security_type, quantity, price, value_cents)"
                     " VALUES (1, ?, ?, 'equity', ?, ?, ?)", (sid, ticker, qty, price, value))
    conn.commit()
    assert sorted(prices.held_tickers(conn)) == ["AAA", "FUNDX"]

    m = prices.movers(conn, live={"AAA": Quote(price=13.0, change_pct=0.05, previous_close=12.38, as_of=None)})
    by_ticker = {r["ticker"]: r for r in m["rows"]}
    assert m["by"] == "change" and m["live"] == 1 and m["rows"][0]["ticker"] == "AAA"
    assert by_ticker["AAA"]["source"] == "live" and by_ticker["AAA"]["value_cents"] == 13000   # 10 x $13
    assert by_ticker["FUNDX"]["source"] == "plaid" and by_ticker["FUNDX"]["price"] == 50.0


def test_overview_shows_live_prices_with_a_fake_alpaca(settings, conn, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Broker')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification, current_balance_cents)"
                 " VALUES (1, 1, 'a1', 'Fake Brokerage', 'investment', 'asset', 12000)")
    conn.execute("INSERT INTO holdings (account_id, security_id, ticker, name, security_type, quantity, price, value_cents)"
                 " VALUES (1, 's1', 'AAA', 'Fake AAA Corp', 'equity', 10, 12.0, 12000)")
    conn.commit()
    fake = QuoteGateway(KEY_ID, SECRET, fetch=alpaca_fetch({"AAA": 13.0}, previous={"AAA": 12.38}))
    client = TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher,
                                   quotes_factory=lambda s: fake), base_url="https://localhost:8443")
    html = client.get("/").text
    assert "$13.00" in html and "▲ 5.0%" in html
    assert "prices as of " in html
    assert KEY_ID not in html and SECRET not in html
