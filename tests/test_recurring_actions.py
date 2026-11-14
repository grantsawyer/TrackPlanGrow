"""Recurring items' "Then do this" (user's request 2026-11-03; migration 0060): a confirmed item's actions apply to each of
its transactions, ahead of rules and behind your own choices. Fixed fake data."""

from datetime import date

import pytest

from fintrack import items, recurring, rules

CHECKING = 1
ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification, current_balance_cents)"
                 " VALUES (?, 1, 'acc-1', 'Fake Checking', 'depository', ?, 500000)", (CHECKING, items.classify("depository")))
    conn.commit()
    return conn


_ids = iter(range(1, 1_000_000))


def add(conn, day, cents, name="FAKE BANK TRANSFER", key="TRANSFER_OUT") -> int:
    tid = next(_ids)
    conn.execute("INSERT INTO transactions (id, plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key)"
                 " VALUES (?, ?, ?, ?, ?, ?, ?)", (tid, f"ra{tid}", CHECKING, day, cents, name, key))
    conn.commit()
    return tid


def rent_item(conn, status="confirmed") -> int:
    """A monthly $1,600 band on the shared transfer text (like a detected rent)."""
    key = f"{recurring.merchant_key(None, 'FAKE BANK TRANSFER')}{recurring.AMOUNT_MARK}160000"
    item_id = conn.execute(
        """INSERT INTO recurring_items (name, merchant_key, account_id, category_key, amount_cents, frequency, days_of_month,
               next_expected_date, type, status, source) VALUES ('Fake Rent', ?, ?, 'RENT_AND_UTILITIES', 160000,
               'monthly', '1', '2026-12-01', 'bill', ?, 'local')""", (key, CHECKING, status)).lastrowid
    conn.commit()
    return item_id


def shown(conn, tid):
    return conn.execute("SELECT category, kind, category_source, description FROM transactions_effective WHERE id = ?",
                        (tid,)).fetchone()


def save_actions(conn, item_id, actions):
    item = conn.execute("SELECT * FROM recurring_items WHERE id = ?", (item_id,)).fetchone()
    recurring.save_item(conn, item_id, name=item["name"], amount_text="1600", direction="out", frequency="monthly",
                        next_date=item["next_expected_date"], account_id=CHECKING, actions=actions)
    rules.run(conn)


def test_item_category_lands_on_its_transactions_only(db):
    rent = [add(db, d, 160000) for d in ("2026-10-01", "2026-11-02")]
    other = add(db, "2026-11-05", 500000)                       # same text, another amount: not the item's
    item_id = rent_item(db)
    save_actions(db, item_id, [rules.Action("set_category", "RENT_AND_UTILITIES")])
    for tid in rent:
        assert tuple(shown(db, tid))[:3] == ("RENT_AND_UTILITIES", "expense", "recurring")   # now spending
    assert shown(db, other)["category"] == "TRANSFER_OUT"
    assert db.execute("SELECT category_key FROM recurring_items WHERE id = ?", (item_id,)).fetchone()[0] == "RENT_AND_UTILITIES"


def test_your_choice_beats_the_item_and_the_item_beats_a_rule(db):
    yours, ruled = add(db, "2026-10-01", 160000), add(db, "2026-11-02", 160000)
    db.execute("UPDATE transactions SET user_category = 'GENERAL_SERVICES' WHERE id = ?", (yours,))
    db.commit()
    rules.save(db, None, [rules.Condition("description", "contains", ["fake bank"])],
               [rules.Action("set_category", "GENERAL_MERCHANDISE"), rules.Action("set_description", "Rule text")])
    save_actions(db, rent_item(db), [rules.Action("set_category", "RENT_AND_UTILITIES")])
    assert shown(db, yours)["category"] == "GENERAL_SERVICES"
    assert shown(db, ruled)["category"] == "RENT_AND_UTILITIES"
    assert shown(db, ruled)["description"] == "Rule text"       # the item sets no description: the rule's stays
    preview = rules.preview(db, [rules.Condition("amount", "equals", [160000])],
                            [rules.Action("set_category", "GENERAL_SERVICES")])
    assert "keeps its recurring item's category" in [s["outcome"] for s in preview.samples]


def test_dismissing_the_item_takes_its_category_back(db):
    tid = add(db, "2026-11-02", 160000)
    item_id = rent_item(db)
    save_actions(db, item_id, [rules.Action("set_category", "RENT_AND_UTILITIES")])
    recurring.set_status(db, item_id, "dismissed")
    rules.run(db)
    assert tuple(shown(db, tid))[:3] == ("TRANSFER_OUT", "transfer", "plaid")


def test_tags_follow_and_one_time_actions_run_once(db):
    tid = add(db, "2026-11-02", 160000)
    item_id = rent_item(db)
    save_actions(db, item_id, [rules.parse_action(db, "add_tag", "🏠 Home"), rules.Action("not_transfer")])
    tag = db.execute("SELECT id FROM tags WHERE name = '🏠 Home'").fetchone()[0]
    assert db.execute("SELECT from_rule FROM transaction_tags WHERE transaction_id = ? AND tag_id = ?", (tid, tag)).fetchone()[0] == 1
    assert db.execute("SELECT transfer_ignore FROM transactions WHERE id = ?", (tid,)).fetchone()[0] == 1
    db.execute("UPDATE transactions SET transfer_ignore = 0 WHERE id = ?", (tid,))        # you undo it
    db.commit()
    rules.run(db)
    assert db.execute("SELECT transfer_ignore FROM transactions WHERE id = ?", (tid,)).fetchone()[0] == 0   # not redone
    save_actions(db, item_id, [])                                                         # actions removed
    assert not db.execute("SELECT 1 FROM transaction_tags WHERE transaction_id = ?", (tid,)).fetchone()


def test_recurring_is_not_an_item_action(db):
    with pytest.raises(recurring.RecurringError):
        save_actions(db, rent_item(db), [rules.Action("recurring", "none")])


# ---------------------------------------------------------------- the window

@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_confirm_opens_the_window_with_matches_and_actions(client, db):
    for d in ("2026-09-01", "2026-10-01", "2026-11-02"):
        add(db, d, 160000)
    item_id = rent_item(db, status="detected")
    row = client.get("/recurring").text.split("Needs review (1)")[1].split("</section>")[0]
    assert f'<a class="button small" href="/recurring/{item_id}/edit">Confirm</a>' in row
    page = client.get(f"/recurring/{item_id}/edit").text
    matches, then = page.split("<h2>Matches</h2>")[1].split("<h2>Then do this</h2>")
    assert "Description like “fake bank transfer”, amount within 10% · 3 transactions" in matches
    assert 'name="amount"' in matches and 'name="category_key"' not in page
    # prefilled with the item's category, as a "Set category" row; "Recurring" isn't offered
    first = then.split('<div class="rule-row" data-rule-row>')[1]
    assert '<option value="set_category" selected>' in first and 'value="RENT_AND_UTILITIES" selected' in first
    assert 'value="recurring"' not in then.split("<template")[0] and ">Confirm</button>" in then

    saved = client.post("/recurring", headers=ORIGIN, follow_redirects=False, data={
        "item_id": str(item_id), "name": "Fake Rent", "amount": "1600", "direction": "out", "frequency": "monthly",
        "next_date": "2026-12-01", "account_id": str(CHECKING),
        "act-0-kind": "set_category", "act-0-value": "RENT_AND_UTILITIES",
        "act-1-kind": "set_description", "act-1-value": "Fake Rent"})
    assert saved.headers["location"].startswith("/recurring?msg=")
    assert db.execute("SELECT status FROM recurring_items WHERE id = ?", (item_id,)).fetchone()[0] == "confirmed"
    assert [(a.kind, a.value) for a in recurring.actions_of(db, item_id)] == [
        ("set_category", "RENT_AND_UTILITIES"), ("set_description", "Fake Rent")]
    latest = db.execute("SELECT category, kind, description FROM transactions_effective WHERE date = '2026-11-02'").fetchone()
    assert tuple(latest) == ("RENT_AND_UTILITIES", "expense", "Fake Rent")


def test_a_bad_action_row_returns_to_the_window(client, db):
    item_id = rent_item(db)
    bad = client.post("/recurring", headers=ORIGIN, follow_redirects=False, data={
        "item_id": str(item_id), "name": "Fake Rent", "amount": "1600", "direction": "out", "frequency": "monthly",
        "next_date": "2026-12-01", "act-0-kind": "set_category", "act-0-value": ""})
    assert bad.headers["location"].startswith(f"/recurring/{item_id}/edit?error=")
    assert recurring.actions_of(db, item_id) == []
