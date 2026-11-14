"""Investments page: performance cards and value over time (the investment mix and the
holdings table are on the Portfolio page, web/portfolio.py).

Holdings are informational: the account balances on the Net worth page already
include them, so nothing here is added to net worth.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from fintrack import live_values, manual_positions, performance, price_history, prices, watchlist
from fintrack.web.deps import get_conn, get_quotes, get_settings, redirect, templates

router = APIRouter()


@router.get("/investments", response_class=HTMLResponse)
def investments_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings),
                     quotes=Depends(get_quotes)):
    price_history.refresh_if_due(conn, quotes)     # daily history, at most every 6 h per server run
    # Live prices for your tickers + the benchmark ETFs in one (5-minute cached) Alpaca refresh;
    # today's benchmark prices are recorded (past days come from price_history at each sync).
    live = quotes.quotes(prices.held_tickers(conn) + manual_positions.position_tickers(conn)
                         + performance.benchmark_symbols() + watchlist.tickers(conn)) if quotes else {}
    performance.record_benchmarks(conn, live)
    # Manual accounts with positions revalue from the same quotes (user's choice: on page load).
    live_values.revalue_all(conn, live)
    rows = performance.holding_rows(conn, live)
    as_of = performance.market_date(live)     # ranges end on the last trading day (see market_date)
    holdings_series = performance.holding_series(conn, rows)
    # your watchlist (user's request 2026-11-04): the card's second tab; its rows add their own line to the chart too
    watched = performance.watch_rows(conn, live)
    return templates.TemplateResponse(
        request,
        "investments.html",
        {
            "settings": settings,
            # Your stocks vs S&P 500 / Russell 2000 / MSCI World, per chart range (performance.py).
            "metrics": performance.metrics(conn, live),
            "perf_ranges": performance.ranges(as_of),
            # % change chart (user's request 2026-10-07; charts.js "performance"): daily prices of every
            # holding (the "Your stocks" line, value-weighted like its card) and of the benchmarks;
            # benchmark cards and table rows add their own line (up to 4 at once).
            # "1 day" draws the session's minute prices instead (user's request 2026-10-27).
            "perf_chart": (chart := {"series": {**performance.benchmark_series(conn), **holdings_series,
                                                **performance.watch_series(conn, watched)},
                                     "mine": list(holdings_series), "end": as_of.isoformat(),
                                     "intraday": performance.intraday_series(quotes, live, rows, as_of, watched)}),
            "compare_series": chart["series"],
            # When prices were last fetched (live quotes), else the last sync.
            "prices_as_of": max((q.fetched_at for q in live.values() if q.fetched_at), default=None),
            "quotes_on": quotes is not None,
            # Investments table (every non-cash holding): values for each range; sorted by % change.
            "holding_rows": rows,
            "watch_rows": watched,
            "tab": "watchlist" if request.query_params.get("tab") == "watchlist" else "investments",
            "msg": request.query_params.get("msg"), "error": request.query_params.get("error"),
        },
    )


# ---------------------------------------------------------------- your watchlist (user's request 2026-11-04)

@router.post("/investments/watchlist")
def add_watched(ticker: str = Form(""), name: str = Form(""), conn=Depends(get_conn), quotes=Depends(get_quotes)):
    """Add a ticker to the watchlist (Alpaca must have a price for it); back to the Watchlist tab."""
    try:
        added = watchlist.add(conn, quotes, ticker, name)
    except watchlist.WatchlistError as exc:
        return redirect("/investments", tab="watchlist", error=str(exc))
    return redirect("/investments", tab="watchlist", msg=f"Watching {added}.")


@router.get("/investments/watchlist/search", response_class=HTMLResponse)
def search_watched(request: Request, ticker: str = "", conn=Depends(get_conn), quotes=Depends(get_quotes)):
    """The ticker box's matches as you type (user's decision 2026-11-04): over Alpaca's stock list stored here (fetched
    first if it's missing or a week old), each a one-click Watch."""
    if ticker.strip():
        watchlist.refresh_assets(conn, quotes)
    return templates.TemplateResponse(request, "_watch_results.html", {"found": watchlist.search(conn, ticker)})


@router.post("/investments/watchlist/{ticker}/delete")
def remove_watched(ticker: str, conn=Depends(get_conn)):
    watchlist.remove(conn, ticker)
    return redirect("/investments", tab="watchlist")
