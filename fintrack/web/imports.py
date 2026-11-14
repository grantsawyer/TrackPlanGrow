"""Import page (Settings section; docs/import-plan.md phase 2, user's request 2026-11-13): a bank's file into an account.

One form: pick the account and the file, and the preview appears (POST /import/preview, swapped in by htmx on every
change). The preview shows the file's first rows with a picker above each column, the questions the file leaves open
(month or day first, which sign is money out, what to do where it overlaps the account's transactions), a picker per
bank category, and every row as it would be added. Each change posts the whole form again, the file included: the
browser keeps the chosen file, so the server never stores it (user's decision: keep nothing). Import posts the same form
to /import/apply, which prepares the plan again from what was posted and imports it as one batch (imports/engine.py);
it's refused until every question is answered. Past imports are listed below with Undo.

Answers in the preview belong to the file and account they were made for (`previewed` = account + the file's sha256):
choosing another file or account starts over. Phones see a note only, and the gate refuses their POSTs to /import/...
(web/remote.py): importing is on the Mac, like connecting a bank.
"""

from __future__ import annotations

import hashlib
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from fintrack import categories, sync
from fintrack.backup import backup_database
from fintrack.imports import engine
from fintrack.imports import layout as lay
from fintrack.imports.readers import MAX_BYTES
from fintrack.web.deps import get_conn, get_settings, redirect, templates
from fintrack.web.remote import via_phone

router = APIRouter()

# The pickers above the file's columns: role -> label ('' = not imported).
ROLE_LABELS = [("", "Ignore"), ("date", "Date"), ("authorized", "Purchase date"), ("description", "Description"),
               ("amount", "Amount"), ("debit", "Debit (out)"), ("credit", "Credit (in)"), ("direction", "Debit / credit"),
               ("balance", "Balance"), ("card", "Card"), ("category", "Bank category"),
               # investment files (phase 5)
               ("symbol", "Symbol"), ("action", "Action"), ("quantity", "Quantity"), ("price", "Price"),
               ("value", "Value")]
PREVIEW_ROWS = 8          # the file's rows shown under the pickers
LIST_ROWS = 400           # rows listed in the preview (the rest are counted)
STATUS_LABELS = {"already": "Imported before", "covered": "Dates already there", "duplicate": "Likely duplicate",
                 "deleted": "Deleted by you"}


class Upload:
    """What the form posted: the file, the account, and the answers (when they were made for this file)."""

    def __init__(self, form) -> None:
        upload = form.get("file")
        self.file_name = getattr(upload, "filename", "") or ""
        self.upload = upload if self.file_name else None
        self.data = b""
        account = str(form.get("account") or "")
        self.account_id = int(account) if account.isdigit() else None
        self.form = form

    async def read(self) -> None:
        if self.upload is not None:
            self.data = await self.upload.read(MAX_BYTES + 1)        # read_table refuses anything over the cap

    @property
    def key(self) -> str:
        return f"{self.account_id}:{hashlib.sha256(self.data).hexdigest()}"

    def answers(self) -> dict:
        """engine.prepare's keyword answers from the form, if they were made for this file and account."""
        f = self.form
        if f.get("previewed") != self.key:
            return {}
        answers: dict = {}
        width = str(f.get("cols") or "")
        if width.isdigit():
            columns: dict[str, int] = {}
            for i in range(int(width)):
                role = str(f.get(f"col_{i}") or "")
                if role in lay.ROLES:
                    if role in columns:
                        label = dict(ROLE_LABELS)[role]
                        raise ValueError(f"{label} is chosen for two columns.")
                    columns[role] = i
            answers.update(columns=columns, exact_columns=True)
        if f.get("dates") in ("mdy", "dmy"):
            answers["date_order"] = f.get("dates")
        if f.get("money_out") in ("1", "-1"):
            answers["sign"] = int(f.get("money_out"))
        if "primary_card" in f:
            answers["primary_card"] = str(f.get("primary_card") or "")
        if f.get("overlap") in engine.OVERLAP_CHOICES:
            answers["overlap"] = f.get("overlap")
        answers["take"] = {int(v) for v in f.getlist("take") if str(v).isdigit()}
        names = {k[len("catname_"):]: str(v) for k, v in f.items() if k.startswith("catname_")}
        answers["categories"] = {name: str(f.get(f"cat_{n}") or "") for n, name in names.items()}
        if f.get("kind") in lay.KINDS:
            answers["kind"] = f.get("kind")
        actions = {k[len("actname_"):]: str(v) for k, v in f.items() if k.startswith("actname_")}
        answers["actions"] = {key: str(f.get(f"act_{n}") or "") for n, key in actions.items()}
        if f.get("add_dividends_shown"):                # the box was on the page: ticked or not
            answers["add_dividends"] = f.get("add_dividends") == "1"
        funds = {k[len("holdname_"):]: str(v) for k, v in f.items() if k.startswith("holdname_")}
        answers["holdings"] = {symbol: str(f.get(f"hold_{n}") or "") for n, symbol in funds.items()}
        as_of = str(f.get("as_of") or "")
        if len(as_of) == 10 and as_of[4] == "-":
            answers["as_of"] = as_of
        return answers


def _category_labels(conn) -> dict[str, str]:
    return {r["key"]: r["label"] for r in categories.list_categories(conn)}


def _category_key(conn, primary: str, detailed: str, split, remaps) -> str:
    """Our category for Plaid's terms, as sync stores it (remaps of deleted categories included)."""
    key = categories.plaid_key(primary, detailed, split)
    return remaps.get(key) or remaps.get(detailed) or key


def _preview(conn, request: Request, up: Upload) -> dict:
    """The preview's context: the plan (or the error), the grid of the file's first rows with their pickers, the
    questions, the bank categories with their mapping, and the rows."""
    ctx: dict = {"previewed": up.key, "roles": ROLE_LABELS, "plan": None, "error": None, "grid": None,
                 "today_iso": date.today().isoformat(),
                 "status_labels": STATUS_LABELS, "list_rows": LIST_ROWS, "kinds": lay.KINDS, "kind": ""}
    if up.account_id is None or not up.data:
        return ctx
    answers: dict = {}
    try:
        answers = up.answers()
        plan = engine.prepare(conn, up.account_id, up.data, up.file_name, **answers)
    except ValueError as exc:                    # ImportFileError, or two columns given the same role
        ctx["error"] = str(exc)
        plan = None
    table, where = engine.peek(up.data, up.file_name)
    if where is not None:
        if plan:
            roles = {i: r for r, i in plan.detection.layout.columns.items()}
        else:
            roles = {i: r for r, i in (answers.get("columns") or {}).items()}
        rows = [r + [""] * (where.width - len(r)) for r in table[where.data_start:where.data_start + PREVIEW_ROWS]]
        ctx["grid"] = {"width": where.width, "header": where.header, "rows": rows, "roles": roles}
    ctx["kind"] = plan.kind if plan else answers.get("kind", "")
    if plan is None:
        return ctx
    if plan.kind != "transactions":
        from fintrack.imports import investments
        family = lay.DATE_FORMATS.get(plan.detection.layout.date_format)
        ctx.update(plan=plan, new=plan.new, action_kinds=investments.ACTION_KINDS, signed=False, date_family=family,
                   date_order=answers.get("date_order") or ("" if "date_order" in plan.detection.questions else family),
                   actions=[{"n": n, "key": k, "kind": v[0], "example": v[1], "count": v[2]}
                            for n, (k, v) in enumerate(sorted(plan.action_keys.items(), key=lambda kv: -kv[1][2]))],
                   listed=sorted((p for p in plan.rows if p.status != "already"),
                                 key=lambda p: (getattr(p.row, "posted", ""), p.row.line), reverse=plan.kind == "activity"),
                   total_value=sum(p.row.value_cents or 0 for p in plan.new if plan.kind == "positions"),
                   # a manual account: which line each fund goes to (a proxy: by dollars; user's request 2026-11-13)
                   funds=[{"n": n, "symbol": symbol, "description": v["description"], "count": v["count"],
                           "chosen": str(v["target"].line_id) if v["target"].line_id is not None else "new"}
                          for n, (symbol, v) in enumerate(sorted(plan.holding_keys.items()))],
                   fund_lines=plan.lines or [])
        return ctx

    split, remaps = categories.split_set(conn), categories.remaps(conn)
    labels = _category_labels(conn)
    for p in plan.rows:
        key = p.category_key or _category_key(conn, p.primary, p.detailed, split, remaps)
        p.category_label = labels.get(key, key.replace("_", " ").title())
    # Each bank category with the category it gets now: yours, else the built-in match, else none (Uncategorized).
    bank = []
    for n, (name, count) in enumerate(plan.bank_categories.most_common()):
        builtin = engine.bank_category(name)
        current = plan.categories.get(name) or (
            _category_key(conn, *builtin, split, remaps) if builtin and builtin != engine.CARD_PAYMENT else "")
        bank.append({"n": n, "name": name, "count": count, "key": current})
    layout = plan.detection.layout
    ctx.update(
        plan=plan,
        new=plan.new,
        money_out=sum(p.row.amount_cents for p in plan.new if p.row.amount_cents > 0),
        money_in=-sum(p.row.amount_cents for p in plan.new if p.row.amount_cents < 0),
        listed=sorted((p for p in plan.rows if p.status != "already"), key=lambda p: (p.row.posted, p.row.line),
                      reverse=True),
        date_family=lay.DATE_FORMATS.get(layout.date_format),
        date_order=answers.get("date_order") or ("" if "date_order" in plan.detection.questions
                                                 else lay.DATE_FORMATS.get(layout.date_format)),
        signed="amount" in layout.columns and "direction" not in layout.columns,
        sign="" if "sign" in plan.detection.questions else str(layout.sign or ""),
        # nothing is chosen for you (user's decision: ask each time); your last choice for this account is pointed out
        overlap_choice=plan.choice or "",
        last_choice=engine.last_overlap_choice(conn, up.account_id),
        bank=bank,
        category_list=categories.list_categories(conn),
    )
    return ctx


def _page(request: Request, conn, settings, account_id: int | None, **extra) -> HTMLResponse:
    return templates.TemplateResponse(request, "import.html", {
        "settings": settings,
        "page_path": "/import",
        "phone": via_phone(request),
        "accounts": engine.importable_accounts(conn),
        "account_id": account_id,
        "batches": engine.batches(conn),
        "msg": request.query_params.get("msg"),
        "error": request.query_params.get("error"),
        **extra,
    })


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request, account: str = "", conn=Depends(get_conn), settings=Depends(get_settings)):
    return _page(request, conn, settings, int(account) if account.isdigit() else None)


@router.post("/import/preview", response_class=HTMLResponse)
async def import_preview(request: Request, conn=Depends(get_conn)):
    up = Upload(await request.form())
    await up.read()
    return templates.TemplateResponse(request, "_import_preview.html", _preview(conn, request, up))


@router.post("/import/apply", response_class=HTMLResponse)
async def import_apply(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    """Import what the preview showed (prepared again from the posted file and answers), after a backup, holding the
    sync lock like `fintrack import --apply`."""
    up = Upload(await request.form())
    await up.read()
    ctx = _preview(conn, request, up)
    plan = ctx["plan"]
    if plan is None or plan.needs:
        error = ctx["error"] or "Answer the questions in the preview first."
        return _page(request, conn, settings, up.account_id, error=error)
    if not plan.new and not plan.cards_missing and not plan.primary_change and not plan.zeroed:
        return _page(request, conn, settings, up.account_id, error="Nothing to add from that file.")
    try:
        with sync.sync_lock(settings.home):
            backup_database(settings.db_path, settings.backups_dir)
            batch, added = engine.apply(conn, plan)
    except sync.SyncInProgress as exc:
        return _page(request, conn, settings, up.account_id, error=str(exc))
    return redirect("/import", account=str(up.account_id),
                    msg=f"Added {added} transaction{'' if added == 1 else 's'} to {plan.account.name}."
                    + (f" Its card is ····{plan.primary_card}." if plan.primary_change and plan.account.is_manual else ""))


@router.post("/import/{batch_id}/undo")
def import_undo(batch_id: int, conn=Depends(get_conn), settings=Depends(get_settings)):
    """Remove everything one import added, after a backup."""
    try:
        with sync.sync_lock(settings.home):
            backup_database(settings.db_path, settings.backups_dir)
            removed = engine.undo(conn, batch_id)
    except (engine.ImportFileError, sync.SyncInProgress) as exc:
        return redirect("/import", error=str(exc))
    return redirect("/import", msg=f"Removed {removed} transaction{'' if removed == 1 else 's'}.")
