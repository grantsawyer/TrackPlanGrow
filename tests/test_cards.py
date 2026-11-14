"""Card statements, paying accounts, and statement payments in the cash-flow forecast.
Fixed fake data and dates (no Plaid calls)."""

from datetime import date, timedelta

import pytest

from fintrack import cards, forecast, items, recurring

TODAY = date(2026, 10, 3)
CHECKING, SAVINGS, CARD, BROKERAGE = 1, 2, 3, 4


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    for acc_id, name, account_type, balance in [
        (CHECKING, "Fake Checking", "depository", 200000), (SAVINGS, "Fake Savings", "depository", 1000000),
        (CARD, "Fake Card", "credit", 50000), (BROKERAGE, "Fake Brokerage", "investment", 900000),
    ]:
        conn.execute(
            "INSERT INTO accounts (id, item_id, plaid_account_id, name, mask, type, classification, current_balance_cents)"
            " VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
            (acc_id, f"acc-{acc_id}", name, f"{acc_id:04d}", account_type, items.classify(account_type), balance),
        )
    conn.commit()
    return conn


_ids = iter(range(1, 1_000_000))


def add(conn, day, cents, account, key="FOOD_AND_DRINK", name="FAKE"):
    conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (f"c{next(_ids)}", account, day.isoformat() if isinstance(day, date) else day, cents, name, key),
    )
    conn.commit()


def statement(conn, balance, statement_date, due_date, last_payment=None, last_payment_date=None, overdue=False):
    with conn:
        cards.store_statement(conn, CARD, {
            "last_statement_balance": balance, "last_statement_issue_date": date.fromisoformat(statement_date),
            "next_payment_due_date": date.fromisoformat(due_date), "minimum_payment_amount": -12.5,
            "last_payment_amount": last_payment,
            "last_payment_date": date.fromisoformat(last_payment_date) if last_payment_date else None,
            "is_overdue": overdue,
        })


# ---------------------------------------------------------------- what's owed

def test_owed_is_statement_minus_payments_since_the_statement(db):
    statement(db, 500.00, "2026-09-20", "2026-10-15")
    add(db, "2026-09-18", -9900, CARD, key="CREDIT_CARD_PAYMENT")    # before the statement: not counted
    add(db, "2026-09-25", -20000, CARD, key="CREDIT_CARD_PAYMENT")   # $200 paid toward it
    add(db, "2026-09-26", 4000, CARD)                                # new purchase: not a payment
    [d] = cards.statements(db, TODAY)
    assert (d.statement_cents, d.paid_cents, d.owed_cents, d.overdue) == (50000, 20000, 30000, False)

    # Plaid already knows a bigger payment than our transactions show: trust the larger.
    statement(db, 500.00, "2026-09-20", "2026-10-15", last_payment=250.00, last_payment_date="2026-09-30")
    assert cards.statements(db, TODAY)[0].owed_cents == 25000


def test_paid_and_past_due_statements(db):
    statement(db, 300.00, "2026-09-05", "2026-10-01", last_payment=300.00, last_payment_date="2026-09-30")
    [paid] = cards.statements(db, TODAY)
    assert paid.owed_cents == 0 and not paid.overdue

    statement(db, 300.00, "2026-09-05", "2026-10-01")       # due date passed, nothing paid
    assert cards.statements(db, TODAY)[0].overdue


def test_payment_out_of_the_paying_account_pays_before_the_card_side_posts(db):
    cards.set_payer(db, CARD, str(CHECKING))
    statement(db, 500.00, "2026-09-20", "2026-10-02")                           # due yesterday
    add(db, "2026-10-02", 50000, CHECKING, key="CREDIT_CARD_PAYMENT")           # left checking; card hasn't posted it
    [d] = cards.statements(db, TODAY)
    assert d.owed_cents == 0 and not d.overdue
    checking = next(a for a in forecast.cash_flow(db, today=TODAY, horizon=30).accounts if a.account_id == CHECKING)
    assert not any(e.name == "Fake Card statement" for e in checking.events)


def test_bank_side_payment_counts_only_at_the_exact_amount_and_once(db):
    cards.set_payer(db, CARD, str(CHECKING))
    statement(db, 500.00, "2026-09-20", "2026-10-15")
    add(db, "2026-09-25", 30000, CHECKING, key="CREDIT_CARD_PAYMENT")           # another amount (another card?)
    add(db, "2026-09-19", 50000, CHECKING, key="CREDIT_CARD_PAYMENT")           # before the statement
    assert cards.statements(db, TODAY)[0].owed_cents == 50000
    add(db, "2026-09-26", -20000, CARD, key="CREDIT_CARD_PAYMENT")              # $200 reached the card...
    add(db, "2026-09-27", 30000, CHECKING, key="CREDIT_CARD_PAYMENT")           # ...and the $300 rest left checking
    assert cards.statements(db, TODAY)[0].owed_cents == 0


# ---------------------------------------------------------------- paying accounts

def test_paying_account_auto_chosen_none_and_fallback(db):
    # Two checking/savings accounts: automatic can't pick one.
    assert cards.payers(db)[CARD] == {"pay_from": None, "mode": "auto"}
    db.execute("UPDATE accounts SET is_hidden = 1 WHERE id = ?", (SAVINGS,)); db.commit()
    assert cards.payers(db)[CARD] == {"pay_from": CHECKING, "mode": "auto"}     # now exactly one

    cards.set_payer(db, CARD, str(BROKERAGE))
    assert cards.payers(db)[CARD] == {"pay_from": BROKERAGE, "mode": "chosen"}
    cards.set_payer(db, CARD, "none")
    assert cards.payers(db)[CARD] == {"pay_from": None, "mode": "none"}
    cards.set_payer(db, CARD, str(BROKERAGE))
    db.execute("UPDATE accounts SET is_hidden = 1 WHERE id = ?", (BROKERAGE,)); db.commit()
    assert cards.payers(db)[CARD] == {"pay_from": CHECKING, "mode": "auto"}     # chosen account hidden
    cards.set_payer(db, CARD, "auto")
    assert db.execute("SELECT COUNT(*) FROM card_payment_accounts").fetchone()[0] == 0

    with pytest.raises(ValueError):
        cards.set_payer(db, CARD, str(CARD))          # a card can't pay a card
    with pytest.raises(ValueError):
        cards.set_payer(db, CHECKING, "auto")         # only cards have a paying account


# ---------------------------------------------------------------- forecast

def test_statement_payment_leaves_the_paying_account_on_the_due_date(db):
    cards.set_payer(db, CARD, str(CHECKING))
    statement(db, 500.00, "2026-09-20", "2026-10-15")
    flow = forecast.cash_flow(db, today=TODAY, horizon=30)
    checking = next(a for a in flow.accounts if a.account_id == CHECKING)
    assert any(e.name == "Fake Card statement" and e.change_cents == -50000 for e in checking.events)
    assert checking.points[11][1] == 200000 and checking.points[12][1] == 150000   # Oct 14 vs Oct 15
    savings = next(a for a in flow.accounts if a.account_id == SAVINGS)
    assert not savings.events


def test_overdue_statement_is_assumed_paid_today(db):
    cards.set_payer(db, CARD, str(CHECKING))
    statement(db, 120.00, "2026-09-01", "2026-09-28")
    checking = next(a for a in forecast.cash_flow(db, today=TODAY, horizon=30).accounts if a.account_id == CHECKING)
    assert checking.points[0][1] == 200000 and checking.points[1][1] == 200000 - 12000   # today: actual balance
    assert checking.events[0].overdue and checking.events[0].day == TODAY


def test_past_card_payments_and_spending_are_not_projected(db):
    cards.set_payer(db, CARD, str(CHECKING))
    statement(db, 500.00, "2026-09-20", "2026-10-15")
    for k in range(1, 4):                       # past $1,000 card payments from checking
        add(db, TODAY - timedelta(days=20 * k), 100000, CHECKING, key="CREDIT_CARD_PAYMENT")
    add(db, TODAY - timedelta(days=5), 6000, CHECKING)          # past everyday spending
    checking = next(a for a in forecast.cash_flow(db, today=TODAY, horizon=30).accounts if a.account_id == CHECKING)
    # Only the statement moves the balance: flat before it, one step on the due date.
    assert {c for _, c in checking.points[:12]} == {200000}
    assert {c for _, c in checking.points[12:]} == {150000}


def test_confirmed_recurring_card_payment_is_replaced_by_the_statement(db):
    cards.set_payer(db, CARD, str(CHECKING))
    statement(db, 500.00, "2026-09-20", "2026-10-15")
    recurring.save_item(db, None, name="Fake Card Autopay", amount_text="900", direction="out", frequency="monthly",
                        next_date="2026-10-16", account_id=CHECKING, category_key="CREDIT_CARD_PAYMENT")
    checking = next(a for a in forecast.cash_flow(db, today=TODAY, horizon=30).accounts if a.account_id == CHECKING)
    assert [e.name for e in checking.events] == ["Fake Card statement"]


def test_non_checking_payer_is_forecast_but_not_in_the_cash_total(db):
    cards.set_payer(db, CARD, str(BROKERAGE))
    statement(db, 500.00, "2026-09-20", "2026-10-15")
    flow = forecast.cash_flow(db, today=TODAY, horizon=30)
    assert BROKERAGE in {a.account_id for a in flow.accounts}
    assert flow.combined.start_cents == 200000 + 1000000          # checking + savings only


# ---------------------------------------------------------------- Overview

def test_upcoming_list_includes_statements_and_flags_missing_payer(db):
    from fintrack.forecast import upcoming_30

    statement(db, 500.00, "2026-09-20", "2026-10-15")
    recurring.save_item(db, None, name="Fake Payroll", amount_text="2000", direction="in", frequency="monthly",
                        next_date="2026-10-10", account_id=CHECKING, category_key=None)
    rows = upcoming_30(db, TODAY)
    assert [r["name"] for r in rows] == ["Fake Payroll", "Fake Card statement"]          # no masks (2026-10-23)
    assert rows[1]["needs_payer"] and rows[1]["amount_cents"] == 50000     # two checking/savings: no automatic payer

    cards.set_payer(db, CARD, str(SAVINGS))
    card_row = upcoming_30(db, TODAY)[1]
    assert card_row["account_name"] == "Fake Savings" and not card_row["needs_payer"]

    statement(db, 500.00, "2026-09-20", "2026-10-15", last_payment=500.00, last_payment_date="2026-10-01")
    assert [r["name"] for r in upcoming_30(db, TODAY)] == ["Fake Payroll"]   # paid: nothing to show


def test_balance_chart_data_puts_the_payer_first_with_events(db):
    from fintrack import visuals

    cards.set_payer(db, CARD, str(SAVINGS))
    statement(db, 500.00, "2026-09-20", "2026-10-15")
    data = visuals.balance_forecast(forecast.cash_flow(db, today=TODAY, horizon=30), days=30, prefer=SAVINGS)
    first = data["accounts"][0]
    assert first["id"] == SAVINGS and len(first["labels"]) == 31 and first["balance"][0] == 10000.0
    day = first["labels"].index("2026-10-15")
    assert first["events"][day] == ["Fake Card statement −$500.00"]
    assert first["balance"][day] == 9500.0 and data["threshold"] == 500.0


def test_cash_flow_shows_no_masks(db):
    """Neither the Overview's Cash flow card nor the Cash flow page shows account masks (user's requests 2026-10-23)."""
    from fintrack import visuals
    from fintrack.forecast import upcoming_30

    cards.set_payer(db, CARD, str(SAVINGS))
    statement(db, 500.00, "2026-09-20", "2026-10-15")
    db.execute("UPDATE accounts SET mask = '0002' WHERE id = ?", (SAVINGS,))
    db.commit()
    row = upcoming_30(db, TODAY)[0]
    assert (row["name"], row["account_name"]) == ("Fake Card statement", "Fake Savings") and "account_mask" not in row
    chart = visuals.balance_forecast(forecast.cash_flow(db, today=TODAY, horizon=30), days=30, prefer=SAVINGS)
    assert chart["accounts"][0]["label"] == "Fake Savings" and "····" not in str(chart)


# ---------------------------------------------------------------- estimated statements (user's request 2026-10-23)

def test_average_monthly_spend_is_the_last_three_complete_months_without_payments(db):
    for month, cents in (("2026-06", 99999), ("2026-07", 30000), ("2026-08", 60000), ("2026-09", 90000)):
        add(db, f"{month}-10", cents, CARD)                                       # June: a fourth month back, ignored
    add(db, "2026-09-12", -15000, CARD)                                           # a refund lowers September
    add(db, "2026-09-20", -500000, CARD, key="CREDIT_CARD_PAYMENT")               # payments to the card: never
    add(db, "2026-09-21", -70000, CARD, key="TRANSFER_IN")                        # ... nor a transfer in
    add(db, "2026-10-01", 777700, CARD)                                           # this month (incomplete): not yet
    assert cards.monthly_spend(db, CARD, TODAY) == (round((30000 + 60000 + 75000) / 3), 3)


def test_average_uses_the_months_there_are_and_none_without_a_complete_month(db):
    add(db, "2026-10-01", 5000, CARD)
    assert cards.monthly_spend(db, CARD, TODAY) == (None, 0)                     # only the current month
    add(db, "2026-09-15", 40000, CARD)
    assert cards.monthly_spend(db, CARD, TODAY) == (40000, 1)


def test_recent_spend_is_the_last_30_days_today_included_without_payments(db):
    # TODAY = Oct 3: the window is Sep 4 .. Oct 3
    add(db, "2026-09-03", 11100, CARD)                                            # 31 days ago: out
    add(db, "2026-09-04", 2000, CARD)
    add(db, "2026-10-03", 3000, CARD)
    add(db, "2026-09-20", -1000, CARD)                                            # a refund lowers it
    add(db, "2026-09-21", -500000, CARD, key="CREDIT_CARD_PAYMENT")               # payments: never
    add(db, "2026-09-22", -70000, CARD, key="TRANSFER_IN")                        # ... nor a transfer in
    assert cards.recent_spend(db, CARD, TODAY) == 4000
    assert next(e for e in cards.estimates(db, TODAY) if e.card_id == CARD).last_30_cents == 4000
    add(db, "2026-09-25", -9000, CARD)
    assert cards.recent_spend(db, CARD, TODAY) == 0                               # never negative


def test_pay_day_is_the_due_day_else_the_usual_payment_day(db):
    for day in ("2026-07-22", "2026-08-21", "2026-09-22"):
        add(db, day, -10000, CARD, key="CREDIT_CARD_PAYMENT")
    assert cards.pay_day(db, CARD) == 22
    statement(db, 500.00, "2026-09-20", "2026-10-15")
    assert cards.pay_day(db, CARD) == 15


def test_estimated_payments_follow_the_known_statement_monthly_from_the_paying_account(db):
    cards.set_payer(db, CARD, str(CHECKING))
    statement(db, 500.00, "2026-09-20", "2026-10-31")
    for month in ("2026-07", "2026-08", "2026-09"):
        add(db, f"{month}-10", 60000, CARD)
    [e] = cards.estimates(db, TODAY)
    assert (e.monthly_cents, e.months, e.pay_day, e.first_due, e.pay_from) == (60000, 3, 31, date(2026, 11, 30), CHECKING)
    events = cards.estimated_payment_events(db, TODAY, date(2027, 1, 31))
    assert [(day, change) for _, day, _, change, _ in events] == [
        (date(2026, 11, 30), -60000), (date(2026, 12, 31), -60000), (date(2027, 1, 31), -60000)]   # the 31st, clamped
    # in the forecast: the known statement, then the estimates; a confirmed recurring card payment isn't added again
    recurring.save_item(db, None, name="Fake Card autopay", amount_text="600", direction="out", frequency="monthly",
                        next_date="2026-10-25", account_id=CHECKING, category_key="CREDIT_CARD_PAYMENT")
    checking = next(a for a in forecast.cash_flow(db, today=TODAY, horizon=90).accounts if a.account_id == CHECKING)
    assert [(e.day, e.name, e.change_cents) for e in checking.events] == [
        (date(2026, 10, 31), "Fake Card statement", -50000),
        (date(2026, 11, 30), "Fake Card statement (estimated)", -60000),
        (date(2026, 12, 31), "Fake Card statement (estimated)", -60000)]
    assert checking.at(90) == 200000 - 50000 - 120000
    # on Nov 5 the average is Aug-Oct (October had no charges: $400) and the Nov 30 estimate is coming up
    upcoming = [r for r in forecast.upcoming_30(db, date(2026, 11, 5)) if r.get("estimated")]
    assert [(r["date"], r["amount_cents"], r["account_name"]) for r in upcoming] == [("2026-11-30", 40000, "Fake Checking")]


def test_no_estimate_without_a_paying_account_or_spend(db):
    cards.set_payer(db, CARD, "none")
    statement(db, 500.00, "2026-09-20", "2026-10-15")
    add(db, "2026-09-10", 60000, CARD)
    assert cards.estimated_payment_events(db, TODAY, date(2027, 1, 31)) == []
    cards.set_payer(db, CARD, str(CHECKING))
    add(db, "2026-09-11", -60000, CARD)                                            # a refund cancels the month out
    assert cards.estimated_payment_events(db, TODAY, date(2027, 1, 31)) == []


def test_cash_flow_page_cards(db, settings, cipher, gateway):
    """Cash flow page (user's request 2026-10-24): Coming up in its own card, and a Credit cards card with each card's
    average monthly spend (and their total), due day and paying account; the last 30 days' spend (2026-10-27)."""
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    client = TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                        base_url="https://localhost:8443")
    cards.set_payer(db, CARD, str(CHECKING))
    today = date.today()
    due = date(today.year + (today.month == 12), today.month % 12 + 1, 22)                  # the 22nd next month
    statement(db, 500.00, str(today.replace(day=1)), str(due))
    for back in (1, 2, 3):
        month = (today.replace(day=1) - timedelta(days=28 * back)).replace(day=10)
        add(db, month, 30000, CARD)
    page = client.get("/cash-flow").text
    sections = page.split('<section class="card">')
    coming = next(s for s in sections if "Coming up in the next 30 days" in s)
    assert "<h2>Coming up in the next 30 days</h2>" in coming and "Projected balance" not in coming
    table = next(s for s in sections if "<h2>Credit cards</h2>" in s)
    row = table.split('href="/accounts/3">Fake Card</a>')[1].split("</tr>")[0]
    assert "$300.00" in row and "22nd" in row and "Fake Checking" in row
    add(db, today, 4500, CARD)                                                  # today: in the last 30 days
    recent = 4500 + sum(30000 for back in (1, 2, 3)
                        if (today - (today.replace(day=1) - timedelta(days=28 * back)).replace(day=10)).days < 30)
    page = client.get("/cash-flow").text
    table = next(s for s in page.split('<section class="card">') if "<h2>Credit cards</h2>" in s)
    assert '<th>Card</th><th class="num">Last 30 days</th><th class="num">Monthly Avg.</th>' in table   # 2026-10-27
    row = table.split('href="/accounts/3">Fake Card</a>')[1].split("</tr>")[0]
    assert row.index(f"${recent / 100:,.2f}") < row.index("$300.00")
    assert (f'<tr class="total-row"><td>Total</td><td class="num"><span class="w-long">${recent / 100:,.2f}</span>' in table
            and '<td class="num"><span class="w-long">$300.00</span>' in table)            # both totals (whole on a phone)
    assert "Next estimated payment" not in table
    assert "<h3>Coming up in the next 30 days</h3>" in client.get("/").text                 # the Overview: unchanged
