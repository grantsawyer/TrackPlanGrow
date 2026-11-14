"""Projection page: net worth 1/5/10 years out, assumptions, loan terms, what-if scenarios."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from fintrack import categories, loans, projection
from fintrack.web.deps import get_conn, get_settings, redirect, templates

router = APIRouter()

SCENARIO_ROWS = 3   # rows of each adjustment type in the scenario form


def _chart(history, baseline, scenario):
    """Aligned monthly series for Chart.js: actual history, then projections from this month."""
    months = sorted({p.month for p in history} | {p.month for p in baseline.points})
    hist = {p.month: p.net_cents / 100 for p in history}
    base = {p.month: p.net_cents / 100 for p in baseline.points}
    scen = {p.month: p.net_cents / 100 for p in scenario.points} if scenario else {}
    today = baseline.points[0].month
    hist[today] = base[today]      # history line ends where the projection starts
    return {
        "months": months,
        "today": today,
        "history": [hist.get(m) for m in months],
        "baseline": [base.get(m) for m in months],
        "scenario": [scen.get(m) for m in months] if scenario else None,
    }


@router.get("/projection", response_class=HTMLResponse)
def projection_page(request: Request, conn=Depends(get_conn), settings=Depends(get_settings)):
    q = request.query_params
    base = projection.baseline_assumptions(conn)
    baseline = projection.project(conn, base=base)

    scenario_row, scenario = None, None
    if q.get("scenario", "").isdigit():
        try:
            scenario_row, adjustments = projection.get_scenario(conn, int(q["scenario"]))
            scenario = projection.project(conn, adjustments, base=base)
        except LookupError:
            pass

    scenario_loans = {o.account_id: o for o in scenario.loans} if scenario else {}
    return templates.TemplateResponse(
        request,
        "projection.html",
        {
            "settings": settings,
            "base": base,
            "baseline": baseline,
            "scenario": scenario,
            "scenario_row": scenario_row,
            "scenario_loans": scenario_loans,
            "scenarios": projection.list_scenarios(conn),
            "terms": {r["id"]: r for r in loans.liabilities_with_terms(conn)},
            "chart": _chart(projection.history_by_month(conn), baseline, scenario),
            "years": (1, 5, 10),
            "msg": q.get("msg"),
            "error": q.get("error"),
        },
    )


@router.post("/projection/settings")
def save_settings(
    monthly_savings: str = Form(""), investment_return: str = Form("5"), property_growth: str = Form("0"),
    redirect_paid_off: str = Form(""), scenario: str = Form(""), conn=Depends(get_conn),
):
    back = {"scenario": scenario} if scenario else {}
    try:
        projection.save_settings(conn, monthly_savings, investment_return, property_growth, redirect_paid_off == "on")
    except projection.ProjectionError as exc:
        return redirect("/projection", error=str(exc), **back)
    return redirect("/projection", msg="Assumptions saved.", **back)


@router.post("/projection/terms/{account_id}")
def save_terms(account_id: int, apr: str = Form(""), payment: str = Form(""), scenario: str = Form(""),
               conn=Depends(get_conn)):
    back = {"scenario": scenario} if scenario else {}
    try:
        loans.set_terms(conn, account_id, apr, payment)
    except loans.LoanError as exc:
        return redirect("/projection", error=str(exc), **back)
    return redirect("/projection", msg="Loan terms saved.", **back)


# ---------------------------------------------------------------- scenarios

def _form_context(conn, request, row=None, adjustments=None):
    adjustments = adjustments or {}
    pad = lambda items, n: list(items) + [{}] * (n - len(items))  # noqa: E731
    amortizing = [o for o in projection.project(conn).loans if o.amortizes]
    return {
        "settings": request.app.state.settings,
        "row": row,
        "adj": adjustments,
        "category_rows": pad(adjustments.get("category_changes", []), SCENARIO_ROWS),
        "recurring_rows": pad(adjustments.get("recurring_changes", []), SCENARIO_ROWS),
        "extra_rows": pad(adjustments.get("extra_payments", []), 2),
        "categories": [c for c in categories.list_categories(conn) if c["kind"] == "expense"],
        "averages": projection.category_averages(conn),
        "loans": amortizing,
        "error": request.query_params.get("error"),
    }


@router.get("/projection/scenarios/new", response_class=HTMLResponse)
def new_scenario(request: Request, conn=Depends(get_conn)):
    return templates.TemplateResponse(request, "scenario_form.html", _form_context(conn, request))


@router.get("/projection/scenarios/{scenario_id}/edit", response_class=HTMLResponse)
def edit_scenario(request: Request, scenario_id: int, conn=Depends(get_conn)):
    try:
        row, adjustments = projection.get_scenario(conn, scenario_id)
    except LookupError:
        return redirect("/projection", error="That scenario doesn't exist.")
    return templates.TemplateResponse(request, "scenario_form.html", _form_context(conn, request, row, adjustments))


def _adjustments_from_form(form) -> dict:
    """Build the adjustments JSON from the scenario form; blank rows are skipped."""
    adj: dict = {"category_changes": [], "recurring_changes": [], "extra_payments": []}
    for i in range(SCENARIO_ROWS):
        key, amount = form.get(f"cat_key_{i}", ""), form.get(f"cat_amount_{i}", "")
        if key and amount.strip():
            adj["category_changes"].append({"category": key, "monthly_cents": projection.parse_signed_amount(amount)})
        name, amount = form.get(f"rec_name_{i}", "").strip(), form.get(f"rec_amount_{i}", "")
        if name and amount.strip():
            adj["recurring_changes"].append({"name": name, "monthly_cents": projection.parse_signed_amount(amount)})
    for i in range(2):
        acc, amount = form.get(f"extra_acc_{i}", ""), form.get(f"extra_amount_{i}", "")
        if acc.isdigit() and amount.strip():
            cents = projection.parse_signed_amount(amount)
            if cents <= 0:
                raise projection.ProjectionError("Extra payments must be positive.")
            adj["extra_payments"].append({"account_id": int(acc), "monthly_cents": cents})
    for field, label, low, high in (("savings_rate_pct", "Savings rate", -100, 100),
                                    ("investment_return_pct", "Investment return", -50, 50)):
        text = form.get(field, "").strip()
        if text:
            adj[field] = projection.parse_percent(text, label, low, high)
    return {k: v for k, v in adj.items() if v not in ([], None)}


@router.post("/projection/scenarios")
async def save_scenario(request: Request, conn=Depends(get_conn)):
    form = await request.form()
    scenario_id = int(form["scenario_id"]) if form.get("scenario_id", "").isdigit() else None
    try:
        adjustments = _adjustments_from_form(form)
        scenario_id = projection.save_scenario(conn, scenario_id, form.get("name", ""), adjustments)
    except projection.ProjectionError as exc:
        target = f"/projection/scenarios/{scenario_id}/edit" if scenario_id else "/projection/scenarios/new"
        return redirect(target, error=str(exc))
    return redirect("/projection", scenario=str(scenario_id), msg="Scenario saved.")


@router.post("/projection/scenarios/{scenario_id}/delete")
def remove_scenario(scenario_id: int, conn=Depends(get_conn)):
    projection.delete_scenario(conn, scenario_id)
    return redirect("/projection", msg="Scenario deleted.")
