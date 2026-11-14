"""Savings page: post-tax and pre-tax income, spending, where the money went (retirement, investment, cash) and the
rate per month: what was saved from income (money moved from investments into a bank account and purpose accounts are
left out; savings.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from fintrack import savings, spending, visuals
from fintrack.web.deps import get_conn, get_settings, templates

router = APIRouter()


@router.get("/savings", response_class=HTMLResponse)
def savings_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    # Date range in the ribbon: Spending's controls (user's request 2026-10-11); default 365 days
    period = spending.resolve_period(conn, request.query_params, default="365")
    series = savings.trim_leading_empty(savings.range_series(conn, period.start, period.end))
    # The cards' bars: by day for This / Last month (user's request 2026-10-12), else the table's months
    by_day = period.mode == "days"
    bars = savings.range_series(conn, period.start, period.end, by_day=True) if by_day else series
    # The first two tiles are about now (this month, last month) whatever the range
    now, previous = savings.monthly_series(conn, months=2)[::-1]
    retirement_chart = visuals.savings_mix(conn, bars, period.start, period.end, "retirement", by_day)
    investment_chart = visuals.savings_mix(conn, bars, period.start, period.end, "investment", by_day)
    return templates.TemplateResponse(
        request,
        "savings.html",
        {
            "settings": settings,
            "page_path": "/savings",
            "series": series,
            "period": period,
            "ranges": spending.RANGES,
            "retirement_chart": retirement_chart,
            "investment_chart": investment_chart,
            "net_cash_chart": visuals.net_cash_mix(bars, by_day),
            "net_savings_chart": visuals.net_savings_mix(bars, by_day),
            # Hide a card (and its table columns) whose kind has no amounts in the period (user's request 2026-10-12);
            # Pre-tax hides when it's all zero.
            "show": {"retirement": bool(retirement_chart["series"]), "investment": bool(investment_chart["series"]),
                     "pretax": any(m.pretax_cents for m in series)},
            "current": now,
            "last_full": previous if (previous.total_income_cents or previous.spending_cents) else None,
            "range_rate": savings.combined_rate(series),
            "range_saved": sum(m.saved_cents for m in series),
        },
    )
