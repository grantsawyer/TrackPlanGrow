"""FastAPI application factory: middleware, shared dependencies, and routers."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from fintrack import db, remote
from fintrack.config import Settings
from fintrack.crypto import TokenCipher
from fintrack.quotes import QuoteGateway
from fintrack.demo import make_gateway
from fintrack.plaid_client import PlaidGateway
from fintrack.web.deps import get_conn
from fintrack.web.remote import gate as phone_gate, via_phone

WEB_DIR = Path(__file__).parent
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def content_security_policy(settings: Settings, phone: bool = False) -> str:
    """The browser may load resources only from this app, plus exactly what
    Plaid Link needs (its script, its frame, and the API host for this env).
    Plaid forbids self-hosting the Link script, so it can't be vendored.

    `phone`: a phone's pages (the phone listener) may frame this app's own pages, and only those: a swipe shows the page
    beside in a frame (ui.js; user's request 2026-11-11). Other sites still can't frame it (clickjacking), and the Mac's
    pages keep frame-ancestors 'none'."""
    plaid_api = "https://production.plaid.com/" if settings.is_production else "https://sandbox.plaid.com/"
    return "; ".join([
        "default-src 'self'",
        "script-src 'self' https://cdn.plaid.com/link/v2/stable/link-initialize.js",
        "frame-src 'self' https://cdn.plaid.com/" if phone else "frame-src https://cdn.plaid.com/",
        f"connect-src 'self' {plaid_api}",
        "frame-ancestors 'self'" if phone else "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'self'",
    ])


def create_app(
    settings: Settings,
    gateway_factory: Callable[[Settings], PlaidGateway] = make_gateway,   # Sandbox also serves the demo data (demo.py)
    cipher_factory: Callable[[Path], TokenCipher] = TokenCipher,
    quotes_factory: Callable[[Settings], QuoteGateway | None] | None = None,
) -> FastAPI:
    """`gateway_factory` / `cipher_factory` / `quotes_factory` let tests swap in fakes."""
    # Interactive API docs are off: nothing here is meant to be called by other tools.
    app = FastAPI(title="fintrack", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.gateway_factory = gateway_factory
    app.state.cipher_factory = cipher_factory
    # Live quotes (Alpaca) only when both keys are configured; otherwise Plaid prices only.
    app.state.quotes_factory = quotes_factory or (
        lambda s: QuoteGateway(s.alpaca_key_id, s.alpaca_secret_key) if s.quotes_enabled else None)
    app.state.remote_throttle = remote.Throttle()   # wrong passcodes from phones (web/remote.py)
    csp = content_security_policy(settings)
    phone_csp = content_security_policy(settings, phone=True)

    # --- Protection against other websites reaching this local server ---
    # 1. Host check blocks DNS-rebinding (an attacker's domain resolving to 127.0.0.1). With phone access on, your
    #    home's public address (REMOTE_HOST, as phones dial it) is a name too (remote.py).
    hosts = ["localhost", "127.0.0.1"] + ([settings.remote_host] if settings.remote_access and settings.remote_host else [])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    # 2. Origin check blocks cross-site form posts (CSRF) against state-changing routes,
    #    e.g. a malicious page trying to start a Link session or remove an Item. Each listener accepts only its own
    #    origin: phones' pages (REMOTE_HOST through the gate) post to the phone listener, this Mac's to localhost.
    local_origins = {settings.base_url, f"https://127.0.0.1:{settings.port}"}
    phone_origins = {settings.remote_origin}

    @app.middleware("http")
    async def security(request: Request, call_next):
        phone = via_phone(request)
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin not in (phone_origins if phone else local_origins):
                return PlainTextResponse("Cross-origin request refused", status_code=403)
        # 3. Phones (through the gate): no bank connections, passcode session required.
        refusal = phone_gate(request) if phone else None
        response = refusal or await call_next(request)
        response.headers["Content-Security-Policy"] = phone_csp if phone else csp
        response.headers["X-Content-Type-Options"] = "nosniff"
        # "same-origin", NOT "no-referrer": with no-referrer, browsers send
        # `Origin: null` on ordinary form POSTs, and the Origin check above
        # would refuse our own forms. same-origin still sends no referrer to
        # any other site.
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"  # don't cache financial pages on disk
        return response

    # 4. Compression (outermost, so it sees the finished response). Pages shrink about 10x: a phone over mobile data
    #    and its read-only copy's refresh (about 100 pages; docs/phone-offline.md) need it. BREACH (guessing a secret in
    #    a compressed page from its size) needs a secret in the body and repeated requests the attacker can trigger:
    #    pages carry no secret tokens, the session cookie is SameSite=Lax (left off other sites' background requests),
    #    and the phone gate needs the phone's own certificate.
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    @app.get("/revision", response_class=PlainTextResponse)
    def data_revision(conn=Depends(get_conn)):
        """Current data revision; pages compare it with the one they were rendered from."""
        return str(db.revision(conn))

    # imported here to avoid a circular import
    from fintrack.web import (account_types, accounts, budgets, credit_cards, dashboard, forecast, investments, manual,
                              networth, portfolio, projection, recurring, savings, setup, spending, transaction_edits,
                              transaction_groups, transactions)
    from fintrack.web import remote as remote_page              # not fintrack.remote (keys, certificate, passcode)
    from fintrack.web import transfers as transfers_page       # not fintrack.transfers (the matching itself)
    from fintrack.web import tags as tags_page                 # not fintrack.tags (the tags themselves)
    from fintrack.web import imports as imports_page           # not fintrack.imports (the engine)

    app.include_router(dashboard.router)
    app.include_router(projection.router)
    app.include_router(budgets.router)
    app.include_router(forecast.router)
    app.include_router(recurring.router)
    app.include_router(setup.router)
    app.include_router(manual.router)
    app.include_router(networth.router)
    app.include_router(investments.router)
    app.include_router(portfolio.router)
    app.include_router(savings.router)
    app.include_router(credit_cards.router)
    app.include_router(tags_page.router)
    app.include_router(accounts.router)
    app.include_router(account_types.router)
    app.include_router(spending.router)
    app.include_router(transactions.router)
    app.include_router(transaction_groups.router)
    app.include_router(transaction_edits.router)
    app.include_router(transfers_page.router)
    app.include_router(imports_page.router)
    app.include_router(remote_page.router)
    from fintrack.web import offline                            # the phone's read-only copy (docs/phone-offline.md)
    app.include_router(offline.router)
    return app
