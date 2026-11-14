"""Your watchlist (user's request 2026-11-04): stock tickers you follow without holding them, on the Investments card's
Watchlist tab, priced like your holdings (performance.watch_rows) and drawable on its chart.

Prices come from Alpaca through the QuoteGateway, as for holdings: a live quote with the page's other tickers, and daily
closes in ticker_prices (price_history.backfill, at every sync and page refresh, and once when you add a ticker so its
ranges fill at once). Only the ticker symbols are sent. A ticker is added only when Alpaca has a price for it, so a typo
doesn't sit on the list empty. Its name is yours to type, else your holdings' name for it, else Alpaca's company name.
Finding a ticker by company: `search`, over Alpaca's stock list stored locally (below).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
MAX_NAME = 60


class WatchlistError(ValueError):
    """A change was refused; the message is shown to you."""


@dataclass
class Watched:
    ticker: str
    name: str


def clean_ticker(text: str) -> str:
    ticker = (text or "").strip().upper().lstrip("$")
    if not TICKER.match(ticker):
        raise WatchlistError("Enter a ticker, like AAPL, or pick a company from the matches under the box.")
    return ticker


def items(conn: sqlite3.Connection) -> list[Watched]:
    """The watchlist, in the order you added it."""
    return [Watched(r[0], r[1]) for r in conn.execute("SELECT ticker, name FROM watchlist ORDER BY added_at, ticker")]


def tickers(conn: sqlite3.Connection) -> list[str]:
    return [w.ticker for w in items(conn)]


def add(conn: sqlite3.Connection, quotes, ticker_text: str, name: str = "", today: date | None = None) -> str:
    """Add a ticker Alpaca can price (`quotes`: the QuoteGateway; None without keys), then fetch its daily history.
    Returns the ticker."""
    from fintrack import manual_positions, price_history

    ticker = clean_ticker(ticker_text)
    if conn.execute("SELECT 1 FROM watchlist WHERE ticker = ?", (ticker,)).fetchone():
        raise WatchlistError(f"{ticker} is already on your watchlist.")
    if quotes is None:
        raise WatchlistError("A watchlist needs Alpaca keys (ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY in .env).")
    if quotes.quotes([ticker]).get(ticker) is None:
        raise WatchlistError(f"Alpaca has no price for {ticker}: check the ticker, or pick a match under the box.")
    name = " ".join((name or "").split())[:MAX_NAME]
    if not name:                     # your holdings' name for it, else Alpaca's company name
        row = conn.execute("""SELECT COALESCE((SELECT MAX(name) FROM holdings WHERE ticker = ?),
                                              (SELECT name FROM alpaca_assets WHERE symbol = ?))""", (ticker, ticker)).fetchone()
        name = (row[0] or "")[:MAX_NAME] if row else ""
    today = today or date.today()
    closes = quotes.history([ticker], today - timedelta(days=price_history.HISTORY_DAYS)).get(ticker, [])
    with conn:
        conn.execute("INSERT INTO watchlist (ticker, name) VALUES (?, ?)", (ticker, name))
        for day, price in closes:
            if day < today.isoformat() and price > 0:        # today belongs to the live quote
                manual_positions._save_price(conn, ticker, day, price, "alpaca")
    return ticker


def remove(conn: sqlite3.Connection, ticker: str) -> None:
    """Take a ticker off the list. Its stored prices stay (a manual position may share them)."""
    with conn:
        conn.execute("DELETE FROM watchlist WHERE ticker = ?", ((ticker or "").upper(),))


# ---------------------------------------------------------------- finding a ticker by company (user's decision 2026-11-04)
# Alpaca's list of active US stocks and ETFs (QuoteGateway.assets, migration 0067), fetched about weekly at the sync (or
# by the first search when it's missing) and searched here: nothing leaves the app as you type.

ASSETS_MAX_AGE = timedelta(days=7)
SEARCH_LIMIT = 40          # the popup's list scrolls (user's request 2026-11-04)
_NAME_TAIL = re.compile(r"\s+(Common Stock|Ordinary Shares)$", re.IGNORECASE)


def clean_name(name: str) -> str:
    """'NVIDIA Corporation Common Stock' -> 'NVIDIA Corporation'."""
    return _NAME_TAIL.sub("", " ".join((name or "").split()))


def refresh_assets(conn: sqlite3.Connection, quotes, force: bool = False, now: float | None = None) -> bool:
    """Fetch Alpaca's list when it's older than ASSETS_MAX_AGE (or `force`), replacing the stored one in one go. False when
    nothing was fetched (fresh enough, no keys, or Alpaca unreachable: the old list stays)."""
    import time

    now = now if now is not None else time.time()
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'alpaca_assets_at'").fetchone()
    fresh = row is not None and now - float(row[0]) < ASSETS_MAX_AGE.total_seconds()
    if quotes is None or (fresh and not force):
        return False
    found = quotes.assets()
    if not found:
        return False
    with conn:
        conn.execute("DELETE FROM alpaca_assets")
        conn.executemany("INSERT OR REPLACE INTO alpaca_assets (symbol, name, exchange) VALUES (?, ?, ?)",
                         [(s.upper(), clean_name(n), e) for s, n, e in found])
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('alpaca_assets_at', ?)"
                     " ON CONFLICT (key) DO UPDATE SET value = excluded.value", (str(now),))
    return True


# Not a company's own stock, so after companies among equally good matches: funds (Alpaca's list doesn't say which are, so
# by their names: "ETF", "Fund", the big fund issuers, leverage like "2X" or "Ultra") and warrants, rights and units.
_FUND = re.compile(r"\b(ETF|ETFS|ETN|FUND|TRUST|PROSHARES|DIREXION|GRANITESHARES|TRADR|ISHARES|SPDR|INVESCO|VANECK|"
                   r"ULTRA|ULTRAPRO|ULTRASHORT|\d+X|WARRANTS?|RIGHTS?|UNITS?)\b")


def _loose(q: str, name: str) -> bool:
    """`q` in two pieces in `name` (user's request 2026-11-04: "spacex" -> "SPACE Exploration"): the first a whole word of
    at least 3 letters and at least half of `q`, the rest later in the name (its spaces ignored). A whole word, so a few
    letters that happen to appear in order don't match."""
    for cut in range(max(3, (len(q) + 1) // 2), len(q)):
        head, tail = q[:cut], q[cut:]
        for m in re.finditer(r"(?:^|[^A-Z0-9])(" + re.escape(head) + r")(?=$|[^A-Z0-9])", name):
            if tail in name[m.end(1):].replace(" ", ""):
                return True
    return False


def search(conn: sqlite3.Connection, text: str, limit: int = SEARCH_LIMIT) -> list[dict]:
    """Up to `limit` stocks or ETFs whose ticker or company name matches `text` (any case). Order: the exact ticker,
    then tickers starting with it, then name matches: first the good ones (a word starting with it, or `_loose`), then a
    name merely containing it; among each, companies before funds (`_FUND`; so "spacex" finds the company ahead of the
    funds named after it, user's request 2026-11-04), then word starts before loose ones, OTC listings after the rest,
    shorter names first.
    Those already on your watchlist are left out. [{symbol, name, exchange}]."""
    q = " ".join((text or "").split()).upper()
    if not q:
        return []
    watched = set(tickers(conn))
    half = q[:(len(q) + 1) // 2].replace("%", "")
    rows = conn.execute("SELECT symbol, name, exchange FROM alpaca_assets WHERE symbol LIKE ? OR upper(name) LIKE ?",
                        (q.replace("%", "") + "%", "%" + half + "%")).fetchall()

    def rank(r) -> tuple | None:
        symbol, name = r[0], r[1].upper()
        if symbol == q:
            return (0, False, False, 0, False, 0, symbol)
        if symbol.startswith(q):
            return (1, False, False, 0, r[2] == "OTC", len(r[1]), symbol)
        if re.search(r"(^|[^A-Z0-9])" + re.escape(q), name):
            good, how = True, 0
        elif _loose(q, name):
            good, how = True, 1
        elif q in name:
            good, how = False, 2
        else:
            return None                           # the prefilter's wider net caught it, but it doesn't match
        return (2, not good, bool(_FUND.search(name)), how, r[2] == "OTC", len(r[1]), symbol)
    ranked = sorted((k, r) for r in rows if r[0] not in watched and (k := rank(r)) is not None)
    return [{"symbol": r[0], "name": r[1], "exchange": r[2]} for _, r in ranked[:limit]]
