"""The phone's read-only copy (docs/phone-offline.md): what a phone saves, and its service worker.

With PHONE_OFFLINE_COPY=on, a phone's pages (the phone listener only; remote.via_phone) carry
`data-offline-copy="on"`, and static/offline-copy.js registers the worker below. While fintrack answers, the worker
saves the pages listed by GET /offline/pages, encrypted, in the phone's browser storage; when it doesn't (the Mac
asleep), it shows them read-only. With the setting off, phones' pages carry "off" and the same script unregisters the
worker and wipes the copy. The Mac's own pages never carry the attribute.

Saved pages are found again by a standard address (`key`): the path, plus `range` on the pages with a date range (a
preset; the From / To fields don't count with one, as on the pages themselves), plus `page` on Transactions after the
first. A page opened with no range shows its default range, so the worker treats the bare path as that preset.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from fintrack import db, spending
from fintrack.config import Settings
from fintrack.web.deps import get_conn, get_settings
from fintrack.web.remote import via_phone
from fintrack.web.transaction_filters import _filters_from, _query
from fintrack.web.transactions import PAGE_SIZE

router = APIRouter()

WORKER_PATH = "/offline-sw.js"   # at the site's root, so the worker's scope can be the whole site
_STATIC = Path(__file__).parent / "static"
_WORKER_FILE = _STATIC / "offline-sw.js"

# Pages with a date range, and the range each opens on when none is chosen (each page's resolve_period default;
# tests/test_offline.py checks them against the rendered pages). Every preset is saved; Custom is not.
RANGE_PAGES = {
    "/transactions": "365",
    "/spending": spending.DEFAULT_RANGE,      # this month
    "/credit-cards": "ytd",
    "/savings": "365",
    "/transfers": "ytd",
    "/networth": "90",
    "/accounts": "365",
}
PRESETS = [key for key in spending.RANGES if key != "custom"]
# Pages without a date range: their default view only. Settings' pages (bank connections, rules, categories) are
# for managing, not reading, and stay out.
PLAIN_PAGES = ["/", "/investments", "/portfolio", "/cash-flow", "/budgets", "/tags", "/recurring", "/projection"]
# Static files that a phone never needs: Plaid Link (bank connections are on the Mac only) and the worker itself.
_STATIC_SKIP = {"link.js", "offline-sw.js"}


def copy_state(request: Request) -> str:
    """For base.html: "on" / "off" on a phone's pages (the script registers or removes the worker), "" on the Mac's."""
    if not via_phone(request):
        return ""
    settings: Settings = request.app.state.settings
    return "on" if settings.phone_offline_copy else "off"


def session_id(request: Request) -> str:
    """The phone's passcode session as the page script sees it: the session's expiry (seconds), the cookie's public
    half (the signature stays in the HttpOnly cookie). Each login has its own, so a new login makes a new key and
    wipes the old copy; past it, the copy is wiped (offline-store.js). "" without a valid session."""
    from fintrack import remote
    settings: Settings = request.app.state.settings
    token = request.cookies.get(remote.SESSION_COOKIE)
    return token.split(".", 1)[0] if remote.valid_session(settings, token) else ""


def _transaction_pages(conn, range_key: str) -> int:
    """How many pages of 100 the Transactions list has for a preset (its own count query, so it can't drift)."""
    where, params = _query(_filters_from({"range": range_key}))
    rows_sql, rows_params = _transactions_source(conn)
    total = conn.execute(f"SELECT COUNT(*) FROM ({rows_sql}) WHERE {where}", rows_params + params).fetchone()[0]
    return max(1, -(-total // PAGE_SIZE))


def _transactions_source(conn):
    from fintrack import investment_activity   # the list's rows: transactions and investment activity
    return investment_activity.as_transactions(conn)


def static_files() -> list[str]:
    """Every static file a saved page may use (CSS, scripts, icons): no data in them, saved unencrypted."""
    return sorted(f"/static/{path.relative_to(_STATIC).as_posix()}" for path in _STATIC.rglob("*")
                  if path.is_file() and path.name not in _STATIC_SKIP and not path.name.startswith("."))


def page_list(conn) -> list[dict]:
    """What a phone saves: each page's `path`, and for a page with a date range its `default` range and the number of
    pages of each preset (`ranges`; 1 except on Transactions). The worker fetches path?range=<key>[&page=<n>]."""
    pages: list[dict] = []
    for path, default in RANGE_PAGES.items():
        ranges = {key: _transaction_pages(conn, key) if path == "/transactions" else 1 for key in PRESETS}
        pages.append({"path": path, "default": default, "ranges": ranges})
    pages += [{"path": path} for path in PLAIN_PAGES]
    # Each account's page (visible accounts only, as on the Accounts page)
    accounts = conn.execute("SELECT id FROM accounts WHERE is_hidden = 0 ORDER BY id").fetchall()
    pages += [{"path": f"/accounts/{row[0]}"} for row in accounts]
    return pages


@router.get(WORKER_PATH)
def worker() -> Response:
    """The worker's code (no data in it): reachable before the passcode, like /static/ (web/remote.py)."""
    return Response(_WORKER_FILE.read_text(encoding="utf-8"), media_type="text/javascript")


@router.get("/offline/pages")
def pages(request: Request, conn=Depends(get_conn), settings: Settings = Depends(get_settings)) -> Response:
    """The list a phone saves, with the data revision (skip saving when unchanged) and when the data last synced
    (the copy's date). Phones only, with the setting on; the passcode session is checked before this (web/remote.py)."""
    if not via_phone(request) or not settings.phone_offline_copy:
        return PlainTextResponse("Not found", status_code=404)
    synced = conn.execute("SELECT MIN(last_synced_at) FROM items WHERE status <> 'removed'").fetchone()[0]
    return JSONResponse({"revision": db.revision(conn), "synced": synced, "pages": page_list(conn),
                         "static": static_files()})
