"""Editing one transaction (HTMX row swaps): the edit panel and Save, category, transfer, tags, recurring, reward bonus,
reviewing a bank removal, deleting one you added; the row helpers the other Transactions modules share."""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from fintrack import (ledger, authorized_users, categories, category_icons, investment_activity, logos, purposes, recurring,
                      manual_transactions, removals, rewards, rules, tags, transfers)
from fintrack.web.deps import get_conn, templates
from fintrack.web.transaction_filters import _filters_from, _query

router = APIRouter()


def _logo_context(conn) -> dict:
    """What each row's logo cell shows (_txn_row.html): a transfer's other side (logos.transfer_logos), cached merchant
    logos by logo address and by merchant (logos.lookup), else its category's icon (category_icons.resolve)."""
    by_url, by_merchant = logos.lookup(conn)
    return {"logos_by_url": by_url, "logos_by_merchant": by_merchant, "transfer_logos": logos.transfer_logos(conn),
            "category_icon_of": category_icons.resolve(conn)}


def _account_columns(conn, rows, view: str) -> dict:
    """A card's page shows each transaction's rewards in a column of its own (user's request 2026-10-26) when the card
    is set up: `rewards_col` = its unit, `rewards_of` = {id: rewards}; and, for an account with authorized users' cards,
    which card each was on (`card_col`, user's request 2026-10-29). A row, edit panel or rule panel returned alone
    gets them too, so its cells line up with the table's."""
    if view != "account" or not rows:
        return {}
    account_id = rows[0]["account_id"]
    columns = {"card_col": True} if account_id in authorized_users.cards_by_account(conn) else {}
    card = rewards.all_rewards(conn).get(account_id)
    if card is not None:
        columns.update({"rewards_col": card.unit,
                        "rewards_of": rewards.transaction_rewards(conn, account_id, [r["id"] for r in rows])})
    return columns


def _shown(conn, transaction_id: int):
    """One row as the list shows it: a transaction (transactions_effective), or investment activity under its negative
    id (investment_activity.as_transactions; editable since 2026-10-31). None if there's no such row."""
    if transaction_id > 0:
        return conn.execute("SELECT * FROM transactions_effective WHERE id = ?", (transaction_id,)).fetchone()
    sql, params = investment_activity.as_transactions(conn)
    return conn.execute(f"SELECT * FROM ({sql}) WHERE id = ?", params + [transaction_id]).fetchone()


def _activity_of(conn, rows) -> dict:
    """{negative id: investment_activity.details row} for the activity among these rows."""
    found = investment_activity.details(conn, [-r["id"] for r in rows if r["id"] < 0])
    return {-i: r for i, r in found.items()}


def _ids(form) -> list[int]:
    """Group edit's checked rows: transaction ids, and investment activity's negative ones."""
    return [int(i) for i in form.getlist("ids") if re.fullmatch(r"-?\d+", str(i)) and int(i) != 0]


def _current_path(request: Request) -> str:
    """The page an HTMX request came from, as a path and query ("" if none)."""
    current = urlsplit(request.headers.get("HX-Current-URL", ""))
    return current.path + ("?" + current.query if current.query else "")


def _reload_with_review_open(request: Request, **message: str) -> HTMLResponse:
    """An edit from the Categories to review card: the page again, the card open. The row may leave the card and is
    probably in the list below too, so swapping one row in place wouldn't do."""
    current = urlsplit(request.headers.get("HX-Current-URL", "/transactions"))
    kept = [(k, v) for k, v in parse_qsl(current.query) if k not in ("review", "msg", "error")]
    path = current.path if re.fullmatch(r"/transactions", current.path) else "/transactions"
    return HTMLResponse("", headers={"HX-Redirect": path + "?" + urlencode([*kept, ("review", "1"), *message.items()])})


def _row(request: Request, conn, transaction_id: int, view: str = "") -> HTMLResponse:
    """One row, as the list it's in shows it: `view` "account" = a Balances account page's table (no select box, no
    account column; user's request 2026-10-13), else the Transactions page's; "review" = the Categories to review card:
    the page reloads instead."""
    if view == "review":
        return _reload_with_review_open(request)
    row = _shown(conn, transaction_id)
    return templates.TemplateResponse(
        request, "_txn_row.html", {"t": row, "categories": categories.list_categories(conn), **_logo_context(conn),
                                   "activity_of": _activity_of(conn, [row]),
                                   "recurring_of": recurring.items_of(conn, [row]) if transaction_id > 0 else {},
                                   "bonus_of": rewards.bonus_labels(conn, [row["id"]]), "account_view": view == "account",
                                   "tags_of": tags.tags_of(conn, [row["id"]]),
                                   **_account_columns(conn, [row], view)}
    )


AUTOMATIC = "__auto__"       # group edit: "Back to automatic" (rule or Plaid)


def _back(return_url: str, **message: str) -> RedirectResponse:
    """Back to the list the edit came from (only ever a /transactions URL), with a message."""
    # ... or a Balances account page, whose rows are edited the same way (user's request 2026-10-13)
    base = return_url if re.match(r"/transactions|/accounts/\d+(\?|$)", return_url) else "/transactions"
    base = re.sub(r"([?&])(msg|error)=[^&]*&?", r"\1", base).rstrip("?&")
    return RedirectResponse(base + ("&" if "?" in base else "?") + urlencode(message), status_code=303)


REMOVAL_ACTIONS = {
    "use": (removals.use_return, "Kept the returned transaction, with your changes."),
    "not_same": (removals.not_same, "Not the same: the match is forgotten."),
    "keep": (removals.keep, "Kept."),
    "delete": (removals.delete, "Deleted."),
}


@router.post("/transactions/{transaction_id}/removal")
def review_removal(transaction_id: int, action: str = Form(""), return_url: str = Form("/transactions"),
                   conn=Depends(get_conn)):
    """Your decision on a transaction the bank removed (removals.py): use its return, not the same, keep, delete.
    Then transfer pairs and money-lent values are worked out again, as after other edits."""
    if action not in REMOVAL_ACTIONS:
        return _back(return_url, error="Choose what to do with it.")
    change, done = REMOVAL_ACTIONS[action]
    try:
        change(conn, transaction_id)
    except LookupError as exc:
        return _back(return_url, error=str(exc))
    transfers.detect_transfers(conn)
    purposes.update(conn)
    return _back(return_url, msg=done)


def _targets(conn, form) -> list[int]:
    """Group edit's rows: the checked ones, or ("all") every row the list's filters find, investment activity included
    (negative ids)."""
    if form.get("all") != "1":
        return _ids(form)
    sql, source_params = investment_activity.as_transactions(conn)
    where, params = _query(_filters_from(form))
    return [r[0] for r in conn.execute(f"SELECT id FROM ({sql}) WHERE {where}", source_params + params)]


# ---------------------------------------------------------------- edit panel (milestone 14)

@router.get("/transactions/{transaction_id}/edit", response_class=HTMLResponse)
def edit_panel(request: Request, transaction_id: int, view: str = "", conn=Depends(get_conn)):
    """The panel under a row (clicking the row opens it): description, category, transfer
    matching, and the way into "create a rule"."""
    t = _shown(conn, transaction_id)
    if t is None:
        return HTMLResponse("", status_code=404)
    if transaction_id < 0:
        # investment activity (user's request 2026-10-31): description, category and tags; Plaid's own details below
        return templates.TemplateResponse(request, "_txn_edit.html", {
            "t": t, "categories": categories.list_categories(conn), "activity": _activity_of(conn, [t])[t["id"]],
            # added by you or imported: yours to delete (ledger.py; docs/parity-plan.md)
            "activity_deletable": ledger.is_yours(conn, -transaction_id),
            "tags_here": tags.tags_of(conn, [t["id"]]).get(t["id"], []), "all_tags": tags.active_tags(conn),
            "account_view": view == "account"})
    return templates.TemplateResponse(request, "_txn_edit.html",
                                      {"t": t, "categories": categories.list_categories(conn),
                                       # purpose accounts (money lent etc.) a transfer can be linked to (purposes.py)
                                       "purposes": purposes.accounts(conn), "purpose_link": purposes.link_of(conn, t["id"]),
                                       # the raw bank text (migration 0030), to find text for a destination rule
                                       "original_description": conn.execute(
                                           "SELECT original_description FROM transactions WHERE id = ?", (t["id"],)).fetchone()[0],
                                       **_recurring_context(conn, t),
                                       # a reward bonus on a card you've set up (rewards.py, user's request 2026-10-26)
                                       "reward_unit": rewards.unit_for_transaction(conn, t["id"]),
                                       "reward_bonus": rewards.bonus_of(conn, t["id"]),
                                       # rewards a credit used (a statement credit, a manual rebate; 2026-11-04), and
                                       # whether you added it (it can be deleted; manual_transactions.py)
                                       "rewards_used": rewards.rewards_used(conn, [t["id"]]).get(t["id"]),
                                       "is_manual": manual_transactions.is_manual(conn, t["id"]),
                                       "imported": manual_transactions.is_imported(conn, t["id"]),
                                       "deletable": manual_transactions.deletable(conn, t["id"]),
                                       "manual_pair": manual_transactions.pair_of(conn, t["id"]),
                                       # a bank charge partly paid with rewards: (value, rewards used) (2026-11-04)
                                       "rewards_part": manual_transactions.part_of(conn, t["id"]),
                                       # a rewards redemption's other line, and the points it used (on either line)
                                       **_pair_context(conn, t["id"]),
                                       # where it happened, when Plaid says (migration 0049)
                                       "location": location_label(t),
                                       # its tags and every tag's name for suggestions (tags.py, 2026-10-28)
                                       "tags_here": tags.tags_of(conn, [t["id"]]).get(t["id"], []),
                                       "all_tags": tags.active_tags(conn),
                                       "account_view": view == "account", "panel_view": view,
                                       # back here after saving a rule from this panel
                                       "rule_return": _current_path(request),
                                       **_account_columns(conn, [t], view)})


def location_label(t) -> str:
    """Where it happened, for the edit panel (migration 0049; user's request 2026-10-28): "Store 1234 · 1 Main St,
    Springfield, IL 62701", the parts Plaid sent; the country only when it isn't the US. "" without a location."""
    keys = t.keys()
    get = lambda k: t[k] if k in keys else None      # noqa: E731
    place = ", ".join(p for p in (get("location_address"), get("location_city"),
                                  " ".join(p for p in (get("location_region"), get("location_postal_code")) if p))
                      if p)
    country = get("location_country")
    if country and country != "US":
        place = f"{place}, {country}" if place else country
    store = get("location_store_number")
    return " · ".join(p for p in (f"Store {store}" if store else "", place) if p)


def _recurring_context(conn, t) -> dict:
    """The edit panel's Recurring line (user's request 2026-10-16): the confirmed item this transaction belongs to, or
    "you marked it not recurring", else the confirmed items of its direction to add it to and the schedules for a new one."""
    linked, item_id = recurring.link_of(conn, t["id"])
    return {"recurring_item": recurring.items_of(conn, [t]).get(t["id"]),
            "recurring_excluded": linked and item_id is None,
            "recurring_choices": [i for i in recurring.list_items(conn, "confirmed")
                                  if (i["amount_cents"] > 0) == (t["amount_cents"] > 0)],
            "recurring_frequencies": recurring.FREQUENCY_LABELS}


@router.post("/transactions/{transaction_id}/bonus", response_class=HTMLResponse)
def set_bonus(request: Request, transaction_id: int, action: str = Form("save"), bonus: str = Form(""),
              view: str = Form(""), conn=Depends(get_conn)):
    """The edit panel's Reward bonus line (user's request 2026-10-26): add or change a one-off bonus on this
    transaction, or remove it. Returns the row; a bonus it can't read goes back to the panel's message instead."""
    if conn.execute("SELECT 1 FROM transactions WHERE id = ?", (transaction_id,)).fetchone() is None:
        return HTMLResponse("", status_code=404)
    try:
        if action == "remove":
            rewards.delete_bonus(conn, transaction_id)
        else:
            unit = rewards.unit_for_transaction(conn, transaction_id)
            if unit is None:
                raise ValueError("Set up the card's rewards first.")
            rewards.set_bonus(conn, transaction_id, rewards.parse_bonus(unit, bonus))
    except ValueError as exc:
        return HTMLResponse(html.escape(str(exc)), headers={"HX-Retarget": f"#bonus-msg-{transaction_id}",
                                                            "HX-Reswap": "innerHTML"})
    return _row(request, conn, transaction_id, view)


# ---------------------------------------------------------------- transactions you add (user's request 2026-11-04)

def _pair_context(conn, transaction_id: int) -> dict:
    """For the edit panel of a rewards redemption's line: the other line (`pair_other`) and the rewards it used
    (`pair_used`, from the credit), shown on both and editable on the credit."""
    other = manual_transactions.pair_of(conn, transaction_id)
    if other is None:
        return {"pair_other": None, "pair_used": None}
    used = rewards.rewards_used(conn, [transaction_id, other])
    return {"pair_other": conn.execute("SELECT id, date, description, amount_cents FROM transactions_effective WHERE id = ?",
                                       (other,)).fetchone(),
            "pair_used": next(iter(used.values()), None)}


@router.post("/transactions/{transaction_id}/delete")
def delete_manual(transaction_id: int, conn=Depends(get_conn)):
    """Delete one you added (and its rewards pair) or one imported; the page reloads, as two rows may go."""
    try:
        manual_transactions.delete(conn, transaction_id)
    except ValueError as exc:
        return HTMLResponse(html.escape(str(exc)), status_code=400)
    rules.run(conn)
    return HTMLResponse("", headers={"HX-Refresh": "true"})


@router.post("/activity/{activity_id}/delete")
def delete_activity(activity_id: int, conn=Depends(get_conn)):
    """Delete investment activity you added or imported (both rows of a two-row one); a manual account's holdings
    follow (ledger.delete). The page reloads."""
    from fintrack import manual_positions
    try:
        ledger.delete(conn, activity_id)
    except (ValueError, manual_positions.PositionError) as exc:
        return HTMLResponse(html.escape(str(exc)), status_code=400)
    return HTMLResponse("", headers={"HX-Refresh": "true"})


@router.post("/transactions/{transaction_id}/recurring", response_class=HTMLResponse)
def set_recurring(request: Request, transaction_id: int, action: str = Form(""), item_id: str = Form(""),
                  frequency: str = Form("monthly"), view: str = Form(""), conn=Depends(get_conn)):
    """The edit panel's Recurring line: add to a confirmed item, make a new one from this transaction, say it isn't part
    of any ("exclude"), or undo that. Returns the row (with or without its recurring icon)."""
    if conn.execute("SELECT 1 FROM transactions WHERE id = ?", (transaction_id,)).fetchone() is None:
        return HTMLResponse("", status_code=404)
    _apply_recurring(conn, transaction_id, action, item_id, frequency)
    return _row(request, conn, transaction_id, view)


def _apply_recurring(conn, transaction_id: int, action: str, item_id: str = "", frequency: str = "monthly",
                     take_category: bool = True) -> None:
    """Add to a confirmed item (taking its category, unless you chose one just now), make a new one, say it isn't part
    of any, or undo that."""
    try:
        if action == "add" and item_id.isdigit():
            recurring.set_link(conn, transaction_id, int(item_id))
            if take_category:
                _take_recurring_category(conn, transaction_id, int(item_id))
        elif action == "create":
            recurring.create_from_transaction(conn, transaction_id, frequency)
        elif action == "exclude":
            recurring.set_link(conn, transaction_id, None)
        elif action == "undo":
            recurring.clear_link(conn, transaction_id)
    except recurring.RecurringError:
        pass                                  # e.g. the item was dismissed in another tab: the row shows the truth
    rules.run(conn)                           # the item's actions (2026-11-03) follow the transaction in or out


def _take_recurring_category(conn, transaction_id: int, item_id: int) -> None:
    """Added to an existing recurring item: the transaction takes the item's category (user's request 2026-10-29), as
    your choice, like picking it in the panel. Nothing changes when the item has none or the transaction already shows
    it (it stays automatic)."""
    if any(a.kind == "set_category" for a in recurring.actions_of(conn, item_id)):
        return                                # the item's own "Set category" applies it (rules.run)
    item = conn.execute("SELECT category_key FROM recurring_items WHERE id = ?", (item_id,)).fetchone()
    shown = conn.execute("SELECT category FROM transactions_effective WHERE id = ?", (transaction_id,)).fetchone()
    if not item or not item[0] or not shown or shown[0] == item[0]:
        return
    try:
        categories.set_transaction_category(conn, transaction_id, item[0])
    except ValueError:
        return                                # the item's category was deleted since
    transfers.detect_transfers(conn)          # as when saving the panel: a category change can make a pair (in)eligible
    purposes.update(conn)


@router.post("/transactions/{transaction_id}/edit", response_class=HTMLResponse)
def save_edit(request: Request, transaction_id: int, description: str = Form(""), category: str = Form(""),
              purpose: str = Form("keep"), view: str = Form(""), new_tag: str = Form(""), recurring_choice: str = Form("", alias="recurring"),
              bonus: str = Form(""), bonus_shown: str = Form(""), rewards_used: str = Form(""),
              rewards_used_shown: str = Form(""), part_value: str = Form(""), part_rewards: str = Form(""),
              part_shown: str = Form(""), conn=Depends(get_conn)):
    """Save the panel: your description (blank = the bank's), category ('' = automatic), tag, recurring and reward bonus.
    Returns the updated row (the panel closes itself, ui.js). Investment activity (a negative id) has the same
    description, category and tag; the rest doesn't apply to it."""
    if transaction_id < 0:
        return _save_activity(request, conn, -transaction_id, description, category, new_tag, view)
    before = conn.execute("SELECT user_category, merchant_name, name, rule_description FROM transactions WHERE id = ?",
                          (transaction_id,)).fetchone()
    if before is None:
        return HTMLResponse("", status_code=404)
    # a bonus it can't read comes back to the panel's message, before anything is saved
    # (`bonus_shown`: the panel had the field, as a blank one arrives the same as none at all: blank removes the bonus)
    bonus_amount, bonus_unit = None, rewards.unit_for_transaction(conn, transaction_id) if bonus_shown else None
    if bonus_unit and bonus.strip():
        try:
            bonus_amount = rewards.parse_bonus(bonus_unit, bonus)
        except ValueError as exc:
            return HTMLResponse(html.escape(str(exc)), headers={"HX-Retarget": f"#bonus-msg-{transaction_id}",
                                                                "HX-Reswap": "innerHTML"})
    # rewards used (a credit on a card you've set up; 2026-11-04), checked the same way; blank = none
    used_amount, used_unit = None, rewards.unit_for_transaction(conn, transaction_id) if rewards_used_shown else None
    if used_unit and rewards_used.strip():
        try:
            used_amount = rewards.parse_rewards_used(used_unit, rewards_used)
        except ValueError as exc:
            return HTMLResponse(html.escape(str(exc)), headers={"HX-Retarget": f"#used-msg-{transaction_id}",
                                                                "HX-Reswap": "innerHTML"})
    # partly paid with rewards (a bank charge; user's request 2026-11-04): saved first, so one it can't read comes back
    # before anything else is saved
    if part_shown:
        try:
            manual_transactions.set_part(conn, transaction_id, part_value, part_rewards)
        except ValueError as exc:
            return HTMLResponse(html.escape(str(exc)), headers={"HX-Retarget": f"#part-msg-{transaction_id}",
                                                                "HX-Reswap": "innerHTML"})
    # The box is prefilled with the shown description: the bank's own wording (unchanged) stays
    # automatic, so later updates to it still show; anything else is yours; blank = the bank's.
    if description.strip() in (before["rule_description"], before["merchant_name"] or before["name"]):
        description = ""
    categories.set_transaction_description(conn, transaction_id, description)
    category_changed = before[0] != (category or None)
    if category_changed:
        categories.set_transaction_category(conn, transaction_id, category or None)
        manual_transactions.follow_category(conn, transaction_id)   # a manual rewards pair keeps one category
        transfers.detect_transfers(conn)      # a category change can make a transfer pair (in)eligible
    # Recurring: "" = leave it; "item:<id>", "new:<frequency>", "exclude", "undo" (user's request 2026-11-01)
    kind, _, value = recurring_choice.partition(":")
    if kind == "item":
        _apply_recurring(conn, transaction_id, "add", value, take_category=not category_changed)
    elif kind == "new":
        _apply_recurring(conn, transaction_id, "create", frequency=value)
    elif kind in ("exclude", "undo"):
        _apply_recurring(conn, transaction_id, kind)
    if bonus_unit:
        if bonus_amount is not None:
            rewards.set_bonus(conn, transaction_id, bonus_amount)
        else:
            rewards.delete_bonus(conn, transaction_id)
    if used_unit:
        if used_amount is not None:
            rewards.set_rewards_used(conn, transaction_id, used_amount)
        else:
            rewards.delete_rewards_used(conn, transaction_id)
    # Purpose account link (shown for transfers only): "" = not linked, an id = linked, "keep" = not on the form
    if purpose != "keep":
        try:
            purposes.set_link(conn, transaction_id, int(purpose) if purpose.isdigit() else None)
        except purposes.PurposeError:
            pass                              # e.g. the category change just made it a non-transfer
    purposes.update(conn)                     # a category change can make it (un)linkable; balances follow
    # A tag typed in the panel and sent with Enter (which saves the panel) is added too; an unusable name is skipped.
    if new_tag.strip():
        try:
            tags.add(conn, [transaction_id], new_tag)
        except tags.TagError:
            pass
    return _row(request, conn, transaction_id, view)


def _save_activity(request: Request, conn, activity_id: int, description: str, category: str, new_tag: str,
                   view: str) -> HTMLResponse:
    """save_edit for investment activity (user's request 2026-10-31): Plaid's wording, unchanged, stays automatic."""
    before = conn.execute("SELECT name FROM investment_transactions WHERE id = ?", (activity_id,)).fetchone()
    if before is None:
        return HTMLResponse("", status_code=404)
    investment_activity.set_description(conn, activity_id, "" if description.strip() == before[0] else description)
    try:
        investment_activity.set_categories(conn, [activity_id], category or None)
    except ValueError:
        pass                                  # deleted in another tab: the row shows what it has
    if new_tag.strip():
        try:
            tags.add(conn, [-activity_id], new_tag)
        except tags.TagError:
            pass
    return _row(request, conn, -activity_id, view)


# ---------------------------------------------------------------- tags (tags.py, user's request 2026-10-28)

def _tag_line(request: Request, conn, transaction_id: int, error: str = "") -> HTMLResponse:
    """The edit panel's Tags line again, plus the row's chips swapped in by id (hx-swap-oob), so the panel stays open
    while you add or remove several."""
    return templates.TemplateResponse(request, "_txn_tag_line.html", {
        "t": {"id": transaction_id}, "tags_here": tags.tags_of(conn, [transaction_id]).get(transaction_id, []),
        "all_tags": tags.active_tags(conn), "tag_error": error, "oob": True})


@router.post("/transactions/{transaction_id}/tags", response_class=HTMLResponse)
def add_tag(request: Request, transaction_id: int, new_tag: str = Form(""), conn=Depends(get_conn)):
    """Tag one transaction from its edit panel: an existing tag, or a new one by that name."""
    if _shown(conn, transaction_id) is None:
        return HTMLResponse("", status_code=404)
    try:
        tags.add(conn, [transaction_id], new_tag)
    except tags.TagError as exc:
        return _tag_line(request, conn, transaction_id, str(exc))
    return _tag_line(request, conn, transaction_id)


@router.post("/transactions/{transaction_id}/tags/remove", response_class=HTMLResponse)
def remove_tag(request: Request, transaction_id: int, tag_id: int = Form(...), conn=Depends(get_conn)):
    tags.remove(conn, [transaction_id], tag_id)
    return _tag_line(request, conn, transaction_id)


@router.post("/transactions/{transaction_id}/category", response_class=HTMLResponse)
def set_category(request: Request, transaction_id: int, category: str = Form(""), view: str = Form(""),
                 conn=Depends(get_conn)):
    """Called by the row's dropdown (HTMX). '' = back to automatic (rule or Plaid; investment activity: by its type)."""
    if transaction_id < 0:
        investment_activity.set_categories(conn, [-transaction_id], category or None)
        return _row(request, conn, transaction_id, view)
    categories.set_transaction_category(conn, transaction_id, category or None)
    # A category change can make a transaction eligible/ineligible for transfer matching (income).
    transfers.detect_transfers(conn)
    return _row(request, conn, transaction_id, view)


@router.post("/transactions/{transaction_id}/transfer", response_class=HTMLResponse)
def set_transfer(request: Request, transaction_id: int, ignore: str = Form("1"), view: str = Form(""),
                 conn=Depends(get_conn)):
    """'Not a transfer' (ignore=1) or 'Allow matching again' (ignore=0)."""
    transfers.set_not_transfer(conn, transaction_id, ignore == "1")
    return _row(request, conn, transaction_id, view)
