"""Categories, rules, transfer exclusion, and the monthly spending report."""

import re
from datetime import date
from pathlib import Path

import pytest

from fintrack import categories, items, rules, spending, sync, transfers

CHECKING, CARD, SAVINGS, MORTGAGE, HIDDEN = 1, 2, 3, 4, 5


@pytest.fixture
def db(conn):
    """One Item with checking, a credit card, savings, a mortgage, and a hidden account."""
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    for acc_id, name, account_type, hidden in [
        (CHECKING, "Checking", "depository", 0), (CARD, "Card", "credit", 0),
        (SAVINGS, "Savings", "depository", 0), (MORTGAGE, "Mortgage", "loan", 0),
        (HIDDEN, "Old Checking", "depository", 1),
    ]:
        conn.execute(
            "INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification, is_hidden)"
            " VALUES (?, 1, ?, ?, ?, ?, ?)",
            (acc_id, f"acc-{acc_id}", name, account_type, items.classify(account_type), hidden),
        )
    conn.commit()
    return conn


_ids = iter(range(1, 10_000))


def add(conn, account, amount, day, key, name="Fake merchant", merchant=None, pending=0, detailed=None):
    tid = next(_ids)
    conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, merchant_name,"
        " plaid_category_primary, plaid_category_detailed, plaid_category_key, pending)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (f"t{tid}", account, day, amount, name, merchant, key, detailed, key, pending),
    )
    conn.commit()
    return conn.execute("SELECT id FROM transactions WHERE plaid_transaction_id = ?", (f"t{tid}",)).fetchone()[0]


def eff(conn, transaction_id):
    return conn.execute("SELECT * FROM transactions_effective WHERE id = ?", (transaction_id,)).fetchone()


# ---------------------------------------------------------------- Plaid mapping

@pytest.mark.parametrize("primary, detailed, expected", [
    ("LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", "CREDIT_CARD_PAYMENT"),
    ("LOAN_PAYMENTS", "LOAN_PAYMENTS_MORTGAGE_PAYMENT", "LOAN_PAYMENTS"),
    ("TRANSFER_OUT", "TRANSFER_OUT_WITHDRAWAL", "CASH_WITHDRAWAL"),
    ("TRANSFER_OUT", "TRANSFER_OUT_SAVINGS", "TRANSFER_OUT"),
    ("FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE", "FOOD_AND_DRINK"),
    (None, None, "UNCATEGORIZED"),
    ("OTHER", "OTHER_OTHER", "UNCATEGORIZED"),        # Plaid couldn't classify it (user's rule 2026-10-25)
])
def test_plaid_key(primary, detailed, expected):
    assert categories.plaid_key(primary, detailed) == expected


def test_every_v2_primary_has_a_category(conn):
    v2_primaries = {"INCOME", "LOAN_DISBURSEMENTS", "LOAN_PAYMENTS", "TRANSFER_IN", "TRANSFER_OUT", "BANK_FEES",
                    "ENTERTAINMENT", "FOOD_AND_DRINK", "GENERAL_MERCHANDISE", "HOME_IMPROVEMENT", "MEDICAL",
                    "PERSONAL_CARE", "GENERAL_SERVICES", "GOVERNMENT_AND_NON_PROFIT", "TRANSPORTATION",
                    "TRAVEL", "RENT_AND_UTILITIES", "OTHER"}
    keys = {c["key"] for c in categories.list_categories(conn)}
    assert v2_primaries <= keys


def test_default_kinds(conn):
    kinds = {c["key"]: c["kind"] for c in categories.list_categories(conn)}
    assert kinds["CREDIT_CARD_PAYMENT"] == kinds["TRANSFER_IN"] == kinds["TRANSFER_OUT"] == "transfer"
    assert kinds["LOAN_DISBURSEMENTS"] == "transfer"   # borrowed money isn't income
    assert kinds["INCOME"] == "income"
    assert kinds["LOAN_PAYMENTS"] == kinds["CASH_WITHDRAWAL"] == "expense"


# ---------------------------------------------------------------- category precedence and rules

def test_category_precedence_you_over_rule_over_plaid(db):
    tid = add(db, CHECKING, 500, "2026-09-10", "GENERAL_MERCHANDISE", merchant="Fake Grocer")
    assert (eff(db, tid)["category"], eff(db, tid)["category_source"]) == ("GENERAL_MERCHANDISE", "plaid")

    rule_id = rules.add_simple(db, "grocer", "FOOD_AND_DRINK", "merchant")
    assert (eff(db, tid)["category"], eff(db, tid)["category_source"]) == ("FOOD_AND_DRINK", "rule")

    categories.set_transaction_category(db, tid, "TRAVEL")
    assert (eff(db, tid)["category"], eff(db, tid)["category_source"]) == ("TRAVEL", "you")

    categories.set_transaction_category(db, tid, None)   # back to automatic
    assert eff(db, tid)["category"] == "FOOD_AND_DRINK"
    rules.delete(db, rule_id)                            # rule gone -> Plaid's category
    assert eff(db, tid)["category"] == "GENERAL_MERCHANDISE"


def test_sync_applies_rules_to_new_transactions(conn, settings, gateway, cipher):
    from tests.fakes import page, txn

    session_id, _ = items.start_create_session(conn, settings, gateway, "banking")
    items.complete_create(conn, settings, gateway, cipher, session_id, "public-sandbox-fake")
    rules.add_simple(conn, "coffee co", "ENTERTAINMENT", "merchant")
    gateway.sync_queue = [page(added=[txn("t1", 4.5)])]   # merchant "Fake Coffee Co"
    sync.sync_all(conn, gateway, cipher, settings.home)
    row = conn.execute("SELECT category, category_source FROM transactions_effective").fetchone()
    assert tuple(row) == ("ENTERTAINMENT", "rule")


def test_custom_category_and_kind_change(db):
    key = categories.add_category(db, "Kids activities", "expense")
    assert key == "KIDS_ACTIVITIES"
    with pytest.raises(ValueError):
        categories.add_category(db, "kids activities", "expense")   # duplicate name
    categories.set_kind(db, "TRANSFER_OUT", "expense")              # e.g. count Venmo as spending
    tid = add(db, CHECKING, 2000, "2026-09-10", "TRANSFER_OUT", name="Fake P2P to friend")
    assert eff(db, tid)["kind"] == "expense"


# ---------------------------------------------------------------- transfer exclusion

def test_credit_card_payment_excluded_on_both_sides(db):
    pay = add(db, CHECKING, 50000, "2026-09-10", "CREDIT_CARD_PAYMENT")
    received = add(db, CARD, -50000, "2026-09-11", "CREDIT_CARD_PAYMENT")
    purchase = add(db, CARD, 50000, "2026-09-05", "GENERAL_MERCHANDISE")
    transfers.detect_transfers(db)
    assert eff(db, pay)["kind"] == eff(db, received)["kind"] == "transfer"
    report = spending.month_report(db, "2026-09")
    assert report.spending_cents == 50000          # only the purchase, not the payment too
    assert report.transfers_out_cents == 50000


def test_own_account_transfer_excluded(db):
    add(db, CHECKING, 100000, "2026-09-10", "TRANSFER_OUT")
    add(db, SAVINGS, -100000, "2026-09-10", "TRANSFER_IN")
    report = spending.month_report(db, "2026-09")
    assert report.spending_cents == 0 and report.income_cents == 0


def test_miscategorized_transfer_caught_by_matching(db):
    out = add(db, CHECKING, 25000, "2026-09-10", "GENERAL_SERVICES", name="ONLINE XFER")
    into = add(db, SAVINGS, -25000, "2026-09-12", "OTHER", name="ONLINE XFER")
    assert transfers.detect_transfers(db) == 1
    assert eff(db, out)["transfer_pair_id"] == into and eff(db, into)["transfer_pair_id"] == out
    assert spending.month_report(db, "2026-09").spending_cents == 0


@pytest.mark.parametrize("case", ["too_far", "same_account", "pending", "income", "different_amount", "hidden"])
def test_transfer_matching_guards(db, case):
    out_account, in_account, in_day, pending, in_key, in_amount = CHECKING, SAVINGS, "2026-09-12", 0, "OTHER", -25000
    if case == "too_far":
        in_day = "2026-09-14"            # 4 days > 3-day window
    elif case == "same_account":
        in_account = CHECKING
    elif case == "pending":
        pending = 1
    elif case == "income":
        in_key = "INCOME"                 # a paycheck never pairs with a purchase
    elif case == "different_amount":
        in_amount = -25001
    elif case == "hidden":
        in_account = HIDDEN
    add(db, out_account, 25000, "2026-09-10", "GENERAL_SERVICES", pending=pending)
    add(db, in_account, in_amount, in_day, in_key)
    assert transfers.detect_transfers(db) == 0


def test_matching_prefers_closest_date(db):
    out = add(db, CHECKING, 1000, "2026-09-10", "OTHER")
    add(db, SAVINGS, -1000, "2026-09-13", "OTHER")
    near = add(db, CARD, -1000, "2026-09-11", "OTHER")
    transfers.detect_transfers(db)
    assert eff(db, out)["transfer_pair_id"] == near


def test_not_a_transfer_sticks(db):
    out = add(db, CHECKING, 4000, "2026-09-10", "FOOD_AND_DRINK", name="Fake dinner")
    refund = add(db, CARD, -4000, "2026-09-11", "FOOD_AND_DRINK", name="Fake refund")
    transfers.detect_transfers(db)
    assert eff(db, out)["kind"] == "transfer"
    transfers.set_not_transfer(db, out)
    assert eff(db, out)["kind"] == "expense" and eff(db, refund)["kind"] == "expense"
    transfers.detect_transfers(db)     # e.g. the next sync
    assert eff(db, out)["is_transfer"] == 0
    transfers.set_not_transfer(db, out, ignore=False)
    assert eff(db, out)["is_transfer"] == 1


def test_loan_account_entries_excluded_but_payment_from_checking_counts(db):
    add(db, CHECKING, 180000, "2026-09-01", "LOAN_PAYMENTS", detailed="LOAN_PAYMENTS_MORTGAGE_PAYMENT")
    mirror = add(db, MORTGAGE, -180000, "2026-09-01", "LOAN_PAYMENTS")
    transfers.detect_transfers(db)
    assert eff(db, mirror)["kind"] == "excluded"
    report = spending.month_report(db, "2026-09")
    assert report.spending_cents == 180000
    assert report.loan_entries_count == 1


def test_atm_withdrawal_counts_as_spending(db):
    add(db, CHECKING, 6000, "2026-09-03", "CASH_WITHDRAWAL")
    assert spending.month_report(db, "2026-09").spending_cents == 6000


# ---------------------------------------------------------------- monthly report

def test_month_report(db):
    add(db, CHECKING, 4000, "2026-09-02", "FOOD_AND_DRINK")
    add(db, CARD, 6000, "2026-09-20", "FOOD_AND_DRINK")
    add(db, CARD, -1000, "2026-09-21", "FOOD_AND_DRINK", name="Fake refund")
    add(db, CARD, 30000, "2026-09-15", "TRAVEL", pending=1)        # pending counts
    add(db, CHECKING, -500000, "2026-09-01", "INCOME")
    add(db, HIDDEN, 99999, "2026-09-05", "TRAVEL")                 # removed connection: ignored
    add(db, CHECKING, 2000, "2026-08-15", "FOOD_AND_DRINK")        # last month
    add(db, CHECKING, 1000, "2026-10-01", "FOOD_AND_DRINK")        # next month

    r = spending.month_report(db, "2026-09")
    assert r.spending_cents == 4000 + 6000 - 1000 + 30000
    assert r.income_cents == 500000
    assert r.previous_spending_cents == 2000
    lines = {line.category: line for line in r.lines}
    assert [line.category for line in r.lines] == ["TRAVEL", "FOOD_AND_DRINK"]  # biggest first
    assert (lines["FOOD_AND_DRINK"].amount_cents, lines["FOOD_AND_DRINK"].count) == (9000, 3)
    assert lines["FOOD_AND_DRINK"].change_cents == 7000
    assert lines["TRAVEL"].share == pytest.approx(30000 / 39000)


def test_month_in_progress_compares_same_days(db):
    add(db, CHECKING, 1000, "2026-09-02", "FOOD_AND_DRINK")        # Sep 1..4: compared
    add(db, CHECKING, 9000, "2026-09-20", "FOOD_AND_DRINK")        # later in Sep: not compared yet
    add(db, CHECKING, 1500, "2026-10-03", "FOOD_AND_DRINK")

    r = spending.month_report(db, "2026-10", today=date(2026, 10, 4))
    assert r.compare_through_day == 4
    assert r.previous_spending_cents == 1000
    assert r.lines[0].change_cents == 500

    # A finished month compares with all of the month before.
    done = spending.month_report(db, "2026-09", today=date(2026, 10, 4))
    assert done.compare_through_day is None


def test_compare_day_clamped_to_shorter_month(db):
    add(db, CHECKING, 700, "2026-02-28", "FOOD_AND_DRINK")
    r = spending.month_report(db, "2026-03", today=date(2026, 3, 31))
    assert (r.compare_through_day, r.previous_spending_cents) == (28, 700)


@pytest.mark.parametrize("month, delta, expected", [("2026-01", -1, "2025-12"), ("2026-12", 1, "2027-01"), ("2026-09", 0, "2026-09")])
def test_shift_month(month, delta, expected):
    assert spending.shift_month(month, delta) == expected


def test_parse_month_falls_back_to_current():
    from datetime import date
    assert spending.parse_month("2026-13", today=date(2026, 10, 3)) == "2026-10"
    assert spending.parse_month("garbage", today=date(2026, 10, 3)) == "2026-10"
    assert spending.parse_month("2026-9") == "2026-09"


# ---------------------------------------------------------------- templates stay CSP-safe

def test_templates_have_no_inline_handlers_or_styles():
    templates_dir = Path(__file__).parents[1] / "fintrack" / "web" / "templates"
    for path in templates_dir.glob("*.html"):
        text = path.read_text()
        assert not re.search(r"\son[a-z]+\s*=", text), f"inline event handler in {path.name}"
        assert not re.search(r"\sstyle\s*=", text), f"inline style in {path.name}"
        assert not re.search(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>", text), f"inline script in {path.name}"


def test_a_category_spent_in_only_last_period_is_still_listed(db):
    """User's request 2026-11-04: By category shows a category whose spending dropped to nothing, at $0, after the rest."""
    from fintrack import visuals
    add(db, CARD, 4000, "2026-09-05", "FOOD_AND_DRINK")
    add(db, CARD, 9000, "2026-08-05", "TRAVEL")                        # only last month
    add(db, CARD, 2500, "2026-08-06", "ENTERTAINMENT")                 # only last month, smaller
    report = spending.month_report(db, "2026-09")
    assert [(l.category, l.amount_cents, l.previous_cents) for l in report.lines] == [
        ("FOOD_AND_DRINK", 4000, 0), ("TRAVEL", 0, 9000), ("ENTERTAINMENT", 0, 2500)]
    assert report.spending_cents == 4000 and report.previous_spending_cents == 11500
    shares = visuals.category_shares(report.lines)
    assert [s["key"] for s in shares["slices"]] == ["FOOD_AND_DRINK"]            # no tile for $0
    rows = visuals.grouped_category_lines(report.lines, shares)
    assert [(r.category, r.change_cents) for r in rows] == [("FOOD_AND_DRINK", 4000), ("TRAVEL", -9000), ("ENTERTAINMENT", -2500)]
