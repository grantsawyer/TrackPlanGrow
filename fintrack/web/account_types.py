"""Account types page (Settings, user's request 2026-10-13): your table of account types and subtypes
(account_types.py, migration 0032): add, rename, reorder, move a subtype to another type, change a type's behaviour,
delete. Accounts are given a type on their Balances account page."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from fintrack import account_types
from fintrack.web.deps import get_conn, get_settings, templates

router = APIRouter()


def _back(**message: str) -> RedirectResponse:
    return RedirectResponse("/account-types?" + urlencode(message), status_code=303)


def _do(change, done: str) -> RedirectResponse:
    try:
        change()
    except account_types.TypeError_ as exc:
        return _back(error=str(exc))
    return _back(msg=done)


@router.get("/account-types", response_class=HTMLResponse)
def types_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    return templates.TemplateResponse(request, "account_types.html", {
        "settings": settings, "types": account_types.tree(conn, with_automatic=True),
        "kinds": {k: v.label for k, v in account_types.KINDS.items()},
        "msg": request.query_params.get("msg"), "error": request.query_params.get("error")})


@router.post("/account-types/add")
def add_type(name: str = Form(""), kind: str = Form(""), conn=Depends(get_conn)):
    return _do(lambda: account_types.add_type(conn, name, kind), "Type added.")


@router.post("/account-types/{node_id}/add")
def add_subtype(node_id: int, name: str = Form(""), conn=Depends(get_conn)):
    return _do(lambda: account_types.add_subtype(conn, node_id, name), "Subtype added.")


@router.post("/account-types/{node_id}/rename")
def rename(node_id: int, name: str = Form(""), conn=Depends(get_conn)):
    return _do(lambda: account_types.rename(conn, node_id, name), "Renamed.")


@router.post("/account-types/{node_id}/kind")
def set_kind(node_id: int, kind: str = Form(""), conn=Depends(get_conn)):
    return _do(lambda: account_types.set_kind(conn, node_id, kind), "Saved.")


@router.post("/account-types/{node_id}/order")
def reorder(node_id: int, direction: str = Form("up"), conn=Depends(get_conn)):
    return _do(lambda: account_types.move(conn, node_id, direction), "Moved.")


@router.post("/account-types/{node_id}/move")
def move_to(node_id: int, parent: str = Form(""), conn=Depends(get_conn)):
    return _do(lambda: account_types.move_to(conn, node_id, int(parent) if parent.isdigit() else -1), "Moved.")


@router.post("/account-types/{node_id}/delete")
def delete(node_id: int, conn=Depends(get_conn)):
    count = []
    response = _do(lambda: count.append(account_types.delete(conn, node_id)), "Deleted.")
    if count and count[0]:
        return _back(msg=f"Deleted. {count[0]} account{'' if count[0] == 1 else 's'} went back to automatic.")
    return response
