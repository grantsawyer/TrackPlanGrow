"""Rewards spent as transactions (user's requests 2026-11-03 / 04; rewards.py, manual_transactions.py, migration 0062), and
bonus points. All data made up."""

from datetime import date

import pytest

from fintrack import items, manual_transactions, rewards, spending, tags

CARD, OTHER_CARD = 1, 2
TODAY = date(2026, 11, 3)


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    for acc_id, name in [(CARD, "Fake Travel Card"), (OTHER_CARD, "Fake Other Card")]:
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, mask, type, classification)"
                     " VALUES (?, 1, ?, ?, '0000', 'credit', ?)", (acc_id, f"acc-{acc_id}", name, items.classify("credit")))
    conn.commit()
    rewards.set_up(conn, CARD, "points", 100)                      # 1x on everything
    return conn


_ids = iter(range(1, 10_000))


def add(conn, amount, day, key, name="FAKE SHOP", account=CARD) -> int:
    tid = next(_ids)
    conn.execute("INSERT INTO transactions (id, plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key)"
                 " VALUES (?, ?, ?, ?, ?, ?, ?)", (tid, f"rr{tid}", account, day, amount, name, key))
    conn.commit()
    return tid


def flight(conn, **kw) -> int:
    """A $750 flight paid with 50,000 points, tagged for a fake trip."""
    args = dict(account_id=CARD, day="2026-10-20", description="Fake flight to Denver", amount_text="750",
                direction="out", category_key="TRAVEL", tag_names="🏝️ Fake trip", rewards_text="50,000", today=TODAY)
    args.update(kw)
    return manual_transactions.add(conn, **args)


def shown(conn, account=CARD):
    return [tuple(r) for r in conn.execute(
        """SELECT description, amount_cents, category, category_source FROM transactions_effective
           WHERE account_id = ? ORDER BY amount_cents DESC""", (account,))]


def card_spent(conn) -> int:
    return next(c for c in spending.card_spending(conn, "2026-10-01", "2026-10-31") if c.account_id == CARD).spending_cents


def test_paid_with_rewards_is_a_cost_and_a_rebate_in_one_category(db):
    cost = flight(db)
    assert shown(db) == [("Fake flight to Denver", 75000, "TRAVEL", "you"),
                         ("Fake flight to Denver (Credit)", -75000, "TRAVEL", "you")]
    assert card_spent(db) == 0                                     # no money moved: the category nets to zero
    [s] = [s for s in tags.summaries(db) if s.tag.name == "🏝️ Fake trip"]
    assert s.spent_cents == 75000 and [line.label for line in s.lines] == ["Travel"]   # the trip costs $750
    rebate = manual_transactions.pair_of(db, cost)
    assert manual_transactions.pair_of(db, rebate) == cost and rewards.rewards_used(db, [rebate]) == {rebate: 50_000}
    assert sum(rewards.transaction_rewards(db, CARD).values()) == 0     # ordinary spending: the points cancel out


def test_changing_the_category_moves_both_and_deleting_takes_both(db):
    cost = flight(db)
    db.execute("UPDATE transactions SET user_category = 'GENERAL_MERCHANDISE' WHERE id = ?", (cost,))
    db.commit()
    manual_transactions.follow_category(db, cost)
    assert {r[2] for r in shown(db)} == {"GENERAL_MERCHANDISE"}
    manual_transactions.delete(db, manual_transactions.pair_of(db, cost))       # from the rebate's side
    assert shown(db) == []
    bank = add(db, 1000, "2026-10-02", "FOOD_AND_DRINK")
    with pytest.raises(ValueError, match="you added"):
        manual_transactions.delete(db, bank)


def test_a_manual_transaction_paid_in_cash_and_what_is_refused(db):
    manual_transactions.add(db, account_id=OTHER_CARD, day="2026-10-20", description="Fake taxi", amount_text="20",
                            direction="out", category_key="TRANSPORTATION", today=TODAY)
    assert shown(db, OTHER_CARD) == [("Fake taxi", 2000, "TRANSPORTATION", "you")]
    for kw, message in [({"amount_text": ""}, "amount"), ({"category_key": ""}, "category"), ({"day": "2026-12-01"}, "future"),
                        ({"description": " "}, "Describe"), ({"direction": "in"}, "money out"),
                        ({"account_id": OTHER_CARD}, "rewards are set up")]:
        with pytest.raises(ValueError, match=message):
            flight(db, **kw)


def test_balance_takes_off_rewards_used_and_adds_bonus_points(db):
    rewards.set_balance(db, CARD, "2026-10-01", "points", 100_000, today=TODAY)
    add(db, 20000, "2026-10-05", "FOOD_AND_DRINK")                 # $200 at 1x: 200 points
    rewards.save_bonus_points(db, CARD, None, "2026-10-10", "60,000", "Sign-up bonus", today=TODAY)
    flight(db)
    now = rewards.current_balance(db, CARD)
    assert (now.earned_since, now.redeemed_since, now.total) == (60_200, 50_000, 100_000 + 60_200 - 50_000)


def test_points_turned_into_cash_record_the_points_used(db):
    add(db, 30000, "2026-10-02", "TRAVEL")
    credit = add(db, -25000, "2026-10-12", "INCOME", name="FAKE POINTS TO CASH")    # filed as rewards income
    rewards.set_rewards_used(db, credit, 25_000)
    assert card_spent(db) == 30000 and sum(rewards.transaction_rewards(db, CARD).values()) == 300   # income earns nothing
    s = rewards.redeemed_summaries(db, [CARD], "2026-10-01", "2026-10-31")[CARD]
    assert (s.amount, s.value_cents, s.cents_per_point, s.cash_rewards_cents) == (25_000, 25000, 1.0, 0)
    with pytest.raises(ValueError, match="money in"):
        rewards.set_rewards_used(db, next(i for i, in db.execute("SELECT id FROM transactions WHERE amount_cents = 30000")), 5)

def test_credit_cards_summary_value_per_point_and_share_of_cost(db):
    add(db, 100000, "2026-10-05", "TRAVEL")                        # $1,000 spent
    add(db, -35000, "2026-10-06", "INCOME", name="FAKE SIGN-UP BONUS")    # a cash sign-up bonus: income, not rewards used
    flight(db)                                                     # 50,000 points -> $750
    s = rewards.redeemed_summaries(db, [CARD, OTHER_CARD], "2026-10-01", "2026-10-31")[CARD]
    assert (s.amount, s.value_cents, s.cents_per_point, s.cash_rewards_cents) == (50_000, 75000, 1.5, 35000)
    assert s.share_of(card_spent(db)) == pytest.approx(75000 / 175000)     # $750 of $1,750
    rewards.save_bonus_points(db, CARD, None, "2026-10-10", "60,000", "Sign-up bonus", today=TODAY)
    [bonus] = rewards.card_bonuses(db, [CARD], "2026-10-01", "2026-10-31")[CARD]
    assert (bonus.transaction_id, bonus.description, bonus.amount) == (None, "Sign-up bonus", 60_000)


# ---------------------------------------------------------------- pages

ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_add_a_transaction_on_the_transactions_page(client, db):
    today = date.today().isoformat()
    page = client.get("/transactions").text
    assert 'action="/transactions/new"' in page and 'value="redemption" data-rule-kind' in page
    assert '<fieldset data-for="redemption" class="wide" hidden disabled>' in page     # shown for a redemption only
    cash = client.post("/transactions/new", headers=ORIGIN, follow_redirects=False, data={
        "date": today, "account": str(CARD), "description": "Fake lunch", "amount": "20", "direction": "out",
        "category": "FOOD_AND_DRINK", "type": "cash", "rewards": "5,000"})                # a cash one ignores rewards
    assert "msg=" in cash.headers["location"] and not db.execute("SELECT 1 FROM transaction_rewards_used").fetchone()
    db.execute("DELETE FROM transactions")
    db.commit()
    missing = client.post("/transactions/new", headers=ORIGIN, follow_redirects=False, data={
        "date": today, "account": str(CARD), "description": "Fake flight", "amount": "750", "direction": "out",
        "category": "TRAVEL", "type": "redemption", "rewards": ""})
    assert "error=Enter+the+rewards" in missing.headers["location"]
    saved = client.post("/transactions/new", headers=ORIGIN, follow_redirects=False, data={
        "date": today, "account": str(CARD), "description": "Fake flight", "amount": "750", "direction": "out",
        "category": "TRAVEL", "tags": "🏝️ Fake trip", "type": "redemption", "rewards": "50,000"})
    assert "msg=" in saved.headers["location"]
    cost = db.execute("SELECT id FROM transactions WHERE amount_cents = 75000").fetchone()[0]
    rebate = manual_transactions.pair_of(db, cost)
    panel = client.get(f"/transactions/{rebate}/edit").text
    assert 'name="rewards_used"' in panel and 'value="50,000"' in panel and 'hx-post="/transactions/' in panel and 'class="icon-delete" hx-post="/transactions/' in panel
    cards_page = client.get("/credit-cards?range=ytd").text
    assert "Redeemed · 1.50¢ each" in cards_page and "$750" in cards_page
    assert "<h2>Redemptions</h2>" not in client.get(f"/accounts/{CARD}").text     # rewards are transactions now
    gone = client.post(f"/transactions/{cost}/delete", headers=ORIGIN)
    assert gone.headers.get("HX-Refresh") == "true" and not db.execute("SELECT 1 FROM transactions").fetchone()
    bad = client.post("/transactions/new", headers=ORIGIN, follow_redirects=False,
                      data={"date": today, "account": str(CARD), "description": "x", "amount": "", "category": "TRAVEL"})
    assert "error=" in bad.headers["location"]


# ---------------------------------------------------------------- a bank charge partly paid with rewards (migration 0063)

def test_a_charge_partly_paid_with_rewards(db):
    charge = add(db, 5000, "2026-10-20", "TRAVEL", name="FAKE AIRLINE")         # $50 charged: a $200 ticket
    tags.add(db, [charge], "🏝️ Fake trip")
    manual_transactions.set_part(db, charge, "150", "10,000")
    assert manual_transactions.part_of(db, charge) == (15000, 10_000)
    assert card_spent(db) == 5000                                  # the category counts the $50 that left
    [s] = [s for s in tags.summaries(db) if s.tag.name == "🏝️ Fake trip"]
    assert s.spent_cents == 20000                                  # the trip: what the ticket cost
    assert sum(rewards.transaction_rewards(db, CARD).values()) == 50     # the pair's points cancel: the $50 earns
    # it follows the charge: category, tags; changing and clearing
    from fintrack import categories
    categories.set_transaction_category(db, charge, "GENERAL_MERCHANDISE")
    assert {r[2] for r in shown(db)} == {"GENERAL_MERCHANDISE"}
    tags.remove(db, [charge], tags.tag_by_name(db, "🏝️ Fake trip").id)
    assert not db.execute("SELECT 1 FROM transaction_tags").fetchone()
    manual_transactions.set_part(db, charge, "120", "8,000")
    assert manual_transactions.part_of(db, charge) == (12000, 8_000) and len(shown(db)) == 3
    manual_transactions.set_part(db, charge, "", "")
    assert manual_transactions.part_of(db, charge) is None and len(shown(db)) == 1
    with pytest.raises(ValueError, match="bank's charge"):
        manual_transactions.set_part(db, add(db, -1000, "2026-10-21", "TRAVEL"), "5", "500")


def test_a_pair_goes_when_its_charge_does(db):
    charge = add(db, 5000, "2026-10-20", "TRAVEL")
    manual_transactions.set_part(db, charge, "150", "10,000")
    db.execute("DELETE FROM transactions WHERE id = ?", (charge,))       # a pending charge the bank dropped
    db.commit()
    manual_transactions.sync_parts(db)
    assert shown(db) == []


def test_the_panel_says_a_charge_was_partly_paid_with_rewards(client, db):
    charge = add(db, 5000, date.today().isoformat(), "TRAVEL", name="FAKE AIRLINE")
    panel = client.get(f"/transactions/{charge}/edit").text
    assert 'name="part_value"' in panel and 'name="part_rewards"' in panel
    bad = client.post(f"/transactions/{charge}/edit", headers=ORIGIN, data={"part_shown": "1", "part_value": "150", "part_rewards": ""})
    assert bad.headers.get("HX-Retarget") == f"#part-msg-{charge}"
    client.post(f"/transactions/{charge}/edit", headers=ORIGIN,
                data={"part_shown": "1", "part_value": "150", "part_rewards": "10,000", "category": "", "description": ""})
    assert manual_transactions.part_of(db, charge) == (15000, 10_000)
    assert 'value="150.00"' in client.get(f"/transactions/{charge}/edit").text


def test_rows_you_added_have_their_icon(client, db):
    flight(db, day=date.today().isoformat(), today=date.today())
    add(db, 1000, date.today().isoformat(), "FOOD_AND_DRINK")
    page = client.get("/transactions").text
    assert page.count('aria-label="Added by you"') == 2               # the cost and its rebate, not the bank's row


def test_a_rewards_pair_is_ordinary_spending_in_the_rate_table(db):
    add(db, 10000, "2026-10-05", "FOOD_AND_DRINK")
    flight(db)
    est = rewards.estimates(db, spending.card_spending(db, "2026-10-01", "2026-10-31"), "2026-10-01", "2026-10-31")[CARD]
    lines = {line.category: (line.amount_cents, line.count, line.earned) for g in est.groups for line in g.lines}
    assert lines == {"FOOD_AND_DRINK": (10000, 1, 100), "TRAVEL": (0, 2, 0)}      # the pair nets to zero
    assert est.total == 100 and sum(g.spending_cents for g in est.groups) == card_spent(db)


def test_a_redemption_s_panels_name_each_other_and_skip_recurring_and_bonus(client, db):
    cost = flight(db, day=date.today().isoformat(), today=date.today())
    credit = manual_transactions.pair_of(db, cost)
    panel = client.get(f"/transactions/{cost}/edit").text
    assert 'name="recurring"' not in panel and 'name="bonus"' not in panel and 'name="rewards_used"' not in panel
    assert "50,000 points" in panel and "Rewards redemption · credit: Fake flight to Denver (Credit)" in panel
    panel = client.get(f"/transactions/{credit}/edit").text
    assert 'name="rewards_used"' in panel and 'value="50,000"' in panel and 'name="recurring"' not in panel
    assert "Rewards redemption · cost: Fake flight to Denver," in panel
    cash = manual_transactions.add(db, account_id=CARD, day=date.today().isoformat(), description="Fake taxi",
                                   amount_text="20", direction="out", category_key="TRANSPORTATION")
    panel = client.get(f"/transactions/{cash}/edit").text
    assert "Manual cash transaction" in panel and "From the bank" not in panel and 'name="recurring"' in panel
