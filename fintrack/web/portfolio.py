"""Portfolio page (Investments section, after Investments; user's request 2026-10-07):
total investments, their value over time stacked by account type (replaced the investment
mix chart, user's request 2026-10-07), and the holdings in each account
(Plaid + manual positions; informational, already in the account balances)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from fintrack import live_values, manual_positions, performance, price_history, prices, visuals
from fintrack.web.deps import get_conn, get_quotes, get_settings, templates

router = APIRouter()


@router.get("/portfolio", response_class=HTMLResponse)
def portfolio_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings),
                   quotes=Depends(get_quotes)):
    price_history.refresh_if_due(conn, quotes)     # daily history, at most every 6 h per server run
    # The holdings table includes manual positions: revalue them from live quotes first, like
    # Overview and Investments do (user's choice: on page load; 5-minute cache; no-op if unchanged).
    live = quotes.quotes(prices.held_tickers(conn) + manual_positions.position_tickers(conn)) if quotes else {}
    live_values.revalue_all(conn, live)
    holdings = conn.execute(
        """
        SELECT h.*, a.name AS account_name, a.mask AS account_mask
        FROM holdings h JOIN accounts a ON a.id = h.account_id
        WHERE a.is_hidden = 0
        ORDER BY a.name, h.value_cents DESC
        """
    ).fetchall()
    # One group per account (largest first), each with its holdings and a subtotal.
    groups: dict[int, dict] = {}
    for h in holdings:
        group = groups.setdefault(h["account_id"], {"id": h["account_id"], "name": h["account_name"], "mask": h["account_mask"],
                                                    "total_cents": 0, "rows": []})
        group["rows"].append(h)
        group["total_cents"] += h["value_cents"] or 0
    holding_groups = sorted(groups.values(), key=lambda g: -g["total_cents"])
    return templates.TemplateResponse(
        request,
        "portfolio.html",
        {
            "settings": settings,
            # Total / Retirement / Investment accounts: balances + market performance per range.
            "measures": performance.portfolio_measures(conn, live),
            # Value over time stacked by account type, with 1 day / YTD / 1, 3, 5, 10-year buttons (2026-11-13).
            "by_type": visuals.investments_by_type(conn, days=3651),     # up to the 10-year range
            # Transfers into and out of investment / retirement accounts, last 12 months (contributions.py).
            "transfers": visuals.investment_transfers(conn),
            # 1 day, YTD, 1 / 3 / 5 / 10 years (user's request 2026-11-13; YTD selected first), the same as the holdings
            # table's columns.
            "type_ranges": performance.portfolio_ranges(performance.market_date(live)),
            "holding_groups": holding_groups,
            # Holdings filters (ui.js): kind of each holding, as on the old investment mix chart.
            "security_groups": visuals.SECURITY_GROUPS,
            "holding_kinds": sorted({"Cash" if h["is_cash"] else visuals.SECURITY_GROUPS.get(
                (h["security_type"] or "").lower(), "Other") for h in holdings},
                key=["Stocks", "ETFs", "Funds", "Bonds", "Cash", "Other"].index),
            # Holdings table: % change per security (same prices / rules as the Investments table),
            # in the columns the user asked for (2026-11-13): 1 day, YTD, 1 / 3 / 5 / 10 years.
            "changes": {r.security_id: r.periods for r in performance.holding_rows(conn, live)},
            "table_ranges": performance.portfolio_ranges(performance.market_date(live)),
        },
    )
