"""Investment performance cards: your stocks vs three benchmarks (user's request 2026-10-07).

Four metrics, each as a % change over the Investments chart's ranges (1 day, 1 week,
90 days, 180 days, year to date, 365 days; `ranges()`) plus today's change:

* Your stocks: the stocks and funds you hold now (cash left out), weighted by their
  current value. Each one's change runs from its recorded price at the start of the range
  (prices.py records one per price date at each sync; price_history.py fills past days from
  Alpaca) to its live Alpaca price, or its
  latest recorded price without one. This is the change of today's holdings, so buys and
  sells during the range don't count, just as an index's change ignores money flowing in.
* Benchmarks: ETFs that track each index, because Alpaca's free plan quotes US-listed
  symbols but not the indexes themselves: SPY (S&P 500), IWM (Russell 2000), URTH (MSCI
  World). Their prices are recorded here (benchmark_prices, migration 0018) because
  that table is also filled from Alpaca's daily history at each sync (price_history.py).

History only starts when recording starts, so a range reaching further back uses the
first recorded price and says "since <date>". With no earlier price, the change is None.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace, dataclass, field
from datetime import date, datetime, timedelta

from fintrack import networth, prices, visuals
from fintrack.money import to_cents
from fintrack.quotes import MARKET_ZONE, SESSION

BENCHMARKS = [("SPY", "S&P 500"), ("IWM", "Russell 2000"), ("URTH", "MSCI World")]

@dataclass(frozen=True)
class Range:
    key: str              # stable id for the buttons and the remembered choice ("ytd" every year)
    label: str            # button text
    lookback: int         # % change is measured from this many days ago (YTD: from last Dec 31)
    default: bool = False

    @property
    def period_days(self) -> int:
        """Days named in the Investments table's "N day Period" header (user's request 2026-10-27): the look-back,
        except a week counts its 5 trading days."""
        return 5 if self.key == "7" else self.lookback

    @property
    def chart_days(self) -> int:
        """Days the chart shows: the start day through today, so it starts where the % does."""
        return self.lookback + 1


def ranges(today: date | None = None) -> list[Range]:
    """The Investments ranges, in button order: 1 day, 1 week, YTD, 90, 180, 365 days (YTD after
    1 week: user's request 2026-10-07). Portfolio picks its own subset and order."""
    today = today or date.today()
    ytd = (today - date(today.year - 1, 12, 31)).days
    return [Range("1", "1 day", 1), Range("7", "1 week", 7), Range("ytd", "YTD", ytd),
            Range("90", "90 days", 90, default=True), Range("180", "180 days", 180), Range("365", "365 days", 365)]


# Longer ranges for Portfolio (user's request 2026-11-13: 1 day, YTD, 1 / 3 / 5 / 10 years instead of 90 / 180 days).
LONG_RANGES = [Range("1095", "3 years", 1095), Range("1825", "5 years", 1825), Range("3650", "10 years", 3650)]


def all_ranges(today: date | None = None) -> list[Range]:
    """Every range a change is worked out for: Investments' (`ranges`) and Portfolio's longer ones."""
    return ranges(today) + LONG_RANGES


def portfolio_ranges(today: date | None = None) -> list[Range]:
    """Portfolio's buttons and holdings columns, in order: 1 day, YTD (selected first), 1, 3, 5, 10 years."""
    by_key = {r.key: r for r in all_ranges(today)}
    labels = {"1": "1 day", "ytd": "YTD", "365": "1 year", "1095": "3 years", "1825": "5 years", "3650": "10 years"}
    return [replace(by_key[key], label=label, default=key == "ytd") for key, label in labels.items()]



def quote_date(quote, fallback: date) -> date:
    """The (local) date of a live quote's price: before the open or at a weekend, that's the last
    trading day, not today."""
    return datetime.fromtimestamp(quote.as_of).date() if quote is not None and quote.as_of else fallback


def market_date(live: dict | None, today: date | None = None) -> date:
    """The day every range ends on: the newest live quote's date (the last trading day), else today.
    So overnight and at weekends "1 day" is the last session's move, the same as "today" on the
    cards (found by the user 2026-10-08: ▲0.1% for 1 day vs ▼0.1% today, just after midnight)."""
    today = today or date.today()
    dates = [quote_date(q, today) for q in (live or {}).values() if q is not None and q.as_of]
    return min(max(dates), today) if dates else today


@dataclass
class Period:
    change: float | None      # 0.024 = +2.4%; None when there's no earlier price
    since: str | None         # the start price's date
    start: str = ""           # the range's start date; since > start means history is shorter


@dataclass
class Metric:
    key: str
    label: str
    symbol: str | None = None                  # the benchmark ETF; None for your stocks
    periods: dict[str, Period] = field(default_factory=dict)       # by Range.key
    today: float | None = None                 # vs the previous close (live quotes only)


# ---------------------------------------------------------------- recording

def record_benchmarks(conn: sqlite3.Connection, quotes: dict, today: date | None = None) -> None:
    """Save today's benchmark prices from live quotes ({ticker: quotes.Quote}). The date is
    the quote's own (local) date, so a weekend visit re-records Friday's close."""
    rows = []
    for symbol, _ in BENCHMARKS:
        q = quotes.get(symbol)
        if q is None:
            continue
        day = datetime.fromtimestamp(q.as_of).date() if q.as_of else (today or date.today())
        rows.append((symbol, day.isoformat(), q.price))
    with conn:
        conn.executemany(
            """
            INSERT INTO benchmark_prices (symbol, price_date, price) VALUES (?, ?, ?)
            ON CONFLICT (symbol, price_date) DO UPDATE SET price = excluded.price
            WHERE price != excluded.price          -- unchanged: no write, no page-revision bump
            """,
            rows,
        )


def benchmark_symbols() -> list[str]:
    return [symbol for symbol, _ in BENCHMARKS]


def benchmark_series(conn: sqlite3.Connection) -> dict[str, dict]:
    """Recorded prices per benchmark, oldest first, for the chart's comparison line:
    {symbol: {"label": "S&P 500", "prices": [["2026-10-07", 500.0], ...]}}. The chart scales
    them to your investments' value on the first day of the range (charts.js `area`)."""
    series = {symbol: {"label": label, "prices": []} for symbol, label in BENCHMARKS}
    for r in conn.execute("SELECT symbol, price_date, price FROM benchmark_prices ORDER BY price_date"):
        if r["symbol"] in series:
            series[r["symbol"]]["prices"].append([r["price_date"], r["price"]])
    return series


# ---------------------------------------------------------------- helpers

def _start_price(conn: sqlite3.Connection, table: str, key_col: str, key: str, start: str) -> tuple[str, float] | None:
    """The price on or before `start`, else the first one after it (history is shorter)."""
    row = conn.execute(
        f"SELECT price_date, price FROM {table} WHERE {key_col} = ? AND price_date <= ?"
        " ORDER BY price_date DESC LIMIT 1", (key, start)).fetchone()
    if row is None:
        row = conn.execute(
            f"SELECT price_date, price FROM {table} WHERE {key_col} = ? AND price_date > ?"
            " ORDER BY price_date LIMIT 1", (key, start)).fetchone()
    return (row[0], row[1]) if row else None


def _latest_price(conn: sqlite3.Connection, table: str, key_col: str, key: str) -> tuple[str, float] | None:
    row = conn.execute(f"SELECT price_date, price FROM {table} WHERE {key_col} = ?"
                       " ORDER BY price_date DESC LIMIT 1", (key,)).fetchone()
    return (row[0], row[1]) if row else None


def _change(start: tuple[str, float] | None, end: tuple[str, float] | None) -> float | None:
    """% change from start to end; None unless the start is an earlier day with a price."""
    if not start or not end or start[0] >= end[0] or not start[1]:
        return None
    return end[1] / start[1] - 1


# ---------------------------------------------------------------- metrics

def _holdings_change(conn: sqlite3.Connection, live: dict, today: date, metric: Metric,
                     account_ids: list[int] | None = None) -> Metric:
    """Fill `metric` with the value-weighted change of current non-cash holdings (all accounts,
    or only `account_ids`): per range, and today's. Shared by "Your stocks" (Investments) and
    the Portfolio measures. `today` is the as-of date (market_date), not necessarily the calendar."""
    only = "" if account_ids is None else f"AND h.account_id IN ({','.join('?' * len(account_ids))})"
    if account_ids is not None and not account_ids:
        held = []
    else:
        held = conn.execute(
            f"""
            SELECT h.security_id, MAX(h.ticker) AS ticker, SUM(h.value_cents) AS value_cents,
                   SUM(h.quantity) AS quantity, MAX({prices._QUOTABLE_SQL}) AS quotable
            FROM holdings h JOIN accounts a ON a.id = h.account_id
            WHERE a.is_hidden = 0 AND h.is_cash = 0 AND COALESCE(h.value_cents, 0) > 0 {only}
            GROUP BY h.security_id
            """, account_ids or []
        ).fetchall()
    positions = []        # (value now, end (date, price), live quote or None)
    for h in held:
        quote = live.get(h["ticker"]) if h["ticker"] and h["quotable"] else None
        if quote is not None:
            end = (quote_date(quote, today).isoformat(), quote.price)
            value = h["quantity"] * quote.price * 100 if h["quantity"] else h["value_cents"]
        else:
            end = _latest_price(conn, "security_prices", "security_id", h["security_id"])
            value = h["value_cents"]
        positions.append((h["security_id"], value, end, quote))

    for r in all_ranges(today):
        start_day = (today - timedelta(days=r.lookback)).isoformat()
        weighted, weight, since = 0.0, 0.0, None
        for security_id, value, end, _ in positions:
            start = _start_price(conn, "security_prices", "security_id", security_id, start_day)
            change = _change(start, end)
            if change is None:
                continue
            weighted, weight = weighted + value * change, weight + value
            since = start[0] if since is None else min(since, start[0])
        metric.periods[r.key] = Period(weighted / weight if weight else None, since, start_day)

    today_parts = [(value, q.change_pct) for _, value, _, q in positions if q is not None and q.change_pct is not None]
    total = sum(v for v, _ in today_parts)
    metric.today = sum(v * c for v, c in today_parts) / total if total else None
    return metric


def _your_stocks(conn: sqlite3.Connection, live: dict, today: date) -> Metric:
    return _holdings_change(conn, live, today, Metric("portfolio", "Your stocks"))


def holding_series(conn: sqlite3.Connection, rows: list[HoldingRow], today: date | None = None) -> dict[str, dict]:
    """Daily prices per holding for the Investments performance chart: its "Your stocks" line (weighted
    by value_cents) and the comparison line a row's click adds (user's requests 2026-10-07): {"sec:<security_id>": {"label": ticker or name, "prices":
    [[date, price], ...]}}, oldest first, plus today's live price. Holdings without any price
    (cash, asset lines without a ticker) are left out, so their rows aren't clickable."""
    series = {}
    for row in rows:
        points = [[d, p] for d, p in conn.execute(
            "SELECT price_date, price FROM security_prices WHERE security_id = ? AND price > 0 ORDER BY price_date",
            (row.security_id,))]
        if row.source == "live" and row.price and row.price_date and (not points or points[-1][0] < row.price_date):
            points.append([row.price_date, row.price])     # the live price, on its own (trading) date
        if points:
            # value_cents: the holding's weight in the chart's "Your stocks" line.
            series[f"sec:{row.security_id}"] = {"label": row.ticker or row.name, "prices": points,
                                                "value_cents": row.value_cents or 0}
    return series


def intraday_series(quotes, live: dict, rows: list[HoldingRow], day: date, watched: list[HoldingRow] | None = None) -> dict:
    """Minute prices for the chart's "1 day" range (user's request 2026-10-27): {"series": {key: [[unix
    seconds, price], ...]}, "base": {key: previous close}}, keyed like the daily series (benchmark symbol,
    "sec:<security_id>"). Only symbols with a live quote (its previous close is the 0% line, so the last
    point matches the cards' "today"); the quote itself is the last point, at 4 PM at the latest. Empty
    without Alpaca keys."""
    keys = {symbol: symbol for symbol, _ in BENCHMARKS if symbol in live}
    keys.update({f"sec:{r.security_id}": r.ticker for r in rows if r.source == "live" and r.ticker in live})
    keys.update({r.security_id: r.ticker for r in watched or [] if r.source == "live" and r.ticker in live})   # "tick:<T>"
    if quotes is None or not keys:
        return {"series": {}, "base": {}}
    minutes = quotes.intraday(sorted(set(keys.values())), day)
    # The session's close: a later quote (an after-hours IEX trade) is drawn at 4 PM, so the axis stays on the session.
    close = int(datetime.combine(day, SESSION[1], MARKET_ZONE).timestamp())
    series, base = {}, {}
    for key, ticker in keys.items():
        quote = live[ticker]
        points = [[t, p] for t, p in minutes.get(ticker, [])]
        if not points or not quote.previous_close:
            continue
        if quote.as_of and quote.as_of > points[-1][0] and quote_date(quote, day) == day:
            at = min(quote.as_of, close)
            if at > points[-1][0]:
                points.append([at, quote.price])
            else:
                points[-1][1] = quote.price         # the 4 PM bar takes the closing / after-hours price
        series[key], base[key] = points, quote.previous_close
    return {"series": series, "base": base}


# ---------------------------------------------------------------- Portfolio measures

@dataclass
class PortfolioMeasure:
    key: str
    label: str
    total_cents: int                 # current balances of these accounts (net worth's figures)
    metric: Metric                   # market performance of their holdings, per range


def portfolio_measures(conn: sqlite3.Connection, live: dict | None = None,
                       today: date | None = None) -> list[PortfolioMeasure]:
    """[Total investments, Retirement, Investment accounts] (user's request 2026-10-07): each
    account's type as on the By-account-type chart (visuals.investment_type_label); retirement =
    visuals.RETIREMENT_TYPES (401(k)/IRA/Roth/HSA and similar; the user's choice). Totals are
    current balances, so retirement + investment accounts = total. % change = market performance
    of the holdings in those accounts (like "Your stocks"); accounts without holdings (manual,
    valued by hand) count in the totals only."""
    live = live or {}
    today = market_date(live, today)
    counted, _ = networth.counted_accounts(conn)
    groups: dict[str, list] = {"total": [], "retirement": [], "investment": []}
    for a in counted:
        if (a["type"], a["classification"]) != ("investment", "asset"):
            continue
        groups["total"].append(a)
        retirement = visuals.is_retirement_account(a)
        groups["retirement" if retirement else "investment"].append(a)
    labels = {"total": "Total investments", "retirement": "Retirement", "investment": "Investment accounts"}
    return [PortfolioMeasure(key, labels[key], sum(a["current_balance_cents"] for a in accounts),
                             _holdings_change(conn, live, today, Metric(key, labels[key]), [a["id"] for a in accounts]))
            for key, accounts in groups.items()]


def _benchmark(conn: sqlite3.Connection, symbol: str, label: str, live: dict, today: date) -> Metric:
    metric = Metric(symbol, label, symbol=symbol)
    quote = live.get(symbol)
    end = (quote_date(quote, today).isoformat(), quote.price) if quote else _latest_price(conn, "benchmark_prices", "symbol", symbol)
    for r in all_ranges(today):
        start_day = (today - timedelta(days=r.lookback)).isoformat()
        start = _start_price(conn, "benchmark_prices", "symbol", symbol, start_day)
        change = _change(start, end)
        metric.periods[r.key] = Period(change, start[0] if change is not None else None, start_day)
    metric.today = quote.change_pct if quote else None
    return metric


# ---------------------------------------------------------------- Investments table

@dataclass
class HoldingRow:
    name: str
    ticker: str | None
    quantity: float | None
    price: float | None                  # now: live quote, else the latest recorded price
    source: str                          # "live" or "recorded"
    value_cents: int | None = None       # your holding now: shares x live price, else its recorded value
    security_id: str = ""                # for lookups (Portfolio holdings table)
    price_date: str | None = None        # the date of `price` (a live quote's own date)
    # The table's "1 day" column (user's request 2026-10-27): a live quote's change vs the previous close (the
    # cards' "today"), else the recorded "1 day" range change.
    day_change: float | None = None
    # by Range.key: (% change, $ change of your holding in cents, start price's date)
    periods: dict[str, tuple[float | None, int | None, str | None]] = field(default_factory=dict)


def holding_rows(conn: sqlite3.Connection, live: dict | None = None, today: date | None = None) -> list[HoldingRow]:
    """Every non-cash holding (stocks, ETFs, funds, ...; Plaid + manual positions; user's request
    2026-10-07, at first ETFs and funds only), combined across accounts, with its change over
    each chart range: the same prices and rules as the "Your stocks" card.
    $ change = your shares x the price change. Sorted by the default range's % change, highest
    first (no change last), which is the table's default order before any JavaScript runs."""
    live = live or {}
    today = market_date(live, today)
    held = conn.execute(
        f"""
        SELECT h.security_id, MAX(h.name) AS name, MAX(h.ticker) AS ticker, SUM(h.quantity) AS quantity,
               MAX(h.price) AS holding_price, SUM(h.value_cents) AS value_cents
        FROM holdings h JOIN accounts a ON a.id = h.account_id
        WHERE a.is_hidden = 0 AND h.is_cash = 0 AND COALESCE(h.value_cents, 0) > 0
        GROUP BY h.security_id
        """).fetchall()
    rows = []
    for h in held:
        quote = live.get(h["ticker"]) if h["ticker"] else None
        if quote is not None:
            end, source = (quote_date(quote, today).isoformat(), quote.price), "live"
        else:
            end = _latest_price(conn, "security_prices", "security_id", h["security_id"])
            source = "recorded"
        row = HoldingRow(h["name"] or h["ticker"] or h["security_id"], h["ticker"], h["quantity"],
                         end[1] if end else h["holding_price"], source,
                         # Same rule as the Overview movers: live price x shares, else the recorded value.
                         to_cents(h["quantity"] * quote.price) if quote is not None and h["quantity"]
                         else h["value_cents"], h["security_id"])
        for r in all_ranges(today):
            start = _start_price(conn, "security_prices", "security_id", h["security_id"],
                                 (today - timedelta(days=r.lookback)).isoformat())
            change = _change(start, end)
            dollars = (to_cents(h["quantity"] * (end[1] - start[1]))
                       if change is not None and h["quantity"] else None)
            row.periods[r.key] = (change, dollars, start[0] if change is not None else None)
        row.price_date = end[0] if end else None
        row.day_change = quote.change_pct if quote is not None and quote.change_pct is not None else row.periods["1"][0]
        rows.append(row)
    default = next(r.key for r in ranges(today) if r.default)
    rows.sort(key=lambda row: (row.periods[default][0] is None, -(row.periods[default][0] or 0)))
    return rows


def metrics(conn: sqlite3.Connection, live: dict | None = None, today: date | None = None) -> list[Metric]:
    """[Your stocks, S&P 500, Russell 2000, MSCI World]. `live` = {ticker: quotes.Quote} for your
    tickers and the benchmark symbols (empty without Alpaca keys)."""
    live = live or {}
    today = market_date(live, today)
    return [_your_stocks(conn, live, today)] + [_benchmark(conn, s, label, live, today) for s, label in BENCHMARKS]


# ---------------------------------------------------------------- one account's holdings

@dataclass
class AccountHolding:
    name: str
    ticker: str | None
    quantity: float | None
    price: float | None          # live quote, else the latest recorded price, else Plaid's holding price
    price_source: str            # "live" / "recorded"
    value_cents: int | None
    cost_basis_cents: int | None  # None when the institution doesn't report one
    is_cash: bool


def account_holdings(conn: sqlite3.Connection, account_id: int, live: dict | None = None) -> list[AccountHolding]:
    """A connected investment account's holdings for its own page (user's request 2026-10-12), largest first. Price and
    value follow the Investments table's rule (holding_rows): live price x shares when there's a live quote, else the
    latest recorded price and Plaid's reported value, so the two pages agree. Cash is one line with a value only."""
    live = live or {}
    result = []
    for h in conn.execute("SELECT * FROM holdings WHERE account_id = ? AND COALESCE(value_cents, 0) <> 0"
                          " ORDER BY value_cents DESC", (account_id,)):
        if h["is_cash"]:
            result.append(AccountHolding(h["name"] or "Cash", None, None, None, "recorded", h["value_cents"],
                                         None, True))
            continue
        quote = live.get(h["ticker"]) if h["ticker"] else None
        if quote is not None:
            price, source = quote.price, "live"
            value = to_cents(h["quantity"] * quote.price) if h["quantity"] else h["value_cents"]
        else:
            recorded = _latest_price(conn, "security_prices", "security_id", h["security_id"])
            price, source, value = (recorded[1] if recorded else h["price"]), "recorded", h["value_cents"]
        result.append(AccountHolding(h["name"] or h["ticker"] or h["security_id"], h["ticker"], h["quantity"], price,
                                     source, value, h["cost_basis_cents"], False))
    return sorted(result, key=lambda r: -(r.value_cents or 0))


# ---------------------------------------------------------------- your watchlist (user's request 2026-11-04)

def watch_rows(conn: sqlite3.Connection, live: dict | None = None, today: date | None = None) -> list[HoldingRow]:
    """The Watchlist tab's rows, as the Investments table's (holding_rows) without shares: each watched ticker's price (its
    live quote, else its latest close in ticker_prices), its change today and over each range, and the $ change of one
    share (`periods[key][1]`, cents). `security_id` is its chart key, "tick:<ticker>". Sorted like holding_rows."""
    from fintrack import watchlist

    live = live or {}
    today = market_date(live, today)
    rows = []
    for w in watchlist.items(conn):
        quote = live.get(w.ticker)
        if quote is not None:
            end, source = (quote_date(quote, today).isoformat(), quote.price), "live"
        else:
            end, source = _latest_price(conn, "ticker_prices", "ticker", w.ticker), "recorded"
        row = HoldingRow(w.name or w.ticker, w.ticker, None, end[1] if end else None, source,
                         security_id=f"tick:{w.ticker}", price_date=end[0] if end else None)
        for r in all_ranges(today):
            start = _start_price(conn, "ticker_prices", "ticker", w.ticker, (today - timedelta(days=r.lookback)).isoformat())
            change = _change(start, end)
            row.periods[r.key] = (change, to_cents(end[1] - start[1]) if change is not None else None,
                                  start[0] if change is not None else None)
        row.day_change = quote.change_pct if quote is not None and quote.change_pct is not None else row.periods["1"][0]
        rows.append(row)
    default = next(r.key for r in ranges(today) if r.default)
    rows.sort(key=lambda row: (row.periods[default][0] is None, -(row.periods[default][0] or 0)))
    return rows


def watch_series(conn: sqlite3.Connection, rows: list[HoldingRow]) -> dict[str, dict]:
    """Daily prices per watched ticker for the chart's comparison line a row's click adds, as holding_series: {"tick:<T>":
    {"label": T, "prices": [[date, price], ...]}}, plus today's live price. Not part of "Your stocks"."""
    series = {}
    for row in rows:
        points = [[d, p] for d, p in conn.execute(
            "SELECT price_date, price FROM ticker_prices WHERE ticker = ? AND price > 0 ORDER BY price_date", (row.ticker,))]
        if row.source == "live" and row.price and row.price_date and (not points or points[-1][0] < row.price_date):
            points.append([row.price_date, row.price])
        if points:
            series[row.security_id] = {"label": row.ticker, "prices": points}
    return series
