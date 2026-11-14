"""Accounts page (/accounts, Accounts section, beside Net worth; named Balances at /balances until 2026-10-14): how your
net worth's value changed over time, by kind of account (user's request 2026-10-12), with a card per kind listing its
accounts and the Summary card. Since 2026-10-13 (user's request) the cards per kind of account and the Change in value
table are on the Net worth page; both pages get their figures from balances_context. Calculations: balances.py, from
the daily net-worth snapshots. Also each account's own page (/accounts/{id}): every account has one page, which since
2026-10-15 (user's request) also holds what the connected investment account's activity page (/accounts/{id}/activity)
and a manual account's page (/manual/{id}) showed; both old addresses redirect here."""

from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from fintrack import (account_names, account_types, authorized_users, balances, cards, categories, contributions, investment_activity, items,
                      live_values, manual, manual_positions, networth, performance, prices, purposes, recurring, removals,
                      rewards, spending, tags, visuals)
from fintrack.web.deps import get_conn, get_quotes, get_settings, redirect, templates

router = APIRouter()


def balances_context(request: Request, conn, path: str, default_show: str = "pct", default_range: str = "365") -> dict:
    """The date range in the ribbon (the Savings / Spending controls; default 365 days) and everything the Balances
    and Net worth pages draw from it: tiles, the Net worth card, the cards per kind of account and the table.
    `path` = the page, for the $ / % toggle's links; `default_show` = the toggle's starting mode (Balances: % change;
    Net worth: $, user's request 2026-10-13); `default_range` = the date range when none is chosen (a spending.RANGES
    key; Balances: 365 days; Net worth: 90 days, user's request 2026-10-13)."""
    period = spending.resolve_period(conn, request.query_params, default=default_range)
    snaps = balances.Snapshots(conn)
    today = date.today()
    table = balances.range_changes(conn, period.start, period.end, today, snaps=snaps)
    # The cards' bars: by day for This / Last month (as on Savings), else the table's months
    by_day = period.mode == "days"
    bars = balances.range_changes(conn, period.start, period.end, today, by_day=True, snaps=snaps) if by_day else table
    whole = balances.summary(conn, period.start, period.end, today, snaps=snaps)
    # The $ / % toggle (Balances' ribbon, user's request 2026-10-12): what the cards' bars show; % change by default.
    # Each button keeps the rest of the address (the date range); the range form keeps the choice (a hidden field).
    show = request.query_params.get("show", default_show)
    if show not in visuals.BALANCE_SHOWS:
        show = default_show
    show_links = {s: f"{path}?" + urlencode({**request.query_params, "show": s}) for s in visuals.BALANCE_SHOWS}
    # A kind's card (and table column) shows only when it held anything in the range; Cash always shows.
    # Property & loans has no card of its own: it is a part of the Net worth card and a table column.
    shown = [g for g, _label in balances.GROUPS
             if g == "cash" or (whole and whole.has(g)) or any(p.has(g) for p in table)]
    return {
        "page_path": path,
        "period": period,
        "ranges": spending.RANGES,
        "table": table,
        "whole": whole,
        "groups": [(g, label) for g, label in balances.GROUPS if g in shown],
        "networth_chart": visuals.networth_change_mix(bars, shown, whole, show),
        "cards": [(g, label, visuals.balance_change_mix(bars, g, whole, show)) for g, label in balances.GROUPS
                  if g in shown and g != "property_loans"],
        "show": show,
        "show_links": show_links,
    }


def accounts_card_context(conn) -> dict:
    """The Accounts card (shared with Overview; on Balances since 2026-10-13, user's request) and the accounts left out
    of net worth (non-USD or no balance), listed under it."""
    from fintrack.web.dashboard import account_groups      # lazy: dashboard imports many page modules

    institutions = [{"item": i, "accounts": items.accounts_for_item(conn, i["id"])} for i in items.active_items(conn)]
    kinds = balances.kind_of_accounts(conn)
    # grouped by kind in the account cards' order (Cash, Retirement, Investment, Other assets, Credit cards; user's
    # request 2026-10-13), not by account type as on the Overview
    groups = balances.regroup_by_kind(
        account_groups(institutions, manual.list_accounts(conn)), kinds)
    return {
        "account_groups": groups,
        # a proportion bar per side, coloured like the Net worth page's cards (user's request 2026-10-13)
        "account_bars": balances.proportion_bars(groups, kinds),
        "excluded": networth.counted_accounts(conn)[1],
    }


def account_cards(conn, shared: dict) -> list[dict]:
    """The Balances page's account cards (user's request 2026-10-13; they replaced the three tiles): one per kind of
    account shown (Property & loans has none), each with its accounts and, for the period in the ribbon, the kind's
    total value at the end and its $ and % change (balances.summary: the same figures as the Net worth page's cards;
    credit cards' % follows the amount owed, opposite colours)."""
    whole = shared["whole"]
    cards = []
    for key, label in shared["groups"]:
        if key == "property_loans":
            continue
        pct = whole.pct(key) if whole else None
        invert = key in visuals.LIABILITY_GROUPS
        cards.append({"key": key, "label": label, "accounts": balances.account_rows(conn, key),
                      "value_cents": whole.value_after(key) if whole else 0,
                      "change_cents": whole.change(key) if whole else 0,
                      "pct": -pct if invert and pct is not None else pct, "invert": invert})
    return cards


def holdings_context(conn, account_id: int, live: dict) -> dict:
    """The Holdings card's data (_holdings_card.html): this account's holdings at live prices where quoted
    (performance.account_holdings) with their totals."""
    holdings = performance.account_holdings(conn, account_id, live)
    return {"holdings": holdings,
            "holdings_value": sum(h.value_cents or 0 for h in holdings),
            "holdings_basis": sum(h.cost_basis_cents for h in holdings if h.cost_basis_cents is not None),
            "any_basis": any(h.cost_basis_cents is not None for h in holdings)}


@router.post("/accounts/{account_id}/destination-rules")
def add_destination_rule(account_id: int, pattern: str = Form(""), conn=Depends(get_conn)):
    try:
        contributions.add_destination_rule(conn, account_id, pattern)
    except ValueError as exc:
        return redirect(f"/accounts/{account_id}", error=str(exc))
    return redirect(f"/accounts/{account_id}", msg="Rule added.")


@router.post("/destination-rules/{rule_id}/delete")
def delete_destination_rule(rule_id: int, account_id: int = Form(...), conn=Depends(get_conn)):
    contributions.delete_destination_rule(conn, rule_id)
    return redirect(f"/accounts/{account_id}", msg="Rule removed.")


@router.get("/accounts/{account_id}", response_class=HTMLResponse)
def account_page(account_id: int, request: Request, conn=Depends(get_conn), settings=Depends(get_settings),
                 quotes=Depends(get_quotes)):
    """One account's page, opened from an Accounts card, Setup or the Overview (user's request 2026-10-13): details,
    holdings (investment accounts), transactions or a manual account's History, its rules, and for a manual account its
    kind, archive and delete (from /manual/{id} since 2026-10-15). Under /accounts, so the Accounts section with its
    Accounts tab."""
    row = conn.execute("SELECT a.*, i.institution_name, i.last_synced_at FROM accounts a"
                       " LEFT JOIN items i ON i.id = a.item_id WHERE a.id = ?", (account_id,)).fetchone()
    if row is None:
        return HTMLResponse("Not found", status_code=404)
    holdings = {}
    if "investment" in (row["type"], row["plaid_type"]):
        # The investment account page's Holdings card (user's request 2026-10-13), at the same live prices: the
        # cached quotes for every held ticker, then the usual revalue (no write when nothing moved) so the balance
        # above agrees with the holdings.

        live = quotes.quotes(prices.held_tickers(conn) + manual_positions.position_tickers(conn)) if quotes else {}
        if live_values.revalue_all(conn, live):
            row = conn.execute("SELECT a.*, i.institution_name, i.last_synced_at FROM accounts a"
                               " LEFT JOIN items i ON i.id = a.item_id WHERE a.id = ?", (account_id,)).fetchone()
        holdings = {"show_holdings": True, **holdings_context(conn, account_id, live)}
        if not row["is_manual"]:                          # destination rules (moved here 2026-10-13)
            holdings["destination_rules"] = contributions.destination_rules(conn, account_id)
    manual_history = manual_history_context(conn, row, quotes) if row["is_manual"] else {}
    if row["is_manual"]:                                 # its value may have just been revalued or relinked
        row = conn.execute("SELECT a.*, i.institution_name, i.last_synced_at FROM accounts a"
                           " LEFT JOIN items i ON i.id = a.item_id WHERE a.id = ?", (account_id,)).fetchone()
    source = (row["manual_institution"] or "Manual") if row["is_manual"] else (row["institution_name"] or "Unknown institution")
    return templates.TemplateResponse(request, "account.html",
                                      {"settings": settings, "account": row, "source": source,
                                       "msg": request.query_params.get("msg"), "error": request.query_params.get("error"),
                                       # posted transactions the bank removed, for your review (removals.py)
                                       "removed": removals.pending_review(conn, account_id),
                                       "takes_transactions": manual.takes_transactions(row),
                                       # its activity list: every account but money lent / property (parity-plan.md)
                                       "shows_activity": manual.takes_transactions(row) or row["type"] == "investment",
                                       "activity_kinds": _activity_kinds(),
                                       **holdings, **manual_history, **account_details(conn, row),
                                       **account_transactions(conn, row, request.query_params)})


def _activity_kinds() -> dict:
    from fintrack.imports import investments
    return {k: v for k, v in investments.ACTION_KINDS.items() if k != "ignore"}


@router.post("/accounts/{account_id}/activity")
def add_activity(account_id: int, kind: str = Form(""), date: str = Form(""), holding: str = Form(""),
                 new_ticker: str = Form(""), quantity: str = Form(""), price: str = Form(""), amount: str = Form(""),
                 description: str = Form(""), conn=Depends(get_conn)):
    """Add activity on a manual investment account (docs/parity-plan.md phase 2): a ledger row, as a connected
    account's activity is; its holdings follow (ledger.py)."""
    from fintrack import ledger
    try:
        ledger.add(conn, account_id, kind, date, holding, new_ticker, quantity, price, amount, description)
    except ValueError as exc:
        return _back_to(account_id, error=str(exc))
    return _back_to(account_id, msg="Activity added.")


def manual_history_context(conn, account, quotes) -> dict:
    """A manual account's History (moved from /manual/{id}, user's request 2026-10-13; _manual_history.html): money lent
    = its linked transfers + adjustments (purposes.entries), else your events (manual_positions.events) or typed values.
    Refreshed first: positions revalued at live prices, money lent relinked (writes only when something changed)."""
    account_id = account["id"]
    if manual_positions.has_positions(conn, account_id):
        tickers = manual_positions.position_tickers(conn, account_id)
        manual_positions.revalue(conn, account_id, quotes.quotes(tickers) if quotes and tickers else {})
    purpose = purposes.is_purpose(manual.get_account(conn, account_id))
    if purpose:
        purposes.update(conn)
    latest = conn.execute("SELECT MAX(date) FROM manual_valuations WHERE account_id = ?", (account_id,)).fetchone()[0]
    return {"show_manual_history": True, "purpose": purpose,
            # from the old /manual/{id} page (2026-10-15): the kind (when no type of yours sets it) and the value's date
            "kinds": manual.KINDS, "kind": manual.kind_of(account), "value_as_of": latest,
            "positions": manual_positions.lines(conn, account_id),
            # its balances only: what activity did is under Transactions (user's request 2026-11-14)
            "events": [e for e in manual_positions.events(conn, account_id) if not e.from_activity],
            # your entries are balances; the rest is activity (docs/parity-plan.md, 2026-11-14)
            "event_types": {"balance": manual_positions.EVENT_TYPES["balance"]}, "event_units": manual_positions.EVENT_UNITS,
            "typed_valuations": [v for v in manual.valuations(conn, account_id)
                                 if v["note"] != manual_positions.VALUATION_NOTE],
            "purpose_entries": purposes.entries(conn, account_id) if purpose else [],
            "purpose_rules": purposes.rules(conn, account_id) if purpose else [],
            "today": date.today().isoformat()}


def account_details(conn, account) -> dict:
    """The account page's top card (user's request 2026-10-13): its type (yours, or the automatic match) with the
    type table to choose from, and for a credit card its current statement (balance, dates, paid or not; cards.py) and
    the account that pays it (the cash-flow forecast's setting, cards.set_payer)."""
    from fintrack import logos

    context = {"types": account_types.tree(conn), "logo": logos.institution_logo_files(conn).get(account["id"]),
               "type_path": account_types.path(conn, account["type_node_id"]),
               "auto_path": account_types.path(conn, account_types.automatic(conn, account)),
               "kind_labels": {k: v.label for k, v in account_types.KINDS.items()}}
    if account["type"] == "credit":
        statement = next((s for s in cards.statements(conn) if s.card_id == account["id"]), None)
        raw = conn.execute("SELECT * FROM card_statements WHERE account_id = ?", (account["id"],)).fetchone()
        auto = cards.automatic_payer(conn)
        due = raw["due_date"] if raw else None
        estimate = next((e for e in cards.estimates(conn) if e.card_id == account["id"]), None)
        context.update({"card": {"statement": statement, "raw": raw, "estimate": estimate,
                                 # a manual card's closing / due days (docs/parity-plan.md, 2026-11-14)
                                 "settings": cards.statement_settings(conn, account["id"]) if account["is_manual"] else None,
                                 "days_to_due": (date.fromisoformat(due) - date.today()).days if due else None,
                                 "payer": cards.payers(conn).get(account["id"], {"pay_from": None, "mode": "auto"}),
                                 "options": cards.payer_options(conn), "auto": auto}})
        # its rewards balances as of dates you enter (user's request 2026-10-26; rewards.py, migration 0044)
        context.update({"reward_balances": rewards.balances(conn, account["id"]),
                        "reward_current": rewards.current_balance(conn, account["id"]),
                        "reward_unit": rewards.balance_unit(conn, account["id"]), "reward_units": rewards.UNITS, "reward_label": rewards.bonus_label,
                        "today_iso": date.today().isoformat()})
        # its bonus points (user's request 2026-11-03; migration 0061)
        context["reward_bonus_points"] = rewards.bonus_points(conn, account["id"])
    return context


def _back_to(account_id: int, **message: str) -> RedirectResponse:
    return redirect(f"/accounts/{account_id}", **message)


# ---------------------------------------------------------------- rewards balances (a credit card's page)

def _balance_change(account_id: int, change, done: str) -> RedirectResponse:
    try:
        change()
    except ValueError as exc:
        return _back_to(account_id, error=str(exc))
    return _back_to(account_id, msg=done)


# ---------------------------------------------------------------- bonus points (user's request 2026-11-03)

@router.post("/accounts/{account_id}/bonus-points")
def add_bonus_points(account_id: int, date_text: str = Form("", alias="date"), amount: str = Form(""),
                     note: str = Form(""), conn=Depends(get_conn)):
    return _balance_change(account_id, lambda: rewards.save_bonus_points(conn, account_id, None, date_text, amount, note),
                           "Bonus saved.")


@router.post("/accounts/{account_id}/bonus-points/{bonus_id}/delete")
def delete_bonus_points(account_id: int, bonus_id: int, conn=Depends(get_conn)):
    return _balance_change(account_id, lambda: rewards.delete_bonus_points(conn, account_id, bonus_id), "Bonus deleted.")


@router.post("/accounts/{account_id}/reward-balances")
def add_reward_balance(account_id: int, as_of: str = Form(""), unit: str = Form("points"), amount: str = Form(""),
                       conn=Depends(get_conn)):
    """Add a rewards balance as of a date (one already on that date is replaced)."""
    return _balance_change(account_id, lambda: rewards.set_balance(
        conn, account_id, as_of, unit, rewards.parse_balance(unit, amount)), "Balance saved.")


@router.post("/accounts/{account_id}/reward-balances/{balance_id}")
def save_reward_balance(account_id: int, balance_id: int, as_of: str = Form(""), unit: str = Form("points"),
                        amount: str = Form(""), conn=Depends(get_conn)):
    """Change a balance's date or amount."""
    return _balance_change(account_id, lambda: rewards.set_balance(
        conn, account_id, as_of, unit, rewards.parse_balance(unit, amount), balance_id), "Balance saved.")


@router.post("/accounts/{account_id}/reward-balances/{balance_id}/delete")
def delete_reward_balance(account_id: int, balance_id: int, conn=Depends(get_conn)):
    return _balance_change(account_id, lambda: rewards.delete_balance(conn, account_id, balance_id), "Balance deleted.")


@router.post("/accounts/{account_id}/name")
def rename_account(account_id: int, name: str = Form(""), conn=Depends(get_conn)):
    """Rename (user's request 2026-10-13). A Plaid account: your name survives syncs, blank = the automatic name
    (account_names.rename). A manual account: its own name, never blank."""
    row = conn.execute("SELECT is_manual FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        return HTMLResponse("Not found", status_code=404)
    try:
        if row["is_manual"]:
            name = " ".join(name.split())
            if not name or len(name) > 60:
                raise ValueError("Give the account a name of 60 characters or fewer.")
            with conn:
                conn.execute("UPDATE accounts SET name = ? WHERE id = ?", (name, account_id))
        else:
            account_names.rename(conn, account_id, name)
    except ValueError as exc:
        return _back_to(account_id, error=str(exc))
    return _back_to(account_id, msg="Name reset." if not name.strip() else "Account renamed.")


@router.post("/accounts/{account_id}/institution")
def set_institution(account_id: int, request: Request, institution: str = Form(""), conn=Depends(get_conn),
                    settings=Depends(get_settings)):
    """A manual account's institution (user's request 2026-10-13); saving looks its logo up right away (Plaid's free
    /institutions/search, no Item; logos.find_manual_institution_logos), and syncs try again for names not found."""
    from fintrack import logos
    from fintrack.web.deps import get_gateway

    row = conn.execute("SELECT is_manual FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None or not row["is_manual"]:
        return _back_to(account_id, error="Only manual accounts take an institution here.")
    name = " ".join(institution.split())
    if len(name) > 60:
        return _back_to(account_id, error="Keep the institution to 60 characters or fewer.")
    with conn:
        conn.execute("UPDATE accounts SET manual_institution = ? WHERE id = ?", (name or None, account_id))
    if name and not conn.execute("SELECT 1 FROM institution_searches WHERE query = ? AND status = 'ok'",
                                 (name.lower(),)).fetchone():
        try:
            gateway = get_gateway(request)
        except Exception:                                 # e.g. Plaid keys not set: the name still saves, no logo
            gateway = None
        if gateway is not None:
            logos.find_manual_institution_logos(conn, settings.home, gateway, only=name)
    return _back_to(account_id, msg="Institution saved." if name else "Institution removed.")


@router.post("/accounts/{account_id}/statement-settings")
def statement_settings(account_id: int, closing_day: str = Form(""), due_day: str = Form(""), minimum: str = Form(""),
                       conn=Depends(get_conn)):
    """A manual card's closing and due days, and the current statement's minimum (user's decision 2026-11-14)."""
    try:
        cards.set_statement_settings(conn, account_id, closing_day, due_day, minimum)
    except ValueError as exc:
        return _back_to(account_id, error=str(exc))
    return _back_to(account_id, msg="Statement saved.")


@router.post("/accounts/{account_id}/statement-date")
def statement_date(account_id: int, statement_date: str = Form(""), conn=Depends(get_conn)):
    """Change the current statement's closing date (blank = the scheduled one); its balance follows."""
    try:
        cards.set_statement_date(conn, account_id, statement_date)
    except ValueError as exc:
        return _back_to(account_id, error=str(exc))
    return _back_to(account_id, msg="Closing date saved.")


@router.post("/accounts/{account_id}/mask")
def set_mask(account_id: int, mask: str = Form(""), conn=Depends(get_conn)):
    """A manual account's number, its last 4 (user's request 2026-11-13): a card's transactions whose card isn't this
    one are an authorized user's (authorized_users.py), so a manual card needs its own to tell them apart. Plaid sets a
    connected account's."""
    row = conn.execute("SELECT is_manual FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None or not row["is_manual"]:
        return _back_to(account_id, error="Only manual accounts take a number here.")
    mask = mask.strip()
    if mask and not authorized_users.is_card_number(mask):
        return _back_to(account_id, error="Enter the last 4 digits (or letters) only.")
    with conn:
        conn.execute("UPDATE accounts SET mask = ? WHERE id = ?", (mask or None, account_id))
    return _back_to(account_id, msg="Number saved." if mask else "Number removed.")


@router.post("/accounts/{account_id}/type")
def set_account_type(account_id: int, node: str = Form(""), conn=Depends(get_conn)):
    """Give the account a type or subtype from your table ("" = automatic; account_types.assign)."""
    try:
        account_types.assign(conn, account_id, int(node) if node.isdigit() else None)
    except account_types.TypeError_ as exc:
        return _back_to(account_id, error=str(exc))
    return _back_to(account_id, msg="Type saved." if node else "Type set to automatic.")


@router.post("/accounts/{account_id}/kind")
def set_manual_kind(account_id: int, kind: str = Form(""), conn=Depends(get_conn)):
    """A manual account's kind (e.g. Other asset -> Property; manual.update_account rebuilds its history). Ignored while
    a type of yours sets it. Moved from /manual/{id} (2026-10-15)."""
    try:
        account = manual.get_account(conn, account_id)
        manual.update_account(conn, account_id, account["name"], kind)
    except (LookupError, manual.ManualError) as exc:
        return _back_to(account_id, error=str(exc))
    return _back_to(account_id, msg="Kind saved.")


@router.post("/accounts/{account_id}/pay-from")
def set_pay_from(account_id: int, pay_from: str = Form("auto"), conn=Depends(get_conn)):
    """The account a card's bills are paid from (the cash-flow forecast's setting; cards.set_payer)."""
    try:
        cards.set_payer(conn, account_id, pay_from)
    except ValueError as exc:
        return _back_to(account_id, error=str(exc))
    return _back_to(account_id, msg="Paying account saved.")


TRANSACTION_ROWS = 200      # an account page lists the newest 200; the Transactions page has the rest


def account_transactions(conn, account, q=None) -> dict:
    """The account page's Transactions card (user's request 2026-10-13): the Transactions page's row format without the
    account column, newest first. Connected accounts: their transactions, filtered by the card's own form (user's
    request 2026-10-29: search, card, category, From / To; the Transactions page's filters, `_filters_from` / `_query`,
    with this account, or one of its cards, as the account); investment accounts list their investment activity the same
    way, edited like the rest (investment_activity.as_transactions, sweeps hidden; user's request 2026-10-31); purpose accounts (money lent): the transfers linked to
    them; other manual accounts: none (their events are on their own page). `more` = the Transactions page's address
    for all of them when there are more than TRANSACTION_ROWS."""
    from fintrack.web.transaction_edits import _activity_of, _logo_context
    from fintrack.web.transaction_filters import SORTS, _filters_from, _query, _url_filters

    q = q or {}
    account_id = account["id"]
    filtering = {}
    if account["type"] == "receivable":
        rows = conn.execute("""SELECT t.* FROM purpose_links l JOIN transactions_effective t ON t.id = l.transaction_id
                               WHERE l.account_id = ? AND t.account_hidden = 0 ORDER BY t.date DESC, t.id DESC LIMIT ?""",
                            (account_id, TRANSACTION_ROWS + 1)).fetchall()
        more = None
    elif not account["is_manual"] or manual.takes_transactions(account) or account["type"] == "investment":
        # (a manual bank account, card or loan holds transactions too: imported or added; 2026-11-13)
        # all dates unless From / To say otherwise; ?card= = one of its cards ("<id>:main" / "<id>:<last 4>")
        cards = authorized_users.cards_by_account(conn).get(account_id, [])
        card = q.get("card", "") if q.get("card", "") in {c.value for c in cards} else ""
        filters = _filters_from({"q": q.get("q", ""), "category": q.get("category", ""), "range": "custom",
                                 "from": q.get("from", ""), "to": q.get("to", ""), "account": card or str(account_id),
                                 "sort": q.get("sort", ""), "dir": q.get("dir", "")})
        where_sql, params = _query(filters)
        # headings sort the whole filtered list before the newest-200 cut (user's request 2026-10-29; SORTS)
        sort, sort_dir = filters["sort"] or "date", filters["dir"] or "desc"
        order = SORTS[sort].format(d="ASC" if sort_dir == "asc" else "DESC")
        # an investment account's rows are its activity (the Transactions page's list source)
        source, source_params = (investment_activity.as_transactions(conn)
                                 if "investment" in (account["type"], account["plaid_type"])
                                 else ("SELECT * FROM transactions_effective", []))
        rows = conn.execute(f"SELECT * FROM ({source}) WHERE {where_sql} ORDER BY {order} LIMIT ?",
                            source_params + params + [TRANSACTION_ROWS + 1]).fetchall()
        more = "/transactions?" + urlencode({**_url_filters(filters), "range": "custom"})
        kept = {k: v for k, v in (("q", filters["q"]), ("card", card), ("category", filters["category"]),
                                  ("from", filters["from"]), ("to", filters["to"])) if v}

        def sort_url(column: str) -> str:
            """A heading: sort by this column, newest / biggest first; again = reverse (as on Transactions)."""
            flip = "asc" if sort == column and sort_dir != "asc" else ""
            return f"/accounts/{account_id}?" + urlencode({**kept, "sort": column, **({"dir": flip} if flip else {})})

        filtering = {"txn_filters": {k: filters[k] for k in ("q", "category", "from", "to")} | {"card": card},
                     "filtered": any((filters["q"], filters["category"], filters["from"], filters["to"], card)),
                     "sort": sort, "sort_dir": sort_dir, "sort_url": sort_url,
                     "date_order": sort == "date" and sort_dir == "desc",
                     "account_cards": cards, "categories": categories.list_categories(conn),
                     # its cards' use (authorized users, user's request 2026-10-29): spending over the last 365 days
                     "card_summaries": authorized_users.card_summaries(
                         conn, account_id, (date.today() - timedelta(days=365)).isoformat())}
    else:
        rows, more = [], None
    shown = rows[:TRANSACTION_ROWS]
    from fintrack.web.transaction_edits import _account_columns
    column = _account_columns(conn, shown, "account")
    return {"transactions": shown, **column, **filtering,
            # a card's rewards balances you entered, as lines among its transactions (user's request 2026-10-26); not
            # while filtering: a balance is the whole account's, so it would read as the filtered rows' own
            # ...nor in another order than newest first (they belong between dates)
            "lines": [("txn", t) for t in shown] if filtering.get("filtered") or not filtering.get("date_order", True) else
                     _with_balances(conn, account, shown, more=len(rows) > TRANSACTION_ROWS),
            "more": more if len(rows) > TRANSACTION_ROWS else None, **_logo_context(conn),
            # the recurring icon, as on Transactions (user's request 2026-10-16)
            "recurring_of": recurring.items_of(conn, [r for r in shown if r["id"] > 0]),
            "bonus_of": rewards.bonus_labels(conn, [r["id"] for r in shown]),
            "tags_of": tags.tags_of(conn, [r["id"] for r in shown]),
            # investment activity's ticker and quantity x price (as on Transactions)
            "activity_of": _activity_of(conn, shown)}


def _with_balances(conn, account, shown, more: bool) -> list:
    """The table's lines, newest first: ("txn", row) and, on a credit card, ("balance", rewards.Balance) for each
    rewards balance you entered. A balance as of a date counts that whole day, so it sits above the day's transactions.
    With more transactions than shown, balances older than the oldest shown are left off (they'd sit out of place)."""
    entered = rewards.balances(conn, account["id"]) if account["type"] == "credit" else []
    if more and shown:
        entered = [b for b in entered if b.as_of >= shown[-1]["date"]]
    lines, pending = [], list(entered)                   # both newest first
    for t in shown:
        while pending and pending[0].as_of >= t["date"]:
            lines.append(("balance", pending.pop(0)))
        lines.append(("txn", t))
    lines.extend(("balance", b) for b in pending)
    return lines


@router.get("/accounts", response_class=HTMLResponse)
def balances_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    shared = balances_context(request, conn, "/accounts")
    return templates.TemplateResponse(
        request,
        "accounts.html",
        {"settings": settings, **shared, "account_cards": account_cards(conn, shared), **accounts_card_context(conn)},
    )


# ---------------------------------------------------------------- old addresses
# The page was /balances (tab "Balances") until 2026-10-14, when the user renamed it Accounts and moved it to /accounts,
# and each account's page from /balances/accounts/{id} to /accounts/{id}. Old links and bookmarks still land.

@router.get("/balances")
def balances_moved(request: Request):
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/accounts{query}", status_code=307)


@router.get("/accounts/{account_id}/activity")
def activity_moved(account_id: int):
    """A connected investment account's activity page was folded into its account page (user's request 2026-10-15),
    which has the same Holdings card and its activity."""
    return RedirectResponse(f"/accounts/{account_id}", status_code=307)


@router.get("/balances/accounts/{account_id}")
def balance_account_moved(account_id: int, request: Request):
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/accounts/{account_id}{query}", status_code=307)
