"""Edits to many transactions at once: group edit (category, money lent, tag) and the Categories to review card."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from fintrack import categories, investment_activity, purposes, tags, transfers
from fintrack.web.deps import get_conn
from fintrack.web.transaction_edits import AUTOMATIC, _back, _targets

router = APIRouter()


@router.post("/transactions/bulk-category")
async def bulk_category(request: Request, conn=Depends(get_conn)):
    """Group edit: set (or reset) the category of the checked rows, or of every transaction
    matching the list's filters ("all"). Transfers are re-detected afterwards, like single edits."""
    form = await request.form()
    return_url = form.get("return_url", "/transactions")
    # Its own field name: "category" is also one of the list's filters (carried in hidden fields).
    choice = form.get("apply_category", "")
    if not choice:
        return _back(return_url, error="Choose a category to apply.")
    key = None if choice == AUTOMATIC else choice
    targets = _targets(conn, form)
    if not targets:
        return _back(return_url, error="Select some transactions first.")
    try:
        changed = categories.set_categories(conn, [i for i in targets if i > 0], key)
        changed += investment_activity.set_categories(conn, [-i for i in targets if i < 0], key)
    except ValueError as exc:
        return _back(return_url, error=str(exc))
    transfers.detect_transfers(conn)
    purposes.update(conn)                      # kinds may have changed: purpose links and balances follow
    what = "back to automatic" if key is None else "recategorized"
    return _back(return_url, msg=f"{changed} transaction{'' if changed == 1 else 's'} {what}"
                 + (f" ({len(targets) - changed} already were)" if changed < len(targets) else "") + ".")


@router.post("/transactions/bulk-purpose")
async def bulk_purpose(request: Request, conn=Depends(get_conn)):
    """Group edit: link the checked rows (or every transaction matching the list's filters) to a purpose account, or
    unlink them ("none"). Only transfers can be linked; others are skipped and counted in the message. A link set
    this way is yours, so rules leave it alone (user's request 2026-10-12)."""
    form = await request.form()
    return_url = form.get("return_url", "/transactions")
    choice = form.get("apply_purpose", "")
    if not choice:
        return _back(return_url, error="Choose a purpose account to link to (or None to unlink).")
    account_id = None if choice == "none" else int(choice) if str(choice).isdigit() else -1
    if account_id == -1 or (account_id is not None and account_id not in {p["id"] for p in purposes.accounts(conn)}):
        return _back(return_url, error="That purpose account doesn't exist.")
    targets = _targets(conn, form)
    if not targets:
        return _back(return_url, error="Select some transactions first.")
    changed = skipped = 0
    for transaction_id in targets:
        if transaction_id < 0:
            skipped += 1                        # investment activity: not a bank transfer
            continue
        try:
            changed += purposes.set_link(conn, transaction_id, account_id)
        except purposes.PurposeError:
            skipped += 1                        # not a transfer (or not on a bank account)
    purposes.update(conn)                       # balances follow
    name = "None" if account_id is None else next(p["name"] for p in purposes.accounts(conn) if p["id"] == account_id)
    done = f"{changed} transaction{'' if changed == 1 else 's'} " + ("unlinked" if account_id is None else f"linked to {name}")
    extra = [f"{skipped} skipped: not transfers"] if skipped else []
    if len(targets) - changed - skipped:
        extra.append(f"{len(targets) - changed - skipped} already were")
    return _back(return_url, msg=done + (f" ({'; '.join(extra)})" if extra else "") + ".")


# ---------------------------------------------------------------- the "Categories to review" card (user's request 2026-11-01)

def _to_list(return_url: str) -> str:
    return return_url if re.match(r"/transactions(\?|$)", return_url) else "/transactions"


@router.post("/transactions/category-review/accept")
async def accept_category_review(request: Request, conn=Depends(get_conn)):
    """Dismiss the card: the rows it showed keep their categories."""
    form = await request.form()
    categories.accept_reviewed(conn, [int(i) for i in form.getlist("ids") if str(i).isdigit()])
    return RedirectResponse(_to_list(form.get("return_url", "/transactions")), status_code=303)


# Filters, sorting and bulk edits on the card (user's request 2026-11-14). Its own query parameters (r_q, r_cat,
# r_account, r_sort, r_dir) so they never mix with the list's filters.
REVIEW_SORTS = {
    "date": lambda r: (r["date"], r["id"]),
    "description": lambda r: ((r["description"] or "").lower(), r["date"]),
    "category": lambda r: ((r["category_label"] or "").lower(), r["date"]),
    "account": lambda r: ((r["account_name"] or "").lower(), r["date"]),
    "amount": lambda r: (r["amount_cents"], r["date"]),
}


def review_view(rows: list[dict], params) -> dict:
    """The card's rows as filtered and sorted by its own controls, and what the controls offer."""
    q = (params.get("r_q") or "").strip().lower()
    category, account = params.get("r_cat") or "", params.get("r_account") or ""
    sort = params.get("r_sort") if params.get("r_sort") in REVIEW_SORTS else "date"
    # newest / biggest / A-Z first as each column's own default; r_dir=asc / desc flips it
    default_desc = sort in ("date", "amount")
    desc = {"asc": False, "desc": True}.get(params.get("r_dir") or "", default_desc)
    shown = [r for r in rows
             if (not q or any(q in (r[k] or "").lower() for k in ("description", "category_label", "account_name")))
             and (not category or r["category"] == category)
             and (not account or str(r["account_id"]) == account)]
    shown.sort(key=REVIEW_SORTS[sort], reverse=desc)
    return {
        "rows": shown,
        "q": params.get("r_q") or "", "category": category, "account": account, "sort": sort, "desc": desc,
        "categories": sorted({(r["category"], r["category_label"]) for r in rows}, key=lambda c: c[1].lower()),
        "accounts": sorted({(str(r["account_id"]), r["account_name"]) for r in rows}, key=lambda a: a[1].lower()),
        "filtered": bool(q or category or account),
    }


@router.post("/transactions/category-review/bulk")
async def review_bulk(request: Request, conn=Depends(get_conn)):
    """The card's bulk bar: the checked rows get one category (your choice, so they leave the card), or keep theirs."""
    form = await request.form()
    ids = [int(i) for i in form.getlist("ids") if str(i).isdigit()]
    if ids and form.get("action") == "accept":
        categories.accept_reviewed(conn, ids)
    elif ids and form.get("category"):
        for transaction_id in ids:
            try:
                categories.set_transaction_category(conn, transaction_id, str(form.get("category")))
            except (ValueError, LookupError):
                pass                                  # deleted in another tab
        transfers.detect_transfers(conn)
        purposes.update(conn)
    return RedirectResponse(_to_list(str(form.get("return_url") or "/transactions")), status_code=303)


@router.post("/transactions/category-review/{transaction_id}")
def review_category(transaction_id: int, category: str = Form(""), return_url: str = Form("/transactions"),
                    conn=Depends(get_conn)):
    """A subcategory picked on the card: your choice, as in the edit panel, so the row leaves the card. Back with the card
    open. The dropdown's first entry (the category it has) changes nothing."""
    if category:
        try:
            categories.set_transaction_category(conn, transaction_id, category)
        except (ValueError, LookupError):
            pass                              # deleted in another tab: the card shows what's true
        else:
            transfers.detect_transfers(conn)  # as when saving the panel
            purposes.update(conn)
    return RedirectResponse(_to_list(return_url), status_code=303)


@router.post("/transactions/bulk-tag")
async def bulk_tag(request: Request, conn=Depends(get_conn)):
    """Group edit: add a tag to (creating it if new) or remove one from the checked rows, or every transaction matching
    the list's filters ("all")."""
    form = await request.form()
    return_url = form.get("return_url", "/transactions")
    name, action = form.get("tag_name", ""), form.get("tag_action", "add")
    if not name.strip():
        return _back(return_url, error="Type a tag to add or remove.")
    targets = _targets(conn, form)
    if not targets:
        return _back(return_url, error="Select some transactions first.")
    plural = lambda n: f"{n} transaction{'' if n == 1 else 's'}"    # noqa: E731
    if action == "remove":
        tag = tags.tag_by_name(conn, name)
        if tag is None:
            return _back(return_url, error=f"There's no tag called {name.strip()}.")
        removed = tags.remove(conn, targets, tag.id)
        return _back(return_url, msg=f"Removed {tag.name} from {plural(removed)}.")
    try:
        tag, added = tags.add(conn, targets, name)
    except tags.TagError as exc:
        return _back(return_url, error=str(exc))
    extra = f" ({len(targets) - added} already had it)" if added < len(targets) else ""
    return _back(return_url, msg=f"Tagged {plural(added)} {tag.name}{extra}.")
