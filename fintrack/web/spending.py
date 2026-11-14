"""Spending report page, and the Categories & rules page."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from fintrack import categories, category_icons, purposes, recurring, rules, spending, tags, transfers, visuals
from fintrack.web.deps import get_conn, get_settings, redirect, templates

router = APIRouter()


@router.get("/spending", response_class=HTMLResponse)
def spending_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    period = spending.resolve_period(conn, request.query_params)
    if period.mode == "days":
        # One calendar month (this or last): the cumulative chart against the month before, and both months' lists.
        month = period.month
        report = spending.month_report(conn, month)
        prev_month = spending.shift_month(month, -1)
        compare = visuals.cumulative_spending(conn, month=month)
        panes = [("this", spending.month_transactions(conn, month)), ("prior", spending.month_transactions(conn, prev_month))]
        txn_query = f"month={month}"
    else:
        # Any other range: monthly totals, one list, compared with the equal-length period before it.
        month = prev_month = ""
        report = spending.range_report(conn, period.start, period.end)
        compare = visuals.monthly_spending(conn, period.start, period.end, with_average=period.key in spending.AVERAGE_RANGES)
        panes = [("this", spending.range_transactions(conn, period.start, period.end))]
        txn_query = f"from={period.start}&to={period.end}"
    shares = visuals.category_shares(report.lines)
    return templates.TemplateResponse(
        request,
        "spending.html",
        {
            "settings": settings,
            "period": period,
            "ranges": spending.RANGES,
            "report": report,
            "shares": shares,
            "category_rows": visuals.grouped_category_lines(report.lines, shares),
            "compare": compare,
            "panes": panes,
            # each category's icon (category_icons.resolve): a phone's transaction rows show it in place of the name
            "category_icon_of": category_icons.resolve(conn),
            "month": month,
            "prev_month": prev_month,
            "prior_start_end": spending.prior_period(period.start, period.end),
            "txn_query": txn_query,
            "average_12": spending.trailing_average_cents(conn),
            # What to set against the 12 month average, and how to word it. One calendar month (This/Last month):
            # its actual spending, neither extrapolated nor rescaled (nine days of a month stretched to 30.44 would
            # be wild). Any other range: spending per month, by the same formula as the average itself.
            "pace": (report.spending_cents if period.mode == "days"
                     else spending.average_per_month_cents(report.spending_cents, period.start, period.end)),
            "pace_words": ("so far" if report.compare_through_day else "last month") if period.mode == "days"
                          else "a month",
        },
    )


# ---------------------------------------------------------------- categories & rules

@router.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    """Rules (Settings → Rules): each as a sentence, in the order they run (fintrack/rules.py)."""
    q = request.query_params
    listed = rules.listing(conn)
    return templates.TemplateResponse(request, "rules.html", {
        "settings": settings,
        # keywords by their category's name; custom rules in the order they run (user's request 2026-11-02)
        "keyword_rules": sorted((r for r in listed if r["is_keyword"]), key=lambda r: r["category"].casefold()),
        "custom_rules": [r for r in listed if not r["is_keyword"]],
        "rules": listed,
        "filter_fields": {k: v for k, v in rules.FIELD_LABELS.items() if any(k in r["fields"] for r in listed)},
        "filter_kinds": {k: v for k, v in rules.ACTION_LABELS.items() if any(k in r["kinds"] for r in listed)},
        "msg": q.get("msg"),
        "error": q.get("error"),
    })


@router.get("/categories", response_class=HTMLResponse)
def categories_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings), open_panel: str = ""):
    q = request.query_params
    if q.get("pattern"):                                  # an old "rule…" link: the rule form is on /rules now
        return redirect("/rules/new", pattern=q.get("pattern", ""), category=q.get("category", ""))
    cats = categories.list_categories(conn)
    counts = categories.transaction_counts(conn)
    tops = [dict(c) for c in cats if not c["parent_key"]]
    by_key = {c["key"]: c for c in tops}
    for c in tops:
        c["children"] = []
    for c in cats:
        if c["parent_key"] in by_key:
            by_key[c["parent_key"]]["children"].append({**dict(c), "count": counts.get(c["key"], 0)})
    for c in tops:
        c["count"] = counts.get(c["key"], 0) + sum(ch["count"] for ch in c["children"])
    return templates.TemplateResponse(
        request,
        "categories.html",
        {
            "settings": settings,
            # Categories as a table of categories and subcategories (user's request 2026-10-13: like account types)
            "tree": tops,
            "categories": cats,
            # Plaid categories that can be split into Plaid's subcategories (not custom, not a fixed split).
            "plaid_options": categories.plaid_subcategory_options(conn),   # the + panel's Plaid subcategories
            "plaid_categories": categories.plaid_category_options(conn),   # the Add category card's, by group
            "kind_icons": category_icons.KIND_DEFAULT,                     # a new category's icon until you choose
            "open_panel": open_panel,         # the panel to leave open: one just used
            "protected": categories.PROTECTED,
            "kinds": categories.KIND_ORDER,           # the Counts as choices, in the page's order
            "kind_order": categories.KIND_ORDER,
            "kind_labels": categories.KIND_LABELS,
            # Icons (user's request 2026-10-20): each category's icon, the name's best matches for the picker, and
            # the library by group (category_icons.py).
            "icon_of": category_icons.resolve(conn),
            "icon_suggestions": {c["key"]: category_icons.suggestions(c["label"]) for c in cats},
            "icon_groups": [(g, [i for i in category_icons.ICONS if i.group == g]) for g in category_icons.GROUPS],
            "msg": q.get("msg"),
            "error": q.get("error"),
        },
    )


def _category_change(conn, change, *args, done: str, recheck: bool = False):
    """Run a change to the category table (categories.<change>(conn, *args)) and come back to the page. `recheck` =
    what counts may have changed, so transfer pairs and money-lent links are worked out again (as other category edits
    do). `done` may use {result}, the change's return value."""
    from fintrack import purposes

    try:
        result = change(conn, *args)
    except (ValueError, LookupError) as exc:
        return redirect("/categories", error=str(exc))
    if recheck:
        transfers.detect_transfers(conn)
        purposes.update(conn)
    return redirect("/categories", msg=done.format(result=result))


@router.post("/categories/{key}/rename")
def rename_category(key: str, label: str = Form(""), conn=Depends(get_conn)):
    return _category_change(conn, categories.rename_category, key, label, done="Renamed.")


@router.post("/categories/{key}/add")
def add_subcategory(key: str, label: str = Form(""), conn=Depends(get_conn)):
    return _category_change(conn, categories.add_subcategory, key, label, done="Subcategory added.")


@router.post("/categories/{key}/icon")
def change_icon(key: str, icon: str = Form(""), conn=Depends(get_conn)):
    """The category's icon from the library ("" = automatic again; category_icons.set_icon)."""
    return _category_change(conn, category_icons.set_icon, key, icon, done="Icon updated.")


@router.post("/categories/{key}/move")
def move_category_to(key: str, parent: str = Form(""), conn=Depends(get_conn)):
    """Under another category, or its own category in a group (categories.move)."""
    return _category_change(conn, categories.move, key, parent, done="Moved.", recheck=True)


@router.post("/categories/{key}/delete")
def delete_category(key: str, target: str = Form(""), conn=Depends(get_conn)):
    return _category_change(conn, categories.delete_category, key, target,
                            done="Deleted. {result} transactions moved.", recheck=True)


@router.post("/categories")
def create_category(label: str = Form(""), kind: str = Form("expense"), icon: str = Form(""), conn=Depends(get_conn)):
    """From a group's Add category card (user's request 2026-11-02): a name and an icon ("" = automatic, by the name)."""
    try:
        if icon and icon not in category_icons.BY_NAME:      # before anything is created
            raise ValueError("Choose an icon from the list.")
        key = categories.add_category(conn, label, kind)
        category_icons.set_icon(conn, key, icon)
    except ValueError as exc:
        return redirect("/categories", error=str(exc))
    return redirect("/categories", msg=f"Added category {label.strip()!r}.")


@router.post("/categories/{key}/kind")
def change_kind(key: str, kind: str = Form(...), conn=Depends(get_conn)):
    try:
        categories.set_kind(conn, key, kind)
    except (ValueError, LookupError) as exc:
        return redirect("/categories", error=str(exc))
    # Income categories are excluded from transfer matching, so re-match.
    transfers.detect_transfers(conn)
    return redirect("/categories", msg="Category updated.")


@router.post("/categories/{key}/plaid-sub", response_class=HTMLResponse)
def add_plaid_sub(request: Request, key: str, code: str = Form(""), conn=Depends(get_conn), settings=Depends(get_settings)):
    """Add one Plaid subcategory from the + panel (user's request 2026-11-02). Returns the page with that panel still open; htmx
    swaps in just that category's card, so only the subcategory moves."""
    try:
        categories.add_plaid_subcategory(conn, key, code)
    except ValueError:
        pass                                  # already added in another tab: the card shows what's true
    else:
        rules.run(conn)                       # rules can test Plaid's category; transfers are matched again
    return categories_page(request, conn, settings, open_panel=key)


@router.post("/categories/plaid-category", response_class=HTMLResponse)
def add_plaid_top(request: Request, key: str = Form(""), kind: str = Form(""), conn=Depends(get_conn), settings=Depends(get_settings)):
    """Add one of Plaid's categories from a group's Add category card (user's request 2026-11-02). Returns the page with the card
    still open; htmx swaps in just that group, so only the category moves."""
    try:
        categories.add_plaid_category(conn, key)
    except ValueError:
        pass                                  # already added in another tab: the group shows what's true
    else:
        rules.run(conn)                       # rules can test Plaid's category; transfers are matched again
    return categories_page(request, conn, settings, open_panel=f"group:{kind}")


def _builder(request: Request, conn, settings, rule, rule_id: int | None = None, error: str = "", return_url: str = "",
             similar: dict | None = None) -> HTMLResponse:
    """The rule builder (rule_form.html): new, prefilled, or an existing rule's."""
    return templates.TemplateResponse(request, "rule_form.html", {
        "settings": settings, "page_path": "/rules", "rule": rule, "rule_id": rule_id, "error": error, "return_url": return_url, "similar": similar,
        "field_labels": rules.FIELD_LABELS, "ops": rules.OPS, "action_labels": rules.ACTION_LABELS,
        "names": rules.labels(conn), "categories": categories.list_categories(conn),
        "accounts": conn.execute("SELECT id, name, mask FROM accounts WHERE is_hidden = 0 ORDER BY name").fetchall(),
        "recurring_items": recurring.list_items(conn, "confirmed"), "purpose_accounts": purposes.accounts(conn),
        "all_tags": tags.active_tags(conn), "not_recurring": rules.NOT_RECURRING,
    })


@router.get("/rules/new", response_class=HTMLResponse)
def new_rule(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    """A blank rule, or prefilled: ?from=<transaction> (its edit panel's "Create a rule…"), ?pattern=&category= (old links)."""
    q = request.query_params
    rule = rules.blank_from_transaction(conn, int(q["from"])) if q.get("from", "").isdigit() else None
    if rule is None and q.get("pattern"):
        rule = rules.Rule(0, 0, [rules.Condition("description", "contains", [q["pattern"]])],
                          [rules.Action("set_category", q.get("category", ""))])
    return _builder(request, conn, settings, rule or rules.Rule(0, 0, [rules.Condition("description", "contains", [""])],
                                                      [rules.Action("set_category", "")]),
                    return_url=q.get("return_url", ""))


@router.get("/rules/preview", response_class=HTMLResponse)
def rule_preview(request: Request, conn=Depends(get_conn)):
    """The builder's live preview: what saving it would do. Half-filled rows are skipped."""
    q = request.query_params
    conditions, actions = rules.from_form(conn, q, preview=True)
    rule_id = int(q["rule_id"]) if q.get("rule_id", "").isdigit() else None
    return templates.TemplateResponse(request, "_rule_preview.html", {"preview": rules.preview(conn, conditions, actions, rule_id)})


def _keyword_row(request: Request, conn, rule_id: int) -> HTMLResponse:
    row = next((r for r in rules.listing(conn) if r["id"] == rule_id and r["is_keyword"]), None)
    return templates.TemplateResponse(request, "_keyword_row.html", {"r": row}) if row else HTMLResponse("", status_code=404)


def _keyword_edit(request: Request, conn, rule_id: int, category: str | None = None, keywords: str | None = None,
                  error: str = "") -> HTMLResponse:
    rule = next((r for r in rules.load(conn) if r.id == rule_id and rules.is_keyword(r)), None)
    if rule is None:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(request, "_keyword_edit.html", {
        "r": {"id": rule_id}, "categories": categories.list_categories(conn), "error": error,
        "category": rule.actions[0].value if category is None else category,
        "keywords": "\n".join(rule.conditions[0].values) if keywords is None else keywords})


@router.get("/rules/{rule_id}/keywords", response_class=HTMLResponse)
def edit_keywords(request: Request, rule_id: int, conn=Depends(get_conn)):
    """A keywords row's pencil (user's request 2026-11-02): edit it in place."""
    return _keyword_edit(request, conn, rule_id)


@router.get("/rules/{rule_id}/keyword-row", response_class=HTMLResponse)
def keyword_row(request: Request, rule_id: int, conn=Depends(get_conn)):
    """Cancel: the row as it was."""
    return _keyword_row(request, conn, rule_id)


@router.post("/rules/{rule_id}/keywords", response_class=HTMLResponse)
def save_keywords(request: Request, rule_id: int, category: str = Form(""), keywords: str = Form(""), conn=Depends(get_conn)):
    """Save a row edited in place; returns the clean row, or the form with what's wrong."""
    try:
        rules.update_keywords(conn, rule_id, category, keywords)
    except ValueError as exc:
        conn.rollback()
        return _keyword_edit(request, conn, rule_id, category, keywords, str(exc))
    return _keyword_row(request, conn, rule_id)


@router.get("/rules/{rule_id}/edit", response_class=HTMLResponse)
def edit_rule_page(request: Request, rule_id: int, conn=Depends(get_conn), settings=Depends(get_settings)):
    rule = next((r for r in rules.load(conn) if r.id == rule_id), None)
    if rule is None:
        return redirect("/rules", error="That rule no longer exists.")
    if rules.is_keyword(rule):                            # edited in place on the Rules page, not here
        return RedirectResponse(f"/rules#rule-{rule_id}", status_code=303)
    return _builder(request, conn, settings, rule, rule_id)


async def _save_rule(request: Request, conn, settings, rule_id: int | None):
    form = await request.form()
    return_url = form.get("return_url", "")
    choice = form.get("on_similar", "")
    merged = False
    try:
        conditions, actions = rules.from_form(conn, form)
        # A new rule like one you have: ask first (user's request 2026-11-02). Nothing's saved, and a tag it just made is undone.
        similar = rules.find_similar(conn, conditions, actions) if rule_id is None and choice != "new" else None
        if similar and choice == "merge" and similar.new_phrases:
            saved, merged = rules.add_keywords(conn, similar), True
        elif similar:
            conn.rollback()
            shown, shown_actions = rules.from_form(conn, form, preview=True)
            names = rules.labels(conn)
            return _builder(request, conn, settings, rules.Rule(0, 0, shown, shown_actions), None, "", return_url, similar={
                "id": similar.rule.id, "new_phrases": similar.new_phrases,
                "conditions": [rules.describe_condition(c, names) for c in similar.rule.conditions],
                "actions": [rules.describe_action(a, names) for a in similar.rule.actions]})
        else:
            saved = rules.save(conn, rule_id, conditions, actions)
    except ValueError as exc:
        conditions, actions = rules.from_form(conn, form, preview=True)
        conn.rollback()
        return _builder(request, conn, settings, rules.Rule(rule_id or 0, 0, conditions, actions), rule_id, str(exc), return_url)
    count = next((r["count"] for r in rules.listing(conn) if r["id"] == saved), 0)
    msg = f"{'Added to the existing rule' if merged else 'Rule saved'}. It matches {count} transaction{'' if count == 1 else 's'}."
    if re.match(r"/(transactions|accounts/\d+)(\?|$)", return_url):
        from fintrack.web.transaction_edits import _back
        return _back(return_url, msg=msg)
    return redirect("/rules", msg=msg)


@router.post("/rules")
async def create_rule(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    return await _save_rule(request, conn, settings, None)


@router.post("/rules/order")
async def order_rules(request: Request, conn=Depends(get_conn)):
    """The order you dragged them into (ui.js)."""
    form = await request.form()
    rules.reorder(conn, [int(i) for i in form.getlist("ids") if str(i).isdigit()])
    return HTMLResponse("")


@router.post("/rules/{rule_id}/move")
def move_rule(rule_id: int, step: int = Form(0), conn=Depends(get_conn)):
    """The arrows: one place up or down (for keyboards and phones, where dragging isn't available)."""
    rules.move(conn, rule_id, 1 if step > 0 else -1)
    return redirect("/rules")


@router.post("/rules/{rule_id}/delete")
def remove_rule(rule_id: int, results: str = Form("original"), conn=Depends(get_conn)):
    """The trash icon's choice (user's request 2026-11-02): back to original, or keep what it decided."""
    keep = results == "keep"
    rules.delete(conn, rule_id, keep=keep)
    return redirect("/rules", msg="Rule deleted. Its results stay as your own edits." if keep else "Rule deleted. Its transactions went back to their original results.")


@router.post("/rules/{rule_id}")
async def edit_rule(request: Request, rule_id: int, conn=Depends(get_conn), settings=Depends(get_settings)):
    return await _save_rule(request, conn, settings, rule_id)
