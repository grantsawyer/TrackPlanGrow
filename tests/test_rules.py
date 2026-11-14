"""Rules (fintrack/rules.py; user's request 2026-11-02): conditions on several fields, several actions. Made-up data only."""

import json
import sqlite3

import pytest

from fintrack import categories, db as database, items, recurring, rules, tags
from fintrack.rules import Action, Condition

CHECKING, CARD = 1, 2
ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    for acc_id, kind in [(CHECKING, "depository"), (CARD, "credit")]:
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification) VALUES (?, 1, ?, ?, ?, ?)",
                     (acc_id, f"acc-{acc_id}", f"Fake {kind}", kind, items.classify(kind)))
    conn.commit()
    return conn


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


_n = iter(range(1, 100_000))


def add(conn, cents=1000, name="FAKE SHOP", merchant=None, day="2026-09-10", key="GENERAL_MERCHANDISE", account=CARD):
    n = next(_n)
    conn.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, merchant_name,"
                 " plaid_category_key) VALUES (?, ?, ?, ?, ?, ?, ?)", (f"r{n}", account, day, cents, name, merchant, key))
    conn.commit()
    return conn.execute("SELECT id FROM transactions WHERE plaid_transaction_id = ?", (f"r{n}",)).fetchone()[0]


def eff(conn, tid):
    return conn.execute("SELECT * FROM transactions_effective WHERE id = ?", (tid,)).fetchone()


def category(conn, tid):
    return eff(conn, tid)["category"]


def set_category(key="TRAVEL"):
    return Action("set_category", key)


def contains(*phrases):
    return Condition("description", "contains", list(phrases))


def test_every_kind_of_condition(db):
    parents = {"SUB": "FOOD_AND_DRINK"}
    row = {"merchant_name": "Fake Coffee", "name": "POS 123", "amount_cents": 4500, "date": "2026-09-10",
           "plaid_category_key": "SUB", "account_id": CARD}

    def yes(field, op, *values):
        return rules.condition_matches(Condition(field, op, list(values)), row, parents)
    assert yes("description", "contains", "COFFEE") and yes("description", "contains", "pos") and yes("description", "contains", "no", "pos")
    assert not yes("description", "contains", "tea") and yes("description", "not_contains", "tea")
    assert not yes("description", "not_contains", "pos")                   # in either text
    assert yes("description", "is", "fake coffee") and yes("description", "starts", "fake") and not yes("description", "starts", "coffee")
    assert yes("merchant", "contains", "coffee") and not yes("merchant", "contains", "pos") and yes("bank_text", "contains", "pos")
    assert yes("amount", "over", 4499) and not yes("amount", "over", 4500) and yes("amount", "under", 4501) and yes("amount", "equals", 4500)
    assert yes("amount", "between", 4500, 5000) and not yes("amount", "between", 1, 4499)
    assert yes("direction", "is", "out") and not yes("direction", "is", "in")
    assert rules.condition_matches(Condition("amount", "equals", [4500]), {**row, "amount_cents": -4500}, parents)   # its size
    assert yes("date", "on_or_after", "2026-09-10") and yes("date", "on_or_before", "2026-09-10") and not yes("date", "on_or_after", "2026-09-11")
    assert yes("date", "between", "2026-09-01", "2026-09-30")
    assert yes("category", "is", "SUB") and yes("category", "is", "FOOD_AND_DRINK") and not yes("category", "is", "TRAVEL")   # parent includes subs
    assert yes("category", "not", "TRAVEL") and not yes("category", "not", "FOOD_AND_DRINK")
    assert yes("account", "is", CARD) and not yes("account", "is", CHECKING) and yes("account", "not", CHECKING)


def test_all_conditions_must_match(db):
    small, big = add(db, 1000, "FAKE UBER"), add(db, 9000, "FAKE UBER")
    other = add(db, 1000, "FAKE LYFT", account=CHECKING)
    rules.save(db, None, [contains("uber"), Condition("amount", "under", [5000])], [set_category()])
    assert category(db, small) == "TRAVEL" and category(db, big) == category(db, other) == "GENERAL_MERCHANDISE"
    assert eff(db, small)["category_source"] == "rule"


def test_category_condition_tests_plaids_not_another_rules(db):
    tid = add(db, name="FAKE SHOP", key="GENERAL_MERCHANDISE")
    rules.save(db, None, [contains("fake")], [set_category("TRAVEL")])
    second = rules.save(db, None, [Condition("category", "is", ["TRAVEL"])], [Action("set_description", "Chained")])
    third = rules.save(db, None, [Condition("category", "is", ["GENERAL_MERCHANDISE"])], [Action("set_description", "Plaids")])
    assert second and third and eff(db, tid)["description"] == "Plaids"


def test_several_rules_first_to_set_wins_tags_add_up_and_order_can_change(db):
    tid = add(db, name="FAKE COFFEE")
    a = rules.save(db, None, [contains("coffee")], [set_category("TRAVEL"), Action("add_tag", str(tags.find_or_create(db, "Fake A").id))])
    b = rules.save(db, None, [contains("fake")], [set_category("ENTERTAINMENT"), Action("set_description", "Set by B"),
                                                  Action("add_tag", str(tags.find_or_create(db, "Fake B").id))])
    assert category(db, tid) == "TRAVEL" and eff(db, tid)["description"] == "Set by B"
    assert [t.name for t in tags.tags_of(db, [tid])[tid]] == ["Fake A", "Fake B"]
    rules.reorder(db, [b, a])
    assert category(db, tid) == "ENTERTAINMENT"
    rules.move(db, b, 1)
    assert category(db, tid) == "TRAVEL" and [r.id for r in rules.load(db)] == [a, b]
    rules.move(db, b, 1)                                                   # already last
    assert [r.id for r in rules.load(db)] == [a, b]


def test_your_own_choices_win(db):
    tid = add(db, name="FAKE SHOP")
    rule = rules.save(db, None, [contains("fake")], [set_category(), Action("set_description", "From a rule")])
    assert eff(db, tid)["description"] == "From a rule"
    categories.set_transaction_category(db, tid, "ENTERTAINMENT")
    categories.set_transaction_description(db, tid, "Mine")
    rules.run(db)
    assert (category(db, tid), eff(db, tid)["description"], eff(db, tid)["category_source"]) == ("ENTERTAINMENT", "Mine", "you")
    categories.set_transaction_category(db, tid, None)                     # back to automatic: the rule's
    categories.set_transaction_description(db, tid, None)
    assert category(db, tid) == "TRAVEL" and eff(db, tid)["description"] == "From a rule"
    assert rule


def test_editing_and_deleting_update_what_it_decided(db):
    tid = add(db, name="FAKE SHOP")
    tag = tags.find_or_create(db, "Fake Tag")
    rule = rules.save(db, None, [contains("fake")], [set_category(), Action("set_description", "Renamed"), Action("add_tag", str(tag.id))])
    assert category(db, tid) == "TRAVEL" and tid in tags.tags_of(db, [tid])
    rules.save(db, rule, [contains("fake")], [set_category("ENTERTAINMENT")])
    assert category(db, tid) == "ENTERTAINMENT" and eff(db, tid)["description"] == "FAKE SHOP" and not tags.tags_of(db, [tid])
    rules.save(db, rule, [contains("nothing like it")], [set_category("ENTERTAINMENT")])
    assert category(db, tid) == "GENERAL_MERCHANDISE"
    rules.save(db, rule, [contains("fake")], [set_category("ENTERTAINMENT")])
    rules.delete(db, rule)
    assert (category(db, tid), eff(db, tid)["category_source"]) == ("GENERAL_MERCHANDISE", "plaid") and rules.load(db) == []
    with pytest.raises(ValueError):
        rules.save(db, 99999, [contains("x")], [set_category()])
    with pytest.raises(ValueError):
        rules.save(db, None, [], [set_category()])
    with pytest.raises(ValueError):
        rules.save(db, None, [contains("x")], [])


def test_tags_you_add_or_remove_stick(db):
    tid = add(db, name="FAKE SHOP")
    tag = tags.find_or_create(db, "Fake Tag")
    rule = rules.save(db, None, [contains("fake")], [Action("add_tag", str(tag.id))])
    tags.remove(db, [tid], tag.id)                                         # a rule's tag you took off stays off
    rules.run(db)
    assert not tags.tags_of(db, [tid])
    tags.add(db, [tid], "Fake Tag")                                        # yours: it stays when the rule goes
    rules.delete(db, rule)
    assert tid in tags.tags_of(db, [tid])


def test_one_time_actions_happen_once_stay_and_never_override_you(db):
    tid = add(db, -5000, name="FAKE SHOP")
    transfer = add(db, 700, name="FAKE MOVE", key="TRANSFER_OUT", account=CHECKING)
    db.execute("INSERT INTO accounts (id, name, type, classification, is_manual) VALUES (9, 'Fake lent', 'receivable', 'asset', 1)")
    db.execute("INSERT INTO recurring_items (id, name, account_id, amount_cents, frequency, next_expected_date, type, status, source) "
               "VALUES (5, 'Fake Bill', ?, 5000, 'monthly', '2026-10-01', 'bill', 'confirmed', 'manual')", (CARD,))
    db.commit()
    rule = rules.save(db, None, [contains("fake shop")], [Action("not_transfer"), Action("accept"), Action("recurring", "5")])
    row = db.execute("SELECT transfer_ignore, category_reviewed FROM transactions WHERE id = ?", (tid,)).fetchone()
    assert tuple(row) == (1, 1) and recurring.link_of(db, tid) == (True, 5)
    # undone by you: not redone
    db.execute("UPDATE transactions SET transfer_ignore = 0 WHERE id = ?", (tid,))
    db.commit()
    recurring.clear_link(db, tid)
    rules.run(db)
    assert db.execute("SELECT transfer_ignore FROM transactions WHERE id = ?", (tid,)).fetchone()[0] == 0
    assert recurring.link_of(db, tid)[0] is False
    linked = rules.save(db, None, [contains("fake move")], [Action("purpose", "9")])
    assert db.execute("SELECT account_id, source FROM purpose_links WHERE transaction_id = ?", (transfer,)).fetchone()[0] == 9
    # deleting: kept, they stay
    rules.delete(db, linked, keep=True)
    assert db.execute("SELECT account_id FROM purpose_links WHERE transaction_id = ?", (transfer,)).fetchone()[0] == 9
    assert rule


def test_deleting_back_to_original_undoes_what_it_did(db):
    tid = add(db, -5000, name="FAKE SHOP")
    transfer = add(db, 700, name="FAKE MOVE", key="TRANSFER_OUT", account=CHECKING)
    db.execute("INSERT INTO accounts (id, name, type, classification, is_manual) VALUES (9, 'Fake lent', 'receivable', 'asset', 1)")
    db.execute("INSERT INTO recurring_items (id, name, account_id, amount_cents, frequency, next_expected_date, type, status, source) "
               "VALUES (5, 'Fake Bill', ?, 5000, 'monthly', '2026-10-01', 'bill', 'confirmed', 'manual')", (CARD,))
    db.commit()
    one = rules.save(db, None, [contains("fake shop")], [Action("not_transfer"), Action("accept"), Action("recurring", "5"), set_category()])
    two = rules.save(db, None, [contains("fake move")], [Action("purpose", "9")])
    rules.delete(db, one)
    rules.delete(db, two)
    row = db.execute("SELECT transfer_ignore, category_reviewed FROM transactions WHERE id = ?", (tid,)).fetchone()
    assert tuple(row) == (0, 0) and recurring.link_of(db, tid)[0] is False and category(db, tid) == "GENERAL_MERCHANDISE"
    assert not db.execute("SELECT 1 FROM purpose_links WHERE transaction_id = ?", (transfer,)).fetchone()


def test_deleting_and_keeping_makes_its_results_yours(db):
    tag = tags.find_or_create(db, "Fake Tag")
    tid, mine, other = add(db, name="FAKE SHOP"), add(db, name="FAKE MINE"), add(db, name="OTHER")
    categories.set_transaction_category(db, mine, "ENTERTAINMENT")
    earlier = rules.save(db, None, [contains("fake mine")], [Action("set_description", "Earlier")])
    rule = rules.save(db, None, [contains("fake")], [set_category(), Action("set_description", "Renamed"), Action("add_tag", str(tag.id))])
    rules.delete(db, rule, keep=True)
    t = eff(db, tid)
    assert (t["category"], t["category_source"], t["description"]) == ("TRAVEL", "you", "Renamed")
    assert [g.name for g in tags.tags_of(db, [tid])[tid]] == ["Fake Tag"]
    m = eff(db, mine)                                                       # your category stays; the earlier rule's description isn't made yours
    assert (m["category"], m["description"], m["user_description"]) == ("ENTERTAINMENT", "Earlier", None)
    assert category(db, other) == "GENERAL_MERCHANDISE" and earlier
    assert [r.id for r in rules.load(db)] == [earlier]


def test_the_delete_choices(client, db):
    tid = add(db, name="FAKE SHOP")
    rule = rules.save(db, None, [contains("fake")], [set_category()])
    page = client.get("/rules").text
    assert 'name="results" value="original"' in page and 'name="results" value="keep"' in page and "data-popover-close" in page
    done = client.post(f"/rules/{rule}/delete", data={"results": "keep"}, headers=ORIGIN, follow_redirects=False)
    assert "your+own+edits" in done.headers["location"] and category(db, tid) == "TRAVEL"
    second = rules.save(db, None, [contains("other")], [set_category()])
    client.post(f"/rules/{second}/delete", data={"results": "original"}, headers=ORIGIN)
    assert rules.load(db) == []


def test_finding_a_rule_like_a_new_one(db):
    tech = [set_category()]
    rule = rules.save(db, None, [contains("Apple", "Google")], tech)
    assert rules.find_similar(db, [contains("google", "APPLE")], tech).new_phrases == []              # the same, in any order and case
    assert rules.find_similar(db, [contains("apple")], tech).new_phrases == []                       # already covered
    found = rules.find_similar(db, [contains("Zoom", "apple")], tech)
    assert found.rule.id == rule and found.new_phrases == ["Zoom"]
    assert rules.find_similar(db, [contains("Zoom")], [set_category("BANK_FEES")]) is None            # does something else
    assert rules.find_similar(db, [contains("Zoom"), Condition("amount", "over", [100])], tech) is None   # checks something else
    assert rules.find_similar(db, [Condition("description", "starts", ["Zoom"])], tech) is None       # only contains
    both = rules.save(db, None, [contains("Slack"), Condition("amount", "over", [100])], [set_category("BANK_FEES")])
    more = rules.find_similar(db, [Condition("amount", "over", [100]), contains("Teams")], [set_category("BANK_FEES")])
    assert more.rule.id == both and more.new_phrases == ["Teams"]
    assert rules.find_similar(db, [contains("Slack"), Condition("amount", "over", [200])], [set_category("BANK_FEES")]) is None
    rules.add_keywords(db, found)
    assert next(r for r in rules.load(db) if r.id == rule).conditions[0].values == ["Apple", "Google", "Zoom"]


def test_adding_a_rule_like_one_you_have_asks_first(client, db):
    a = add(db, name="FAKE ZOOM")
    existing = rules.save(db, None, [contains("Apple")], [set_category()])
    form = {"cond-0-field": "description", "cond-0-op": "contains", "cond-0-value": "Zoom", "act-0-kind": "set_category", "act-0-value": "TRAVEL"}
    asked = client.post("/rules", data=form, headers=ORIGIN)
    assert "You already have a rule like this" in asked.text and "Add Zoom to that rule" in asked.text and ">Zoom</textarea>" in asked.text
    assert len(rules.load(db)) == 1 and category(db, a) == "GENERAL_MERCHANDISE"                      # nothing saved yet
    merged = client.post("/rules", data={**form, "on_similar": "merge"}, headers=ORIGIN, follow_redirects=False)
    assert "Added+to+the+existing+rule" in merged.headers["location"]
    assert [r.conditions[0].values for r in rules.load(db)] == [["Apple", "Zoom"]] and category(db, a) == "TRAVEL"
    # nothing new: says so, and offers it
    same = client.post("/rules", data={**form, "cond-0-value": "zoom"}, headers=ORIGIN)
    assert "An existing rule already does this" in same.text and f'href="/rules/{existing}/edit"' in same.text and 'value="merge"' not in same.text
    # or anyway
    client.post("/rules", data={**form, "on_similar": "new"}, headers=ORIGIN)
    assert len(rules.load(db)) == 2
    # editing a rule isn't asked
    done = client.post(f"/rules/{existing}", data=form, headers=ORIGIN, follow_redirects=False)
    assert done.status_code == 303


def test_asking_leaves_no_new_tag_behind(client, db):
    tag = tags.find_or_create(db, "Fake Old")
    db.commit()
    rules.save(db, None, [contains("Apple")], [Action("add_tag", str(tag.id))])
    form = {"cond-0-field": "description", "cond-0-op": "contains", "cond-0-value": "Zoom", "act-0-kind": "add_tag", "act-0-value": "Fake Old"}
    assert "Add Zoom to that rule" in client.post("/rules", data=form, headers=ORIGIN).text
    client.post("/rules", data={**form, "act-0-value": "Fake Brand New"}, headers=ORIGIN)              # no match: saved, tag made
    assert tags.tag_by_name(db, "Fake Brand New")


def test_a_later_transfer_gets_its_purpose_once_it_is_one(db):
    """Not a transfer yet: tried again on later runs, not recorded as done."""
    db.execute("INSERT INTO accounts (id, name, type, classification, is_manual) VALUES (9, 'Fake lent', 'receivable', 'asset', 1)")
    db.commit()
    tid = add(db, 700, name="FAKE MOVE", key="GENERAL_MERCHANDISE", account=CHECKING)
    rules.save(db, None, [contains("fake move")], [Action("purpose", "9")])
    assert not db.execute("SELECT 1 FROM purpose_links WHERE transaction_id = ?", (tid,)).fetchone()
    categories.set_transaction_category(db, tid, "TRANSFER_OUT")
    rules.run(db)
    assert db.execute("SELECT account_id FROM purpose_links WHERE transaction_id = ?", (tid,)).fetchone()[0] == 9


def test_parsing_the_forms_text(db):
    assert rules.parse_condition(db, "description", "contains", ["Uber | Lyft "]).values == ["Lyft", "Uber"]
    assert rules.parse_condition(db, "description", "contains", ["Uber\r\n\r\n lyft \nBolt"]).values == ["Bolt", "lyft", "Uber"]
    assert rules.parse_action(db, "set_description", " Two\nlines ").value == "Two lines"
    assert rules.parse_condition(db, "amount", "between", ["$1,000.50", "20"]).values == [2000, 100050]    # sorted
    assert rules.parse_condition(db, "amount", "over", ["50", ""]).values == [5000]
    assert rules.parse_condition(db, "date", "between", ["2026-09-30", "2026-09-01"]).values == ["2026-09-01", "2026-09-30"]
    assert rules.parse_condition(db, "account", "is", [str(CARD)]).values == [CARD]
    for bad in [("description", "contains", [" "]), ("amount", "over", ["lots"]), ("amount", "over", ["-5"]),
                ("amount", "between", ["1"]), ("date", "on_or_after", ["soon"]), ("category", "is", ["NOPE"]), ("category", "is", []),
                ("account", "is", ["999"]), ("direction", "is", ["sideways"]), ("amount", "contains", ["1"]), ("nonsense", "is", ["1"])]:
        with pytest.raises(ValueError):
            rules.parse_condition(db, *bad)
    assert rules.parse_action(db, "add_tag", "Fake New").value.isdigit()   # a new tag is made
    for bad in [("set_description", " "), ("set_category", "NOPE"), ("add_tag", " "), ("recurring", "x"), ("purpose", ""), ("nonsense", "")]:
        with pytest.raises(ValueError):
            rules.parse_action(db, *bad)


def test_rules_list_their_conditions_and_actions(db):
    tag = tags.find_or_create(db, "Fake Tag")
    rules.save(db, None, [contains("Uber", "Lyft"), Condition("amount", "under", [5000]), Condition("direction", "is", ["out"]),
                          Condition("account", "is", [CARD])], [set_category(), Action("add_tag", str(tag.id))])
    rule = rules.listing(db)[0]
    assert rule["conditions"] == ["description contains “Lyft” or “Uber”", "amount is under $50.00", "money out", "account is Fake credit"]
    assert rule["actions"] == ["set category to Travel", "tag Fake Tag"]


def test_the_preview_says_what_saving_would_do(db):
    mine, plain, earlier = add(db, name="FAKE A", day="2026-09-03"), add(db, name="FAKE B", day="2026-09-02"), add(db, name="FAKE EARLY", day="2026-09-01")
    categories.set_transaction_category(db, mine, "ENTERTAINMENT")
    rule = rules.save(db, None, [contains("early")], [set_category("BANK_FEES")])
    p = rules.preview(db, [contains("fake")], [set_category()])
    assert (p.matches, p.changes, p.yours, p.earlier) == (3, 1, 1, 1)
    assert [s["outcome"] for s in p.samples] == ["keeps your category", "→ Travel", "keeps an earlier rule's category"]
    assert rules.preview(db, [], [set_category()]).matches == 0
    assert category(db, plain) == "GENERAL_MERCHANDISE"                    # only a preview
    # a subcategory the row already has isn't a change (its full name differs from its label)
    sub = categories.add_subcategory(db, "FOOD_AND_DRINK", "Fake Sub")
    has_it = add(db, name="FAKE SUBBED")
    rules.save(db, None, [contains("subbed")], [set_category(sub)])
    same = rules.preview(db, [contains("subbed")], [set_category(sub)], rules.load(db)[-1].id)
    assert (same.matches, same.changes) == (1, 0) and has_it
    # editing a rule previews it in its own place, not after itself
    assert rules.preview(db, [contains("early")], [set_category("TRAVEL")], rule).changes == 1


def _form(**extra):
    return {"cond-0-field": "description", "cond-0-op": "contains", "cond-0-value": "fake",
            "cond-1-field": "amount", "cond-1-op": "under", "cond-1-value": "50",
            "act-0-kind": "set_category", "act-0-value": "TRAVEL",
            "act-1-kind": "add_tag", "act-1-value": "Fake Web Tag", **extra}


def test_the_pages(client, db):
    tid = add(db, 1000, name="FAKE SHOP", merchant="Fake Merchant")
    assert "No keywords yet" in client.get("/rules").text and "No custom rules yet" in client.get("/rules").text
    blank = client.get("/rules/new").text
    assert 'data-rule-template="cond"' in blank and 'name="cond-0-field"' in blank and 'name="act-0-kind"' in blank
    preview = client.get("/rules/preview", params={k: v for k, v in _form().items()}).text
    assert "<strong>1</strong> transaction match" in preview and "→ Travel" in preview and "tag Fake Web Tag" in preview
    assert not tags.tag_by_name(db, "Fake Web Tag")                        # a preview makes nothing
    half = client.get("/rules/preview", params={"cond-0-field": "amount", "cond-0-op": "over", "cond-0-value": ""}).text
    assert "No transactions match" in half                                 # half-filled: no error

    bad = client.post("/rules", data={**_form(), "cond-1-value": "lots"}, headers=ORIGIN)
    assert "Amounts need to be numbers" in bad.text and rules.load(db) == [] and ">fake</textarea>" in bad.text
    assert 'value="Fake Web Tag"' in bad.text                              # what you typed stays
    assert "at least one action" in client.post("/rules", data={"cond-0-field": "description", "cond-0-op": "contains", "cond-0-value": "x"}, headers=ORIGIN).text

    done = client.post("/rules", data=_form(), headers=ORIGIN, follow_redirects=False)
    assert done.status_code == 303 and "It+matches+1+transaction" in done.headers["location"]
    assert category(db, tid) == "TRAVEL" and [t.name for t in tags.tags_of(db, [tid])[tid]] == ["Fake Web Tag"]
    rule = rules.load(db)[0].id
    page = client.get("/rules").text
    assert "<li>amount is under $50.00</li>" in page and "<li>set category to Travel</li>" in page and f'href="/rules/{rule}/edit"' in page and 'draggable="true"' in page
    # filters (2026-11-02): each rule carries what ui.js filters on; only what's in use is offered
    assert 'data-search="description contains “fake” amount is under $50.00 set category to travel tag fake web tag"' in page
    assert 'data-fields="amount description"' in page and 'data-kinds="add_tag set_category"' in page
    bar = page.split("data-rule-filters")[1].split("data-rule-shown")[0]
    assert '<option value="amount">Amount</option>' in bar and 'value="date"' not in bar and '<option value="add_tag">' in bar and 'value="purpose"' not in bar
    edit = client.get(f"/rules/{rule}/edit").text
    assert ">fake</textarea>" in edit and 'value="50.00"' in edit and 'value="Fake Web Tag"' in edit and f'action="/rules/{rule}"' in edit
    client.post(f"/rules/{rule}", data={**_form(), "act-0-value": "ENTERTAINMENT"}, headers=ORIGIN)
    assert category(db, tid) == "ENTERTAINMENT"
    second = rules.save(db, None, [contains("x"), Condition("amount", "over", [1])], [set_category()])
    client.post("/rules/order", data={"ids": [second, rule]}, headers=ORIGIN)
    assert [r.id for r in rules.load(db)] == [second, rule]
    client.post(f"/rules/{rule}/move", data={"step": "-1"}, headers=ORIGIN)
    assert [r.id for r in rules.load(db)] == [rule, second]
    client.post(f"/rules/{rule}/delete", headers=ORIGIN)
    assert category(db, tid) == "GENERAL_MERCHANDISE"
    assert client.get("/rules/9999/edit", follow_redirects=False).status_code == 303


def test_create_a_rule_from_a_transaction_comes_back(client, db):
    tid = add(db, name="FAKE SHOP", merchant="Fake Merchant")
    panel = client.get(f"/transactions/{tid}/edit", headers={"HX-Request": "true", "HX-Current-URL": "https://localhost:8443/transactions?q=fake"}).text
    assert f"/rules/new?from={tid}" in panel and "return_url=/transactions%3Fq%3Dfake" in panel
    builder = client.get(f"/rules/new?from={tid}&return_url=/transactions%3Fq%3Dfake").text
    assert ">Fake Merchant</textarea>" in builder and '<option value="GENERAL_MERCHANDISE" selected>' in builder
    assert 'name="return_url" value="/transactions?q=fake"' in builder
    done = client.post("/rules", data={**_form(), "return_url": "/transactions?q=fake"}, headers=ORIGIN, follow_redirects=False)
    assert done.headers["location"].startswith("/transactions?q=fake&msg=Rule+saved")
    away = client.post("/rules", data={**_form(), "on_similar": "new", "return_url": "https://elsewhere.invalid/"}, headers=ORIGIN, follow_redirects=False)
    assert away.headers["location"].startswith("/rules?msg=")


def test_a_rules_description_unchanged_in_the_panel_stays_automatic(client, db):
    tid = add(db, name="FAKE SHOP")
    rules.save(db, None, [contains("fake")], [Action("set_description", "From a rule")])
    client.post(f"/transactions/{tid}/edit", data={"description": "From a rule", "category": ""}, headers=ORIGIN)
    assert db.execute("SELECT user_description FROM transactions WHERE id = ?", (tid,)).fetchone()[0] is None


def test_deleting_a_category_moves_rule_conditions_too(db):
    rules.save(db, None, [Condition("category", "is", ["TRAVEL", "ENTERTAINMENT"])], [Action("set_category", "TRAVEL")])
    categories.delete_category(db, "TRAVEL", "ENTERTAINMENT")
    rule = rules.load(db)[0]
    assert rule.conditions[0].values == ["ENTERTAINMENT"] and rule.actions[0].value == "ENTERTAINMENT"


def test_the_migration_keeps_existing_rules_and_results(tmp_path):
    """Old rules become conditions and actions with the same ids and order; what they decided stays."""
    conn = database.connect(tmp_path / "old.db")
    for version, path in database.pending_migrations(conn):
        if version < 54:
            conn.executescript(f"BEGIN;\n{path.read_text()}\nINSERT INTO schema_migrations (version) VALUES ({version});\nCOMMIT;")
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification) VALUES (1, 1, 'a', 'Fake', 'credit', 'liability')")
    for pattern, field, key in [("fake, co", "either", "TRAVEL"), ("pos", "description", "BANK_FEES"), ("shop", "merchant", "ENTERTAINMENT")]:
        conn.execute("INSERT INTO category_rules (pattern, field, category_key) VALUES (?, ?, ?)", (pattern, field, key))
    conn.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key, rule_id) "
                 "VALUES ('a', 1, '2026-09-01', 100, 'FAKE, CO', 'OTHER', 1), ('b', 1, '2026-09-01', 100, 'POS 1', 'OTHER', 2), "
                 "('c', 1, '2026-09-01', 100, 'NO RULE', 'OTHER', NULL)")
    conn.commit()
    before = conn.execute("SELECT id, category, category_label, kind, category_source, description FROM transactions_effective ORDER BY id").fetchall()
    database.migrate(conn)
    after = conn.execute("SELECT id, category, category_label, kind, category_source, description FROM transactions_effective ORDER BY id").fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    loaded = rules.load(conn)
    assert [r.id for r in loaded] == [1, 2, 3]
    assert [(r.conditions[0].field, r.conditions[0].op, r.conditions[0].values, r.actions[0].kind, r.actions[0].value) for r in loaded] == [
        ("description", "contains", ["fake, co"], "set_category", "TRAVEL"),
        ("bank_text", "contains", ["pos"], "set_category", "BANK_FEES"),
        ("merchant", "contains", ["shop"], "set_category", "ENTERTAINMENT")]
    rules.apply_all(conn)                                                  # recomputing changes nothing
    again = conn.execute("SELECT id, category, category_label, kind, category_source, description FROM transactions_effective ORDER BY id").fetchall()
    assert [tuple(r) for r in again] == [tuple(r) for r in before]
    assert json.loads(conn.execute('SELECT "values" FROM rule_conditions LIMIT 1').fetchone()[0]) == ["fake, co"]
    assert isinstance(conn, sqlite3.Connection)


def test_alternatives_show_one_per_line(client, db):
    rule = rules.save(db, None, [contains("Uber", "Lyft")], [set_category(), Action("accept")])
    assert ">Lyft\nUber</textarea>" in client.get(f"/rules/{rule}/edit").text


def test_several_inputs_show_alphabetically_and_match_the_same(db):
    """User's request 2026-11-02: including rules saved before this, whose stored order isn't touched."""
    tid = add(db, name="FAKE LYFT")
    db.execute("INSERT INTO accounts (id, name, type, classification, is_manual) VALUES (7, 'Aaa fake', 'depository', 'asset', 1)")
    db.commit()
    rule = rules.save(db, None, [Condition("description", "contains", ["Zed", "apple", "Lyft"]),
                                 Condition("category", "is", ["TRAVEL", "BANK_FEES"]), Condition("account", "not", [CARD, 7])],
                      [Action("set_description", "Matched")])
    db.execute("""UPDATE rule_conditions SET "values" = ? WHERE rule_id = ? AND field = 'description'""", ('["Zed", "apple", "Lyft"]', rule))
    db.commit()
    assert rules.load(db)[0].conditions[0].values == ["apple", "Lyft", "Zed"]
    lines = rules.listing(db)[0]["conditions"]
    assert lines[0] == "description contains “apple” or “Lyft” or “Zed”"
    assert lines[1] == "category is Bank fees or Travel" and lines[2] == "account isn't Aaa fake or Fake credit"
    assert rules.matches(rules.Rule(1, 0, [Condition("description", "contains", ["lyft", "zed"])]), eff(db, tid), {})


def test_keywords_and_custom_rules_are_separate_sections(client, db):
    """User's request 2026-11-02: keywords read as a category and its words, without quotes or "or"."""
    rules.save(db, None, [contains("Uber", "Lyft")], [set_category("TRAVEL")])
    rules.save(db, None, [Condition("bank_text", "contains", ["FAKE PAY"])], [set_category("ENTERTAINMENT")])
    rules.save(db, None, [contains("Coffee"), Condition("amount", "under", [500])], [set_category("BANK_FEES")])
    rules.save(db, None, [contains("Shop")], [Action("set_description", "Renamed")])
    rules.save(db, None, [contains("Two")], [set_category("BANK_FEES"), Action("accept")])
    rules.save(db, None, [Condition("description", "not_contains", ["Nope"])], [set_category("BANK_FEES")])
    page = client.get("/rules").text
    keywords = page.split("<h2>Category keywords</h2>")[1].split("<h2>Custom rules</h2>")[0]
    custom = page.split("<h2>Custom rules</h2>")[1]
    assert '<span class="keyword">Lyft</span><span class="keyword-dot" aria-hidden="true"> · </span><span class="keyword">Uber</span>' in keywords
    assert "“" not in keywords.split('data-search')[0] and ">Lyft or" not in keywords and "(bank text only)" in keywords
    assert keywords.index("Entertainment") < keywords.index("Travel") and "draggable" not in keywords and "/move" not in keywords
    assert "“Coffee”" in custom and "“Shop”" in custom and "“Two”" in custom and "“Nope”" in custom and "“Uber”" not in custom.split('data-search')[0]
    assert custom.count('draggable="true"') == 4 and 'name="results" value="keep"' in keywords


def test_custom_rules_run_before_keywords(db):
    tid = add(db, 1000, name="FAKE UBER")
    keyword = rules.save(db, None, [contains("uber")], [set_category("TRAVEL")])
    assert category(db, tid) == "TRAVEL"
    custom = rules.save(db, None, [contains("uber"), Condition("amount", "under", [5000])], [set_category("BANK_FEES")])
    assert category(db, tid) == "BANK_FEES" and [r.id for r in rules.load(db)] == [custom, keyword]
    rules.move(db, keyword, -1)                                            # keywords have no order to set
    assert [r.id for r in rules.load(db)] == [custom, keyword]
    p = rules.preview(db, [contains("uber")], [set_category("ENTERTAINMENT")])
    assert (p.matches, p.earlier) == (1, 1)                                # the custom rule wins over a new keyword too


def test_keywords_edit_in_place(client, db):
    """User's request 2026-11-02: only a category and keywords; saving shows the clean dotted row."""
    tid, later = add(db, name="FAKE UBER"), add(db, name="FAKE BOLT")
    rule = rules.save(db, None, [contains("Uber", "Lyft")], [set_category("TRAVEL")])
    row = client.get("/rules").text
    assert f'hx-get="/rules/{rule}/keywords"' in row
    form = client.get(f"/rules/{rule}/keywords").text
    assert ">Lyft\nUber</textarea>" in form and '<option value="TRAVEL" selected>' in form
    assert 'name="field"' not in form and "cond-" not in form and "act-" not in form and "Add condition" not in form
    assert f'id="rule-{rule}"' in form and f'hx-get="/rules/{rule}/keyword-row"' in form
    cancel = client.get(f"/rules/{rule}/keyword-row").text
    assert '<span class="keyword">Lyft</span>' in cancel and "<textarea" not in cancel
    saved = client.post(f"/rules/{rule}/keywords", data={"category": "ENTERTAINMENT", "keywords": "Uber\r\n bolt \n\n"}, headers=ORIGIN).text
    assert '<span class="keyword">bolt</span><span class="keyword-dot" aria-hidden="true"> · </span><span class="keyword">Uber</span>' in saved
    assert "Entertainment" in saved and "<textarea" not in saved and ">2</span>" in saved
    assert category(db, tid) == category(db, later) == "ENTERTAINMENT"
    client.post(f"/rules/{rule}/keywords", data={"category": "ENTERTAINMENT", "keywords": "Uber · Lyft | Bolt"}, headers=ORIGIN)
    assert rules.load(db)[0].conditions[0].values == ["Bolt", "Lyft", "Uber"]   # the row's own dots work too
    # problems stay in the form, with what you typed; nothing changes
    blank = client.post(f"/rules/{rule}/keywords", data={"category": "ENTERTAINMENT", "keywords": " · "}, headers=ORIGIN).text
    assert "Enter at least one keyword" in blank and "<textarea" in blank
    bad = client.post(f"/rules/{rule}/keywords", data={"category": "NOPE", "keywords": "Typed"}, headers=ORIGIN).text
    assert "Choose the category" in bad and ">Typed</textarea>" in bad
    assert rules.load(db)[0].conditions[0].values == ["Bolt", "Lyft", "Uber"]
    # the old address comes back here
    assert client.get(f"/rules/{rule}/edit", follow_redirects=False).headers["location"] == f"/rules#rule-{rule}"


def test_keyword_edits_keep_the_text_they_read_and_only_apply_to_keywords(client, db):
    only = rules.save(db, None, [Condition("bank_text", "contains", ["FAKE"])], [set_category()])
    custom = rules.save(db, None, [contains("x"), Condition("amount", "over", [1])], [set_category()])
    client.post(f"/rules/{only}/keywords", data={"category": "TRAVEL", "keywords": "FAKE MORE"}, headers=ORIGIN)
    assert next(r for r in rules.load(db) if r.id == only).conditions[0].field == "bank_text"
    assert client.get(f"/rules/{custom}/keywords").status_code == 404 and client.get(f"/rules/{custom}/keyword-row").status_code == 404
    client.post(f"/rules/{custom}/keywords", data={"category": "TRAVEL", "keywords": "y"}, headers=ORIGIN)
    assert len(next(r for r in rules.load(db) if r.id == custom).conditions) == 2


def test_a_category_an_old_rule_named_can_be_deleted(tmp_path):
    """Found 2026-11-02: the old table's rows blocked it (FOREIGN KEY constraint failed) until migration 0056 emptied it."""
    conn = database.connect(tmp_path / "old.db")
    for version, path in database.pending_migrations(conn):
        if version < 54:
            conn.executescript(f"BEGIN;\n{path.read_text()}\nINSERT INTO schema_migrations (version) VALUES ({version});\nCOMMIT;")
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification) VALUES (1, 1, 'a', 'Fake', 'credit', 'liability')")
    sub = categories.add_subcategory(conn, "FOOD_AND_DRINK", "Fake Bars")
    conn.execute("INSERT INTO category_rules (pattern, field, category_key) VALUES ('fake bar', 'either', ?)", (sub,))
    conn.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key, rule_id) "
                 "VALUES ('a', 1, '2026-09-01', 100, 'FAKE BAR', 'OTHER', 1)")
    conn.commit()
    database.migrate(conn)
    assert conn.execute("SELECT COUNT(*) FROM category_rules").fetchone()[0] == 0
    assert conn.execute("SELECT category, category_source FROM transactions_effective").fetchone()[0] == sub   # its rule still applies
    assert categories.delete_category(conn, sub, "FOOD_AND_DRINK") == 1
    assert not categories.category_exists(conn, sub)
    assert conn.execute("SELECT value FROM rule_actions").fetchone()[0] == "FOOD_AND_DRINK"
    assert conn.execute("SELECT category FROM transactions_effective").fetchone()[0] == "FOOD_AND_DRINK"

