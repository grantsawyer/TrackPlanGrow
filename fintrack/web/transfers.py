"""Transfers page (Settings section, user's request 2026-10-26): the transfers matched between your own accounts.

Over the ribbon's date range (Savings' control, default YTD): the matched pairs added up by route (from one account to
another), every pair with a button to remove its match (a bank transfer matched with an investment account's own
deposit / withdrawal is a pair too, user's request 2026-10-31), and the transactions with no match that could be
matched, each with the transactions that meet the matching rules with it (transfers.py). Matching and removing post back here and keep
the date range.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from fintrack import spending, transfers
from fintrack.web.deps import back_with_range, get_conn, get_settings, templates

router = APIRouter()


@router.get("/transfers", response_class=HTMLResponse)
def transfers_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    period = spending.resolve_period(conn, request.query_params, default="ytd")
    pairs = transfers.matched_pairs(conn, period.start, period.end)
    return templates.TemplateResponse(
        request,
        "transfers.html",
        {
            "settings": settings,
            "page_path": "/transfers",
            "period": period,
            "ranges": spending.RANGES,
            "pairs": pairs,
            "routes": transfers.pair_summary(pairs),
            "total_cents": sum(p.out.amount_cents for p in pairs),
            "options": transfers.match_options(conn, period.start, period.end),
            "window": transfers.MATCH_WINDOW_DAYS,
            "msg": request.query_params.get("msg"),
            "error": request.query_params.get("error"),
        },
    )


def _back(range_key: str, start: str, end: str, change, done: str):
    return back_with_range("/transfers", range_key, start, end, change, done)


@router.post("/transfers/match")
def match_transfer(transaction_id: int = Form(...), partner_id: str = Form(""), range: str = Form(""),
                   start: str = Form("", alias="from"), end: str = Form("", alias="to"), conn=Depends(get_conn)):
    """Match a transaction with the one picked from its list."""
    def change():
        if not partner_id.isdigit():
            raise ValueError("Pick the transaction to match it with.")
        transfers.match(conn, transaction_id, int(partner_id))
    return _back(range, start, end, change, "Matched.")


@router.post("/transfers/{transaction_id}/unmatch")
def unmatch_transfer(transaction_id: int, range: str = Form(""), start: str = Form("", alias="from"),
                     end: str = Form("", alias="to"), conn=Depends(get_conn)):
    """Remove a match: both halves count again as spending or income, and neither is matched until you match it."""
    return _back(range, start, end, lambda: transfers.unmatch(conn, transaction_id), "Match removed.")
