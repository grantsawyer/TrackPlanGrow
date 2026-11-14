"""Shared request dependencies: DB connection, settings, Plaid gateway, cipher, templates; the redirect helper."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from fintrack import authorized_users, category_icons, db, spending
from fintrack.categories import KIND_LABELS
from fintrack.config import Settings
from fintrack.crypto import TokenCipher
from fintrack.money import format_cents
from fintrack.plaid_client import PlaidGateway

# Navigation: sections in the left rail, each section's pages as tabs in the header.
# A tab owns its path and anything under it (/recurring/7/edit belongs to Recurring).
NAV: list[tuple[str, list[tuple[str, str]]]] = [
    ("Overview", [("Overview", "/")]),
    ("Transactions", [("Transactions", "/transactions"), ("Spending", "/spending")]),
    ("Accounts", [("Net worth", "/networth"), ("Accounts", "/accounts")]),
    ("Investments", [("Investments", "/investments"), ("Portfolio", "/portfolio")]),
    # Savings and Cash flow moved here from Transactions (user's request 2026-10-25); their URLs didn't change
    ("Analysis", [("Savings", "/savings"), ("Cash flow", "/cash-flow"), ("Credit cards", "/credit-cards")]),
    ("Planning", [("Budgets", "/budgets"), ("Tags", "/tags"), ("Recurring", "/recurring"), ("Projection", "/projection")]),
    # Transfers: user's request 2026-10-26; Import (bank files): 2026-11-13, docs/import-plan.md
    ("Settings", [("Setup", "/setup"), ("Import", "/import"), ("Account types", "/account-types"),
                  ("Categories", "/categories"), ("Rules", "/rules"), ("Transfers", "/transfers")]),
]
# Rail icon per section (templates/_icons.html).
NAV_ICONS = {"Overview": "home", "Transactions": "receipt", "Accounts": "wallet", "Investments": "trend",
             "Analysis": "bars", "Planning": "calendar", "Settings": "sliders"}
# The rail can be collapsed to icons; the choice is a first-party cookie so the server
# renders it collapsed from the first paint (ui.js sets it; nothing else is stored in it).
RAIL_COOKIE = "ft_rail"
THEME_COOKIE = "ft_theme"      # "light" / "dark" from the rail's switch; absent = follow the system (2026-10-28)
# Pages that live under a tab without sharing its path prefix.
NAV_ALIASES = {"/manual": "/setup", "/connect": "/setup", "/oauth-return": "/setup",
               "/scenarios": "/projection"}


def _active_tab(path: str) -> str:
    for prefix, tab in NAV_ALIASES.items():
        if path == prefix or path.startswith(prefix + "/"):
            return tab
    best = "/"
    for _, tabs in NAV:
        for _, href in tabs:
            if href != "/" and (path == href or path.startswith(href + "/")) and len(href) > len(best):
                best = href
    return best


def nav_for(path: str) -> dict:
    """Sections with the active one marked, and the active section's tabs. On a tab's own page (not one under it, like
    an account's page), `prev` / `next`: the pages beside it in the bottom bar's order, every section's tabs left to
    right, for a phone's sideways swipe (ui.js; user's request 2026-11-11); None at either end."""
    active = _active_tab(path)
    sections, tabs = [], []
    for label, section_tabs in NAV:
        is_active = any(href == active for _, href in section_tabs)
        sections.append({"label": label, "href": section_tabs[0][1], "active": is_active, "icon": NAV_ICONS[label]})
        if is_active:
            tabs = [{"label": t, "href": h, "active": h == active} for t, h in section_tabs]
    order = [href for _, section_tabs in NAV for _, href in section_tabs]
    prev = next_ = None
    if path == active:
        i = order.index(active)
        prev = order[i - 1] if i > 0 else None
        next_ = order[i + 1] if i + 1 < len(order) else None

    def tabs_into(href):
        """A swipe into another section: that section's tabs (its tabs line slides in with the page); else None."""
        if not href or any(t["href"] == href for t in tabs):
            return None
        section_tabs = next(st for _, st in NAV if any(h == href for _, h in st))
        return [{"label": t, "href": h, "active": h == href} for t, h in section_tabs]

    return {"sections": sections, "tabs": tabs, "prev": prev, "next": next_,
            "prev_tabs": tabs_into(prev), "next_tabs": tabs_into(next_)}


def _offline_copy(request: Request) -> dict:
    from fintrack.web import offline   # here: web/offline.py imports this module
    state = offline.copy_state(request)
    return {"offline_copy": state, "offline_session": offline.session_id(request) if state == "on" else ""}


def _page_context(request: Request) -> dict:
    """Every page gets the data revision it was rendered from (see ui.js), the
    navigation, and when the oldest active connection last synced."""
    conn = db.connect(request.app.state.settings.db_path)
    try:
        last_synced = conn.execute(
            "SELECT MIN(last_synced_at) FROM items WHERE status <> 'removed'"
        ).fetchone()[0]
        return {"revision": db.revision(conn), "nav": nav_for(request.url.path), "last_synced": last_synced,
                "rail_collapsed": request.cookies.get(RAIL_COOKIE) == "collapsed",
                # opened by you: stays open at mid widths too (app.css, 641-1100px; 2026-11-13)
                "rail_expanded": request.cookies.get(RAIL_COOKIE) == "expanded",
                # the phone's read-only copy (web/offline.py): "on" / "off" on phones' pages, "" on the Mac's;
                # with "on", the passcode session the copy's key belongs to
                **_offline_copy(request),
                # light / dark chosen with the rail's switch (user's request 2026-10-28); "" = the system's
                "theme": request.cookies.get(THEME_COOKIE) if request.cookies.get(THEME_COOKIE) in ("light", "dark") else ""}
    finally:
        conn.close()


templates = Jinja2Templates(
    directory=Path(__file__).parent / "templates", context_processors=[_page_context]
)
templates.env.filters["money"] = format_cents


def money_whole(cents: int | None) -> str:
    """Headline figures: 123456789 -> '$1,234,568' (tables keep exact cents)."""
    if cents is None:
        return "—"
    dollars = int((abs(cents) + 50) // 100)          # half-up on the cents
    return f"{'-' if cents < 0 else ''}${dollars:,}"


def month_label(ym: str | None, style: str = "long") -> str:
    """'2026-10' -> 'October 2026' (long) or 'Oct 2026' (short)."""
    if not ym:
        return ""
    try:
        moment = datetime.strptime(ym[:7], "%Y-%m")
    except ValueError:
        return ym
    return f"{moment:%B %Y}" if style == "long" else f"{moment:%b %Y}"


def date_label(iso: str | None) -> str:
    """'2026-10-03' -> 'Oct 3'; other years keep the year: 'Oct 3, 2025'."""
    if not iso:
        return ""
    try:
        day = datetime.strptime(str(iso)[:10], "%Y-%m-%d")
    except ValueError:
        return str(iso)
    if day.year == datetime.now().year:
        return f"{day:%b} {day.day}"
    return f"{day:%b} {day.day}, {day.year}"


templates.env.filters["money_whole"] = money_whole
templates.env.filters["month_label"] = month_label
templates.env.filters["date_label"] = date_label


def localtime(utc_text: str | None) -> str:
    """SQLite CURRENT_TIMESTAMP (UTC, 'YYYY-MM-DD HH:MM:SS') -> 'Oct 3, 11:50 AM' local time."""
    if not utc_text:
        return "never"
    try:
        moment = datetime.strptime(utc_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone()
    except ValueError:
        return utc_text
    return f"{moment:%b} {moment.day}, {moment:%I:%M %p}".replace(" 0", " ")


templates.env.filters["localtime"] = localtime


def epoch_localtime(seconds: float | None) -> str:
    """Unix seconds -> 'Oct 5, 2:31 PM' local time (same style as `localtime`)."""
    if not seconds:
        return ""
    moment = datetime.fromtimestamp(seconds)
    return f"{moment:%b} {moment.day}, {moment:%I:%M %p}".replace(" 0", " ")


templates.env.filters["epoch_localtime"] = epoch_localtime


def category_name(c) -> str:
    """A category row's name for pickers: 'Rent & utilities › Rent' for a subcategory."""
    try:
        parent = c["parent_label"]
    except (KeyError, IndexError):
        parent = None
    return f"{parent} › {c['label']}" if parent else c["label"]


templates.env.filters["category_name"] = category_name


def category_option(c) -> str:
    """A category row's text in a drop-down: a subcategory's own name, indented under its parent (user's request 2026-11-02).
    Non-breaking spaces, because native drop-downs ignore padding on an <option> and collapse ordinary spaces."""
    try:
        return f"\u00a0\u00a0\u00a0\u00a0{c['label']}" if c["parent_label"] else c["label"]
    except (KeyError, IndexError):
        return c["label"]


templates.env.filters["category_option"] = category_option


def by_kind(rows) -> list[tuple[str, list]]:
    """categories.list_categories' rows as [(bucket's heading, its rows)], for a drop-down's <optgroup>s (user's request
    2026-11-01): Spending, Transfers, Income, in the order they're already sorted; a subcategory goes with its parent."""
    groups: dict[str, list] = {}
    for c in rows:
        groups.setdefault(KIND_LABELS.get(c["group_kind"], c["group_kind"]), []).append(c)
    return list(groups.items())


templates.env.filters["by_kind"] = by_kind


def _authorized_user(row) -> bool:
    """`{% if t is authorized_user %}`: a transaction on an authorized user's card (authorized_users.py)."""
    try:
        return authorized_users.is_authorized_user(row["account_owner"], row["account_mask"])
    except (KeyError, IndexError):
        return False


templates.env.tests["authorized_user"] = _authorized_user
# The category icon library (category_icons.py), drawn by _icons.html's category_icon macro.
templates.env.globals["CATEGORY_ICONS"] = category_icons.BY_NAME


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(get_settings(request).db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_gateway(request: Request) -> PlaidGateway:
    # Created per request and only when needed, so pages that don't call
    # Plaid still work before keys are configured.
    return request.app.state.gateway_factory(get_settings(request))


def get_quotes(request: Request):
    """Live quote gateway (Alpaca), or None when the keys aren't configured."""
    return request.app.state.quotes_factory(get_settings(request))


def get_cipher(request: Request) -> TokenCipher:
    return request.app.state.cipher_factory(get_settings(request).home)


def redirect(path: str, **params: str) -> RedirectResponse:
    """303 redirect after a form POST, with an optional message in the query string."""
    query = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"{path}{query}", status_code=303)


def back_with_range(path: str, range_key: str, start: str, end: str, change, done: str) -> RedirectResponse:
    """Run `change`, then return to a date-range page (Transfers, Credit cards) with its range kept and a message
    (or the error)."""
    keep = {"range": range_key} if range_key in spending.RANGES and range_key != "custom" else {
        "range": "custom", "from": spending.iso_or_blank(start), "to": spending.iso_or_blank(end)}
    try:
        change()
    except ValueError as exc:
        return redirect(path, **keep, error=str(exc))
    return redirect(path, **keep, msg=done)
