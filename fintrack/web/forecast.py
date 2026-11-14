"""Cash flow: the Cash flow page (Transactions section 2026-10-14, Analysis since 2026-10-25) and its low-balance threshold.

The old Forecast page (folded into Recurring 2026-10-05) redirects here; Recurring's 90-day cash-flow section was
removed at the user's request 2026-10-16, and its threshold setting moved to this page's ribbon. (The month-end
projection that lived on Budgets was removed with the Budgets redesign, 2026-10-06.)
"""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from fintrack import cards, category_icons, forecast, visuals
from fintrack.web.deps import get_conn, get_settings, redirect, templates

router = APIRouter()

CASH_FLOW_PAGE = "/cash-flow"
CHART_DAYS = (30, 90, 180)      # the Cash flow page's range buttons (user's request 2026-10-23)


@router.get("/cash-flow", response_class=HTMLResponse)
def cash_flow_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    """Cash flow (Analysis section; user's request 2026-10-14). One cash account and one range at a time, chosen in
    the ribbon (?account=&days=; user's request 2026-10-24; default: the account paying the most card statements, 30
    days): three tiles (balance and its change over the range, projected change per month, lowest projected balance),
    the projected balance and the balance history over the range; then Coming up and Credit cards (all accounts)."""
    q = request.query_params
    days = int(q["days"]) if q.get("days", "").isdigit() and int(q["days"]) in CHART_DAYS else CHART_DAYS[0]
    flow = forecast.cash_flow(conn, horizon=days)
    cash_ids = {r[0] for r in conn.execute("SELECT id FROM accounts WHERE type = 'depository'")}
    choices = [a for a in flow.accounts if a.account_id in cash_ids]
    wanted = int(q["account"]) if q.get("account", "").isdigit() else forecast.main_payer(conn)
    selected = next((a for a in choices if a.account_id == wanted), choices[0] if choices else None)
    context = {}
    upcoming = ((forecast.upcoming_for(selected, flow.today, days)       # over the selected range
                 + [u for u in forecast.upcoming_30(conn) if u.get("needs_payer")]) if selected
                else forecast.upcoming_30(conn))
    if selected is not None:
        history = forecast.balance_history(conn, selected.account_id, days)
        past = forecast.past_for(conn, selected.account_id, days)
        balance_chart = visuals.balance_forecast(replace(flow, accounts=[selected]), days=days)
        # Balance history in the projected chart's shape (with the warning line); each day's transactions as its
        # events (user's request 2026-10-24)
        events: dict[str, list[str]] = {}
        for t in reversed(past):
            sign = "−" if t["amount_cents"] > 0 else "+"
            events.setdefault(t["date"], []).append(f"{t['name']} {sign}${abs(t['amount_cents']) / 100:,.2f}")
        history_chart = {"accounts": [{"id": selected.account_id, "label": selected.name,
                                       "labels": [d.isoformat() for d, _ in history],
                                       "balance": [visuals.dollars(c) for _, c in history],
                                       "events": [events.get(d.isoformat(), []) for d, _ in history]}],
                         "threshold": balance_chart["threshold"], "label": "Balance", "todayIndex": len(history) - 1,
                         "dots": False, "legend": False}
        # one y-axis for both charts: each gets the other's low and high (charts.js pads the same range)
        both = history_chart["accounts"][0]["balance"] + (balance_chart["accounts"][0]["balance"] if balance_chart["accounts"] else [])
        shared = [min(both), max(both)]
        history_chart["range"] = balance_chart["range"] = shared
        balance_chart["legend"] = False                   # no legends on this page's charts (2026-10-24)
        context = {
            "measures": forecast.cash_measures(selected, history, days),
            "balance_chart": balance_chart, "history_chart": history_chart, "past": past,
        }
    # a date in another year reads "Jan 4, 2027": both lists then get a wider Date column, keeping them aligned
    # (user's request 2026-10-24)
    listed = upcoming + context.get("past", [])
    context["long_dates"] = any(str(r["date"])[:4] != str(flow.today.year) for r in listed if r.get("date"))
    return templates.TemplateResponse(
        request, "cash_flow.html",
        {"settings": settings, "chart_days": CHART_DAYS, "days": days, "choices": choices, "selected": selected,
         # each category's icon (category_icons.resolve): a phone's lists show it beside the name (2026-11-06)
         "category_icon_of": category_icons.resolve(conn),
         **context,
         # Coming up: the selected account's own events (as on its chart), plus statements with no paying account
         "upcoming": upcoming,
         # the Credit cards card (user's request 2026-10-24)
         # most average monthly spend first; cards without an average last (user's request 2026-10-24)
         "card_estimates": sorted(cards.estimates(conn), key=lambda e: -(e.monthly_cents or -1)),
         "payer_names": {r["id"]: r["name"] for r in cards.payer_options(conn)},
         # the low-balance threshold (the Overview's warnings), set in the ribbon
         "threshold_cents": forecast.low_balance_threshold(conn),
         "msg": request.query_params.get("msg"), "error": request.query_params.get("error"),
         "review_count": conn.execute("SELECT COUNT(*) FROM recurring_items WHERE status = 'detected'").fetchone()[0]},
    )


@router.get("/forecast")
def forecast_page(request: Request):
    """The old Forecast page: cash flow is on the Cash flow page now."""
    return RedirectResponse(CASH_FLOW_PAGE, status_code=307)


@router.post("/forecast/threshold")
def save_threshold(amount: str = Form(""), account: str = Form(""), days: str = Form(""), conn=Depends(get_conn)):
    view = {k: v for k, v in (("account", account), ("days", days)) if v.isdigit()}     # back to the same view
    try:
        forecast.set_low_balance_threshold(conn, amount)
    except forecast.ForecastError as exc:
        return redirect(CASH_FLOW_PAGE, error=str(exc), **view)
    return redirect(CASH_FLOW_PAGE, msg="Low-balance threshold saved.", **view)
