"""Authorized users' cards (authorized_users.py, user's request 2026-10-29): "(AU)" on the Transactions list and the
account checklist with each account's cards. Fake data only."""

import re
import pytest

from fintrack import authorized_users, items

CARD, BANK = 1, 2


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    for acc_id, name, mask, kind in ((CARD, "Fake Card", "1111", "credit"), (BANK, "Fake Checking", "2222", "depository")):
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, mask, type, classification)"
                     " VALUES (?, 1, ?, ?, ?, ?, ?)", (acc_id, f"acc-{acc_id}", name, mask, kind, items.classify(kind)))
    rows = [("p1", CARD, "FAKE MAIN SHOP", "1111"), ("p2", CARD, "FAKE NO CARD SHOP", None),
            ("a1", CARD, "FAKE AU SHOP", "9999"), ("a2", CARD, "FAKE OTHER AU SHOP", "8888"),
            ("b1", BANK, "FAKE BANK THING", "FAKE PERSON")]                        # a name: not a card
    for pid, account, name, owner in rows:
        conn.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name,"
                     " plaid_category_key, account_owner) VALUES (?, ?, '2026-10-10', 1000, ?, 'FOOD_AND_DRINK', ?)",
                     (pid, account, name, owner))
    conn.commit()
    return conn


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app
    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_an_authorized_users_card_is_another_cards_last_4():
    au = authorized_users.is_authorized_user
    assert au("9999", "1111") and au("AB12", "1111")
    assert not au("1111", "1111") and not au(None, "1111") and not au("", "1111")
    assert not au("FAKE PERSON", "1111") and not au("12345", "1111")         # a name, or too long for a last 4


def test_cards_are_listed_only_for_accounts_with_authorized_users(db):
    assert authorized_users.cards_by_account(db) == {CARD: [
        authorized_users.Card("1:main", "1111", False), authorized_users.Card("1:8888", "8888", True),
        authorized_users.Card("1:9999", "9999", True)]}


def names(client, query):
    page = client.get("/transactions?range=all" + query).text
    return sorted(n for n in ("FAKE MAIN SHOP", "FAKE NO CARD SHOP", "FAKE AU SHOP", "FAKE OTHER AU SHOP", "FAKE BANK THING")
                  if n in page.split('id="results"')[1])


def test_the_list_marks_au_rows_and_filters_by_accounts_and_cards(client, db):
    page = client.get("/transactions?range=all").text
    rows = re.sub(r'<tr class="txn-sizer".*?</tr>', "", page.split('id="results"')[1], flags=re.S)   # (the hidden sizing row)
    assert rows.count('class="au-mark"') == 2 and 'title="Authorized user\'s card ····9999">(AU)</span>' in rows
    assert len(names(client, "")) == 5
    assert names(client, "&account=1") == ["FAKE AU SHOP", "FAKE MAIN SHOP", "FAKE NO CARD SHOP", "FAKE OTHER AU SHOP"]
    assert names(client, "&account=1:main") == ["FAKE MAIN SHOP", "FAKE NO CARD SHOP"]      # no card: the primary's
    assert names(client, "&account=1:9999") == ["FAKE AU SHOP"]
    assert names(client, "&account=1:9999&account=2") == ["FAKE AU SHOP", "FAKE BANK THING"]  # several checked
    assert names(client, "&account=1:9999,2") == names(client, "&account=1:9999&account=2")   # a kept URL's form
    assert len(names(client, "&account=1;DROP")) == 5                                       # junk: ignored


def test_the_checklist_lists_each_account_and_an_accounts_cards(client, db):
    page = client.get("/transactions?account=1:9999").text
    box = page.split('<details class="checklist"')[1].split("</details>")[0]
    assert 'data-checklist-label>Fake Card (AU)</summary>' in box                  # one picked: named
    assert 'name="account" value="1" data-label="Fake Card"\n        data-child-of="g' in box and 'data-parent="1"' in box
    assert 'value="1:main" data-child-of="1"' in box and "Primary" in box and "AU" in box
    assert 'value="1:9999" data-child-of="1"\n        data-label="Fake Card (AU)" checked>' in box
    assert 'value="2" data-label="Fake Checking"' in box and 'data-child-of="2"' not in box   # one card: no children
    whole = client.get("/transactions?account=1").text.split('<details class="checklist"')[1].split("</details>")[0]
    assert whole.count(" checked>") == 5                                           # its type, the account, 3 cards
    [card_type] = [g.split('data-label="')[1].split('"')[0] for g in whole.split('class="check-row check-group"')[1:]
                   if "checked>" in g.split("</label>")[0]]
    assert f"data-checklist-label>{card_type}</summary>" in whole                   # its type's only account: the type
    none = client.get("/transactions").text.split('<details class="checklist"')[1].split("</details>")[0]
    assert "data-checklist-label>All accounts</summary>" in none and "data-check-all checked" in none


def test_the_accounts_checklist_groups_accounts_by_type(client, db):
    """User's request 2026-10-30: accounts under their account type; the type's box ticks them all (one credit card
    here), and a fully ticked type is named on the button; an account's card values are dropped once it's ticked."""
    box = client.get("/transactions?account=1&account=1:main&account=2").text.split(
        '<details class="checklist"')[1].split("</details>")[0]
    groups = [part.split('data-label="')[1].split('"')[0] for part in box.split('class="check-row check-group"')[1:]]
    assert len(groups) == 2 and box.count('class="check-row check-group"><input type="checkbox" data-parent="g') == 2
    assert box.count('data-parent="g') == 2 and box.count(f'data-label="{groups[0]}" checked>') == 1
    assert "data-checklist-label>" + ", ".join(groups) + "</summary>" in box            # both types fully ticked
    sent = client.get("/transactions?account=1&account=1:main&account=1:9999", headers={"HX-Request": "true"})
    assert sent.headers["HX-Push-Url"] == "/transactions?account=1"


# ---------------------------------------------------------------- the account page (user's request 2026-10-29)

def section(page, start):
    return page.split(start)[1].split("</section>")[0]


def test_the_account_page_shows_its_cards_and_which_card_each_row_was_on(client, db):
    page = client.get(f"/accounts/{CARD}").text
    cards = section(page, "<h2>Cards</h2>")
    assert cards.count("<tr>") == 4                                                # header + primary + two AU cards
    assert f'href="/accounts/{CARD}?card=1%3Amain#transactions">Primary ····1111</a>' in cards
    assert f'href="/accounts/{CARD}?card=1%3A9999#transactions">AU ····9999</a>' in cards
    assert '<td class="num">2</td>' in cards and "$20.00" in cards                 # primary: its row + the one without a card
    txns = section(page, '<section class="card" id="transactions">')
    assert 'hx-push-url="true">Card</a></th>' in txns and '<td class="nowrap">9999 <span class="au-mark">(AU)</span></td>' in txns
    assert '<td class="nowrap">1111</td>' in txns
    assert '<option value="1:9999" >AU ····9999</option>' in txns
    # the edit panel (and rows returned alone after an edit) count the Card column
    au_id = db.execute("SELECT id FROM transactions WHERE plaid_transaction_id = 'a1'").fetchone()[0]
    assert '<td colspan="6">' in client.get(f"/transactions/{au_id}/edit?view=account").text
    assert "<h2>Cards</h2>" not in client.get(f"/accounts/{BANK}").text            # one card: none of this
    assert ">Card</a></th>" not in client.get(f"/accounts/{BANK}").text


def test_the_account_page_filters_its_transactions(client, db):
    def shown(query):
        txns = section(client.get(f"/accounts/{CARD}?{query}").text, '<div id="acct-txns">')
        return sorted(n for n in ("FAKE MAIN SHOP", "FAKE NO CARD SHOP", "FAKE AU SHOP", "FAKE OTHER AU SHOP") if n in txns)

    assert len(shown("")) == 4
    assert shown("card=1:9999") == ["FAKE AU SHOP"]
    assert shown("card=1:main") == ["FAKE MAIN SHOP", "FAKE NO CARD SHOP"]
    assert shown("q=other") == ["FAKE OTHER AU SHOP"]
    assert shown("card=2") == shown("")                                            # not one of its cards: ignored
    assert shown("from=2026-10-11") == [] and "No transactions match." in client.get(f"/accounts/{CARD}?from=2026-10-11").text


def test_the_account_pages_headings_sort_the_whole_list(client, db):
    """Sorting (user's request 2026-10-29): on the server, before the newest-200 cut; again = reverse; filters kept."""
    db.execute("UPDATE transactions SET amount_cents = 500 WHERE plaid_transaction_id = 'a2'")
    db.execute("UPDATE transactions SET amount_cents = 3000 WHERE plaid_transaction_id = 'a1'")
    db.commit()

    def order(query):
        txns = section(client.get(f"/accounts/{CARD}?{query}").text, '<div id="acct-txns">')
        found = [n for n in ("FAKE MAIN SHOP", "FAKE NO CARD SHOP", "FAKE AU SHOP", "FAKE OTHER AU SHOP") if n in txns]
        return sorted(found, key=txns.index)

    assert order("sort=amount")[0] == "FAKE AU SHOP" and order("sort=amount&dir=asc")[0] == "FAKE OTHER AU SHOP"
    assert order("sort=card")[0] == "FAKE AU SHOP"                                 # ····9999 first when descending
    page = client.get(f"/accounts/{CARD}?sort=amount&q=shop").text
    assert f'href="/accounts/{CARD}?q=shop&amp;sort=amount&amp;dir=asc#transactions"' in page      # again = reverse
    assert '<input type="hidden" name="sort" value="amount" form="acct-filters">' in page         # kept on filtering
    assert 'name="sort"' not in client.get(f"/accounts/{CARD}").text.split('<div id="acct-txns">')[1].split("<table")[0]
    assert order("sort=bogus") == order("")                                         # unknown: newest first
