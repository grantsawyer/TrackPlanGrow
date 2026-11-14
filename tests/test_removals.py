"""Posted transactions the bank removed (removals.py, migration 0038, user's request 2026-10-22). Fake data only."""

import pytest

from fintrack import categories, items, manual, purposes, removals

ORIGIN = {"origin": "https://localhost:8443"}
CHECKING, OTHER = 1, 2


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    for acc_id in (CHECKING, OTHER):
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification)"
                     " VALUES (?, 1, ?, ?, 'depository', ?)", (acc_id, f"acc-{acc_id}", f"Fake checking {acc_id}",
                                                              items.classify("depository")))
    conn.commit()
    return conn


_ids = iter(range(1, 100_000))


def add(conn, cents, day, name="FAKE TRANSFER", account=CHECKING, key="TRANSFER_OUT", pending=0):
    tid = conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key,"
        " plaid_category_detailed, pending) VALUES (?, ?, ?, ?, ?, ?, 'TRANSFER_OUT_ACCOUNT_TRANSFER', ?)",
        (f"r{next(_ids)}", account, day, cents, name, key, pending)).lastrowid
    conn.commit()
    return tid


def remove(conn, *ids):
    """What sync does with a posted transaction Plaid lists as removed."""
    conn.executemany("UPDATE transactions SET removed_at = CURRENT_TIMESTAMP WHERE id = ?", [(i,) for i in ids])
    conn.commit()


def kind(conn, tid):
    return conn.execute("SELECT kind FROM transactions_effective WHERE id = ?", (tid,)).fetchone()[0]


def test_a_return_is_found_by_amount_first_then_by_text(db):
    old = add(db, 400000, "2026-10-13")
    remove(db, old)
    add(db, 400000, "2026-10-13", account=OTHER)                      # another account: never
    add(db, 399900, "2026-10-14", name="SOMETHING ELSE")              # different amount and text: never
    far = add(db, 400000, "2026-10-25")                               # more than 7 days later: never
    near_text = add(db, 510000, "2026-10-13")                         # same text, corrected amount
    same = add(db, 400000, "2026-10-15")                              # same amount: wins over text
    assert removals.match_returns(db) == 1
    assert db.execute("SELECT returned_id FROM transactions WHERE id = ?", (old,)).fetchone()[0] == same
    review = removals.pending_review(db)
    assert len(review) == 1 and review[0].new["id"] == same and review[0].changes == ["Date"]
    # Not the same: forgotten, never suggested again; the text match is next
    removals.not_same(db, old)
    review = removals.pending_review(db)[0]
    assert review.new["id"] == near_text and review.changes == ["Amount"]
    removals.not_same(db, old)
    assert removals.pending_review(db)[0].new is None and far not in (same, near_text)


def test_a_transaction_already_there_before_the_removal_is_not_its_return(db):
    """Seen 2026-10-22: a later autopay with the same bank text, synced days before the removal, was suggested."""
    old = add(db, 6767, "2026-10-13", name="FAKE CARD AUTOPAY")
    earlier = add(db, 542078, "2026-10-19", name="FAKE CARD AUTOPAY")
    db.execute("UPDATE transactions SET created_at = datetime('now', '-2 days') WHERE id IN (?, ?)", (old, earlier))
    remove(db, old)
    assert removals.match_returns(db) == 0
    later = add(db, 6767, "2026-10-13", name="FAKE CARD AUTOPAY")   # sent again after the removal
    assert removals.match_returns(db) == 1 and removals.pending_review(db)[0].new["id"] == later


def test_your_choices_keep_counting_until_you_decide(db):
    lent = manual.create_account(db, "Fake loan to a friend", "lent", "0", "2026-08-01")
    purposes.adopt_opening_value(db, lent)
    old = add(db, 400000, "2026-10-13")
    purposes.set_link(db, old, lent)
    purposes.update(db)
    assert manual.get_account(db, lent)["current_balance_cents"] == 400000
    remove(db, old)
    new = add(db, 400000, "2026-10-14")
    removals.match_returns(db)
    purposes.update(db)
    # the removed one keeps counting with its money-lent link; its return isn't counted twice
    assert (kind(db, old), kind(db, new)) == ("transfer", "excluded")
    assert manual.get_account(db, lent)["current_balance_cents"] == 400000
    # Use the returned one: the link moves across, the removed one goes
    assert removals.use_return(db, old) == new
    purposes.update(db)
    assert kind(db, new) == "transfer" and purposes.link_of(db, new) == lent
    assert db.execute("SELECT COUNT(*) FROM transactions WHERE id = ?", (old,)).fetchone()[0] == 0
    assert manual.get_account(db, lent)["current_balance_cents"] == 400000


def test_use_return_carries_your_edits_without_overwriting_its_own(db):
    old = add(db, 6767, "2026-10-13", name="FAKE SHOP", key="GENERAL_MERCHANDISE")
    categories.set_transaction_category(db, old, "TRAVEL")
    categories.set_transaction_description(db, old, "My fake note")
    item_id = db.execute("INSERT INTO recurring_items (name, amount_cents, frequency, next_expected_date, type, status,"
                         " source) VALUES ('Fake bill', 6767, 'monthly', '2026-11-13', 'bill', 'confirmed', 'manual')").lastrowid
    db.execute("INSERT INTO recurring_links (transaction_id, item_id) VALUES (?, ?)", (old, item_id))
    db.commit()
    remove(db, old)
    new = add(db, 6800, "2026-10-14", name="FAKE SHOP", key="GENERAL_MERCHANDISE")
    categories.set_transaction_description(db, new, "Its own note")
    removals.match_returns(db)
    removals.use_return(db, old)
    row = db.execute("SELECT * FROM transactions_effective WHERE id = ?", (new,)).fetchone()
    assert (row["user_category"], row["description"]) == ("TRAVEL", "Its own note")
    assert db.execute("SELECT item_id FROM recurring_links WHERE transaction_id = ?", (new,)).fetchone()[0] == item_id


def test_use_return_carries_your_tags(db):
    from fintrack import tags
    old = add(db, 6767, "2026-10-13", name="FAKE SHOP", key="GENERAL_MERCHANDISE")
    tags.add(db, [old], "Fake Project")
    tags.add(db, [old], "🎁 Fake Gifts")
    remove(db, old)
    new = add(db, 6767, "2026-10-14", name="FAKE SHOP", key="GENERAL_MERCHANDISE")
    tags.add(db, [new], "Fake Project")                                   # already has one of them
    removals.match_returns(db)
    removals.use_return(db, old)
    assert [g.name for g in tags.tags_of(db, [new])[new]] == ["Fake Project", "🎁 Fake Gifts"]


def test_keep_and_delete(db):
    kept, gone = add(db, 1100, "2026-10-09"), add(db, 2200, "2026-10-09", name="OTHER FAKE")
    remove(db, kept, gone)
    assert removals.count(db) == 2
    removals.keep(db, kept)
    removals.delete(db, gone)
    assert removals.count(db) == 0
    assert [r[0] for r in db.execute("SELECT id FROM transactions")] == [kept]
    with pytest.raises(LookupError):
        removals.keep(db, kept)                                         # no longer waiting for review


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_the_review_card_is_on_the_account_page_only(client, db):
    assert 'id="removed-review"' not in client.get(f"/accounts/{CHECKING}").text
    old = add(db, 400000, "2026-10-13")
    remove(db, old)
    new = add(db, 400000, "2026-10-14")
    removals.match_returns(db)
    page = client.get(f"/accounts/{CHECKING}").text
    card = page.split('id="removed-review"')[1].split("</section>")[0]
    assert "Not the same" in card and "Use the returned one" in card and "Date changed" in card
    assert f'value="/accounts/{CHECKING}"' in card                     # decisions come back to this page
    assert 'id="removed-review"' not in client.get(f"/accounts/{OTHER}").text
    # Transactions keeps the row badges only; the Overview says nothing (user's request 2026-10-22)
    listing = client.get("/transactions", params={"range": "all"}).text
    assert 'id="removed-review"' not in listing
    assert 'aria-label="Removed by the bank"' in listing.split(f'<tr id="txn-{old}"')[1].split("</tr>")[0]
    assert 'aria-label="Not counted until you review"' in listing.split(f'<tr id="txn-{new}"')[1].split("</tr>")[0]
    assert "Removed by the bank" not in client.get("/").text
    r = client.post(f"/transactions/{old}/removal", data={"action": "use", "return_url": f"/accounts/{CHECKING}"},
                    headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(f"/accounts/{CHECKING}?msg=")
    assert removals.count(db) == 0
    bad = client.post(f"/transactions/{new}/removal", data={"action": "keep"}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in bad.headers["location"]
