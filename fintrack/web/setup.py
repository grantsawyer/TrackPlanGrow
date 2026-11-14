"""Settings → Setup (connections, manual accounts, configuration checks) and the Plaid Link flow (connect, OAuth return,
reconnect, remove). Was web/accounts.py until 2026-10-14."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from fintrack import db, items, manual
from fintrack.crypto import has_key
from fintrack.item_budget import production_item_budget
from fintrack.plaid_client import LINK_KINDS, PlaidError
from fintrack.web.deps import get_cipher, get_conn, get_gateway, get_settings, redirect, templates

router = APIRouter()


def json_error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


# ---------------------------------------------------------------- pages

@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    """Settings → Setup (user's request 2026-10-14: the Accounts page's contents moved here, first tab of Settings):
    your connections (Refresh / Reconnect / Remove), manual accounts and removed connections, then the configuration
    checks for this install."""
    active = [
        {"item": item, "accounts": items.accounts_for_item(conn, item["id"])}
        for item in items.active_items(conn)
    ]
    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "settings": settings,
            "active": active,
            "removed": items.removed_items(conn),
            "manual_accounts": manual.list_accounts(conn, include_archived=True),
            "budget": production_item_budget(conn, settings.production_item_limit),
            "keychain_ok": has_key(settings.home),
            "pending_migrations": [p.name for _, p in db.pending_migrations(conn)],
            "msg": request.query_params.get("msg"),
            "error": request.query_params.get("error"),
        },
    )


@router.get("/connect", response_class=HTMLResponse)
def connect_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    active = items.active_items(conn)
    return templates.TemplateResponse(
        request,
        "connect.html",
        {
            "settings": settings,
            "kinds": LINK_KINDS,
            "budget": production_item_budget(conn, settings.production_item_limit),
            "connected": active,
            "connected_ids": items.connected_institution_ids(conn),
            "consent_phrase": items.CONSENT_PHRASE,
        },
    )


@router.get("/oauth-return", response_class=HTMLResponse)
def oauth_return(request: Request, settings=Depends(get_settings)):
    """OAuth banks send the browser back here; link.js re-opens the same Link session."""
    return templates.TemplateResponse(request, "oauth_return.html", {"settings": settings})


# ---------------------------------------------------------------- Link (JSON, called by link.js)

class CreateTokenBody(BaseModel):
    kind: str
    consent: str = ""


class LinkAccount(BaseModel):
    name: str | None = None
    mask: str | None = None


class CompleteBody(BaseModel):
    session_id: int
    purpose: str  # 'create' or 'update'
    public_token: str = ""
    # From Link's onSuccess metadata; used to refuse duplicates before exchanging.
    institution_id: str | None = None
    accounts: list[LinkAccount] = []
    allow_duplicate: bool = False


@router.post("/link/token/create")
def link_token_create(
    body: CreateTokenBody, conn=Depends(get_conn), settings=Depends(get_settings), gateway=Depends(get_gateway)
):
    try:
        session_id, link_token = items.start_create_session(conn, settings, gateway, body.kind, body.consent)
    except items.ConsentError as exc:
        return json_error(str(exc), 403)
    except ValueError as exc:
        return json_error(str(exc), 400)
    except PlaidError as exc:
        return json_error(f"Plaid error: {exc.message}", 502)
    return {"session_id": session_id, "link_token": link_token}


@router.post("/items/{item_id}/link-token")
def link_token_update(
    item_id: int, accounts: int = 0, conn=Depends(get_conn), settings=Depends(get_settings),
    gateway=Depends(get_gateway), cipher=Depends(get_cipher),
):
    """Update mode: repair a login, or (?accounts=1) add/remove shared accounts. Never a new Item."""
    try:
        session_id, link_token = items.start_update_session(
            conn, settings, gateway, cipher, item_id, account_selection=bool(accounts)
        )
    except (LookupError, ValueError) as exc:
        return json_error(str(exc), 400)
    except PlaidError as exc:
        return json_error(f"Plaid error: {exc.message}", 502)
    return {"session_id": session_id, "link_token": link_token}


@router.post("/link/complete")
def link_complete(
    body: CompleteBody, conn=Depends(get_conn), settings=Depends(get_settings),
    gateway=Depends(get_gateway), cipher=Depends(get_cipher),
):
    try:
        if body.purpose == "update":
            items.complete_update(conn, gateway, cipher, body.session_id)
            return {"redirect": "/setup?" + urlencode({"msg": "Connection repaired."})}
        result = items.complete_create(
            conn, settings, gateway, cipher, body.session_id, body.public_token,
            institution_id=body.institution_id,
            link_accounts=[a.model_dump() for a in body.accounts],
            allow_duplicate=body.allow_duplicate,
        )
    except items.DuplicateItemError as exc:
        return json_error(str(exc), 409)
    except PlaidError as exc:
        return json_error(f"Plaid error: {exc.message}", 502)
    except LookupError as exc:
        return json_error(str(exc), 400)

    msg = f"Connected {result['institution_name']}."
    if result["duplicate"]:
        msg += " Note: this institution was already connected, so this is a second Item for it."
    return {"redirect": "/setup?" + urlencode({"msg": msg})}


# ---------------------------------------------------------------- item actions (HTML forms)

@router.post("/items/{item_id}/refresh")
def item_refresh(item_id: int, conn=Depends(get_conn), gateway=Depends(get_gateway), cipher=Depends(get_cipher)):
    items.refresh_accounts(conn, gateway, cipher, item_id)
    return redirect("/setup", msg="Accounts refreshed.")


@router.post("/items/{item_id}/remove")
def item_remove(
    item_id: int, typed_name: str = Form(""),
    conn=Depends(get_conn), gateway=Depends(get_gateway), cipher=Depends(get_cipher),
):
    try:
        items.remove_item(conn, gateway, cipher, item_id, typed_name)
    except ValueError as exc:
        return redirect("/setup", error=str(exc))
    except PlaidError as exc:
        return redirect("/setup", error=f"Plaid refused the removal: {exc.message}")
    return redirect("/setup", msg="Item removed. Its history is kept; its accounts are hidden.")


@router.post("/items/{item_id}/simulate-login-required")
def item_simulate_login_required(
    item_id: int, conn=Depends(get_conn), settings=Depends(get_settings),
    gateway=Depends(get_gateway), cipher=Depends(get_cipher),
):
    if settings.is_production:
        return redirect("/setup", error="That test action is Sandbox-only.")
    items.simulate_login_required(conn, settings, gateway, cipher, item_id)
    return redirect("/setup", msg="Simulated an expired login. Use Reconnect to repair it.")
