"""The Transactions page: Sync now, the searchable, filterable list (GET /transactions), merchant logo files, the
chart choices, and adding a transaction. Filters and the list's query: transaction_filters.py; a row's edits:
transaction_edits.py; edits to many rows: transaction_groups.py."""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from fintrack import (authorized_users, categories, investment_activity, live_values, logos, manual, manual_positions,
                      performance, price_history, prices, purposes, recurring, manual_transactions, rewards, rules,
                      spending, sync, tags, transfers, visuals, watchlist)
from fintrack.web.deps import get_cipher, get_conn, get_gateway, get_quotes, get_settings, redirect, templates
from fintrack.web.transaction_filters import (KIND_FILTERS, LOAN_KIND, RANGES, SORTS, _account_groups,
                                              _category_groups, _category_lit, _filters, _flow_average, _flow_label,
                                              _per, _query, _range_days, _short_range, _summary, _url_filters, _widest,
                                              _balance)
from fintrack.web.transaction_edits import _activity_of, _logo_context

router = APIRouter()


PAGE_SIZE = 100


@router.post("/sync")
def sync_now(conn=Depends(get_conn), settings=Depends(get_settings),
             gateway=Depends(get_gateway), cipher=Depends(get_cipher), quotes=Depends(get_quotes)):
    try:
        summary = sync.sync_all(conn, gateway, cipher, settings.home, settings.sync_investment_transactions)
    except sync.SyncInProgress as exc:
        return redirect("/setup", error=str(exc))
    # Alpaca: daily price history (one call), then today's benchmark prices (performance
    # cards) and manual position accounts' values.
    if quotes:
        price_history.backfill(conn, quotes)
        watchlist.refresh_assets(conn, quotes)        # the watchlist's company search, about weekly (2026-11-04)
    live = quotes.quotes(performance.benchmark_symbols() + prices.held_tickers(conn)
                         + manual_positions.position_tickers(conn)) if quotes else {}
    performance.record_benchmarks(conn, live)
    live_values.revalue_all(conn, live)
    # Merchant logos the new transactions point to (downloaded once; never fails the sync), and each institution's
    # logo for the Balances page's account rows (once per institution; Plaid's free /institutions/get_by_id).
    logos.fetch_missing(conn, settings.home)
    logos.fetch_institution_logos(conn, settings.home, gateway)
    logos.find_manual_institution_logos(conn, settings.home, gateway)      # manual accounts' institutions, by name
    if summary.errors:
        return redirect("/setup", error=summary.describe())
    return redirect("/setup", msg=summary.describe())


@router.get("/transactions", response_class=HTMLResponse)
def transactions_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    filters = _filters(request)
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except ValueError:
        page = 1
    # the list: your transactions and investment activity (negative ids; user's request 2026-10-30, editable 2026-10-31)
    source = investment_activity.as_transactions(conn)
    rows_sql, rows_params = source
    where, params = _query(filters)
    total = conn.execute(f"SELECT COUNT(*) FROM ({rows_sql}) WHERE {where}", rows_params + params).fetchone()[0]
    order = SORTS[filters["sort"] or "date"].format(d="ASC" if filters["dir"] == "asc" else "DESC")
    rows = conn.execute(
        f"SELECT * FROM ({rows_sql}) WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
        rows_params + params + [PAGE_SIZE, (page - 1) * PAGE_SIZE],
    ).fetchall()
    # The charts filter the list (user's request 2026-10-26): a bar picks its month / day (`period`), a slice its
    # category; clicking the picked one again clears it. Each chart leaves out its own pick, so it keeps every
    # bar / slice to pick from (the picked one highlighted), and applies the rest, the other chart's pick included.
    kept = _url_filters(filters)

    def toggle(key: str, value: str) -> str:
        """These filters with `key` set to `value`, or without it when it's already that (a second click)."""
        # a new category replaces the checklist's
        changed = {k: v for k, v in kept.items() if k != key}
        if filters[key] != value:
            changed[key] = value
        return "/transactions?" + urlencode(changed)

    # Over time, by day for a month-sized range or shorter, else by month (user's requests 2026-10-26/30): All = money in
    # and out; Spending / Income = one total per bar and a dashed line at the average; Transfers = where the money went
    # (a flow chart, transfer_flows) instead. Bars pick their month / day.
    by_day = _short_range(filters)
    view = {"expense": "spending", "income": "income", "transfer": "transfers", LOAN_KIND: "transfers"}.get(
        filters["kind"], "all")
    flow_average, totals, flows = None, None, None
    if view == "all":
        period = visuals.period_totals(conn, *_query({**filters, "period": ""}), by_day=by_day, source=source)
        flow_average = _flow_average(conn, filters, period, by_day=by_day)
    elif view in ("spending", "income"):
        period = visuals.period_sums(conn, *_query({**filters, "period": ""}), by_day=by_day,
                                     sign=-1 if view == "income" else 1, source=source)
        days = _range_days(conn, filters)
        per, unit = _per(by_day)
        average_cents = round(period["total_cents"] * per / days) if days else None
        period.update({"average": None if average_cents is None else average_cents / 100, "per": unit})
        totals = {"average_cents": average_cents, "per": unit}
    else:
        period = {"labels": []}
        # every flow stays to pick from, the picked one highlighted (its own pick left out, like the bars and slices)
        flows = visuals.transfer_flows(conn, *_query({**filters, "flow": ""}), source=source)
        for f in flows["flows"]:
            f["url"] = toggle("flow", f'{f["from"][2:]}>{f["to"][2:]}')
        flows["picked"] = filters["flow"]
    if period["labels"]:
        period["urls"] = [toggle("period", label) for label in period["labels"]]
        period["picked"] = period["labels"].index(filters["period"]) if filters["period"] in period["labels"] else None
    # By category: the spending, or income when that's the kind filtered; none for transfers / excluded. A slice
    # picks its category (a category with its subcategories); Other picks every category folded into it.
    rings = None
    breakdown = spending.breakdown(conn)
    if filters["kind"] in ("expense", "income"):
        kind = filters["kind"]
        # the pie by category, or (the card's title dropdown, user's requests 2026-11-01) the top descriptions: its own pick left out
        unpicked = {**filters, breakdown: ""}
        if breakdown == "description":
            rings = visuals.top_descriptions(conn, *_query(unpicked), kind, source=source)
            for row in rings["rows"]:
                row["url"], row["lit"] = toggle("description", row["key"]), filters["description"] == row["key"]
        else:
            # colours from the whole range, so picking a bar doesn't repaint the categories
            slots = None
            if filters["period"]:
                whole = visuals.category_rings(conn, *_query({**unpicked, "period": ""}), kind, source=source)
                slots = {g["key"]: g["slot"] for g in whole["parents"] if g["key"] != "_other"}
            rings = visuals.category_rings(conn, *_query(unpicked), kind, slots=slots, source=source)
            rings["picked"] = filters["category"]
            picked = set(filters["category"].split(",")) - {""}
            for g in rings["parents"]:
                if g["key"] == "_other":
                    g["url"] = toggle("category", ",".join(sorted(m["key"] for m in g["members"])))
                    g["lit"] = any(_category_lit(picked, m["key"], m["subs"]) for m in g["members"])
                else:
                    g["url"] = toggle("category", g["key"])
                    g["lit"] = _category_lit(picked, g["key"], [x["key"] for x in g["subs"]])
                for x in g["subs"]:
                    x["url"] = toggle("category", f'{g["key"]}:{x["key"]}')
                    x["lit"] = _category_lit(picked, x["key"], [], parent=g["key"])
            # dim the rest only when something picked is on the chart
            rings["dim"] = any(g["lit"] for g in rings["parents"])
        # the card's corner: what the list shows now, its picks included (user's request 2026-11-01), while the chart keeps
        # everything to pick from; refunds lower the total
        rings["count"] = total
        rings["shown_cents"] = (-1 if kind == "income" else 1) * conn.execute(
            f"SELECT COALESCE(SUM(amount_cents), 0) FROM ({rows_sql}) WHERE {where}", rows_params + params).fetchone()[0]
    # All: a summary in the pie's place instead (user's request 2026-10-31): each group's count and total
    summary = _summary(conn, source, *_query({**filters, "money": ""}), kept) if view == "all" else None
    bounds = conn.execute("SELECT MIN(date), MAX(date) FROM transactions_effective WHERE account_hidden = 0").fetchone()

    def page_url(n: int) -> str:
        return "/transactions?" + urlencode({**_url_filters(filters), "page": n})

    def sort_url(column: str) -> str:
        """Heading link: sort by this column, newest/biggest first; again = reverse."""
        current = filters["sort"] or "date"
        flip = "asc" if current == column and filters["dir"] != "asc" else ""
        kept = {k: v for k, v in _url_filters(filters).items() if k not in ("sort", "dir")}
        return "/transactions?" + urlencode({**kept, "sort": column, **({"dir": flip} if flip else {})})

    # the filter's accounts: every one with transactions or investment activity, connected or manual alike
    # (docs/parity-plan.md); "Add a transaction" lists those that hold transactions (an investment's is Add activity)
    accounts = conn.execute(
        f"""SELECT id, name, mask FROM accounts WHERE is_hidden = 0
            AND ({manual.takes_transactions_sql()} OR type = 'investment') ORDER BY name""").fetchall()
    add_accounts = conn.execute(
        f"SELECT id, name, mask FROM accounts WHERE is_hidden = 0 AND {manual.takes_transactions_sql()} ORDER BY name"
    ).fetchall()
    account_cards = authorized_users.cards_by_account(conn)
    picked_accounts = set(filters["account"].split(",")) - {""}
    account_groups, accounts_label = _account_groups(conn, accounts, account_cards, picked_accounts)
    category_groups, categories_label = _category_groups(
        categories.list_categories(conn), set(filters["category"].split(",")) - {""})

    widest = _widest(conn, source, where, params)
    review_all = categories.review_rows(conn)    # the "Categories to review" card, whatever the list's filters
    from fintrack.web.transaction_groups import review_view
    review = review_view(review_all, request.query_params)   # ...filtered and sorted by its own controls (2026-11-14)
    review_rows = review["rows"]
    keep = [(k, v) for k, v in request.query_params.multi_items() if k != "review"]

    def review_sort_url(column: str) -> str:
        """A heading on the card: sort by it (its own default direction); again = the other way."""
        flip = review["sort"] == column
        direction = ("asc" if review["desc"] else "desc") if flip else ""
        rest = [(k, v) for k, v in keep if k not in ("r_sort", "r_dir")]
        return "/transactions?" + urlencode(rest + [("r_sort", column)] + ([("r_dir", direction)] if direction else [])
                                            + [("review", "1")])
    response = templates.TemplateResponse(
        request,
        "transactions.html",
        {
            "settings": settings,
            "rows": rows,
            "period": period,
            "period_by_day": _short_range(filters),
            "rings": rings,
            "summary": summary,               # All: count and total by group
            "balance": _balance(summary) if summary else None,   # ...shown as what came in and went out (2026-11-11)
            "flow_average": flow_average,
            "chart_view": view,               # all / spending / income / transfers: which over-time chart
            "totals": totals,                 # Spending / Income: the average (cents) and its unit
            "flows": flows,                   # Transfers: the flow chart's data
            "transfers_chart": transfers.chart_style(conn),     # circle (chord) or flow (Sankey), the card's gear
            # the picked bar's tag in the filters clears it
            "period_clear_url": "/transactions?" + urlencode({k: v for k, v in _url_filters(filters).items() if k != "period"}),
            # the picked flow's tag: its two ends by name, and the address without it
            "flow_label": _flow_label(conn, filters["flow"]),
            # the "Categories to review" card: collapsed until you open it; a pick comes back with it open
            "review_rows": review_rows,
            "review_all": review_all,
            "review": review,
            "review_sort_url": review_sort_url,
            # the list's own filters, kept when the card's filters are submitted
            "review_keep": [(k, v) for k, v in keep if not k.startswith("r_")],
            "widest": widest,                 # the list's hidden sizing row
            "review_open": request.query_params.get("review") == "1",
            "review_return": "/transactions?" + urlencode(
                [(k, v) for k, v in request.query_params.multi_items() if k != "review"] + [("review", "1")]),
            "review_done": "/transactions" + (lambda rest: "?" + rest if rest else "")(urlencode(
                [(k, v) for k, v in request.query_params.multi_items() if k != "review"])),
            "breakdown": breakdown,           # Spending / Income: the pie by category, or the top descriptions
            # the picked description's tag: the address without it
            "description_clear_url": "/transactions?" + urlencode(
                {k: v for k, v in _url_filters(filters).items() if k != "description"}),
            "money_clear_url": "/transactions?" + urlencode({k: v for k, v in _url_filters(filters).items() if k != "money"}),
            "flow_clear_url": "/transactions?" + urlencode({k: v for k, v in _url_filters(filters).items() if k != "flow"}),
            "total": total,
            "filters": filters,
            "page": page,
            "pages": max(1, -(-total // PAGE_SIZE)),
            "page_url": page_url,
            "sort_url": sort_url,
            "sort": filters["sort"] or "date",
            "sort_dir": filters["dir"] or "desc",
            "ranges": RANGES,
            # Shown in From/To when a side has no limit (All, or a blank Custom date).
            **dict(zip(("data_first", "data_last"), bounds)),
            "accounts": accounts,
            "add_accounts": add_accounts,
            "today_iso": date.today().isoformat(),           # the Add a transaction form's date
            # the account checklist: an account with authorized users' cards lists them under it (2026-10-29)
            "account_cards": account_cards,
            "picked_accounts": picked_accounts,
            # grouped by account type, and the button's words (user's request 2026-10-30)
            "account_groups": account_groups,
            "accounts_label": accounts_label,
            "categories": categories.list_categories(conn),
            # the category checklist (user's requests 2026-10-29/30): the main groups, their categories, subcategories
            "category_groups": category_groups,
            "categories_label": categories_label,
            "picked_categories": set(filters["category"].split(",")) - {""},
            "picked_tags": set(filters["tag"].split(",")) - {""},
            "purposes": purposes.accounts(conn),          # group edit: link transfers to a purpose account
            "kinds": KIND_FILTERS,
            # the recurring icon: each row's confirmed recurring item (user's request 2026-10-16)
            "recurring_of": recurring.items_of(conn, [r for r in [*rows, *review_rows] if r["id"] > 0]),
            "bonus_of": rewards.bonus_labels(conn, [r["id"] for r in [*rows, *review_rows]]),
            # your tags (tags.py, user's request 2026-10-28): each row's chips, the filter and group edit's choices
            "tags_of": tags.tags_of(conn, [r["id"] for r in [*rows, *review_rows]]),
            # the Tags filter lists inactive ones last, under a header; the others offer only active ones (2026-11-02)
            "all_tags": tags.all_tags(conn),
            "active_tags": tags.active_tags(conn),
            "inactive_tags": tags.split_by_activity(conn)[1],
            # investment activity's ticker and quantity x price, under its description
            "activity_of": _activity_of(conn, rows),
            **_logo_context(conn),
            # Group edit (milestone 14): the bar's hidden fields carry the filters and this URL.
            "this_url": "/transactions" + ("?" + str(request.url.query) if request.url.query else ""),
            "msg": request.query_params.get("msg"),
            "error": request.query_params.get("error"),
        },
    )
    # The filter form sends every ticked box (a checklist's ticked account or category and all its boxes under it, blank
    # fields too); the address bar gets these filters' own short form instead (?category=A,B), as links write them.
    if request.headers.get("HX-Request"):
        response.headers["HX-Push-Url"] = "/transactions?" + urlencode(
            {**_url_filters(filters), **({"page": page} if page > 1 else {})})
    return response


@router.get("/logos/{name}")
def logo_file(name: str, settings=Depends(get_settings)):
    """A cached merchant logo (logos.py). Only hash-named PNGs from the logos folder."""
    if not re.fullmatch(r"[0-9a-f]{64}\.png", name):
        return HTMLResponse("", status_code=404)
    path = settings.home / "logos" / name
    if not path.is_file():
        return HTMLResponse("", status_code=404)
    return FileResponse(path, media_type="image/png")


@router.post("/transactions/breakdown-chart")
def breakdown_chart(by: str = Form(""), return_url: str = Form("/transactions"), conn=Depends(get_conn)):
    """The card's title dropdown on Spending / Income (user's requests 2026-11-01): the pie by category, or the top descriptions."""
    try:
        spending.set_breakdown(conn, by)
    except ValueError:
        pass
    return RedirectResponse(return_url if re.match(r"/transactions(\?|$)", return_url) else "/transactions",
                            status_code=303)


@router.post("/transactions/transfers-chart")
def transfers_chart(style: str = Form(""), return_url: str = Form("/transactions"), conn=Depends(get_conn)):
    """The Transfer flows card's gear popup (user's request 2026-10-31): a circle (each account once) or the flow."""
    try:
        transfers.set_chart_style(conn, style)
    except ValueError:
        pass
    return RedirectResponse(return_url if re.match(r"/transactions(\?|$)", return_url) else "/transactions",
                            status_code=303)


@router.post("/transactions/new")
def add_manual(account: str = Form(""), date_text: str = Form("", alias="date"), description: str = Form(""),
               amount: str = Form(""), direction: str = Form("out"), category: str = Form(""), tags_text: str = Form("", alias="tags"),
               rewards_text: str = Form("", alias="rewards"), kind: str = Form("cash", alias="type"), conn=Depends(get_conn)):
    """The Transactions page's Add transaction: one transaction, or with rewards used its cost and rebate
    (manual_transactions.add); then rules and transfers run as after a sync, so it shows like any other. `type`: cash,
    or a rewards redemption, which needs its rewards points used (2026-11-04)."""
    if kind != "redemption":
        rewards_text = ""                     # a cash transaction never has rewards used
    elif not rewards_text.strip():
        return redirect("/transactions", error="Enter the rewards points used.")
    try:
        manual_transactions.add(conn, account_id=int(account) if account.isdigit() else 0, day=date_text,
                                description=description, amount_text=amount, direction=direction, category_key=category,
                                tag_names=tags_text, rewards_text=rewards_text)
    except ValueError as exc:
        return redirect("/transactions", error=str(exc))
    rules.run(conn)
    return redirect("/transactions", msg="Transaction added." if not rewards_text.strip() else
                    "Added, with its rebate paid with rewards.")
