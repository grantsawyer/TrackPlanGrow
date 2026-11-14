"""Positions in manual investment accounts (fake tickers and prices only)."""

from datetime import date, datetime, timedelta

import pytest

from fintrack import db, manual, networth
from fintrack import manual_positions as mp
from fintrack.quotes import Quote

TODAY = date.today()
D = lambda n: (TODAY - timedelta(days=n)).isoformat()   # noqa: E731


def quote(price, day=TODAY):
    return Quote(price=price, change_pct=0.01, previous_close=None,
                 as_of=int(datetime(day.year, day.month, day.day, 12).timestamp()))


def value_on(conn, account_id, day):
    row = conn.execute("SELECT value_cents FROM manual_valuations WHERE account_id = ? AND date = ?",
                       (account_id, day)).fetchone()
    return row[0] if row else None


@pytest.fixture
def ira(conn):
    """A fake manual IRA, first valued by hand 30 days ago."""
    return manual.create_account(conn, "Fake IRA", "investment", "1000", D(30))


def test_ticker_line_is_valued_at_the_live_price_and_mirrored(conn, ira):
    mp.add_line(conn, ira, "fakx", "", "etf", "10", D(0), live={"FAKX": quote(50.0)})
    assert value_on(conn, ira, D(0)) == 50000 and manual.get_account(conn, ira)["current_balance_cents"] == 50000
    holding = conn.execute("SELECT * FROM holdings WHERE account_id = ?", (ira,)).fetchone()
    assert (holding["ticker"], holding["security_type"], holding["quantity"], holding["value_cents"]) == \
        ("FAKX", "etf", 10.0, 50000)
    assert value_on(conn, ira, D(30)) == 100000                    # the earlier typed value is kept

    mp.revalue(conn, ira, {"FAKX": quote(55.0)})                    # the next live-price refresh
    assert manual.get_account(conn, ira)["current_balance_cents"] == 55000


def test_relative_and_backdated_changes(conn, ira):
    networth.take_snapshot(conn, TODAY - timedelta(days=5))         # a past snapshot day to revalue
    pid = mp.add_line(conn, ira, "FAKX", "", "stock", "10", D(10), live={"FAKX": quote(20.0, TODAY - timedelta(days=10))})
    mp.change_amount(conn, pid, "+5", D(3), live={"FAKX": quote(20.0)})
    assert [(c["date"], c["quantity"]) for c in mp.lines(conn, ira)[0].changes] == [(D(3), 15.0), (D(10), 10.0)]
    assert value_on(conn, ira, D(5)) == 20000 and value_on(conn, ira, D(0)) == 30000
    mp.change_amount(conn, pid, "-15", D(0))                        # sold everything today
    assert value_on(conn, ira, D(0)) == 0
    assert conn.execute("SELECT COUNT(*) FROM holdings WHERE account_id = ?", (ira,)).fetchone()[0] == 0
    with pytest.raises(mp.PositionError, match="fewer than 0"):
        mp.change_amount(conn, pid, "-1", D(0))


def test_asset_lines_and_totals(conn, ira):
    mp.add_line(conn, ira, "FAKX", "", "stock", "2", D(0), live={"FAKX": quote(100.0)})
    cash = mp.add_line(conn, ira, "", "Cash", "cash", "$250", D(0))
    mp.change_amount(conn, cash, "+50", D(0))
    assert value_on(conn, ira, D(0)) == 20000 + 30000
    kinds = {r["name"]: r["is_cash"] for r in conn.execute("SELECT * FROM holdings WHERE account_id = ?", (ira,))}
    assert kinds == {"FAKX": 0, "Cash": 1}


def test_price_sources_plaid_fallback_and_your_price(conn, ira):
    conn.execute("INSERT INTO security_prices (security_id, price_date, ticker, price) VALUES ('plaid-s', ?, 'FUNDX', 40.0)",
                 (D(1),))
    conn.commit()
    pid = mp.add_line(conn, ira, "FUNDX", "Fake Fund", "fund", "10", D(0))  # no live quote: Plaid's price
    line = mp.lines(conn, ira)[0]
    assert (line.price, line.price_source, line.value_cents) == (40.0, "plaid", 40000)
    mp.set_price(conn, pid, "42.50")
    assert mp.lines(conn, ira)[0].price_source == "you"
    mp.revalue(conn, ira, {"FUNDX": quote(41.0)})                   # a live quote doesn't replace yours
    assert mp.lines(conn, ira)[0].price == 42.5


def test_unchanged_refresh_writes_nothing(conn, ira):
    """Page loads revalue (user's choice); with nothing new they must not bump the page revision."""
    mp.add_line(conn, ira, "FAKX", "", "stock", "10", D(0), live={"FAKX": quote(50.0)})
    before = db.revision(conn)
    assert mp.revalue_all(conn, {"FAKX": quote(50.0)}) is False
    assert db.revision(conn) == before


def test_guards(conn, ira):
    car = manual.create_account(conn, "Fake Car", "property", "9000", D(0))
    with pytest.raises(mp.PositionError, match="Investment"):
        mp.add_line(conn, car, "FAKX", "", "stock", "1", D(0))
    mp.add_line(conn, ira, "FAKX", "", "stock", "1", D(0), live={"FAKX": quote(10.0)})
    with pytest.raises(mp.PositionError, match="already a line"):
        mp.add_line(conn, ira, "fakx", "", "stock", "1", D(0))
    with pytest.raises(mp.PositionError, match="name"):
        mp.add_line(conn, ira, "", "", "cash", "100", D(0))
    with pytest.raises(manual.ManualError, match="positions"):
        manual.add_valuation(conn, ira, "500", D(1))
    manual.delete_account(conn, ira, "Fake IRA")                    # mirrored holdings don't block it
    assert conn.execute("SELECT COUNT(*) FROM manual_positions").fetchone()[0] == 0


# ---------------------------------------------------------------- pages

ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def client(settings, conn, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.quotes import QuoteGateway
    from fintrack.web.app import create_app

    QuoteGateway.clear_cache()
    prices = {"FAKX": 50.0}
    from tests.fakes import alpaca_fetch
    fake = QuoteGateway("fake-key", "fake-secret", fetch=alpaca_fetch(prices))
    client = TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher,
                                   quotes_factory=lambda s: fake), base_url="https://localhost:8443")
    client.prices = prices
    yield client
    QuoteGateway.clear_cache()


def test_page_add_line_and_overview_revalues_on_load(client, conn, ira):
    from fintrack.quotes import QuoteGateway

    # History (and Add a value) moved to the account's Balances page (user's request 2026-10-13)
    page = client.get(f"/accounts/{ira}").text
    assert "<h2>Balances</h2>" in page and "Add value" in page               # no tickers yet: both (2026-11-14 names)
    assert "<h2>Positions</h2>" not in page                                   # no separate positions section (2026-10-12)
    moved = client.get(f"/manual/{ira}", follow_redirects=False)            # folded into the account page (2026-10-15)
    assert moved.status_code == 307 and moved.headers["location"] == f"/accounts/{ira}"
    added = client.post(f"/manual/{ira}/events", data={"ticker": "FAKX", "kind": "balance", "unit": "quantity", "amount": "4",
                                                       "date": D(0)}, headers=ORIGIN, follow_redirects=False)
    assert added.headers["location"].startswith(f"/accounts/{ira}?")       # back to where the form is
    page = client.get(f"/accounts/{ira}").text
    assert "Add value" not in page and "$200.00" in page
    assert "style=" not in page.split("<main>")[1]                           # no inline styles (CSP)

    client.prices["FAKX"] = 60.0                                              # price moves...
    QuoteGateway.clear_cache()                                                # ...after the 5-minute cache
    client.get("/")                                                           # an Overview load revalues
    assert manual.get_account(conn, ira)["current_balance_cents"] == 24000


# ---------------------------------------------------------------- dollar events (migration 0025, user's request 2026-10-12)

def price_row(conn, ticker, day, price, source="alpaca"):
    conn.execute("INSERT OR REPLACE INTO ticker_prices (ticker, price_date, price, source) VALUES (?, ?, ?, ?)",
                 (ticker, day, price, source))
    conn.commit()


def test_dollar_events_turn_into_shares_at_that_days_price(conn, ira):
    price_row(conn, "FAKX", D(10), 20.0)
    pid = mp.add_line(conn, ira, "FAKX", "Fake Index Fund", "fund", "$1,000", D(10), mode="opening")
    assert mp.lines(conn, ira)[0].changes[0]["quantity"] == pytest.approx(50.0)        # $1,000 / $20
    assert value_on(conn, ira, D(10)) == 100000                                         # the value you typed
    mp.change_amount(conn, pid, "200", D(5), mode="contribution")                     # +$200 at $20 (latest <= D5)
    assert mp.lines(conn, ira)[0].changes[0]["quantity"] == pytest.approx(60.0)
    # a better price for that day arrives (e.g. Alpaca's official close): the shares follow, the dollars stay
    price_row(conn, "FAKX", D(5), 25.0)
    mp.revalue(conn, ira)
    assert mp.lines(conn, ira)[0].changes[0]["quantity"] == pytest.approx(58.0)         # 50 + 200 / 25
    mp.change_amount(conn, pid, "100", D(2), mode="withdrawal")                        # -$100 at $25
    events = mp.events(conn, ira)
    assert [(e.date, e.kind, e.unit, e.cash_cents) for e in events] == [
        (D(2), "withdrawal", "value", -10000), (D(5), "contribution", "value", 20000), (D(10), "balance", "value", 100000)]
    assert events[0].quantity == pytest.approx(54.0) and events[1].price == 25.0
    assert events[0].shares == pytest.approx(-4.0) and events[0].dollars == -10000          # the event itself: 4 out


def test_a_withdrawal_larger_than_the_line_is_refused_and_nothing_changes(conn, ira):
    price_row(conn, "FAKX", D(10), 10.0)
    pid = mp.add_line(conn, ira, "FAKX", "", "fund", "$100", D(10), mode="opening")    # 10 shares
    with pytest.raises(mp.PositionError, match="less than nothing"):
        mp.change_amount(conn, pid, "150", D(3), mode="withdrawal")
    assert len(mp.events(conn, ira)) == 1


def test_editing_an_event_rederives_it(conn, ira):
    price_row(conn, "FAKX", D(10), 10.0)
    pid = mp.add_line(conn, ira, "FAKX", "", "fund", "$100", D(10), mode="opening")
    mp.change_amount(conn, pid, "50", D(4), mode="contribution")
    mp.edit_event(conn, pid, D(4), D(6), "contribution", "value", "80")                  # new date and amount
    events = mp.events(conn, ira)
    assert [(e.date, e.cash_cents) for e in events] == [(D(6), 8000), (D(10), 10000)]
    assert events[0].quantity == pytest.approx(18.0)
    with pytest.raises(mp.PositionError):                                               # bad edit: original kept
        mp.edit_event(conn, pid, D(6), D(6), "withdrawal", "value", "500")
    assert [(e.date, e.cash_cents) for e in mp.events(conn, ira)] == [(D(6), 8000), (D(10), 10000)]


def test_history_lists_your_balances_and_edits_them_in_place(client, conn, ira):
    """Since 2026-11-14 (docs/parity-plan.md) what you type in History is a balance; contributions, withdrawals and fees
    are activity (Add activity, ledger.py)."""
    price_row(conn, "FAKX", D(10), 20.0)
    client.post(f"/manual/{ira}/events", data={"ticker": "fakx", "kind": "balance", "unit": "value", "amount": "1,000",
                                               "date": D(10)}, headers=ORIGIN)
    refused = client.post(f"/manual/{ira}/events", data={"ticker": "FAKX", "kind": "contribution", "unit": "value",
                                                         "amount": "200", "date": D(4)}, headers=ORIGIN,
                          follow_redirects=False)
    assert "Add+activity" in refused.headers["location"]
    pid = conn.execute("SELECT id FROM manual_positions WHERE account_id = ?", (ira,)).fetchone()[0]
    page = client.get(f"/accounts/{ira}").text                       # History lives here (2026-10-13)
    history = page.split("<h2>Balances</h2>")[1].split("</section>")[0]
    assert history.count('action="/positions/') == 2                          # 1 balance x (save + delete)
    assert "from positions" not in history                                    # the daily values aren't events
    assert '<option value="contribution"' not in history and "Value history" not in page
    response = client.post(f"/positions/{pid}/changes/edit", data={"account_id": ira, "old_date": D(10), "date": D(10),
                                                                   "kind": "balance", "unit": "value", "amount": "1,500"},
                           headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303 and "msg=Saved" in response.headers["location"]
    assert [e.cash_cents for e in mp.events(conn, ira)] == [150000]

# ---------------------------------------------------------------- the Add form (record_event, user's request 2026-10-12)

def test_add_form_events_in_quantity_or_value_create_lines_as_needed(conn, ira):
    price_row(conn, "FAKX", D(10), 10.0)
    price_row(conn, "FAKX", D(5), 20.0)
    mp.record_event(conn, ira, "fakx", D(10), "balance", "quantity", "30")            # a new ticker: a new line
    mp.record_event(conn, ira, "FAKX", D(5), "contribution", "quantity", "5")         # +5 shares
    mp.record_event(conn, ira, "FAKX", D(3), "withdrawal", "value", "100")            # -$100 at $20 = -5 shares
    assert conn.execute("SELECT COUNT(*) FROM manual_positions WHERE account_id = ?", (ira,)).fetchone()[0] == 1
    events = mp.events(conn, ira)
    assert [(e.kind, e.unit, e.typed) for e in events] == [("withdrawal", "value", "100.00"),
                                                           ("contribution", "quantity", "5"), ("balance", "quantity", "30")]
    assert [round(e.quantity, 6) for e in events] == [30.0, 35.0, 30.0]
    assert events[1].dollars == 10000                                                  # 5 shares x $20
    with pytest.raises(mp.PositionError, match="already has an event"):
        mp.record_event(conn, ira, "FAKX", D(5), "contribution", "value", "1")
    with pytest.raises(mp.PositionError):                                              # more than held: nothing written
        mp.record_event(conn, ira, "OTHX", D(2), "withdrawal", "quantity", "1")
    assert conn.execute("SELECT COUNT(*) FROM manual_positions WHERE ticker = 'OTHX'").fetchone()[0] == 0


def test_deleting_a_tickers_last_event_removes_it_from_the_account(conn, ira):
    price_row(conn, "FAKX", D(10), 10.0)
    pid = mp.record_event(conn, ira, "FAKX", D(10), "balance", "quantity", "3")
    mp.delete_change(conn, pid, D(10))
    assert conn.execute("SELECT COUNT(*) FROM manual_positions WHERE account_id = ?", (ira,)).fetchone()[0] == 0


def test_a_fee_reduces_the_line_but_is_not_a_withdrawal_for_savings(conn, ira):
    conn.execute("UPDATE accounts SET subtype = '401k' WHERE id = ?", (ira,))
    price_row(conn, "FAKX", D(10), 10.0)
    mp.record_event(conn, ira, "FAKX", D(10), "balance", "quantity", "20")
    mp.record_event(conn, ira, "FAKX", D(4), "fee", "value", "15")                    # -$15 at $10 = -1.5 shares
    mp.record_event(conn, ira, "FAKX", D(2), "withdrawal", "quantity", "1")
    events = mp.events(conn, ira)
    assert [(e.kind, round(e.quantity, 6)) for e in events] == [("withdrawal", 17.5), ("fee", 18.5), ("balance", 20.0)]
    assert events[1].dollars == -1500 and events[1].typed == "15.00"
    mp.edit_event(conn, events[1].position_id, D(4), D(4), "fee", "quantity", "2")       # still a fee after editing
    assert [e.kind for e in mp.events(conn, ira)][1] == "fee"
