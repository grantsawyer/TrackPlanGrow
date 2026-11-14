"""Net worth page (Accounts section): headline figure, assets/liabilities, then (user's requests 2026-10-13) the history
chart, the cards per kind of account and the Change in value table, all for the date range and $ / % toggle in the
ribbon (web/accounts.py's balances_context, shared with the Accounts page). The Accounts card moved to Balances that day; holdings are on Investments."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from fintrack import balances, networth, visuals
from fintrack.web import remote
from fintrack.web.accounts import balances_context
from fintrack.web.deps import get_conn, get_settings, templates

router = APIRouter()


@router.get("/networth", response_class=HTMLResponse)
def networth_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    current = networth.current_net_worth(conn)
    # $ and 90 days by default (user's requests 2026-10-13)
    shared = balances_context(request, conn, "/networth", default_show="value", default_range="90")
    period = shared["period"]
    # Compact / Detailed (user's request 2026-10-13): Detailed = double-width cards with recent activity beside each;
    # Detailed by default (user's request 2026-10-13); Compact on a phone (user's request 2026-11-06: two small cards a
    # row there), decided by the listener the request came in on (remote.via_phone), never a header.
    # The links keep the rest of the address; the range form keeps the choice (a hidden field).
    layout = request.query_params.get("cards", "compact" if remote.via_phone(request) else "detailed")
    if layout not in ("compact", "detailed"):
        layout = "detailed"
    layout_links = {v: "/networth?" + urlencode({**request.query_params, "cards": v}) for v in ("compact", "detailed")}
    activity = ({key: balances.recent_activity(conn, key, period.start, period.end) for key, _label, _chart in shared["cards"]}
                if layout == "detailed" else None)
    return templates.TemplateResponse(
        request,
        "networth.html",
        {
            "settings": settings,
            "nw": current,
            # % change of net worth, assets and liabilities over the ribbon's range (user's request 2026-10-13)
            "range_change": networth.range_change(conn, period.start, period.end),
            # Over time follows the ribbon: its date range and the $ / % toggle (user's request 2026-10-13)
            "trend": visuals.networth_trend_range(conn, period.start, period.end, shared["show"]),
            "history_days": len(networth.history(conn)),
            "layout": layout,
            "layout_links": layout_links,
            "activity": activity,
            **shared,
        },
    )
