"""Recurring detection, schedules, and the review lifecycle (fixed dates: deterministic)."""

from datetime import date, timedelta

import pytest

from fintrack import items, recurring

TODAY = date(2026, 10, 3)
CHECKING, CARD = 1, 2


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    for acc_id, account_type in [(CHECKING, "depository"), (CARD, "credit")]:
        conn.execute(
            "INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification, current_balance_cents)"
            " VALUES (?, 1, ?, ?, ?, ?, 100000)",
            (acc_id, f"acc-{acc_id}", f"Fake {account_type}", account_type, items.classify(account_type)),
        )
    conn.commit()
    return conn


_ids = iter(range(1, 1_000_000))


def add(conn, day, cents, merchant, account=CHECKING, key="GENERAL_SERVICES", name=None):
    tid = next(_ids)
    conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, merchant_name, plaid_category_key)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (f"r{tid}", account, day.isoformat() if isinstance(day, date) else day, cents, name or merchant.upper(), merchant, key),
    )
    conn.commit()


def monthly(conn, merchant, cents, day_of_month, months, account=CHECKING, key="GENERAL_SERVICES", last=date(2026, 9, 1)):
    for k in range(months):
        d = recurring._add_months(last, -k, day_of_month)
        add(conn, d, cents, merchant, account, key)


def found(conn, merchant):
    hits = [d for d in recurring.detect(conn, TODAY) if d.merchant_key == recurring.merchant_key(merchant, None)]
    return hits[0] if hits else None


# ---------------------------------------------------------------- schedules

def test_monthly_occurrences_clamp_and_restore_day():
    days = recurring.occurrences("monthly", date(2026, 1, 31), date(2026, 1, 1), date(2026, 4, 30), "31")
    assert days == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]
    # Anchored on a clamped date, the intended day (31) still comes back.
    assert recurring.next_after("monthly", date(2026, 2, 28), date(2026, 2, 28), "31") == date(2026, 3, 31)


def test_semimonthly_weekly_quarterly_occurrences():
    assert recurring.occurrences("semimonthly", date(2026, 10, 1), date(2026, 10, 2), date(2026, 11, 20), "1,15") == [
        date(2026, 10, 15), date(2026, 11, 1), date(2026, 11, 15)]
    assert recurring.occurrences("biweekly", date(2026, 10, 2), date(2026, 9, 1), date(2026, 10, 31)) == [
        date(2026, 9, 4), date(2026, 9, 18), date(2026, 10, 2), date(2026, 10, 16), date(2026, 10, 30)]
    assert recurring.occurrences("quarterly", date(2026, 1, 15), date(2026, 3, 1), date(2027, 1, 31), "15") == [
        date(2026, 4, 15), date(2026, 7, 15), date(2026, 10, 15), date(2027, 1, 15)]


@pytest.mark.parametrize("merchant, name, key", [
    ("Netflix", None, "netflix"),
    (None, "POS DEBIT SPOTIFY P1A2B3 NY", "spotify ny"),
    (None, "ACH PPD PAYROLL 0042", "payroll"),
    (None, "CARD 1234", "card"),   # only noise words: fall back rather than empty
])
def test_merchant_key(merchant, name, key):
    assert recurring.merchant_key(merchant, name) == key


# ---------------------------------------------------------------- detection

def test_monthly_subscription(db):
    monthly(db, "Fake Streaming", 1599, 12, months=6)
    d = found(db, "Fake Streaming")
    assert (d.frequency, d.amount_cents, d.amount_varies, d.kind) == ("monthly", 1599, False, "bill")
    assert d.next_expected == date(2026, 10, 12)


def test_semimonthly_paycheck_not_mistaken_for_biweekly(db):
    for month in range(4, 10):
        for day in (1, 15):
            add(db, date(2026, month, day), -450000, "Fake Employer", key="INCOME")
    d = found(db, "Fake Employer")
    assert (d.frequency, d.days_of_month, d.kind, d.amount_cents) == ("semimonthly", "1,15", "income", -450000)
    assert d.next_expected == date(2026, 10, 1)    # last pay Sep 15 -> next Oct 1 (overdue as of Oct 3)


def test_biweekly(db):
    start = date(2026, 6, 5)
    for k in range(9):
        add(db, start + timedelta(days=14 * k), -300000, "Fake Biweekly Payroll", key="INCOME")
    d = found(db, "Fake Biweekly Payroll")
    assert d.frequency == "biweekly" and d.next_expected == start + timedelta(days=14 * 9)


def test_weekly_and_annual(db):
    for k in range(6):
        add(db, date(2026, 10, 2) - timedelta(days=7 * k), 2500, "Fake Lessons")
    add(db, date(2025, 3, 10), 9900, "Fake Domain")
    add(db, date(2026, 3, 10), 9900, "Fake Domain")
    assert found(db, "Fake Lessons").frequency == "weekly"
    annual = found(db, "Fake Domain")
    assert annual.frequency == "annual" and annual.next_expected == date(2027, 3, 10)


def test_varying_utility_flagged(db):
    for k, cents in enumerate([8400, 9100, 7800, 10200, 9500]):
        add(db, recurring._add_months(date(2026, 9, 20), -k, 20), cents, "Fake Power Co")
    d = found(db, "Fake Power Co")
    # Expected = median of the latest three (Jul 78, Aug 91, Sep 84) -> $84.
    assert d.frequency == "monthly" and d.amount_varies and d.amount_cents == 8400


def test_drifting_every_30_days_uses_latest_day(db):
    # Billed every 30 days for 26 months (like Sandbox's history): the day drifts
    # back through the whole month (20th .. 31st), so the latest day is used.
    # With only ~14 months the drift is small and the most common day is used,
    # a few days off; confirming the item corrects it as real charges post.
    for k in range(26):
        add(db, date(2026, 9, 20) - timedelta(days=30 * k), 5000, "Fake Thirty Day")
    d = found(db, "Fake Thirty Day")
    assert d.frequency == "monthly" and d.days_of_month == "20"
    assert d.next_expected == date(2026, 10, 20)


def test_calendar_anchored_end_of_month_keeps_31st(db):
    for last in (date(2026, 9, 30), date(2026, 8, 31), date(2026, 7, 31), date(2026, 6, 30), date(2026, 5, 31)):
        add(db, last, 7000, "Fake Month End")
    d = found(db, "Fake Month End")
    assert d.days_of_month == "31" and d.next_expected == date(2026, 10, 31)


def test_expected_amount_follows_recent_price(db):
    for k, cents in enumerate([1799, 1799, 1799, 1599, 1599, 1599, 1599]):   # price rose 3 months ago
        add(db, recurring._add_months(date(2026, 9, 12), -k, 12), cents, "Fake Streaming")
    d = found(db, "Fake Streaming")
    assert d.amount_cents == 1799 and not d.amount_varies


def test_next_date_never_closer_than_minimum_gap():
    assert recurring.next_expected_after("monthly", date(2026, 9, 20), "29") >= date(2026, 9, 20) + timedelta(days=25)


@pytest.mark.parametrize("case", ["irregular", "stale", "too_few", "amounts_all_over"])
def test_not_detected(db, case):
    if case == "irregular":
        for d in ("2026-05-02", "2026-05-27", "2026-07-30", "2026-08-03", "2026-09-21"):
            add(db, d, 3000, "Fake Diner")
    elif case == "stale":
        monthly(db, "Fake Diner", 1599, 12, months=5, last=date(2026, 6, 12))   # cancelled in July
    elif case == "too_few":
        monthly(db, "Fake Diner", 1599, 12, months=2)
    else:
        for k, cents in enumerate([1000, 9000, 3000, 15000, 500]):
            add(db, recurring._add_months(date(2026, 9, 12), -k, 12), cents, "Fake Diner")
    assert found(db, "Fake Diner") is None


def test_same_day_duplicates_dont_break_schedule(db):
    monthly(db, "Fake Gym", 4000, 5, months=4)
    add(db, date(2026, 9, 5), 4000, "Fake Gym")    # charged twice on one day
    assert found(db, "Fake Gym").frequency == "monthly"


def test_transfer_kind(db):
    monthly(db, "Fake Savings Sweep", 20000, 2, months=4, key="TRANSFER_OUT")
    assert found(db, "Fake Savings Sweep").kind == "transfer"


# ---------------------------------------------------------------- one description, several payments (amount bands)

SHARED = "FAKE BANK FUNDS TRANSFER"


def shared_description(conn):
    """Rent and a monthly fee paid with the same bank text as irregular savings transfers (user's report 2026-10-16)."""
    for k in range(12):                                                    # rent, $1,450 on the 1st, Nov 2025 - Oct 2026
        add(conn, recurring._add_months(date(2026, 10, 1), -k, 1), 145000, SHARED, key="RENT_AND_UTILITIES")
    for k in range(12):                                                    # a $35 fee on the 5th
        add(conn, recurring._add_months(date(2026, 9, 5), -k, 5), 3500, SHARED, key="RENT_AND_UTILITIES")
    for k in range(6):                                                     # $4,500 transfers, several a month, irregular
        for day in (13, 15, 21):
            add(conn, recurring._add_months(date(2026, 9, 1), -k, day), 450000, SHARED, key="TRANSFER_OUT")


def test_shared_description_split_by_amount(db):
    shared_description(db)
    base = recurring.merchant_key(SHARED, None)
    hits = {d.merchant_key: d for d in recurring.detect(db, TODAY) if recurring.split_key(d.merchant_key)[0] == base}
    assert set(hits) == {f"{base} @145000", f"{base} @3500"}               # the $4,500 transfers aren't regular
    rent = hits[f"{base} @145000"]
    assert (rent.amount_cents, rent.frequency, rent.days_of_month, rent.kind) == (145000, "monthly", "1", "bill")
    assert rent.next_expected == date(2026, 11, 1) and rent.occurrences == 12
    assert recurring.split_key(f"{base} @145000") == (base, 145000) and recurring.split_key(base) == (base, None)


def test_amount_bands_need_a_fixed_amount_and_more_than_a_coincidence(db):
    # a restaurant: many prices, the same $19.58 a year apart, and a near-monthly $30-ish that varies: none recurring
    for day, cents in [("2025-09-17", 1958), ("2026-09-17", 1958), ("2026-01-03", 4410), ("2026-02-11", 1225),
                       ("2026-03-02", 3000), ("2026-04-01", 3290), ("2026-05-02", 2830), ("2026-06-01", 3100)]:
        add(db, day, cents, "Fake Diner", key="FOOD_AND_DRINK")
    assert not [d for d in recurring.detect(db, TODAY) if d.merchant_key.startswith("fake diner")]


def bands_of(conn, merchant):
    base = recurring.merchant_key(merchant, None)
    return {recurring.split_key(d.merchant_key)[1]: d for d in recurring.detect(conn, TODAY)
            if recurring.split_key(d.merchant_key)[0] == base and recurring.split_key(d.merchant_key)[1]}


def test_amount_band_occurrences_depend_on_the_category(db):
    """User's request 2026-10-16: yearly bands are allowed, and everyday spending (food) needs more occurrences than
    bills (utilities, subscriptions): bills use the usual minimums, EVERYDAY_MIN_OCCURRENCES for food."""
    for k in range(3):                                         # other amounts so the description fails as a whole
        add(db, f"2026-0{k + 3}-1{k}", 777700 + k * 50000, SHARED, key="TRANSFER_OUT")
    add(db, "2025-04-20", 9500, SHARED, key="BANK_FEES")      # a $95 yearly fee: 2 are enough
    add(db, "2026-04-20", 9500, SHARED, key="BANK_FEES")
    for k in range(3):                                         # a $60 utility bill on the 9th, 3 months: enough
        add(db, recurring._add_months(date(2026, 9, 9), -k, 9), 6000, SHARED, key="RENT_AND_UTILITIES")
    bands = bands_of(db, SHARED)
    assert bands[9500].frequency == "annual" and bands[6000].frequency == "monthly"
    # food at a fixed price: 2 months isn't enough, 3 are (user's request 2026-10-16: you can dismiss it)
    for k in range(2):
        add(db, recurring._add_months(date(2026, 9, 12), -k, 12), 1450, "Fake Deli", key="FOOD_AND_DRINK")
    add(db, "2026-02-02", 5555, "Fake Deli", key="FOOD_AND_DRINK")
    add(db, "2026-02-03", 300, "Fake Deli", key="FOOD_AND_DRINK")
    assert 1450 not in bands_of(db, "Fake Deli")
    add(db, recurring._add_months(date(2026, 9, 12), -2, 12), 1450, "Fake Deli", key="FOOD_AND_DRINK")
    assert bands_of(db, "Fake Deli")[1450].frequency == "monthly"
    # but a weekly food price needs EVERYDAY_MIN_OCCURRENCES["weekly"] (8), where a bill needs 4
    for k in range(5):
        add(db, date(2026, 9, 29) - timedelta(days=7 * k), 875, "Fake Deli", key="FOOD_AND_DRINK")
    assert 875 not in bands_of(db, "Fake Deli")


def test_amount_band_items_match_only_their_amount_and_follow_a_raise(db):
    shared_description(db)
    assert recurring.refresh(db, TODAY)["new"] == 2
    assert recurring.refresh(db, TODAY) == {"new": 0, "updated": 2, "removed": 0}    # stable on the next check
    rent = db.execute("SELECT * FROM recurring_items WHERE amount_cents = 145000").fetchone()
    ids = recurring.matching_transaction_ids(db, rent)
    amounts = {r[0] for r in db.execute(f"SELECT amount_cents FROM transactions WHERE id IN ({','.join('?' * len(ids))})",
                                        list(ids))}
    assert len(ids) == 12 and amounts == {145000}                          # not the fee, not the $4,500 transfers
    # a stored band whose amount moved a little (a rent increase) is the same item, not a new one
    moved = {**dict(rent), "merchant_key": rent["merchant_key"].replace("@145000", "@140000"), "amount_cents": 140000}
    detection = next(d for d in recurring.detect(db, TODAY) if d.amount_cents == 145000)
    assert recurring._stored_item([moved], detection) is moved
    far = {**moved, "amount_cents": 100000, "merchant_key": moved["merchant_key"].replace("@140000", "@100000")}
    assert recurring._stored_item([far], detection) is None


# ---------------------------------------------------------------- review lifecycle

def stored(conn, merchant):
    return conn.execute(
        "SELECT * FROM recurring_items WHERE merchant_key = ?", (recurring.merchant_key(merchant, None),)
    ).fetchone()


def test_refresh_lifecycle(db):
    monthly(db, "Fake Streaming", 1599, 12, months=4)
    assert recurring.refresh(db, TODAY)["new"] == 1
    item = stored(db, "Fake Streaming")
    assert item["status"] == "detected" and item["source"] == "local"

    # Dismissed: never comes back.
    recurring.set_status(db, item["id"], "dismissed")
    recurring.refresh(db, TODAY)
    assert stored(db, "Fake Streaming")["status"] == "dismissed"


def test_confirmed_keeps_edits_and_advances(db):
    monthly(db, "Fake Streaming", 1599, 12, months=4)
    recurring.refresh(db, TODAY)
    item = stored(db, "Fake Streaming")
    recurring.save_item(db, item["id"], name="Streaming (family plan)", amount_text="22.99", direction="out",
                        frequency="monthly", next_date="2026-10-12", account_id=CHECKING, category_key="ENTERTAINMENT")
    add(db, date(2026, 10, 12), 2299, "Fake Streaming")
    recurring.refresh(db, date(2026, 10, 13))
    after = stored(db, "Fake Streaming")
    assert (after["name"], after["amount_cents"], after["status"]) == ("Streaming (family plan)", 2299, "confirmed")
    assert (after["last_seen_date"], after["next_expected_date"]) == ("2026-10-12", "2026-11-12")


def test_unreviewed_detection_removed_when_pattern_stops(db):
    monthly(db, "Fake Streaming", 1599, 12, months=4)
    recurring.refresh(db, TODAY)
    assert recurring.refresh(db, date(2027, 2, 1))["removed"] == 1     # stale by then
    assert stored(db, "Fake Streaming") is None


def test_manual_item_validation_and_delete(db):
    with pytest.raises(recurring.RecurringError):
        recurring.save_item(db, None, name="Rent", amount_text="1450", direction="out", frequency="semimonthly",
                            next_date="2026-11-01", account_id=CHECKING, category_key=None, days_of_month="1")
    item_id = recurring.save_item(db, None, name="Rent", amount_text="1,450", direction="out", frequency="monthly",
                                  next_date="2026-11-01", account_id=CHECKING, category_key="RENT_AND_UTILITIES")
    row = db.execute("SELECT * FROM recurring_items WHERE id = ?", (item_id,)).fetchone()
    assert (row["status"], row["source"], row["amount_cents"], row["type"], row["days_of_month"]) == (
        "confirmed", "manual", 145000, "bill", "1")
    recurring.delete_item(db, item_id)
    assert db.execute("SELECT COUNT(*) FROM recurring_items").fetchone()[0] == 0


# ---------------------------------------------------------------- upcoming (dashboard)

def test_upcoming_next_7_days(db):
    pay = recurring.save_item(db, None, name="Fake Payroll", amount_text="1000", direction="in",
                              frequency="semimonthly", next_date="2026-10-21", account_id=CHECKING,
                              category_key=None, days_of_month="6,21")
    recurring.save_item(db, None, name="Fake Gym", amount_text="40", direction="out", frequency="monthly",
                        next_date="2026-10-08", account_id=CARD, category_key=None)
    recurring.save_item(db, None, name="Fake Phone", amount_text="60", direction="out", frequency="monthly",
                        next_date="2026-10-01", account_id=CARD, category_key=None)            # overdue
    recurring.save_item(db, None, name="Fake Rent", amount_text="1450", direction="out", frequency="monthly",
                        next_date="2026-10-25", account_id=CHECKING, category_key=None)        # outside window

    got = [(u["name"], u["date"], u["overdue"]) for u in recurring.upcoming(db, TODAY, days=7)]
    # Oct 6 payroll is skipped: the next expected payday is Oct 21 (Oct 6 was already seen).
    assert got == [("Fake Phone", "2026-10-01", True), ("Fake Gym", "2026-10-08", False)]

    db.execute("UPDATE recurring_items SET next_expected_date = '2026-10-06' WHERE id = ?", (pay,))
    assert [u["name"] for u in recurring.upcoming(db, TODAY, days=7)] == ["Fake Phone", "Fake Payroll", "Fake Gym"]
