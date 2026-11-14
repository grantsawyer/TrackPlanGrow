"""Security prices over time and the Overview "movers" table.

Plaid's holdings only carry today's price, so each sync records one price per
security per price date (`security_prices`, migration 0012). Movers compare the
latest recorded price with the one before it (or a live quote's previous close).
"""

from __future__ import annotations

import sqlite3
from datetime import date

from fintrack.money import to_cents

# Security types we ask Alpaca about (Plaid's `security.type`). An allowlist, because
# Alpaca's stock data only knows US-listed symbols: a crypto "BTC" or an option's ticker
# could match an unrelated stock or ETF and be valued at its price. Anything else
# (including an unknown type) keeps Plaid's price.
QUOTABLE_TYPES = ("equity", "etf", "mutual fund")
_QUOTABLE_SQL = "LOWER(h.security_type) IN (" + ", ".join(f"'{t}'" for t in QUOTABLE_TYPES) + ")"


def record(conn: sqlite3.Connection, rows: list[tuple], today: date | None = None) -> None:
    """Save prices from holdings rows: (security_id, ticker, name, price, price_as_of).
    Cash and rows without a price are skipped. Call inside the caller's transaction."""
    today_iso = (today or date.today()).isoformat()
    conn.executemany(
        """
        INSERT INTO security_prices (security_id, price_date, ticker, name, price)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (security_id, price_date) DO UPDATE SET
            price = excluded.price, ticker = excluded.ticker, name = excluded.name
        """,
        [(sid, as_of or today_iso, ticker, name, price) for sid, ticker, name, price, as_of in rows
         if price is not None],
    )


def held_tickers(conn: sqlite3.Connection) -> list[str]:
    """Tickers of the quotable positions in visible accounts (what we ask Alpaca about)."""
    return [r[0] for r in conn.execute(
        f"""SELECT DISTINCT h.ticker FROM holdings h JOIN accounts a ON a.id = h.account_id
            WHERE a.is_hidden = 0 AND h.is_cash = 0 AND h.ticker IS NOT NULL AND COALESCE(h.value_cents, 0) > 0
              AND {_QUOTABLE_SQL}"""
    )]


def movers(conn: sqlite3.Connection, limit: int | None = 10, live: dict | None = None,
           benchmark: str | None = None) -> dict:
    """Stocks and funds you hold (not cash), combined across accounts.

    Each row: price now, change since the previous recorded price (None until two
    price dates exist), and value held.
    Sorted by the size of the % change, up or down (the Overview's top 5 are the biggest moves; user's request
    2026-10-23); holdings without a change go last; by value held when no change is known yet. `limit=None` returns
    every holding. `fetched_at` is the newest live-quote fetch time (unix seconds), or None when no live price was used.

    `benchmark` (e.g. "SPY", the Overview's always-shown 6th row): returned as `benchmark`, a row like the others with
    `held` False and no value when you don't hold it; when you do, your holding is that row and is left out of `rows`.

    `live` ({ticker: quotes.Quote}) replaces Plaid's price with a current one: the change is
    then vs the previous close and value = shares x live price. Tickers without a quote, and
    securities whose type isn't in QUOTABLE_TYPES, keep Plaid's price and the recorded-price change.
    """
    rows = conn.execute(
        f"""
        WITH held AS (
            SELECT h.security_id, MAX(h.name) AS name, MAX(h.ticker) AS ticker, MAX(h.price) AS price,
                   SUM(h.value_cents) AS value_cents, SUM(h.quantity) AS quantity,
                   MAX({_QUOTABLE_SQL}) AS quotable
            FROM holdings h JOIN accounts a ON a.id = h.account_id
            WHERE a.is_hidden = 0 AND h.is_cash = 0 AND COALESCE(h.value_cents, 0) > 0
            GROUP BY h.security_id
        ),
        latest AS (
            SELECT security_id, MAX(price_date) AS d FROM security_prices GROUP BY security_id
        )
        SELECT held.*, latest.d AS latest_date, cur.price AS latest_price,
               (SELECT p.price FROM security_prices p
                 WHERE p.security_id = held.security_id AND p.price_date < latest.d
                 ORDER BY p.price_date DESC LIMIT 1) AS previous_price,
               (SELECT MAX(p.price_date) FROM security_prices p
                 WHERE p.security_id = held.security_id AND p.price_date < latest.d) AS previous_date
        FROM held
        LEFT JOIN latest ON latest.security_id = held.security_id
        LEFT JOIN security_prices cur ON cur.security_id = held.security_id AND cur.price_date = latest.d
        """
    ).fetchall()
    live = live or {}
    result = []
    for r in rows:
        price = r["latest_price"] if r["latest_price"] is not None else r["price"]
        change, since, source = None, r["previous_date"], "plaid"
        if r["previous_price"]:
            change = (price - r["previous_price"]) / r["previous_price"]
        value_cents = r["value_cents"]
        quote = live.get(r["ticker"]) if r["ticker"] and r["quotable"] else None
        if quote is not None:
            price, change, since, source = quote.price, quote.change_pct, "previous close", "live"
            if r["quantity"]:
                value_cents = to_cents(r["quantity"] * quote.price)
        result.append({
            "name": r["name"], "ticker": r["ticker"], "price": price, "source": source,
            "change": change, "previous_date": since, "latest_date": r["latest_date"],
            "value_cents": value_cents,
        })
    bench = None
    if benchmark:
        mine = [m for m in result if (m["ticker"] or "").upper() == benchmark.upper()]
        result = [m for m in result if m not in mine]
        bench = {**mine[0], "held": True} if mine else _benchmark_row(conn, benchmark, live)
    has_daily = any(m["change"] is not None for m in result)
    if has_daily:
        result.sort(key=lambda m: (m["change"] is None, -abs(m["change"] or 0)))
    else:
        result.sort(key=lambda m: -(m["value_cents"] or 0))
    fetched = [q.fetched_at for q in live.values() if getattr(q, "fetched_at", None)]
    holdings = result + ([bench] if bench and bench["held"] else [])     # the footnote counts every holding
    return {"rows": result[:limit], "by": "change" if has_daily else "value", "count": len(holdings), "benchmark": bench,
            "live": sum(1 for m in holdings if m["source"] == "live"),
            "fetched_at": max(fetched) if fetched else None}


def _benchmark_row(conn: sqlite3.Connection, symbol: str, live: dict) -> dict:
    """A benchmark you don't hold, as a movers row: the live quote (vs the previous close) when there is one, else its
    last two recorded closes (benchmark_prices, kept by performance.py / price_history.py)."""
    row = {"name": symbol, "ticker": symbol, "price": None, "source": "plaid", "change": None, "previous_date": None,
           "latest_date": None, "value_cents": None, "held": False}
    quote = live.get(symbol)
    if quote is not None:
        return {**row, "price": quote.price, "change": quote.change_pct, "source": "live", "previous_date": "previous close"}
    closes = conn.execute("SELECT price_date, price FROM benchmark_prices WHERE symbol = ? ORDER BY price_date DESC LIMIT 2",
                          (symbol,)).fetchall()
    if closes:
        row.update(price=closes[0]["price"], latest_date=closes[0]["price_date"])
        if len(closes) == 2 and closes[1]["price"]:
            row.update(change=(closes[0]["price"] - closes[1]["price"]) / closes[1]["price"], previous_date=closes[1]["price_date"])
    return row


# ---------------------------------------------------------------- the Overview card's Watchlist view (user's request 2026-11-04)

# What the Overview's Investments card shows, from its title dropdown: your holdings' movers or your watchlist's.
# app_settings 'overview_movers'.
MOVERS_VIEWS = ("investments", "watchlist")


def movers_view(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'overview_movers'").fetchone()
    return row[0] if row and row[0] in MOVERS_VIEWS else "investments"


def set_movers_view(conn: sqlite3.Connection, view: str) -> None:
    if view not in MOVERS_VIEWS:
        raise ValueError("Choose investments or watchlist.")
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('overview_movers', ?)"
                 " ON CONFLICT (key) DO UPDATE SET value = excluded.value WHERE value != excluded.value", (view,))
    conn.commit()


def watch_movers(conn: sqlite3.Connection, live: dict | None = None, benchmark: str | None = None) -> dict:
    """`movers`, for your watchlist's tickers (watchlist.py) instead of your holdings: each one's price (its live quote, else
    its latest close in ticker_prices) and change (vs the previous close, else between its last two closes), the biggest
    moves first; no value (you don't hold them). `benchmark` as in `movers`: its row, or the watched one when you watch it."""
    from fintrack import watchlist

    live = live or {}
    result = []
    for w in watchlist.items(conn):
        row = {"name": w.name or w.ticker, "ticker": w.ticker, "price": None, "source": "plaid", "change": None,
               "previous_date": None, "latest_date": None, "value_cents": None}
        quote = live.get(w.ticker)
        if quote is not None:
            row.update(price=quote.price, change=quote.change_pct, source="live", previous_date="previous close")
        else:
            closes = conn.execute("SELECT price_date, price FROM ticker_prices WHERE ticker = ? ORDER BY price_date DESC LIMIT 2",
                                  (w.ticker,)).fetchall()
            if closes:
                row.update(price=closes[0]["price"], latest_date=closes[0]["price_date"])
                if len(closes) == 2 and closes[1]["price"]:
                    row.update(change=(closes[0]["price"] - closes[1]["price"]) / closes[1]["price"],
                               previous_date=closes[1]["price_date"])
        result.append(row)
    bench = None
    if benchmark:
        mine = [m for m in result if m["ticker"] == benchmark.upper()]
        result = [m for m in result if m not in mine]
        bench = {**mine[0], "held": True} if mine else _benchmark_row(conn, benchmark, live)
    result.sort(key=lambda m: (m["change"] is None, -abs(m["change"] or 0)))
    fetched = [q.fetched_at for q in live.values() if getattr(q, "fetched_at", None)]
    return {"rows": result, "by": "change", "count": len(result), "benchmark": bench,
            "live": sum(1 for m in result if m["source"] == "live"), "fetched_at": max(fetched) if fetched else None}
