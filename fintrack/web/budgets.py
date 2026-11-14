"""Budgets page: summary card + one table of Income / Fixed / Flexible lines (or Income / Needs / Wants: the
or Income and groups you add and name on the page: the main card's settings popup, user's requests 2026-10-28),
edited in place.

Every edit is an HTMX POST that returns the summary and table again (_budget.html), so the
totals, section rows, and summary bars always agree with the line you just changed.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from fintrack import budgets, categories, spending, tags
from fintrack.web.deps import get_conn, get_settings, month_label, redirect, templates

router = APIRouter()


def _settings(conn, here: str) -> dict:
    """The main card's settings popup (_budget_settings.html) and the custom groups' header forms: the framework,
    how many groups there are (adding stops at MAX_GROUPS, removing at MIN_GROUPS), and the page to come back to
    after a change (the same view and month)."""
    return {"framework": budgets.framework(conn), "frameworks": budgets.FRAMEWORKS,
            "group_count": len(budgets.custom_groups(conn)), "max_groups": budgets.MAX_GROUPS,
            "min_groups": budgets.MIN_GROUPS, "here": here}


def _adding(conn) -> dict:
    """"+ Add budget" (_budget_add_line.html, every view): the sections, and the categories of each kind that aren't
    lines yet. Split categories aren't offered (their subcategories are). Subcategories are indented under their parent
    (user's request 2026-11-02); a parent that can't be chosen is still listed above them, grayed out."""
    on_budget = set(budgets.line_sections(conn))
    choices = {"income": [], "expense": []}
    unavailable = {}                         # parents not offered, listed only if a subcategory of theirs is
    for c in categories.list_categories(conn):
        if c["kind"] not in choices:
            continue
        row = {"key": c["key"], "label": c["label"], "parent_label": c["parent_label"], "disabled": False}
        if c["key"] in on_budget or c["is_split"]:
            if not c["parent_key"]:
                unavailable[c["key"]] = {**row, "disabled": True}
            continue
        if c["parent_key"] in unavailable:
            choices[c["kind"]].append(unavailable.pop(c["parent_key"]))
        choices[c["kind"]].append(row)
    # tags (user's request 2026-11-02): spending sections only. Each tag's category budgets are offered under it, to add on
    # their own (user's request 2026-11-04); a tag already on the budget stays listed above them, grayed out.
    taken = set(budgets.tag_lines(conn))
    own: dict[int, list] = {}
    for r in conn.execute("""SELECT b.tag_id, b.category_key, c.label FROM tag_category_budgets b
                             JOIN categories c ON c.key = b.category_key ORDER BY lower(c.label)"""):
        own.setdefault(r["tag_id"], []).append(r)
    choices["tag"] = []
    for t in tags.active_tags(conn):
        key = budgets.tag_line_key(t.id)
        children = [{"key": budgets.tag_line_key(t.id, r["category_key"]), "label": r["label"], "parent_label": t.name,
                     "disabled": False} for r in own.get(t.id, []) if budgets.tag_line_key(t.id, r["category_key"]) not in taken]
        if key not in taken or children:
            choices["tag"].append({"key": key, "label": t.name, "parent_label": None, "disabled": key in taken})
        choices["tag"] += children
    return {"sections": budgets.active_sections(conn), "choices": choices}


def _context(conn, month: str, **extra) -> dict:
    """Template context for the summary and table (full page and the HTMX fragment)."""
    view = budgets.month_view(conn, month)
    return {
        "view": view,
        "month": month,
        **_adding(conn),
        "suggestion_labels": {k: label for k, (label, _) in budgets.SUGGESTIONS.items()},
        **_settings(conn, f"/budgets?month={month}"),
        **extra,
    }


def _table(request: Request, conn, month: str, error: str | None = None) -> HTMLResponse:
    """The summary + table fragment HTMX swaps in (errors are shown inside it)."""
    return templates.TemplateResponse(request, "_budget.html", _context(conn, month, error=error))


VIEWS = {"month": "Month", "year": "1 year", "decade": "10 years"}


def _year(text: str | None, default: int) -> int:
    try:
        year = int(text or "")
    except ValueError:
        return default
    return year if 1900 <= year <= 2200 else default


@router.get("/budgets", response_class=HTMLResponse)
def budgets_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    """Month view (editable), or the 1-year (12 monthly columns) / 10-year (10 annual columns)
    plan: past months are actuals, the current month and later are budget amounts."""
    q = request.query_params
    view = q.get("view") if q.get("view") in VIEWS else "month"
    common = {"settings": settings, "view_mode": view, "views": VIEWS, "msg": q.get("msg"), "error": q.get("error")}
    if view == "month":
        month = spending.parse_month(q.get("month"))
        return templates.TemplateResponse(request, "budgets.html", {
            **_context(conn, month, prev_month=spending.shift_month(month, -1),
                       next_month=spending.shift_month(month, 1)), **common})

    if view == "year":
        # 12 months from `start`, moved a month at a time. Default: last month + the next 11
        # (user's choice 2026-10-06), so one month of actuals shows beside the plan.
        start = spending.parse_month(q.get("start")) if q.get("start") else \
            spending.shift_month(spending.parse_month(None), -1)
        return templates.TemplateResponse(request, "budgets.html", {**common, **_year_context(conn, start)})
    # decade: starts last year by default, so a full year of actuals shows
    first = _year(q.get("start"), date.today().year - 1)
    return templates.TemplateResponse(request, "budgets.html", {
        **common, **_settings(conn, f"/budgets?view=decade&start={first}"), **_adding(conn),
        "plan": budgets.plan(conn, budgets.decade_columns(first)), "title": f"{first}–{first + 9}",
        "prev": {"start": first - 1, "label": str(first - 1)}, "next": {"start": first + 1, "label": str(first + 1)}})


def _year_context(conn, start: str, **extra) -> dict:
    """The 1-year view: 12 months from `start`, editable from the current month on."""
    last = spending.shift_month(start, 11)
    before, after = spending.shift_month(start, -1), spending.shift_month(start, 1)
    return {
        "view_mode": "year",
        "plan": budgets.plan(conn, budgets.year_columns(start)),
        "plan_start": start,
        **_settings(conn, f"/budgets?view=year&start={start}"), **_adding(conn),
        "title": f"{month_label(start, 'short')} – {month_label(last, 'short')}",
        "prev": {"start": before, "label": month_label(before, "short")},
        "next": {"start": after, "label": month_label(after, "short")},
        **extra,
    }


@router.post("/budgets/plan-amount", response_class=HTMLResponse)
def save_plan_amount(request: Request, category: str = Form(...), month: str = Form(...), amount: str = Form(""),
                     scope: str = Form(""), start: str = Form(""), conn=Depends(get_conn)):
    """A budget typed in the 1-year view, for one month only or that month and every month
    after (you choose in the dialog). Returns the 1-year table again (_budget_plan.html)."""
    start, month = spending.parse_month(start), spending.parse_month(month)
    error = None
    if month < spending.parse_month(None):
        error = "Past months show what actually happened; budgets can be set from this month on."
    else:
        try:
            if category.startswith(budgets.TAG_PREFIX):       # a tag's line: that month only, so no scope
                budgets.set_tag_allocation(conn, category, month, amount)
            else:
                budgets.set_budget_scoped(conn, category, month, amount, scope)
        except budgets.BudgetError as exc:
            error = str(exc)
    return templates.TemplateResponse(request, "_budget_plan.html", _year_context(conn, start, error=error))


@router.post("/budgets/amount", response_class=HTMLResponse)
def save_amount(request: Request, category: str = Form(...), month: str = Form(""), amount: str = Form(""),
                scope: str = Form(""), conn=Depends(get_conn)):
    """A line's budget, typed or filled from a suggestion. The page asks first (user's request
    2026-10-06): scope 'month' = this month only, 'forward' = this month and every month after
    (budgets.set_budget_scoped). Without a scope it applies from `month` until your next change.
    Unchanged amounts write nothing, so tabbing through the table doesn't pile up rows."""
    month = spending.parse_month(month)
    if category.startswith(budgets.TAG_PREFIX):               # a tag's line: that month only, so no scope
        try:
            budgets.set_tag_allocation(conn, category, month, amount)
        except budgets.BudgetError as exc:
            return _table(request, conn, month, error=str(exc))
        return _table(request, conn, month)
    current = budgets.budget_for(conn, category, month)
    try:
        new_cents = budgets.parse_amount(amount) if amount.strip() else None
        if new_cents != (current.amount_cents if current else None):
            if scope:
                budgets.set_budget_scoped(conn, category, month, amount, scope)
            else:
                budgets.set_budget(conn, category, month, amount, rollover=current.rollover if current else False)
    except (ValueError, budgets.BudgetError) as exc:
        return _table(request, conn, month, error=str(exc))
    return _table(request, conn, month)


@router.post("/budgets/rollover", response_class=HTMLResponse)
def save_rollover(request: Request, category: str = Form(...), month: str = Form(""), rollover: str = Form(""),
                  conn=Depends(get_conn)):
    month = spending.parse_month(month)
    current = budgets.budget_for(conn, category, month)
    if current is None:
        return _table(request, conn, month, error="Set a budget amount first, then turn on rollover.")
    if current.rollover != (rollover == "on"):
        budgets.set_budget(conn, category, month, f"{current.amount_cents / 100:.2f}", rollover=rollover == "on")
    return _table(request, conn, month)


@router.post("/budgets/lines", response_class=HTMLResponse)
def add_line(request: Request, section: str = Form(...), month: str = Form(""), category: str = Form(""),
             name: str = Form(""), back: str = Form(""), conn=Depends(get_conn)):
    """"+ Add budget": the Month view's table comes back in place; from the 1-year / 10-year views (`back`, user's
    request 2026-10-28) the page comes back to the same range."""
    month = spending.parse_month(month)
    try:
        budgets.add_line(conn, section, category, name)
    except budgets.BudgetError as exc:
        return _return(back, str(exc)) if back else _table(request, conn, month, error=str(exc))
    return _return(back) if back else _table(request, conn, month)


@router.post("/budgets/lines/move", response_class=HTMLResponse)
def move_line(request: Request, category: str = Form(...), section: str = Form(...), month: str = Form(""),
              conn=Depends(get_conn)):
    month = spending.parse_month(month)
    try:
        budgets.move_line(conn, category, section)
    except budgets.BudgetError as exc:
        return _table(request, conn, month, error=str(exc))
    return _table(request, conn, month)


@router.post("/budgets/lines/remove", response_class=HTMLResponse)
def remove_line(request: Request, category: str = Form(...), month: str = Form(""), conn=Depends(get_conn)):
    month = spending.parse_month(month)
    budgets.remove_line(conn, category)
    return _table(request, conn, month)


def _back(back: str) -> str:
    """Only back to a Budgets page (never an outside or other URL)."""
    return back if back == "/budgets" or back.startswith("/budgets?") else "/budgets"


def _return(back: str, error: str | None = None):
    """303 to `back` (a /budgets address), with an error message when a change was refused."""
    from urllib.parse import parse_qsl
    path, _, query = _back(back).partition("?")
    params = dict(parse_qsl(query))
    return redirect(path, **(params | ({"error": error} if error else {})))


@router.post("/budgets/framework")
def set_framework(framework: str = Form(...), back: str = Form("/budgets"), conn=Depends(get_conn)):
    """Fixed / Flexible, Needs / Wants, or Custom (user's requests 2026-10-28), from the main card's settings popup;
    remembered (app_settings), so the page opens in it next time. Back to the same view."""
    try:
        budgets.set_framework(conn, framework)
    except budgets.BudgetError as exc:
        return _return(back, str(exc))
    return _return(back)


@router.post("/budgets/groups")
def add_group(name: str = Form(""), back: str = Form("/budgets"), conn=Depends(get_conn)):
    """A new custom group, from "+ Add group" (user's request 2026-10-28)."""
    try:
        budgets.add_group(conn, name)
    except budgets.BudgetError as exc:
        return _return(back, str(exc))
    return _return(back)


@router.post("/budgets/groups/rename")
def rename_group(key: str = Form(...), name: str = Form(""), back: str = Form("/budgets"), conn=Depends(get_conn)):
    """A group's name, typed in its section header (user's request 2026-10-28)."""
    try:
        budgets.rename_group(conn, key, name)
    except budgets.BudgetError as exc:
        return _return(back, str(exc))
    return _return(back)


@router.post("/budgets/groups/remove")
def remove_group(key: str = Form(...), back: str = Form("/budgets"), conn=Depends(get_conn)):
    """Remove an empty custom group (its header's ×)."""
    try:
        budgets.remove_group(conn, key)
    except budgets.BudgetError as exc:
        return _return(back, str(exc))
    return _return(back)
