"""Credit cards page (Analysis section, user's request 2026-10-25): each card's spending by category over a date range.

The range is the Savings page's ribbon control (`_range_controls.html`, spending.resolve_period), defaulting to YTD.
A card is a visible account of Plaid type 'credit' (as on Cash flow); spending counts as everywhere else
(spending.card_spending). Each card's bar chart is coloured like Spending's treemap, by size within the
card (visuals.card_category_charts). Reward rates you enter turn each card's spending into an estimate of its rewards
(rewards.py: by category, merchant text or both); its chart then has a bar per rate (each rate one colour on every card,
visuals.card_rate_charts) and its table groups the categories by rate; the forms post back here and keep the date range.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from fintrack import categories, rewards, spending, visuals
from fintrack.web.deps import back_with_range, category_name, get_conn, get_settings, templates

router = APIRouter()


@router.get("/credit-cards", response_class=HTMLResponse)
def credit_cards_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    period = spending.resolve_period(conn, request.query_params, default="ytd")
    cards = spending.card_spending(conn, period.start, period.end)
    # largest spending in the range first (user's request 2026-10-26); a stable sort keeps ties by name
    cards.sort(key=lambda c: -c.spending_cents)
    charts = visuals.card_category_charts(cards)
    estimates = rewards.estimates(conn, cards, period.start, period.end)
    setups = rewards.all_rewards(conn)
    rows = {c.account_id: visuals.grouped_category_lines(c.lines, charts[c.account_id]) for c in cards}
    # a card with reward rates: its chart's tiles are its rates, not its categories
    charts.update(visuals.card_rate_charts(estimates, rewards.rate_label))
    cats = categories.list_categories(conn)
    # each card's rewards balance (user's request 2026-10-26): now = the latest you entered + rewards since
    # (rewards.current_balance), else the latest as entered; none until you enter one on the card's page
    reward_balances = {}
    for c in cards:
        current = rewards.current_balance(conn, c.account_id)
        entered = None if current else next(iter(rewards.balances(conn, c.account_id)), None)
        if current or entered:
            reward_balances[c.account_id] = {"current": current, "entered": entered}
    return templates.TemplateResponse(
        request,
        "credit_cards.html",
        {
            "settings": settings,
            "page_path": "/credit-cards",
            "period": period,
            "ranges": spending.RANGES,
            "cards": cards,
            "charts": charts,
            # a card without reward rates: its gray categories combined into one "Other" row, like Spending's By
            # category table. A card with rates lists every category under its rate (estimates[...].groups) instead,
            # since an "Other" row could mix rates.
            "rows": rows,
            "estimates": estimates,
            "rewards": setups,
            "units": rewards.UNITS,
            "rate_label": rewards.rate_label,
            "reward_balances": reward_balances,
            # rewards redeemed in the range, and cash rewards (user's request 2026-11-03; rewards.redeemed_summaries)
            "redeemed": rewards.redeemed_summaries(conn, [c.account_id for c in cards], period.start, period.end),
            # the Add rate drop-down: spending categories and their subcategories
            "categories": cats,
            "category_labels": {c["key"]: category_name(c) for c in cats},
            "parent_kinds": {c["key"]: c["kind"] for c in cats},
            "msg": request.query_params.get("msg"),
            "error": request.query_params.get("error"),
        },
    )


# ---------------------------------------------------------------- reward rates (POST, back to the page)

def _back(range_key: str, start: str, end: str, change, done: str) -> RedirectResponse:
    return back_with_range("/credit-cards", range_key, start, end, change, done)


@router.post("/credit-cards/{account_id}/rewards")
def set_up_rewards(account_id: int, unit: str = Form("points"), base_rate: str = Form(""), range: str = Form(""),
                   start: str = Form("", alias="from"), end: str = Form("", alias="to"), conn=Depends(get_conn)):
    """Start estimating a card's rewards, or change what it earns and its base rate."""
    return _back(range, start, end, lambda: rewards.set_up(conn, account_id, unit, rewards.parse_rate(base_rate)),
                 "Rewards saved.")


@router.post("/credit-cards/{account_id}/rewards/delete")
def remove_rewards(account_id: int, range: str = Form(""), start: str = Form("", alias="from"),
                   end: str = Form("", alias="to"), conn=Depends(get_conn)):
    return _back(range, start, end, lambda: rewards.remove(conn, account_id), "Rewards estimate removed.")


@router.post("/credit-cards/{account_id}/rates")
def set_rate(account_id: int, category: str = Form(""), merchant: str = Form(""), rate: str = Form(""),
             range: str = Form(""), start: str = Form("", alias="from"), end: str = Form("", alias="to"),
             conn=Depends(get_conn)):
    """A card's rate for a category, merchant text ("United": descriptions containing it), or both (adds it, or replaces
    the one it had for the same pair)."""
    return _back(range, start, end,
                 lambda: rewards.set_rate(conn, account_id, category, merchant, rewards.parse_rate(rate)), "Rate saved.")


@router.post("/credit-cards/{account_id}/rates/delete")
def delete_rate(account_id: int, rate_id: int = Form(0), range: str = Form(""), start: str = Form("", alias="from"),
                end: str = Form("", alias="to"), conn=Depends(get_conn)):
    return _back(range, start, end, lambda: rewards.delete_rate(conn, account_id, rate_id), "Rate removed.")
