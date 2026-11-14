"""Transfer matching you control (Transfers page, user's request 2026-10-26; transfers.py, migration 0043): pairs you match
are kept through re-matching and win over the automatic choice, only pairs that meet the rules can be matched, removing a
match marks both halves "Not a transfer", the options list, the summary by route, and the page. All data is made up."""

import pytest

from fintrack import items, transfers

CHECKING, SAVINGS, CARD, BROKERAGE, MORTGAGE, HIDDEN = 1, 2, 3, 4, 5, 6
ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    for acc_id, name, account_type, hidden in [
        (CHECKING, "Fake Checking", "depository", 0), (SAVINGS, "Fake Savings", "depository", 0),
        (CARD, "Fake Card", "credit", 0), (BROKERAGE, "Fake Brokerage", "investment", 0),
        (MORTGAGE, "Fake Mortgage", "loan", 0), (HIDDEN, "Fake Old Checking", "depository", 1),
    ]:
        conn.execute(
            "INSERT INTO accounts (id, item_id, plaid_account_id, name, mask, type, classification, is_hidden)"
            " VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
            (acc_id, f"acc-{acc_id}", name, f"000{acc_id}", account_type, items.classify(account_type), hidden),
        )
    conn.commit()
    return conn


_ids = iter(range(1, 10_000))


def add(conn, account, amount, day, key="GENERAL_MERCHANDISE", name="FAKE MOVE", pending=0):
    tid = next(_ids)
    conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_primary,"
        " plaid_category_key, pending) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (f"t{tid}", account, day, amount, name, key, key, pending),
    )
    conn.commit()
    return conn.execute("SELECT id FROM transactions WHERE plaid_transaction_id = ?", (f"t{tid}",)).fetchone()[0]


def pair_of(conn, transaction_id):
    row = conn.execute("SELECT is_transfer, transfer_pair_id, transfer_ignore FROM transactions WHERE id = ?",
                       (transaction_id,)).fetchone()
    return row["transfer_pair_id"] if row["is_transfer"] else None


def ignored(conn, transaction_id):
    return conn.execute("SELECT transfer_ignore FROM transactions WHERE id = ?", (transaction_id,)).fetchone()[0] == 1


# ---------------------------------------------------------------- matching

def test_your_match_wins_over_the_closest_date_and_survives_rematching(db):
    out = add(db, CHECKING, 10_000, "2026-03-02")
    near = add(db, SAVINGS, -10_000, "2026-03-02")
    far = add(db, CARD, -10_000, "2026-03-04")
    transfers.detect_transfers(db)
    assert pair_of(db, out) == near and pair_of(db, far) is None              # automatic: the closest date
    transfers.match(db, out, far)
    assert pair_of(db, out) == far and pair_of(db, far) == out and pair_of(db, near) is None
    transfers.detect_transfers(db)                                            # e.g. the next sync
    assert pair_of(db, out) == far
    assert [tuple(r) for r in db.execute("SELECT out_id, in_id FROM transfer_matches")] == [(out, far)]


def test_matching_works_from_either_side_and_clears_not_a_transfer(db):
    out = add(db, CHECKING, 2_500, "2026-03-02")
    into = add(db, SAVINGS, -2_500, "2026-03-03")
    transfers.detect_transfers(db)
    transfers.set_not_transfer(db, out)
    assert pair_of(db, out) is None
    transfers.match(db, into, out)                                            # picked from the money in's side
    assert pair_of(db, out) == into and not ignored(db, out)
    assert tuple(db.execute("SELECT out_id, in_id FROM transfer_matches").fetchone()) == (out, into)


@pytest.mark.parametrize("case", ["same account", "amounts differ", "too far apart", "pending", "loan", "hidden",
                                  "income"])
def test_only_pairs_that_meet_the_rules_can_be_matched(db, case):
    out = add(db, CHECKING, 5_000, "2026-03-02")
    other = {
        "same account": lambda: add(db, CHECKING, -5_000, "2026-03-02"),
        "amounts differ": lambda: add(db, SAVINGS, -5_001, "2026-03-02"),
        "too far apart": lambda: add(db, SAVINGS, -5_000, "2026-03-06"),
        "pending": lambda: add(db, SAVINGS, -5_000, "2026-03-02", pending=1),
        "loan": lambda: add(db, MORTGAGE, -5_000, "2026-03-02"),
        "hidden": lambda: add(db, HIDDEN, -5_000, "2026-03-02"),
        "income": lambda: add(db, SAVINGS, -5_000, "2026-03-02", key="INCOME"),
    }[case]()
    with pytest.raises(ValueError):
        transfers.match(db, out, other)
    assert db.execute("SELECT COUNT(*) FROM transfer_matches").fetchone()[0] == 0


def test_removing_a_match_marks_both_halves_and_forgets_yours(db):
    out = add(db, CHECKING, 7_500, "2026-03-02")
    into = add(db, SAVINGS, -7_500, "2026-03-02")
    spare = add(db, CARD, -7_500, "2026-03-03")                               # would pair with `out` if it were free
    transfers.match(db, out, into)
    transfers.unmatch(db, into)
    assert pair_of(db, out) is None and pair_of(db, into) is None and pair_of(db, spare) is None
    assert ignored(db, out) and ignored(db, into) and not ignored(db, spare)
    assert db.execute("SELECT COUNT(*) FROM transfer_matches").fetchone()[0] == 0
    with pytest.raises(ValueError):
        transfers.unmatch(db, into)                                           # not matched any more


def test_not_a_transfer_on_a_half_drops_your_match(db):
    out = add(db, CHECKING, 1_000, "2026-03-02")
    into = add(db, SAVINGS, -1_000, "2026-03-02")
    transfers.match(db, out, into)
    transfers.set_not_transfer(db, into)
    assert pair_of(db, out) is None
    assert db.execute("SELECT COUNT(*) FROM transfer_matches").fetchone()[0] == 0


def test_your_match_is_set_aside_while_a_half_no_longer_qualifies(db):
    out = add(db, CHECKING, 3_000, "2026-03-02")
    into = add(db, SAVINGS, -3_000, "2026-03-02")
    transfers.match(db, out, into)
    db.execute("UPDATE accounts SET is_hidden = 1 WHERE id = ?", (SAVINGS,))
    transfers.detect_transfers(db)
    assert pair_of(db, out) is None
    db.execute("UPDATE accounts SET is_hidden = 0 WHERE id = ?", (SAVINGS,))
    transfers.detect_transfers(db)
    assert pair_of(db, out) == into                                           # kept, so it comes back


# ---------------------------------------------------------------- what the page lists

def test_matched_pairs_and_their_routes(db):
    a = add(db, CHECKING, 10_000, "2026-03-02")
    add(db, SAVINGS, -10_000, "2026-03-02")
    b = add(db, CHECKING, 5_000, "2026-03-10")
    add(db, SAVINGS, -5_000, "2026-03-11")
    c = add(db, SAVINGS, 2_000, "2026-03-15")
    c_in = add(db, CHECKING, -2_000, "2026-03-15")
    add(db, CHECKING, 900, "2025-12-30")                                      # outside the range
    add(db, SAVINGS, -900, "2025-12-30")
    transfers.detect_transfers(db)
    transfers.match(db, c, c_in)
    pairs = transfers.matched_pairs(db, "2026-01-01", "2026-03-31")
    assert [p.out.id for p in pairs] == [c, b, a]                            # newest first
    assert [p.manual for p in pairs] == [True, False, False]
    routes = transfers.pair_summary(pairs)
    assert [(r.out_account, r.in_account, r.count, r.total_cents) for r in routes] == [
        ("Fake Checking", "Fake Savings", 2, 15_000), ("Fake Savings", "Fake Checking", 1, 2_000)]
    assert len(transfers.matched_pairs(db, "", "")) == 4                      # blank = all time


def test_options_list_each_possible_pair_once_and_name_a_taken_partner(db):
    out = add(db, CHECKING, 4_000, "2026-03-02")
    into = add(db, SAVINGS, -4_000, "2026-03-02")
    transfers.detect_transfers(db)
    transfers.unmatch(db, out)                                                # both marked: neither pairs on its own
    taken_out = add(db, BROKERAGE, 6_000, "2026-03-05")
    taken_in = add(db, SAVINGS, -6_000, "2026-03-05")
    loose_in = add(db, CARD, -6_000, "2026-03-06")                           # lost the closest-date tie to taken_in
    add(db, CHECKING, 1_234, "2026-03-07")                                    # nothing could pair with it
    transfers.detect_transfers(db)
    options = transfers.match_options(db, "2026-03-01", "2026-03-31")
    listed = {o.txn.id: o for o in options}
    assert set(listed) == {out, loose_in}                                     # `into` shows under `out`, not again
    assert [(p.id, was) for p, was in listed[out].partners] == [(into, None)]
    (partner, was), = listed[loose_in].partners
    assert partner.id == taken_out and was.id == taken_in                     # matching it breaks that pair
    assert transfers.match_options(db, "2026-04-01", "2026-04-30") == []


# ---------------------------------------------------------------- the page

@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_the_page_matches_and_removes(client, db):
    out = add(db, CHECKING, 8_800, "2026-03-02", name="FAKE TRANSFER OUT")
    into = add(db, SAVINGS, -8_800, "2026-03-03", name="FAKE TRANSFER IN")
    transfers.detect_transfers(db)
    keep = {"range": "custom", "from": "2026-03-01", "to": "2026-03-31"}
    url = "/transfers?range=custom&from=2026-03-01&to=2026-03-31"
    page = client.get(url).text
    assert 'href="/transfers"' in page and "<h2>Matched transfers</h2>" in page
    assert "FAKE TRANSFER OUT" in page and "$88.00" in page and f'action="/transfers/{out}/unmatch"' in page

    response = client.post(f"/transfers/{out}/unmatch", data=keep, headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303 and "range=custom" in response.headers["location"]
    assert pair_of(db, out) is None
    page = client.get(url).text
    assert "No matched transfers in this range." in page and f'<option value="{into}">' in page

    client.post("/transfers/match", data={"transaction_id": out, "partner_id": into, **keep}, headers=ORIGIN)
    assert pair_of(db, out) == into
    assert "Matched by you" in client.get(url).text


def test_the_page_shows_why_a_match_was_refused(client, db):
    out = add(db, CHECKING, 1_000, "2026-03-02")
    far = add(db, SAVINGS, -1_000, "2026-03-20")
    page = client.post("/transfers/match", data={"transaction_id": out, "partner_id": far, "range": "ytd"},
                       headers=ORIGIN).text
    assert "within 3 days" in page and pair_of(db, out) is None
    assert "Pick the transaction" in client.post("/transfers/match", data={"transaction_id": out, "range": "ytd"},
                                                 headers=ORIGIN).text


def test_matching_is_post_only(client, db):
    assert client.get("/transfers/match").status_code == 405
