"""Dashboard (home page): the headline numbers and anything that needs attention."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from fintrack import account_types, balances, budgets, forecast, items, live_values, manual, manual_positions, networth, price_history, prices, savings, visuals, watchlist
from fintrack import tags
from fintrack.web import tags as tags_web
from fintrack.web.deps import get_conn, get_quotes, get_settings, redirect, templates

router = APIRouter()


# Dashboard account groups, in display order. Property and "other" share a group.
ACCOUNT_GROUPS = [("depository", "Cash"), ("credit", "Credit cards"), ("investment", "Investments"),
                  ("loan", "Loans"), ("other", "Property & other")]


def account_groups(institutions: list[dict], manual_accounts: list,
                   types: dict[int, tuple[int, str]] | None = None, changes: dict[int, int] | None = None) -> list[dict]:
    """Visible accounts as [Assets, Liabilities], each with its total and one group per account type (with its accounts
    and subtotal). Liability totals are amounts owed. `types` = account_types.top_types(): accounts are grouped under
    their type's name, in your type table's order (user's request 2026-10-13: replaced account categories); an account
    with none falls back to the groups by Plaid's type (ACCOUNT_GROUPS). `changes` = networth.day_changes(): each row,
    group and side gets `change_cents` (None when none of its accounts has one)."""
    types = types or {}
    changes = changes or {}

    def total_change(values) -> int | None:
        known = [v for v in values if v is not None]
        return sum(known) if known else None
    rows = []
    for entry in institutions:
        for acct in entry["accounts"]:
            rows.append({"acct": acct, "institution": entry["item"]["institution_name"],
                         "needs_reconnect": entry["item"]["status"] != "ok", "change_cents": changes.get(acct["id"])})
    for acct in manual_accounts:
        rows.append({"acct": acct, "institution": acct["manual_institution"] or "Manual", "needs_reconnect": False,
                     "change_cents": changes.get(acct["id"])})
    known = {account_type for account_type, _ in ACCOUNT_GROUPS}

    def group_of(row: dict) -> str:
        if row["acct"]["id"] in types:
            return f"type:{types[row['acct']['id']][1]}"
        return row["acct"]["type"] if row["acct"]["type"] in known else "other"   # property -> other

    # Assets first, then liabilities (each account's own classification, as net worth uses);
    # inside each, one row per type with its accounts.
    sides = []
    by_type = sorted({v for v in types.values()})                # (position, name), in the table's order
    for classification, side_label, other_label in [("asset", "Assets", "Property & other"),
                                                    ("liability", "Liabilities", "Other debts")]:
        groups = []
        for account_type, label in [(f"type:{name}", name) for _pos, name in by_type] + ACCOUNT_GROUPS:
            members = [r for r in rows if group_of(r) == account_type
                       and r["acct"]["classification"] == classification]
            if members:
                members.sort(key=lambda r: -(r["acct"]["current_balance_cents"] or 0))
                groups.append({"label": other_label if account_type == "other" else label, "rows": members,
                               "total_cents": sum(r["acct"]["current_balance_cents"] or 0 for r in members),
                               "change_cents": total_change(r["change_cents"] for r in members)})
        if groups:
            sides.append({"label": side_label, "classification": classification, "groups": groups,
                          "total_cents": sum(g["total_cents"] for g in groups),
                          "change_cents": total_change(g["change_cents"] for g in groups)})
    return sides



MOVERS_BENCHMARK = "SPY"     # always the 6th row of Investment movers, after the 5 biggest moves


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, conn=Depends(get_conn), settings=Depends(get_settings), quotes=Depends(get_quotes)):
    # Live prices for held tickers + manual position tickers when Alpaca keys are set (cached
    # 5 min in quotes.py). Manual accounts with positions revalue first (user's choice: on page
    # load), so every figure below includes them.
    price_history.refresh_if_due(conn, quotes)     # daily history, at most every 6 h per server run
    # + SPY: the movers card's always-shown benchmark row (user's request 2026-10-23)
    # + your watchlist's, when the Investments card shows it (user's request 2026-11-04)
    movers_view = prices.movers_view(conn)
    watched = watchlist.tickers(conn) if movers_view == "watchlist" else []
    live = quotes.quotes(prices.held_tickers(conn) + manual_positions.position_tickers(conn) + [MOVERS_BENCHMARK] + watched) \
        if quotes else None
    live_values.revalue_all(conn, live)
    nw = networth.current_net_worth(conn)
    active = items.active_items(conn)
    series = savings.monthly_series(conn, months=2)  # [last month, this month]
    institutions = [{"item": i, "accounts": items.accounts_for_item(conn, i["id"])} for i in active]
    manual_accounts = manual.list_accounts(conn)
    budget = budgets.month_view(conn, f"{date.today():%Y-%m}")
    groups = account_groups(institutions, manual_accounts, account_types.top_types(conn), networth.day_changes(conn))
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "settings": settings,
            "nw": nw,
            "change": networth.change_since_last_month(conn, nw.net_cents),
            "mtd": savings.month_to_date(conn),
            "this_month": series[-1],
            "last_month": series[0],
            "broken": [i for i in active if i["status"] != "ok"],
            "institutions": institutions,
            "manual_accounts": manual_accounts,
            "account_groups": groups,
            # a proportion bar per side and a swatch per type, as on the Accounts page's Summary card (2026-10-23)
            "account_bars": balances.proportion_bars(groups, balances.kind_of_accounts(conn)),
            "upcoming": forecast.upcoming_30(conn),
            "budget": budget,
            "cal": tags_web.month_card(conn, request.query_params.get("cal")),   # the calendar card; ?cal=YYYY-MM after a label
            # Charts (M13)
            "nw_trend": visuals.networth_trend(conn, days=365),
            "spend_compare": visuals.cumulative_spending(conn),
            # Budgeted categories + one "Other" bar for unbudgeted spending (user's request).
            "budget_bars": visuals.budget_bars(budget, with_other=True),
            "flow": (flow := forecast.cash_flow(conn)),
            # 30-day balance chart; the account that pays most card statements is shown first.
            "balance_chart": visuals.balance_forecast(flow, days=30, prefer=forecast.main_payer(conn)),
            # Latest posted transactions in visible accounts (pending ones left out; user's choice).
            "recent": conn.execute(
                "SELECT * FROM transactions_effective WHERE account_hidden = 0 AND pending = 0"
                " ORDER BY date DESC, id DESC LIMIT 10"
            ).fetchall(),
            # the Investments card: your holdings' movers, or your watchlist's (its title dropdown; 2026-11-04)
            "movers": (prices.watch_movers(conn, live=live, benchmark=MOVERS_BENCHMARK) if movers_view == "watchlist"
                       else prices.movers(conn, limit=None, live=live, benchmark=MOVERS_BENCHMARK)),
            "movers_view": movers_view,
            "quotes_on": quotes is not None,
            "review_count": conn.execute("SELECT COUNT(*) FROM recurring_items WHERE status = 'detected'").fetchone()[0],
        },
    )


@router.get("/overview/calendar", response_class=HTMLResponse)
def overview_calendar(request: Request, month: str = "", conn=Depends(get_conn)):
    """The calendar card alone, for its arrows (htmx swaps it in, so Overview doesn't reload)."""
    return templates.TemplateResponse(request, "_overview_calendar.html", {"cal": tags_web.month_card(conn, month)})


@router.post("/overview/countdown", response_class=HTMLResponse)
def choose_countdown(request: Request, slot: int = Form(0), choice: str = Form(""), month: str = Form(""), conn=Depends(get_conn)):
    """What a countdown follows, from the list under its title (user's request 2026-11-02); blank = the next thing coming up.
    Returns the card for htmx to swap in."""
    try:
        tags_web.choose_countdown(conn, slot, choice)
    except tags.TagError:
        pass                                  # not one of the two: the card shows what's true
    return templates.TemplateResponse(request, "_overview_calendar.html", {"cal": tags_web.month_card(conn, month)})


@router.post("/overview/movers")
def choose_movers(view: str = Form(""), conn=Depends(get_conn)):
    """The Investments card's title dropdown (user's request 2026-11-04): your holdings' movers or your watchlist's."""
    try:
        prices.set_movers_view(conn, view)
    except ValueError:
        pass
    return redirect("/")
