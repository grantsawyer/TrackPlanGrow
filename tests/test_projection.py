"""Net worth projection, loan payoff, scenarios, and loan terms (fixed dates)."""

from datetime import date

import pytest

from fintrack import loans, projection

TODAY = date(2026, 10, 3)


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    conn.commit()
    return conn


def account(conn, acc_id, account_type, classification, cents, name=None, plaid=True):
    conn.execute(
        "INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification, current_balance_cents, is_manual)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (acc_id, 1 if plaid else None, f"acc-{acc_id}" if plaid else None, name or f"Fake {account_type} {acc_id}",
         account_type, classification, cents, 0 if plaid else 1),
    )
    conn.commit()


def base(**overrides):
    values = dict(monthly_savings_cents=0, savings_is_override=True, average_saved_cents=0, average_income_cents=0,
                  months_averaged=12, investment_return_pct=0.0, property_growth_pct=0.0, redirect_paid_off=True)
    values.update(overrides)
    return projection.Assumptions(**values)


def closed_form_balance(principal, apr_pct, payment, n):
    i = apr_pct / 1200
    return principal * (1 + i) ** n - payment * ((1 + i) ** n - 1) / i


# ---------------------------------------------------------------- growth and loans

def test_investments_compound_monthly(db):
    account(db, 1, "investment", "asset", 100000)
    p = projection.project(db, today=TODAY, base=base(investment_return_pct=12.0))
    assert p.at_years(1).assets_cents == 112000            # (1.12)^(12/12) exactly
    assert p.points[0].month == "2026-10" and p.points[12].month == "2027-10"


def test_savings_added_monthly_and_cash_constant(db):
    account(db, 1, "depository", "asset", 50000)
    p = projection.project(db, today=TODAY, base=base(monthly_savings_cents=10000))
    assert p.at_years(1).assets_cents == 50000 + 12 * 10000


def test_loan_amortization_matches_closed_form(db):
    account(db, 1, "loan", "liability", 20000000, name="Fake Mortgage")       # $200,000
    loans.set_terms(db, 1, "6", "1,199.10")
    p = projection.project(db, today=TODAY, base=base())
    expected = closed_form_balance(20000000, 6.0, 119910, 12)
    assert abs(p.at_years(1).liabilities_cents - expected) <= 1
    assert p.at_years(1).net_cents == -p.at_years(1).liabilities_cents


def test_payoff_month_and_redirect(db):
    account(db, 1, "loan", "liability", 100000, name="Fake Small Loan")      # $1,000 at 12%, $100/month
    loans.set_terms(db, 1, "12", "100")
    p = projection.project(db, today=TODAY, base=base())
    outcome = p.loans[0]
    # 1.01^n >= 10000/9000 -> n >= 10.6 -> 11 payments; payments start Nov 2026 -> Sep 2027.
    assert outcome.paid_off_month == "2027-09"
    assert p.points[10].liabilities_cents > 0 and p.points[11].liabilities_cents == 0
    final_payment = round(closed_form_balance(100000, 12.0, 10000, 10) * 1.01)
    # Payoff month: only the unused part of the $100 goes to savings...
    assert p.points[11].assets_cents == 10000 - final_payment
    # ...then the full $100 every month after (invested at 0% here).
    assert p.points[12].assets_cents - p.points[11].assets_cents == 10000
    assert p.points[13].assets_cents - p.points[12].assets_cents == 10000

    no_redirect = projection.project(db, today=TODAY, base=base(redirect_paid_off=False))
    assert no_redirect.points[13].assets_cents == no_redirect.points[12].assets_cents


def test_payment_below_interest_flagged_and_grows(db):
    account(db, 1, "loan", "liability", 6526200, name="Fake Student Loan")   # Sandbox-like: $25 on $65k at 5.25%
    loans.set_terms(db, 1, "5.25", "25")
    p = projection.project(db, today=TODAY, base=base())
    assert p.loans[0].payment_too_low and p.loans[0].paid_off_month is None
    assert p.at_years(1).liabilities_cents > 6526200


def test_cards_from_plaid_held_constant_unless_you_enter_terms(db):
    account(db, 1, "credit", "liability", 50000, name="Fake Card")
    loans.store_plaid_liabilities(db, 1, {"credit": [{"account_id": "acc-1", "aprs": [
        {"apr_type": "purchase_apr", "apr_percentage": 20.0}], "minimum_payment_amount": 25.0}]})
    p = projection.project(db, today=TODAY, base=base())
    assert not p.loans[0].amortizes and "paid in full" in p.loans[0].why_constant
    assert p.at_years(1).liabilities_cents == 50000
    loans.set_terms(db, 1, "20", "100")
    assert projection.project(db, today=TODAY, base=base()).at_years(1).liabilities_cents < 50000


def test_loan_without_terms_held_constant(db):
    account(db, 1, "loan", "liability", 2321133, name="Fake Auto Loan")
    p = projection.project(db, today=TODAY, base=base())
    assert p.loans[0].why_constant == "no rate/payment entered"
    assert p.at_years(10).liabilities_cents == 2321133


# ---------------------------------------------------------------- loan terms storage

def test_negative_plaid_minimum_payment_stored_as_unknown(db):
    # Plaid returned -373.51 for a brand-new card; it must not break the sync.
    account(db, 1, "credit", "liability", 140448, name="Fake New Card")
    loans.store_plaid_liabilities(db, 1, {"credit": [{"account_id": "acc-1", "aprs": [
        {"apr_type": "purchase_apr", "apr_percentage": 19.99}], "minimum_payment_amount": -373.51}]})
    row = db.execute("SELECT * FROM liability_terms").fetchone()
    assert (row["apr_percent"], row["monthly_payment_cents"]) == (19.99, None)


def test_plaid_terms_stored_but_your_terms_win(db):
    account(db, 1, "loan", "liability", 20000000, name="Fake Mortgage")
    payload = {"mortgage": [{"account_id": "acc-1", "interest_rate": {"percentage": 3.99},
                             "next_monthly_payment": 3141.54, "maturity_date": "2045-07-31"}]}
    loans.store_plaid_liabilities(db, 1, payload)
    row = db.execute("SELECT * FROM liability_terms").fetchone()
    assert (row["apr_percent"], row["monthly_payment_cents"], row["source"]) == (3.99, 314154, "plaid")
    assert "matures 2045-07-31" in row["detail"]

    loans.set_terms(db, 1, "4.5", "3,500")
    loans.store_plaid_liabilities(db, 1, payload)                        # next sync
    row = db.execute("SELECT * FROM liability_terms").fetchone()
    assert (row["apr_percent"], row["monthly_payment_cents"], row["source"]) == (4.5, 350000, "you")


def test_terms_validation(db):
    account(db, 1, "loan", "liability", 1000)
    account(db, 2, "depository", "asset", 1000)
    with pytest.raises(loans.LoanError):
        loans.set_terms(db, 1, "abc", "100")
    with pytest.raises(loans.LoanError):
        loans.set_terms(db, 1, "150", "100")
    with pytest.raises(loans.LoanError):
        loans.set_terms(db, 2, "5", "100")                               # not a liability


# ---------------------------------------------------------------- scenarios

def test_category_cut_and_cap(db):
    averages = {"FOOD_AND_DRINK": 50000, "TRAVEL": 10000}
    inputs = projection.apply_scenario(base(monthly_savings_cents=100000), {
        "category_changes": [{"category": "FOOD_AND_DRINK", "monthly_cents": -20000},
                             {"category": "TRAVEL", "monthly_cents": -30000}]}, averages, {"TRAVEL": "Travel"})
    assert inputs.monthly_savings_cents == 100000 + 20000 + 10000          # travel cut capped at its $100 average
    assert any("Travel" in n and "capped" in n for n in inputs.notes)


def test_savings_rate_recurring_and_return_overrides(db):
    inputs = projection.apply_scenario(base(monthly_savings_cents=100000, average_income_cents=800000), {
        "savings_rate_pct": 25, "recurring_changes": [{"name": "Car lease", "monthly_cents": 45000},
                                                      {"name": "Side gig", "monthly_cents": -10000}],
        "investment_return_pct": 7}, {})
    assert inputs.monthly_savings_cents == 200000 - 45000 + 10000
    assert inputs.investment_return_pct == 7


def test_extra_payment_shortens_payoff_and_comes_from_savings(db):
    account(db, 1, "loan", "liability", 100000)
    account(db, 2, "investment", "asset", 0)
    loans.set_terms(db, 1, "12", "100")
    baseline = projection.project(db, today=TODAY, base=base(monthly_savings_cents=50000))
    extra = projection.project(db, {"extra_payments": [{"account_id": 1, "monthly_cents": 10000}]},
                               today=TODAY, base=base(monthly_savings_cents=50000))
    assert extra.loans[0].paid_off_month < baseline.loans[0].paid_off_month
    assert extra.points[1].assets_cents == 40000                          # $500 savings - $100 extra
    assert extra.at_years(1).net_cents > baseline.at_years(1).net_cents   # less interest paid


def test_scenarios_never_change_real_data(db):
    account(db, 1, "loan", "liability", 100000)
    loans.set_terms(db, 1, "12", "100")
    before = [tuple(r) for r in db.execute("SELECT * FROM accounts")]
    sid = projection.save_scenario(db, None, "Aggressive", {"extra_payments": [{"account_id": 1, "monthly_cents": 90000}]})
    _, adj = projection.get_scenario(db, sid)
    projection.project(db, adj, today=TODAY, base=base())
    assert [tuple(r) for r in db.execute("SELECT * FROM accounts")] == before


def test_scenario_storage_and_parse(db):
    with pytest.raises(projection.ProjectionError):
        projection.save_scenario(db, None, " ", {})
    sid = projection.save_scenario(db, None, "Cut dining", {"category_changes": []})
    projection.save_scenario(db, sid, "Cut dining more", {"savings_rate_pct": 30})
    row, adj = projection.get_scenario(db, sid)
    assert row["name"] == "Cut dining more" and adj == {"savings_rate_pct": 30}
    assert projection.parse_signed_amount("-$200") == -20000 and projection.parse_signed_amount("") == 0
    projection.delete_scenario(db, sid)
    with pytest.raises(LookupError):
        projection.get_scenario(db, sid)


# ---------------------------------------------------------------- pages

ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_projection_page_terms_and_settings_forms(client, db):
    account(db, 1, "depository", "asset", 500000)
    account(db, 2, "loan", "liability", 100000, name="Fake Small Loan")
    page = client.get("/projection").text
    assert "held constant: no rate/payment entered" in page

    client.post("/projection/terms/2", data={"apr": "12", "payment": "100"}, headers=ORIGIN)
    page = client.get("/projection").text
    assert "Fake Small Loan" in page and 'value="12.0"' in page and ">you<" in page

    bad = client.post("/projection/settings", data={"investment_return": "500", "property_growth": "0"},
                      headers=ORIGIN, follow_redirects=False)
    assert "between" in client.get(bad.headers["location"]).text
    client.post("/projection/settings", data={"monthly_savings": "1000", "investment_return": "6",
                                              "property_growth": "1", "redirect_paid_off": "on"}, headers=ORIGIN)
    assert projection.baseline_assumptions(db).monthly_savings_cents == 100000


def test_scenario_form_compare_edit_delete(client, db):
    account(db, 1, "depository", "asset", 500000)
    account(db, 2, "loan", "liability", 100000, name="Fake Small Loan")
    loans.set_terms(db, 2, "12", "100")
    projection.save_settings(db, "500", "0", "0", True)

    response = client.post("/projection/scenarios", data={
        "name": "Side gig + extra", "rec_name_0": "Side gig", "rec_amount_0": "-300",
        "extra_acc_0": "2", "extra_amount_0": "200"}, headers=ORIGIN, follow_redirects=False)
    location = response.headers["location"]
    assert "/projection?scenario=" in location
    import re

    page = " ".join(re.sub(r"<[^>]+>", " ", client.get(location).text).split())   # visible text only
    assert "Baseline vs Side gig + extra" in page
    # Monthly savings: $500 baseline -> $800 with the $300 side gig (the $200 extra
    # comes out of savings inside the simulation while the loan is open).
    assert "Monthly savings $500.00 $800.00 $300.00" in page
    assert "Fake Small Loan paid off" in page

    sid = location.split("scenario=")[1].split("&")[0]
    form = client.get(f"/projection/scenarios/{sid}/edit").text
    assert 'value="Side gig"' in form and 'value="-300.00"' in form

    bad = client.post("/projection/scenarios", data={"scenario_id": sid, "name": "x", "extra_acc_0": "2",
                                                     "extra_amount_0": "-5"}, headers=ORIGIN, follow_redirects=False)
    assert "/edit" in bad.headers["location"]

    client.post(f"/projection/scenarios/{sid}/delete", headers=ORIGIN)
    assert projection.list_scenarios(db) == []


# ---------------------------------------------------------------- baseline assumptions

def test_baseline_savings_average_and_override(db):
    account(db, 1, "depository", "asset", 0)
    for month in range(1, 10):                                            # Jan..Sep: $5,000 in, $3,000 out
        for cents, key in ((-500000, "INCOME"), (300000, "FOOD_AND_DRINK")):
            db.execute(
                "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key)"
                " VALUES (?, 1, ?, ?, 'Fake', ?)", (f"p{month}{key}", f"2026-{month:02d}-10", cents, key))
    db.commit()
    a = projection.baseline_assumptions(db, TODAY)
    assert (a.average_saved_cents, a.average_income_cents, a.months_averaged) == (200000, 500000, 9)
    assert a.monthly_savings_cents == 200000 and not a.savings_is_override
    projection.save_settings(db, "1,500", "6", "2", redirect=False)
    a = projection.baseline_assumptions(db, TODAY)
    assert (a.monthly_savings_cents, a.investment_return_pct, a.property_growth_pct, a.redirect_paid_off) == (
        150000, 6.0, 2.0, False)
    with pytest.raises(projection.ProjectionError):
        projection.save_settings(db, "", "90", "0", True)
