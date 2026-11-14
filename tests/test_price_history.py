"""Daily price history from Alpaca (price_history.py) with a fake Alpaca: no network."""

from datetime import date, timedelta

from fintrack import db, manual, price_history
from fintrack import manual_positions as mp
from fintrack.quotes import QuoteGateway
from tests.fakes import alpaca_fetch

TODAY = date.today()
D = lambda n: (TODAY - timedelta(days=n)).isoformat()   # noqa: E731


def gateway(bars):
    return QuoteGateway("fake-key", "fake-secret", fetch=alpaca_fetch({}, bars=bars))


def test_backfill_fills_prices_with_official_closes_but_not_today(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env) VALUES (1, 'i1', 'sandbox')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification)"
                 " VALUES (1, 1, 'a1', 'Fake Brokerage', 'investment', 'asset')")
    conn.execute("INSERT INTO holdings (account_id, security_id, ticker, name, security_type, quantity, value_cents)"
                 " VALUES (1, 's1', 'AAA', 'Fake AAA', 'equity', 1, 10000)")
    conn.execute("INSERT INTO security_prices (security_id, price_date, ticker, price) VALUES ('s1', ?, 'AAA', 99.0)", (D(5),))
    conn.execute("INSERT INTO benchmark_prices (symbol, price_date, price) VALUES ('SPY', ?, 400.0)", (D(1),))  # intraday
    conn.commit()
    sent = price_history.backfill(conn, gateway({
        "AAA": [(D(5), 100.0), (D(4), 101.0), (D(0), 102.0)],
        "SPY": [(D(2), 398.0), (D(1), 401.0)]}))
    assert sent == 4                                                     # today's unfinished bar is dropped
    aaa = dict(conn.execute("SELECT price_date, price FROM security_prices WHERE security_id = 's1'").fetchall())
    assert aaa == {D(5): 100.0, D(4): 101.0}                             # official close replaced Plaid's; no today
    spy = dict(conn.execute("SELECT price_date, price FROM benchmark_prices WHERE symbol = 'SPY'").fetchall())
    assert spy == {D(2): 398.0, D(1): 401.0}                             # official close replaced intraday


def test_manual_positions_get_daily_prices_and_past_values(conn):
    ira = manual.create_account(conn, "Fake IRA", "investment", "1000", D(30))
    mp.add_line(conn, ira, "MANX", "", "etf", "10", D(10))               # no live price yet
    bars = {"MANX": [(D(n), 20.0 + n) for n in range(1, 11)]}            # D(10) = 30, D(1) = 21
    price_history.backfill(conn, gateway(bars))
    sources = {r[0] for r in conn.execute("SELECT source FROM ticker_prices WHERE ticker = 'MANX'")}
    assert sources == {"alpaca"}
    value = lambda d: conn.execute("SELECT value_cents FROM manual_valuations WHERE account_id = ? AND date = ?",  # noqa: E731
                                   (ira, d)).fetchone()[0]
    assert value(D(10)) == 30000 and value(D(0)) == 21000                # 10 shares x that day's close

    before = db.revision(conn)
    price_history.backfill(conn, gateway(bars))                          # the next sync: nothing new
    assert db.revision(conn) == before


def test_your_price_beats_history_and_an_outage_changes_nothing(conn):
    ira = manual.create_account(conn, "Fake IRA", "investment", "1000", D(30))
    pid = mp.add_line(conn, ira, "MANX", "", "fund", "1", D(3))
    mp.set_price(conn, pid, "50")
    price_history.backfill(conn, gateway({"MANX": [(TODAY.isoformat(), 10.0), (D(1), 11.0)]}))
    assert mp.lines(conn, ira)[0].price == 50.0                          # today: your price
    assert price_history.backfill(conn, gateway({})) == 0                # Alpaca had nothing / was down


def test_first_page_load_fills_history_then_waits(settings, conn, cipher, gateway):
    """No sync needed after setup or a restart: the first Investments visit loads the history,
    later visits (within 6 hours) don't call Alpaca's history again."""
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    calls = []
    fake = QuoteGateway("fake-key", "fake-secret", fetch=alpaca_fetch(
        {}, bars={"SPY": [(D(n), 500.0 + n) for n in range(1, 300)]}, calls=calls))
    client = TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher,
                                   quotes_factory=lambda s: fake), base_url="https://localhost:8443")
    client.get("/investments")
    assert conn.execute("SELECT COUNT(*) FROM benchmark_prices WHERE symbol = 'SPY'").fetchone()[0] == 299
    history_calls = lambda: sum("/bars?" in url for url, _ in calls)   # noqa: E731
    assert history_calls() == 1
    client.get("/investments")
    client.get("/portfolio")
    assert history_calls() == 1
