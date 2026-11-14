"""Manual accounts: create, add/edit/delete values and events, purpose rules and adjustments, archive/delete.
The forms all sit on the account's page (/accounts/{id}, web/accounts.py) and return there."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from fintrack import manual, manual_positions, purposes
from fintrack.web.deps import get_conn, get_quotes, get_settings, redirect, templates

router = APIRouter()

# Readable kind for any manual account row, e.g. "Property (home, car, ...)".
templates.env.globals["manual_kind_label"] = lambda account: manual.KINDS[manual.kind_of(account)].label


@router.get("/manual/new", response_class=HTMLResponse)
def new_form(request: Request, settings=Depends(get_settings)):
    q = request.query_params
    return templates.TemplateResponse(
        request,
        "manual_new.html",
        {
            "settings": settings,
            "kinds": manual.KINDS,
            "today": date.today().isoformat(),
            "error": q.get("error"),
            "form": {k: q.get(k, "") for k in ("name", "kind", "value", "date", "note")},
        },
    )


@router.post("/manual")
def create(
    name: str = Form(""), kind: str = Form(""), value: str = Form(""), date_: str = Form("", alias="date"),
    note: str = Form(""), conn=Depends(get_conn),
):
    try:
        account_id = manual.create_account(conn, name, kind, value, date_, note)
        if kind == "lent":                          # a purpose account: its balance comes from linked transfers
            purposes.adopt_opening_value(conn, account_id)
    except manual.ManualError as exc:
        # Send the form back with what you typed.
        return redirect("/manual/new", error=str(exc), name=name, kind=kind, value=value, date=date_, note=note)
    return redirect(f"/accounts/{account_id}", msg=f"Added {name.strip()}.")


@router.get("/manual/{account_id}")
def detail_moved(account_id: int):
    """A manual account's page was folded into its account page (user's request 2026-10-15): value, name, kind,
    institution, type, History, rules, archive and delete all live at /accounts/{id}. Old links still land."""
    return RedirectResponse(f"/accounts/{account_id}", status_code=307)


def _live(quotes, tickers: list[str]) -> dict:
    """Live Alpaca quotes for these tickers ({} without keys)."""
    return quotes.quotes(tickers) if quotes and tickers else {}


# ---------------------------------------------------------------- positions (manual_positions.py)

@router.post("/manual/{account_id}/events")
def add_event(account_id: int, ticker: str = Form(""), date_: str = Form("", alias="date"), kind: str = Form(""),
              unit: str = Form(""), amount: str = Form(""), conn=Depends(get_conn), quotes=Depends(get_quotes)):
    """The History card's Add balance form: a balance on a date, in quantity or value (a statement's, a correction). A
    new ticker becomes a new line. Contributions, withdrawals and fees are activity (ledger.py; docs/parity-plan.md)."""
    if kind != "balance":
        return redirect(f"/accounts/{account_id}", error="Contributions, withdrawals and fees are added with Add activity.")
    try:
        tickers = manual_positions.position_tickers(conn, account_id)
        if ticker.strip() and manual_positions.TICKER_RE.match(ticker.strip().upper()):
            tickers = sorted(set(tickers) | {ticker.strip().upper()})
        manual_positions.record_event(conn, account_id, ticker, date_, kind, unit, amount, _live(quotes, tickers))
    except (manual_positions.PositionError, manual.ManualError) as exc:
        return redirect(f"/accounts/{account_id}", error=str(exc))
    return redirect(f"/accounts/{account_id}", msg="Added.")


@router.post("/positions/{position_id}/changes/edit")
def edit_event(position_id: int, account_id: int = Form(...), old_date: str = Form(...), date_: str = Form("", alias="date"),
               kind: str = Form(""), unit: str = Form(""), amount: str = Form(""),
               conn=Depends(get_conn), quotes=Depends(get_quotes)):
    """Edit one of your balances in place (activity is edited under Transactions)."""
    if kind != "balance":
        return redirect(f"/accounts/{account_id}", error="A balance stays a balance: add activity for the rest.")
    try:
        manual_positions.edit_event(conn, position_id, old_date, date_, kind, unit, amount,
                                    _live(quotes, manual_positions.position_tickers(conn, account_id)))
    except (manual_positions.PositionError, manual.ManualError) as exc:
        return redirect(f"/accounts/{account_id}", error=str(exc))
    return redirect(f"/accounts/{account_id}", msg="Saved.")


@router.post("/positions/{position_id}/changes/delete")
def delete_position_change(position_id: int, account_id: int = Form(...), date_: str = Form("", alias="date"),
                           conn=Depends(get_conn)):
    try:
        manual_positions.delete_change(conn, position_id, date_)
    except manual_positions.PositionError as exc:
        return redirect(f"/accounts/{account_id}", error=str(exc))
    return redirect(f"/accounts/{account_id}", msg="Entry deleted.")


@router.post("/positions/{position_id}/price")
def set_position_price(position_id: int, account_id: int = Form(...), price: str = Form(""),
                       conn=Depends(get_conn)):
    try:
        manual_positions.set_price(conn, position_id, price)
    except manual_positions.PositionError as exc:
        return redirect(f"/accounts/{account_id}", error=str(exc))
    return redirect(f"/accounts/{account_id}", msg="Price saved.")


@router.post("/manual/{account_id}/valuations")
def add_value(
    account_id: int, value: str = Form(""), date_: str = Form("", alias="date"), note: str = Form(""),
    conn=Depends(get_conn),
):
    try:
        manual.add_valuation(conn, account_id, value, date_, note)
    except manual.ManualError as exc:
        return redirect(f"/accounts/{account_id}", error=str(exc))
    return redirect(f"/accounts/{account_id}", msg="Value added.")


@router.post("/valuations/{valuation_id}")
def edit_value(
    valuation_id: int, value: str = Form(""), date_: str = Form("", alias="date"), note: str = Form(""),
    account_id: int = Form(...), conn=Depends(get_conn),
):
    try:
        manual.update_valuation(conn, valuation_id, value, date_, note)
    except manual.ManualError as exc:
        return redirect(f"/accounts/{account_id}", error=str(exc))
    return redirect(f"/accounts/{account_id}", msg="Value updated.")


@router.post("/valuations/{valuation_id}/delete")
def delete_value(valuation_id: int, account_id: int = Form(...), conn=Depends(get_conn)):
    try:
        manual.delete_valuation(conn, valuation_id)
    except manual.ManualError as exc:
        return redirect(f"/accounts/{account_id}", error=str(exc))
    return redirect(f"/accounts/{account_id}", msg="Value deleted.")


# ---------------------------------------------------------------- purpose accounts (purposes.py)

@router.post("/manual/{account_id}/purpose-rules")
def add_purpose_rule(account_id: int, pattern: str = Form(""), conn=Depends(get_conn)):
    try:
        purposes.add_rule(conn, account_id, pattern)
    except (purposes.PurposeError, manual.ManualError) as exc:
        return redirect(f"/accounts/{account_id}", error=str(exc))
    return redirect(f"/accounts/{account_id}", msg="Rule added; matching transfers are linked.")


@router.post("/purpose-rules/{rule_id}/delete")
def delete_purpose_rule(rule_id: int, account_id: int = Form(...), conn=Depends(get_conn)):
    purposes.delete_rule(conn, rule_id)
    return redirect(f"/accounts/{account_id}", msg="Rule removed.")


@router.post("/manual/{account_id}/adjustments")
def add_purpose_adjustment(account_id: int, amount: str = Form(""), date_: str = Form("", alias="date"),
                           note: str = Form(""), conn=Depends(get_conn)):
    try:
        purposes.add_adjustment(conn, account_id, amount, date_, note)
    except (purposes.PurposeError, manual.ManualError) as exc:
        return redirect(f"/accounts/{account_id}", error=str(exc))
    return redirect(f"/accounts/{account_id}", msg="Adjustment added.")


@router.post("/purpose-adjustments/{adjustment_id}/delete")
def delete_purpose_adjustment(adjustment_id: int, account_id: int = Form(...), conn=Depends(get_conn)):
    try:
        purposes.delete_adjustment(conn, adjustment_id)
    except purposes.PurposeError as exc:
        return redirect(f"/accounts/{account_id}", error=str(exc))
    return redirect(f"/accounts/{account_id}", msg="Adjustment deleted.")


@router.post("/purpose-links/{transaction_id}/unlink")
def unlink_purpose(transaction_id: int, account_id: int = Form(...), conn=Depends(get_conn)):
    """Unlink one transfer (rules then leave it alone; link it again from its edit panel on Transactions)."""
    purposes.set_link(conn, transaction_id, None)
    purposes.refresh(conn, account_id)
    return redirect(f"/accounts/{account_id}", msg="Unlinked.")


@router.post("/manual/{account_id}/archive")
def archive(account_id: int, archived: str = Form("1"), conn=Depends(get_conn)):
    manual.archive_account(conn, account_id, archived == "1")
    return redirect(f"/accounts/{account_id}", msg="Archived: no longer counted from today; history kept."
                    if archived == "1" else "Restored.")


@router.post("/manual/{account_id}/delete")
def delete(account_id: int, typed_name: str = Form(""), conn=Depends(get_conn)):
    try:
        manual.delete_account(conn, account_id, typed_name)
    except manual.ManualError as exc:
        return redirect(f"/accounts/{account_id}", error=str(exc))
    return redirect("/setup", msg="Manual account and its history deleted.")
