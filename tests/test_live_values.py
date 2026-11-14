"""Live prices in balances (live_values.py, migration 0029; user's decision 2026-10-12): a connected investment account
= its reported balance + the market move of its quotable holdings. Made-up accounts and prices only."""

from datetime import date, datetime

import pytest

from fintrack import items, live_values, networth
from fintrack.quotes import Quote

BROKERAGE, CHECKING = 1, 2


def quote(price):
    return Quote(price=price, change_pct=0.0, previous_close=None, as_of=int(datetime(2026, 10, 9, 12).timestamp()))


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Broker')")
    for acc_id, kind in [(BROKERAGE, "investment"), (CHECKING, "depository")]:
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification, current_balance_cents,"
                     " reported_balance_cents) VALUES (?, 1, ?, ?, ?, ?, 100000, 100000)",
                     (acc_id, f"acc-{acc_id}", f"Fake {kind}", kind, items.classify(kind)))
    for security_id, ticker, kind, is_cash, qty, value in [
            ("s1", "FAKX", "etf", 0, 10.0, 50000),                # quoted below
            ("s2", "FUNDX", "mutual fund", 0, 5.0, 30000),        # no live quote: keeps its reported value
            ("c1", None, "cash", 1, 200.0, 20000)]:               # cash: never moves
        conn.execute("INSERT INTO holdings (account_id, security_id, ticker, security_type, is_cash, quantity, value_cents)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?)", (BROKERAGE, security_id, ticker, kind, is_cash, qty, value))
    conn.commit()
    return conn


def balance(conn, account_id):
    return conn.execute("SELECT current_balance_cents FROM accounts WHERE id = ?", (account_id,)).fetchone()[0]


def test_live_quotes_move_the_balance_and_net_worth(db):
    assert live_values.revalue_connected(db, {"FAKX": quote(60.0)}, date(2026, 10, 9)) is True
    assert balance(db, BROKERAGE) == 100000 + (60000 - 50000)                # +$100 market move
    assert balance(db, CHECKING) == 100000                                   # not an investment account
    assert networth.current_net_worth(db).net_cents == 110000 + 100000          # net worth includes the move
    snapshot = db.execute("SELECT balance_cents FROM balance_snapshots WHERE account_id = ? AND date = '2026-10-09'",
                          (BROKERAGE,)).fetchone()
    assert snapshot[0] == 110000                                             # today's history follows
    assert live_values.revalue_connected(db, {"FAKX": quote(60.0)}, date(2026, 10, 9)) is False   # unchanged: no write
    assert live_values.revalue_connected(db, {}, date(2026, 10, 9)) is False                       # no quotes: left alone
    assert balance(db, BROKERAGE) == 110000


def test_a_sync_resets_the_base_to_the_new_reported_balance(db):
    live_values.revalue_connected(db, {"FAKX": quote(60.0)}, date(2026, 10, 9))
    items.upsert_accounts(db, 1, [{"account_id": "acc-1", "name": "Fake investment", "type": "investment",
                                   "balances": {"current": 1234.56}}])
    assert tuple(db.execute("SELECT current_balance_cents, reported_balance_cents FROM accounts WHERE id = ?",
                            (BROKERAGE,)).fetchone()) == (123456, 123456)           # the institution's figure again
    live_values.revalue_connected(db, {"FAKX": quote(60.0)}, date(2026, 10, 9))
    assert balance(db, BROKERAGE) == 123456 + 10000                          # ...plus the move once quotes arrive
