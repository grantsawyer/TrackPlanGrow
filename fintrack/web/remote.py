"""Phone access (fintrack/remote.py, gate.py): the passcode page, and the checks web/app.py applies to phones.

Phones reach fintrack only through the gate, which hands each connection (after the phone's client certificate) to
the phone listener on 127.0.0.1:REMOTE_INNER_PORT. Only requests on that listener are checked here; on the main
listener (this Mac's browser) nothing changes.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from fintrack import remote
from fintrack.config import Settings
from fintrack.web.deps import templates

router = APIRouter()

# Bank connections stay on the Mac: Plaid Link's bank-login redirect is registered for localhost, and a stray tap on a
# phone must never spend a Production Item. Prefixes of web/setup.py's Link and Item routes.
BLOCKED_PREFIXES = ("/connect", "/oauth-return", "/link/", "/items/")
# Importing bank files is on the Mac too, for now (user's decision 2026-11-13, docs/import-plan.md): the Import page
# shows phones a note, and its actions (all under /import/) are refused.
IMPORT_PREFIX = "/import/"
# Reachable from a phone before the passcode: the passcode page, the stylesheet / icons it uses, and the read-only
# copy's worker (web/offline.py; code with no data in it).
OPEN_PREFIXES = ("/remote-login", "/static/", "/offline-sw.js")


def via_phone(request: Request) -> bool:
    """Did this request arrive on the phone listener (from the gate)? Decided by the socket it came in on (the
    listening address and port), never by a header."""
    settings: Settings = request.app.state.settings
    server = request.scope.get("server")
    return bool(settings.remote_access and server and tuple(server) == ("127.0.0.1", settings.remote_inner_port))


def gate(request: Request) -> Response | None:
    """For a phone's request: a refusal or a redirect to the passcode page, or None to let it through."""
    settings: Settings = request.app.state.settings
    path = request.url.path
    if path.startswith(BLOCKED_PREFIXES):
        return PlainTextResponse("Connecting and managing banks is available on the Mac only.", status_code=403)
    if path.startswith(IMPORT_PREFIX):
        return PlainTextResponse("Importing files is available on the Mac only.", status_code=403)
    if path.startswith(OPEN_PREFIXES) or remote.valid_session(settings, request.cookies.get(remote.SESSION_COOKIE)):
        return None
    if request.method == "GET" and "hx-request" not in request.headers:
        target = path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/remote-login?next={_safe_next(target)}", status_code=303)
    # A form post or HTMX request after the 30 days ran out: htmx follows HX-Redirect to the passcode page.
    return PlainTextResponse("Enter your passcode again", status_code=401, headers={"HX-Redirect": "/remote-login"})


def _safe_next(target: str | None) -> str:
    """Only a path on this site (never //other.host or a full URL)."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def _page(request: Request, next_url: str, error: str = "", status: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(request, "remote_login.html",
                                      {"next_url": next_url, "error": error}, status_code=status)


@router.get("/remote-login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    if not via_phone(request):
        return RedirectResponse("/", status_code=303)   # on the Mac there's nothing to sign in to
    return _page(request, _safe_next(next))


@router.post("/remote-login", response_class=HTMLResponse)
async def login(request: Request):
    if not via_phone(request):
        return RedirectResponse("/", status_code=303)
    settings: Settings = request.app.state.settings
    form = await request.form()
    next_url = _safe_next(str(form.get("next") or "/"))
    throttle: remote.Throttle = request.app.state.remote_throttle
    if throttle.locked():
        return _page(request, next_url, "Too many wrong passcodes. Try again in 15 minutes.", 429)
    if not remote.passcode_set(settings):
        return _page(request, next_url, "No passcode yet. On the Mac, run: fintrack remote passcode", 403)
    if not remote.check_passcode(settings, str(form.get("passcode") or "")):
        throttle.fail()
        return _page(request, next_url, "Wrong passcode.", 401)
    throttle.clear()
    response = RedirectResponse(next_url, status_code=303)
    # Lax, not Strict: Chrome on Android treats opening the installed app from the home screen as a visit from outside
    # the site and leaves Strict cookies off it, so every launch asked for the passcode again (seen 2026-11-11). Lax
    # still keeps the cookie off other sites' posts and background requests; cross-site posts are refused by the
    # Origin check anyway (web/app.py), and GETs never change anything.
    response.set_cookie(remote.SESSION_COOKIE, remote.new_session(settings), max_age=remote.SESSION_DAYS * 86400,
                        path="/", secure=True, httponly=True, samesite="lax")
    return response
