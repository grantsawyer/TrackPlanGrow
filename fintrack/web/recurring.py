"""Recurring items: review detections, confirm/dismiss/edit, add your own."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from fintrack import categories, forecast, purposes, recurring, rules, tags, visuals
from fintrack.web.deps import get_conn, get_settings, redirect, templates

router = APIRouter()

templates.env.globals["frequency_label"] = lambda f: recurring.FREQUENCY_LABELS.get(f, f)


def _form_options(conn) -> dict:
    return {
        "frequencies": recurring.FREQUENCY_LABELS,
        "accounts": conn.execute(
            "SELECT id, name, mask, type FROM accounts WHERE is_hidden = 0"
            " AND type IN ('depository', 'credit') ORDER BY type, name"       # manual ones too (2026-11-13)
        ).fetchall(),
        "categories": categories.list_categories(conn),
        # "Then do this": the rule builder's action rows (_rule_actions.html), without "Recurring"
        "action_labels": {k: v for k, v in rules.ACTION_LABELS.items() if k in recurring.ACTION_KINDS},
        "names": rules.labels(conn), "purpose_accounts": purposes.accounts(conn), "all_tags": tags.active_tags(conn),
        "recurring_items": [], "not_recurring": rules.NOT_RECURRING, "item_actions": [],
    }


def _match(conn, item) -> dict | None:
    """The window's "Matches" line for a detected item: the description it looks for, whether it's an amount band, and
    how many transactions it has (recurring.transaction_ids)."""
    if not item["merchant_key"]:
        return None
    text, band = recurring.split_key(item["merchant_key"])
    return {"text": text, "band": band is not None, "count": len(recurring.transaction_ids(conn, item))}


def next_30_days(conn) -> list:
    """The "Next 30 days" table: every confirmed item and card statement still owed that moves a checking or savings
    account in the next 30 days (the cash-flow forecast's events for all of them together; overdue bills on today)."""
    flow = forecast.cash_flow(conn)
    target = flow.combined or (flow.accounts[0] if flow.accounts else None)
    if target is None:
        return []
    horizon = flow.today + timedelta(days=30)
    return [e for e in target.events if e.day <= horizon]


@router.get("/recurring", response_class=HTMLResponse)
def recurring_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    """Recurring (user's request 2026-10-16): this month's recurring income and expenses by day at the top, the next
    30 days, then
    the items to review, confirmed, your own and dismissed. The cash-flow forecast is on the Cash flow page."""
    return templates.TemplateResponse(
        request,
        "recurring.html",
        {
            "settings": settings,
            "detected": recurring.list_items(conn, "detected"),
            "confirmed": recurring.list_items(conn, "confirmed"),
            "income_by_day": visuals.recurring_by_day(conn, "income"),
            "bills_by_day": visuals.recurring_by_day(conn, "bill"),
            "upcoming": next_30_days(conn),
            "dismissed": recurring.list_items(conn, "dismissed"),
            "today": date.today().isoformat(),
            "msg": request.query_params.get("msg"),
            "error": request.query_params.get("error"),
            **_form_options(conn),
        },
    )


@router.post("/recurring/refresh")
def check_again(conn=Depends(get_conn)):
    counts = recurring.refresh(conn)
    return redirect("/recurring", msg=f"Checked your history: {counts['new']} new, "
                                      f"{counts['updated']} updated, {counts['removed']} no longer recurring.")


@router.post("/recurring/{item_id}/status")
def change_status(item_id: int, status: str = Form(...), conn=Depends(get_conn)):
    try:
        if status == "remove":
            recurring.delete_item(conn, item_id)
        else:
            recurring.set_status(conn, item_id, status)
    except (recurring.RecurringError, LookupError) as exc:
        return redirect("/recurring", error=str(exc))
    rules.run(conn)                    # a confirmed item's actions apply; a dismissed or removed one's leave
    return redirect("/recurring", msg={"confirmed": "Confirmed: it's now used in forecasts.",
                                       "dismissed": "Dismissed: it won't be suggested again.",
                                       "detected": "Moved back to review.",
                                       "remove": "Removed."}.get(status, "Saved."))


@router.post("/recurring")
async def save(request: Request, conn=Depends(get_conn)):
    """The window's Save (or Confirm): "Matches" fields, then the "Then do this" rows (act-<n>-kind / -value, as in the
    rule builder), applied to the item's transactions at once (rules.run)."""
    form = await request.form()
    f = {k: str(form.get(k, "")) for k in ("item_id", "name", "amount", "direction", "frequency", "next_date",
                                            "account_id", "days_of_month")}
    item_id = f["item_id"]
    try:
        _, actions = rules.from_form(conn, form)
        recurring.save_item(
            conn, int(item_id) if item_id.isdigit() else None, name=f["name"], amount_text=f["amount"],
            direction=f["direction"] or "out", frequency=f["frequency"] or "monthly", next_date=f["next_date"],
            account_id=int(f["account_id"]) if f["account_id"].isdigit() else None,
            days_of_month=f["days_of_month"], actions=actions,
        )
    except (ValueError, LookupError) as exc:          # RecurringError is a ValueError, as are the rows' own errors
        target = f"/recurring/{item_id}/edit" if item_id.isdigit() else "/recurring"
        return redirect(target, error=str(exc))
    rules.run(conn)                                   # its actions, on its transactions
    return redirect("/recurring", msg=f"Saved {f['name'].strip()}. Confirmed items are used in forecasts.")


@router.get("/recurring/{item_id}/edit", response_class=HTMLResponse)
def edit_page(request: Request, item_id: int, conn=Depends(get_conn), settings=Depends(get_settings)):
    item = conn.execute("SELECT * FROM recurring_items WHERE id = ?", (item_id,)).fetchone()
    if item is None:
        return redirect("/recurring", error="That recurring item doesn't exist.")
    return templates.TemplateResponse(
        request,
        "recurring_edit.html",
        {"settings": settings, "item": item, "error": request.query_params.get("error"), **_form_options(conn),
         "item_actions": recurring.form_actions(conn, item), "match": _match(conn, item)},
    )
