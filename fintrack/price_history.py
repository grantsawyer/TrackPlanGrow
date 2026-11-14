"""Daily price history from Alpaca (user's decision 2026-10-07, with the switch from Finnhub).

Plaid only reports today's price, so fintrack used to build price history one day at a time.
Alpaca's free plan has daily closes back to 2016, so every sync fetches the last HISTORY_DAYS
days for the tickers fintrack prices (your stocks / ETFs / funds, manual position tickers, and
the benchmark ETFs) in one call; a ticker seen for the first time gets DEEP_DAYS (ten years, for Portfolio's
10-year range) once, and fills the tables those features read:

* security_prices (Overview movers, performance cards, Investments table): official closes,
  replacing a different price Plaid reported for that day, so every range uses the same source
  as the live quotes' previous close (user's report 2026-10-08); funds Alpaca doesn't cover keep
  Plaid's prices;
* benchmark_prices (benchmark cards and chart lines): official closes replace the prices
  fintrack recorded during the day;
* ticker_prices (manual positions): as source 'alpaca', which outranks Plaid but never a price
  you set; then accounts with positions are revalued from their first entry, so past values use
  real daily prices instead of the earliest one known.

It runs at every sync and, at most every REFRESH_SECONDS per server run, on the page loads that
already fetch live prices (Overview, Investments, Portfolio; `refresh_if_due`), so history is
there right after setup or a restart without waiting for the next sync.

Today is never written from history (its bar is unfinished); live quotes own today. Every write
is skipped when the stored value is already the same, so a repeat sync writes nothing new.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import date, timedelta

from fintrack import manual_positions, performance, prices, watchlist
from fintrack.quotes import QuoteGateway

HISTORY_DAYS = 400          # covers the 365-day and year-to-date ranges, with room to spare
# Portfolio's 10-year range (user's request 2026-11-13): each ticker's closes back this far are fetched once (then
# HISTORY_DAYS at every sync, like the rest); the tickers done are kept in app_settings DEEP_KEY.
DEEP_DAYS = 3660
DEEP_KEY = "price_history_deep"
REFRESH_SECONDS = 6 * 3600  # page loads refresh history at most this often (per server run)

_last_refresh = 0.0         # time.time() of the last page-load refresh in this process
_refresh_lock = threading.Lock()


def tickers(conn: sqlite3.Connection) -> list[str]:
    """Everything fintrack prices: held stocks/ETFs/funds, manual position tickers, benchmarks, your watchlist."""
    return sorted(set(prices.held_tickers(conn)) | set(manual_positions.position_tickers(conn))
                  | set(performance.benchmark_symbols()) | set(watchlist.tickers(conn)))


def backfill(conn: sqlite3.Connection, gateway: QuoteGateway, today: date | None = None) -> int:
    """Fetch and store daily closes; returns how many closes Alpaca sent (0 if unavailable)."""
    import json

    today = today or date.today()
    wanted = tickers(conn)
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (DEEP_KEY,)).fetchone()
    deep_done = set(json.loads(row[0])) if row else set()
    deep = [t for t in wanted if t not in deep_done]
    closes = gateway.history([t for t in wanted if t in deep_done], today - timedelta(days=HISTORY_DAYS)) \
        if len(deep) < len(wanted) else {}
    if deep:                                             # new tickers: ten years, once
        closes.update(gateway.history(deep, today - timedelta(days=DEEP_DAYS)))
        if any(closes.get(t) for t in deep):             # Alpaca answered: don't ask for these again
            with conn:
                conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET"
                             " value = excluded.value", (DEEP_KEY, json.dumps(sorted(deep_done | set(deep)))))
    closes = {t: [(d, p) for d, p in rows if d < today.isoformat() and p > 0] for t, rows in closes.items()}
    if not any(closes.values()):
        return 0
    benchmarks = set(performance.benchmark_symbols())
    # manual positions' tickers and your watchlist's (2026-11-04) keep their history in ticker_prices
    position_tickers = set(manual_positions.position_tickers(conn)) | set(watchlist.tickers(conn))
    with conn:
        # Holdings: every security id carrying this ticker (Plaid's, and manual "manual:X" mirrors).
        for ticker, rows in closes.items():
            for security_id, name in conn.execute(
                    "SELECT security_id, MAX(name) FROM holdings WHERE ticker = ? GROUP BY security_id", (ticker,)):
                conn.executemany(
                    """INSERT INTO security_prices (security_id, price_date, ticker, name, price)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT (security_id, price_date) DO UPDATE SET price = excluded.price
                       WHERE security_prices.price != excluded.price""",
                    [(security_id, d, ticker, name, p) for d, p in rows])
            if ticker in benchmarks:
                conn.executemany(
                    """INSERT INTO benchmark_prices (symbol, price_date, price) VALUES (?, ?, ?)
                       ON CONFLICT (symbol, price_date) DO UPDATE SET price = excluded.price
                       WHERE price != excluded.price""", [(ticker, d, p) for d, p in rows])
            if ticker in position_tickers:
                for d, p in rows:
                    manual_positions._save_price(conn, ticker, d, p, "alpaca")
    # Manual accounts with positions: revalue every day since their first entry.
    for account_id, first in conn.execute(
            """SELECT p.account_id, MIN(c.date) FROM manual_positions p
               JOIN manual_position_changes c ON c.position_id = p.id
               JOIN accounts a ON a.id = p.account_id WHERE a.is_hidden = 0
               GROUP BY p.account_id""").fetchall():
        manual_positions.revalue(conn, account_id, since=first, today=today)
    return sum(len(rows) for rows in closes.values())


def refresh_if_due(conn: sqlite3.Connection, gateway: QuoteGateway | None) -> None:
    """Run `backfill` from a page load, at most every REFRESH_SECONDS in this server process
    (the first page after a restart always runs it). No keys: nothing to do."""
    global _last_refresh
    if gateway is None:
        return
    with _refresh_lock:
        if time.time() - _last_refresh < REFRESH_SECONDS:
            return
        _last_refresh = time.time()      # claimed before the call: concurrent pages don't repeat it
    backfill(conn, gateway)


def reset_refresh_timer() -> None:
    """Tests: let the next page load refresh again."""
    global _last_refresh
    with _refresh_lock:
        _last_refresh = 0.0
