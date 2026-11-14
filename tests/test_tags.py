"""Tags (tags.py, migration 0048, user's request 2026-10-28): your labels on transactions, the Transactions page's tag
controls and the Tags page. Fake data only."""

import json
import re

import pytest

from fintrack import db as dbmod, items, tags

ORIGIN = {"origin": "https://localhost:8443"}
HX = {**ORIGIN, "HX-Request": "true"}
CHECKING, HIDDEN, LOAN = 1, 2, 3


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    for acc_id, hidden, kind in ((CHECKING, 0, "depository"), (HIDDEN, 1, "depository"), (LOAN, 0, "loan")):
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification, is_hidden)"
                     " VALUES (?, 1, ?, ?, ?, ?, ?)",
                     (acc_id, f"acc-{acc_id}", f"Fake account {acc_id}", kind, items.classify(kind), hidden))
    conn.commit()
    return conn


_ids = iter(range(1, 100_000))


def add(conn, cents, day="2026-10-10", key="FOOD_AND_DRINK", name="FAKE SHOP", account=CHECKING):
    tid = conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key)"
        " VALUES (?, ?, ?, ?, ?, ?)", (f"g{next(_ids)}", account, day, cents, name, key)).lastrowid
    conn.commit()
    return tid


# ---------------------------------------------------------------- the tags themselves

def test_names_are_cleaned_unique_ignoring_case_and_emoji_welcome(db):
    a, b = add(db, 1000), add(db, 2000)
    tag, added = tags.add(db, [a, b], "  🏖️   Fake  Trip ")
    assert tag.name == "🏖️ Fake Trip" and added == 2
    again, added = tags.add(db, [a], "🏖️ fake trip")                      # same tag, any case; already on it
    assert again.id == tag.id and added == 0
    assert [t.name for t in tags.all_tags(db)] == ["🏖️ Fake Trip"]
    for bad in ("", "   ", "x" * 41):
        with pytest.raises(tags.TagError):
            tags.add(db, [a], bad)
    assert tags.remove(db, [a], tag.id) == 1 and [g.name for g in tags.tags_of(db, [a, b])[b]] == ["🏖️ Fake Trip"]
    assert a not in tags.tags_of(db, [a, b])


def test_rename_and_delete(db):
    a = add(db, 1000)
    trip, _ = tags.add(db, [a], "Fake Trip")
    other, _ = tags.add(db, [a], "Fake Other")
    before = dbmod.revision(db)
    tags.rename(db, trip.id, "Fake Trip")                                    # unchanged: nothing written
    assert dbmod.revision(db) == before
    tags.rename(db, trip.id, "✈️ Fake Trip")
    with pytest.raises(tags.TagError):
        tags.rename(db, other.id, "✈️ FAKE TRIP")                            # taken (ignoring case)
    tags.delete(db, other.id)
    assert [g.name for g in tags.tags_of(db, [a])[a]] == ["✈️ Fake Trip"]
    db.execute("DELETE FROM transactions WHERE id = ?", (a,))               # a deleted transaction lets go of its tags
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM transaction_tags").fetchone()[0] == 0


def test_summaries_count_spending_income_and_transfers(db):
    """Spent = expense (refunds lower it) + transfers out; received = income + transfers in (user's request 2026-10-28:
    a tag's transfers count). Loan entries and hidden accounts aren't added up."""
    trip = [add(db, 12000, "2026-07-02", "TRAVEL"), add(db, 4500, "2026-07-03", "FOOD_AND_DRINK"),
            add(db, 3000, "2026-07-04", "FOOD_AND_DRINK"), add(db, -1000, "2026-07-05", "FOOD_AND_DRINK"),   # a refund
            add(db, -20000, "2026-07-06", "INCOME"),                                                            # paid back
            add(db, 5100, "2026-07-07", "TRANSFER_OUT"),                                  # your share, by Venmo: spent
            add(db, -2500, "2026-07-07", "TRANSFER_IN"),                                  # a friend paid you: received
            add(db, 7700, "2026-07-08", "LOAN_PAYMENTS", account=LOAN),                   # a loan entry: not counted
            add(db, 99900, "2026-07-08", "TRAVEL", account=HIDDEN)]                                             # hidden
    tags.add(db, trip, "🏖️ Fake Trip")
    tags.add(db, [add(db, 700)], "Fake Small")
    tags.find_or_create(db, "Fake Unused")
    db.commit()
    found = {s.tag.name: s for s in tags.summaries(db)}
    s = found["🏖️ Fake Trip"]
    assert (s.spent_cents, s.received_cents, s.count, s.first, s.last) == (23600, 22500, 8, "2026-07-02", "2026-07-08")
    assert [(line.category, line.side, line.kind, line.cents, line.count) for line in s.lines] == [
        ("TRAVEL", "spent", "expense", 12000, 1), ("FOOD_AND_DRINK", "spent", "expense", 6500, 3),
        ("TRANSFER_OUT", "spent", "transfer", 5100, 1),
        ("INCOME", "received", "income", 20000, 1), ("TRANSFER_IN", "received", "transfer", 2500, 1)]
    assert s.not_counted == 1
    assert [x.tag.name for x in tags.summaries(db)] == ["Fake Small", "🏖️ Fake Trip", "Fake Unused"]  # newest first
    tags.set_dates(db, found["🏖️ Fake Trip"].tag.id, "2026-11-01", "2026-11-05")       # its own End date counts
    assert [x.tag.name for x in tags.summaries(db)] == ["🏖️ Fake Trip", "Fake Small", "Fake Unused"]
    assert found["Fake Unused"].count == 0 and found["Fake Unused"].first is None


# ---------------------------------------------------------------- the pages

@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app
    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_the_edit_panel_adds_and_removes_tags_and_keeps_the_row_in_step(client, db):
    t = add(db, 4200, "2026-10-10")
    panel = client.get(f"/transactions/{t}/edit").text
    assert f'id="tag-line-{t}"' in panel and 'name="new_tag"' in panel and f'list="tag-names-{t}"' in panel
    answer = client.post(f"/transactions/{t}/tags", data={"new_tag": "🧾 Fake Taxes"}, headers=HX).text
    assert f'id="tag-line-{t}"' in answer and "🧾 Fake Taxes" in answer
    assert f'<span class="txn-tags" id="txn-tags-{t}" hx-swap-oob="true"><span class="tag-chip">🧾 Fake Taxes</span></span>' in answer
    bad = client.post(f"/transactions/{t}/tags", data={"new_tag": "   "}, headers=HX).text
    assert "Give the tag a name." in bad
    tag_id = tags.all_tags(db)[0].id
    answer = client.post(f"/transactions/{t}/tags/remove", data={"tag_id": tag_id}, headers=HX).text
    assert f'<span class="txn-tags" id="txn-tags-{t}" hx-swap-oob="true"></span>' in answer
    # Enter in the box saves the panel, adding the tag too; the row comes back with its chip
    row = client.post(f"/transactions/{t}/edit", data={"description": "", "category": "", "new_tag": "Fake Project"},
                      headers=HX).text
    assert '<span class="tag-chip">Fake Project</span>' in row


def test_the_list_shows_chips_filters_by_tag_and_group_edit_tags(client, db):
    a, b, c = add(db, 1000, "2026-10-10"), add(db, 2000, "2026-10-11"), add(db, 3000, "2026-10-12")
    trip, _ = tags.add(db, [a, b], "🏖️ Fake Trip")
    page = client.get("/transactions?range=custom").text
    assert page.count('<span class="tag-chip">🏖️ Fake Trip</span>') == 2
    assert f'name="tag" value="{trip.id}" data-label="🏖️ Fake Trip" >' in page and 'formaction="/transactions/bulk-tag"' in page
    only = client.get(f"/transactions?range=custom&tag={trip.id}").text
    assert 'data-bulk-total="2"' in only and f'id="txn-{c}"' not in only
    assert f'<input type="hidden" name="tag" value="{trip.id}">' in only            # group edit keeps the filter
    # a checklist (user's request 2026-10-30): several tags, and "No tag" for the untagged
    work, _ = tags.add(db, [c], "Fake Work")
    def shown(query):
        found = re.search(r'data-bulk-total="(\d+)"', client.get("/transactions?range=custom" + query).text)
        return found.group(1) if found else "0"                                      # none: no list, no group edit
    assert shown(f"&tag={trip.id}&tag={work.id}") == "3" and shown(f"&tag={trip.id},{work.id}") == "3"
    assert shown("&tag=none") == "0" and shown(f"&tag=none&tag={work.id}") == "1"
    tags.remove(db, [c], work.id)
    assert shown("&tag=none") == "1" and shown("&tag=bogus") == "3"                  # junk: ignored
    box = client.get(f"/transactions?range=custom&tag=none&tag={trip.id}").text.split('aria-label="Tags"')[1].split("</details>")[0]
    assert "data-checklist-label>2 tags</summary>" in box and 'value="none" data-label="No tag" checked>' in box
    done = client.post("/transactions/bulk-tag", data={"ids": [b, c], "tag_name": "🏖️ fake trip", "tag_action": "add",
                       "return_url": "/transactions"}, headers=ORIGIN, follow_redirects=False)
    assert "1+transaction" in done.headers["location"] and "already+had+it" in done.headers["location"]
    client.post("/transactions/bulk-tag", data={"all": "1", "range": "custom", "tag_name": "🏖️ Fake Trip",
                "tag_action": "remove", "return_url": "/transactions"}, headers=ORIGIN)
    assert tags.tags_of(db, [a, b, c]) == {}
    missing = client.post("/transactions/bulk-tag", data={"ids": [a], "tag_name": "Fake Nothing", "tag_action": "remove",
                          "return_url": "/transactions"}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in missing.headers["location"]


def test_the_tags_page_sums_each_tag_and_links_to_its_transactions(client, db):
    assert "No tags yet." in client.get("/tags").text
    nav = client.get("/budgets").text
    assert nav.index('href="/budgets"') < nav.index('href="/tags"') < nav.index('href="/recurring"')   # Planning: after Budgets
    trip = [add(db, 12000, "2026-07-02", "TRAVEL"), add(db, 4000, "2026-07-03", "FOOD_AND_DRINK"),
            add(db, -20000, "2026-07-06", "INCOME")]
    tag, _ = tags.add(db, trip, "🏖️ Fake Trip")
    venmo = add(db, 3000, "2026-07-04", "TRANSFER_OUT")
    tags.add(db, [venmo], "🏖️ Fake Trip")                                      # a transfer counts (user's request)
    page = client.get("/tags").text
    card = page.split(' class="card tag-card tb-1')[1]
    head = card.split("<h2>")[1].split("</h2>")[0]
    assert head.index('class="swatch tag-swatch"') < head.index("<a href=")                       # its colour, then its name
    assert f'href="/transactions?tag={tag.id}&amp;range=custom&amp;from=2026-07-02&amp;to=2026-07-06">🏖️ Fake Trip</a>' in card
    assert ">$190<" in card and ">$200<" in card and 'class="tag-when"' not in card and "Jul 2 – Jul 6" not in card
    assert "category=TRAVEL&amp;kind=expense&amp;only=1" in card and "category=TRANSFER_OUT&amp;kind=transfer&amp;only=1" in card
    assert 'data-share="0.6316"' in card                                        # Travel: 120 of 190
    assert "<th colspan=\"3\">Received</th>" in card and "style=" not in page.split("<main>")[1]
    response = client.post(f"/tags/{tag.id}/rename", data={"name": "✈️ Fake Trip"}, headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303 and tags.all_tags(db)[0].name == "✈️ Fake Trip"
    client.post(f"/tags/{tag.id}/delete", headers=ORIGIN)
    assert tags.all_tags(db) == [] and db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 4


# ---------------------------------------------------------------- the calendar (user's request 2026-10-28)

def test_runs_are_back_to_back_days_and_a_lone_day_is_its_own(db):
    """Without its own dates, a tag's calendar bars only cover continuous days (user's request 2026-10-28)."""
    days = [add(db, 1000, "2026-07-02"), add(db, 2000, "2026-07-03"), add(db, 500, "2026-07-03"),
            add(db, -300, "2026-07-04"),                                           # a refund: still a day, lowers it
            add(db, 4000, "2026-07-09"),                                           # alone
            add(db, 7000, "2026-07-30"), add(db, 100, "2026-07-31", "TRANSFER_OUT"),
            add(db, -5000, "2026-08-01", "INCOME"),                                # received: a day, nothing spent
            add(db, 9900, "2026-07-20", account=HIDDEN)]                           # hidden account: not a day
    tag, _ = tags.add(db, days, "Fake Trip")
    assert tags.calendar_items(db)[tag.id] == [tags.Run("2026-07-02", "2026-07-04", 3200), tags.Run("2026-07-09", "2026-07-09", 4000),
                                     tags.Run("2026-07-30", "2026-08-01", 7100)]


def test_the_calendar_puts_each_run_on_its_days_in_lanes():
    from datetime import date

    from fintrack.web.tags import calendar_month

    def summary(tag_id, name):
        return tags.TagSummary(tags.Tag(tag_id, name), 1, 100, 0, "2026-06-20", "2026-08-15")

    trip, dinner, reno = summary(1, "Fake Trip"), summary(2, "Fake Dinner"), summary(3, "Fake Reno")
    tag_runs = {1: [tags.Run("2026-07-02", "2026-07-08", 500), tags.Run("2026-07-15", "2026-07-15", 40)],  # Thu..Wed; a lone Wed
                2: [tags.Run("2026-07-03", "2026-07-03", 90)],                       # overlaps the trip: a second lane
                3: [tags.Run("2026-06-20", "2026-08-15", 9000)]}                     # starts before July, ends after
    weeks = calendar_month([dinner, trip, reno], tag_runs, "2026-07", today=date(2026, 7, 9))
    first = weeks[0]                                                     # Sun Jun 28 .. Sat Jul 4
    assert [d["day"] for d in first.days] == [28, 29, 30, 1, 2, 3, 4] and not first.days[0]["in_month"]
    lanes = [[(b.summary.tag.name, b.start, b.end, b.starts, b.ends, b.slot) for b in lane] for lane in first.lanes]
    assert lanes == [[("Fake Reno", 1, 7, False, False, "tb-3")],
                     [("Fake Trip", 5, 7, True, False, "tb-1")],
                     [("Fake Dinner", 6, 6, True, True, "tb-2")]]
    second = [[(b.summary.tag.name, b.start, b.end, b.ends) for b in lane] for lane in weeks[1].lanes]
    assert second == [[("Fake Reno", 1, 7, False)], [("Fake Trip", 1, 4, True)]]   # the dinner's lane isn't needed
    third = [[(b.summary.tag.name, b.start, b.end, b.run.spent_cents) for b in lane] for lane in weeks[2].lanes]
    assert third == [[("Fake Reno", 1, 7, 9000)], [("Fake Trip", 4, 4, 40)]]       # the lone day: its own bar
    assert first.lanes[1][0].url == "/transactions?tag=1&range=custom&from=2026-07-02&to=2026-07-08"
    assert weeks[1].days[4]["today"] and len(calendar_month([trip], tag_runs, "2026-09")[0].lanes) == 0


def test_the_tags_page_has_the_calendar_on_the_latest_month(client, db):
    a, b, c = add(db, 5000, "2026-07-02", "TRAVEL"), add(db, 1000, "2026-07-03", "TRAVEL"), add(db, 3000, "2026-07-08")
    tag, _ = tags.add(db, [a, b, c], "🏖️ Fake Trip")
    page = client.get("/tags").text
    cal = page.split('<section class="card tag-calendar">')[1].split("</section>")[0]
    assert "<h2>July 2026</h2>" in cal and 'href="/tags?month=2026-06"' in cal and 'href="/tags?month=2026-08"' in cal
    # Jul 2-3 together, Jul 8 alone (not one bar from Jul 2 to Jul 8)
    assert cal.count('class="cal-bar') == 2
    assert 'class="cal-bar tb-1 cs-5 ce-6"' in cal and 'class="cal-bar tb-1 cs-4 ce-4"' in cal
    assert f'href="#tag-card-{tag.id}" data-focus-tag="{tag.id}"' in cal           # a bar scrolls to its card (2026-11-02)
    # each bar's tooltip has all of the tag's spending, not just its days' (user's request 2026-11-04)
    assert "🏖️ Fake Trip: Jul 2 – Jul 3 · $90 spent" in cal and "🏖️ Fake Trip: Jul 8 · $90 spent" in cal
    later = client.get("/tags?month=2026-09").text.split('<section class="card tag-calendar">')[1].split("</section>")[0]
    assert "<h2>September 2026</h2>" in later and "cal-bar" not in later


def test_a_tags_own_dates_make_its_range_and_other_days_stand_alone(db):
    """Start and End (user's request 2026-10-28): one bar for them; each tagged day outside them is its own bar."""
    days = [add(db, 900, "2026-07-01"),                                       # booked ahead: outside
            add(db, 1000, "2026-07-10"), add(db, 2000, "2026-07-12"),         # inside (no transaction on the 11th)
            add(db, 300, "2026-07-20"), add(db, 400, "2026-07-21")]           # after: two single days, not one run
    tag, _ = tags.add(db, days, "Fake Trip")
    tags.set_dates(db, tag.id, "2026-07-09", "")                              # one date alone is kept...
    assert tags.calendar_items(db)[tag.id][0] == tags.Run("2026-07-01", "2026-07-01", 900)   # ...but isn't a range yet
    tags.set_dates(db, tag.id, "2026-07-09", "2026-07-13")
    assert tags.calendar_items(db)[tag.id] == [
        tags.Run("2026-07-01", "2026-07-01", 900, loose=True), tags.Run("2026-07-09", "2026-07-13", 3000),
        tags.Run("2026-07-20", "2026-07-20", 300, loose=True), tags.Run("2026-07-21", "2026-07-21", 400, loose=True)]
    for start, end in (("2026-07-14", "2026-07-13"), ("soon", ""), ("0002-07-09", ""), ("2026-07-09", "20261-07-13")):
        with pytest.raises(tags.TagError):
            tags.set_dates(db, tag.id, start, end)
    tags.set_dates(db, tag.id, "", "")                                        # cleared: back to consecutive days
    assert [(r.first, r.last) for r in tags.calendar_items(db)[tag.id]] == [
        ("2026-07-01", "2026-07-01"), ("2026-07-10", "2026-07-10"), ("2026-07-12", "2026-07-12"), ("2026-07-20", "2026-07-21")]
    empty = tags.find_or_create(db, "Fake Planned")                           # dates but no transactions yet: still a bar
    db.commit()
    tags.set_dates(db, empty.id, "2026-08-01", "2026-08-03")
    assert tags.calendar_items(db)[empty.id] == [tags.Run("2026-08-01", "2026-08-03", 0)]


def test_the_cards_set_the_dates_and_the_calendar_draws_them(client, db):
    tag, _ = tags.add(db, [add(db, 1000, "2026-07-10"), add(db, 500, "2026-07-01")], "Fake Trip")
    card = client.get("/tags").text.split(' class="card tag-card tb-1')[1]
    assert f'action="/tags/{tag.id}/dates"' not in card                             # boxes in edit mode only (2026-11-04)
    card = client.get(f"/tags?edit={tag.id}").text.split(' class="card tag-card tb-1')[1]
    assert f'action="/tags/{tag.id}/dates"' in card and 'name="start" value="" data-submit-on-leave' in card
    response = client.post(f"/tags/{tag.id}/dates", data={"start": "2026-07-09", "end": "2026-07-12", "month": "2026-07",
                                                          "edit": str(tag.id)}, headers=ORIGIN, follow_redirects=False)
    assert response.headers["location"] == f"/tags?month=2026-07&edit={tag.id}"          # still editing
    assert '<p class="tag-when">Jul 9 – Jul 12</p>' in client.get("/tags?month=2026-07").text
    cal = client.get("/tags?month=2026-07").text.split('<section class="card tag-calendar">')[1].split("</section>")[0]
    assert 'class="cal-bar tb-1 cs-5 ce-7 open-end"' in cal and 'class="cal-bar tb-1 cs-1 ce-1 open-start"' in cal   # Thu 9 - Sun 12
    assert 'class="cal-bar tb-1 cs-4 ce-4 loose"' in cal and "Fake Trip: Jul 1 (outside its dates) · $15 spent" in cal
    bad = client.post(f"/tags/{tag.id}/dates", data={"start": "2026-07-13", "end": "2026-07-12"}, headers=ORIGIN,
                      follow_redirects=False)
    assert "error=" in bad.headers["location"]


def test_the_calendar_has_month_quarter_and_year_views(client, db):
    """The ribbon toggle (user's request 2026-10-28): 1 month, 3 months (ending with the anchor), 1 year (its calendar
    year); the arrows step the view's length."""
    from fintrack.web.tags import view_months, view_title

    assert view_months("quarter", "2026-01") == ["2025-11", "2025-12", "2026-01"]
    assert view_months("year", "2026-07")[0] == "2026-01" and len(view_months("year", "2026-07")) == 12
    assert view_title("quarter", ["2026-08", "2026-09", "2026-10"]) == "Aug – Oct 2026"
    assert view_title("quarter", ["2025-11", "2025-12", "2026-01"]) == "Nov 2025 – Jan 2026"
    assert view_title("year", view_months("year", "2026-07")) == "2026"
    tag, _ = tags.add(db, [add(db, 1000, "2026-07-10"), add(db, 500, "2026-05-02")], "Fake Trip")
    page = client.get("/tags").text
    ribbon = page.split('<div class="tabbar-controls">')[1].split("</nav>")[0]
    assert 'href="/tags?month=2026-07" class="view-month"\n      aria-current="page">1 month' in ribbon
    assert 'href="/tags?view=quarter&amp;month=2026-07"' in ribbon and 'href="/tags?view=year&amp;month=2026-07"' in ribbon
    quarter = client.get("/tags?view=quarter&month=2026-07").text.split('<section class="card tag-calendar">')[1].split("</section>")[0]
    assert "<h2>May – Jul 2026</h2>" in quarter and quarter.count('<div class="cal small">') == 3
    assert 'href="/tags?view=quarter&amp;month=2026-06"' in quarter and 'href="/tags?view=quarter&amp;month=2026-08"' in quarter   # a month at a time (2026-11-02)
    assert quarter.count('class="cal-bar') == 2 and "cal-legend" not in quarter              # no legend (2026-10-29)
    year = client.get("/tags?view=year&month=2026-07").text.split('<section class="card tag-calendar">')[1].split("</section>")[0]
    assert "<h2>2026</h2>" in year and year.count('<div class="cal tiny">') == 12 and 'month=2025-07' in year
    response = client.post(f"/tags/{tag.id}/dates", data={"start": "2026-07-09", "end": "2026-07-12", "month": "2026-07",
                           "view": "year"}, headers=ORIGIN, follow_redirects=False)
    assert response.headers["location"] == "/tags?view=year&month=2026-07"            # back to the same view


def test_plus_makes_a_new_tag_and_a_taken_name_is_refused(client, db):
    """The ribbon's + (user's request 2026-10-29), there even before any tag exists."""
    assert 'action="/tags/new"' in client.get("/tags").text.split('<div class="tabbar-controls">')[1].split("</div>")[0]
    response = client.post("/tags/new", data={"name": "  🎿 Fake  Ski  ", "month": "2026-07", "view": "year"},
                           headers=ORIGIN, follow_redirects=False)
    assert response.headers["location"].startswith("/tags?view=year&month=2026-07&msg=")
    assert [t.name for t in tags.all_tags(db)] == ["🎿 Fake Ski"]
    again = client.post("/tags/new", data={"name": "🎿 fake ski"}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in again.headers["location"] and len(tags.all_tags(db)) == 1
    page = client.get("/tags").text
    assert "🎿 Fake Ski" in page.split("data-tag-cards>")[1]                         # no dates yet: still listed


def test_the_calendar_setting_hides_the_tagged_days(client, db):
    """The gear's checkbox (user's request 2026-10-29): unticked = only tags' own ranges; saved."""
    dated, _ = tags.add(db, [add(db, 1000, "2026-07-10"), add(db, 500, "2026-07-01")], "Fake Trip")
    tags.add(db, [add(db, 700, "2026-07-03")], "Fake Dinner")
    tags.set_dates(db, dated.id, "2026-07-09", "2026-07-12")
    cal = client.get("/tags?month=2026-07").text.split('<section class="card tag-calendar">')[1].split("</section>")[0]
    assert 'action="/tags/settings"' in cal and "checked> Show transaction dates" in cal and cal.count('class="cal-bar') == 4
    response = client.post("/tags/settings", data={"show_days": "0", "month": "2026-07"}, headers=ORIGIN,
                           follow_redirects=False)                     # the hidden 0 alone: the box was unticked
    assert response.headers["location"] == "/tags?month=2026-07" and not tags.calendar_shows_days(db)
    cal = client.get("/tags?month=2026-07").text.split('<section class="card tag-calendar">')[1].split("</section>")[0]
    assert cal.count('class="cal-bar') == 2 and "loose" not in cal and "Fake Dinner" not in cal   # the range only
    client.post("/tags/settings", data={"show_days": ["0", "1"]}, headers=ORIGIN)  # ticked: the last wins
    assert tags.calendar_shows_days(db)


def test_the_cards_are_newest_first_with_undated_ones_on_top():
    """User's request 2026-11-02: one list instead of Upcoming / Visible / Past."""
    from fintrack.web.tags import card_order

    def summary(tag_id, first=None, last=None, start=None, end=None):
        return tags.TagSummary(tags.Tag(tag_id, f"Fake {tag_id}", start, end), 1, 0, 0, first, last)

    trip = summary(1, start="2026-12-01", end="2026-12-05")
    spent = summary(2, "2026-10-01", "2026-10-03")
    old = summary(3, "2026-03-01", "2026-03-04")
    new = summary(4)
    booked = summary(5, "2026-09-15", "2026-09-15", start="2027-01-02", end="2027-01-09")   # paid ahead: by its End
    open_ended = summary(6, start="2026-11-01")
    assert [s.tag.id for s in card_order([old, trip, spent, new, booked, open_ended])] == [4, 5, 1, 6, 2, 3]


def test_cards_on_the_calendar_are_outlined(client, db):
    tags.add(db, [add(db, 1000, "2026-07-10")], "Fake Trip")
    tags.add(db, [add(db, 500, "2026-03-01")], "Fake Old")
    page = client.get("/tags?month=2026-07").text
    assert "tag-section" not in page and ">Upcoming<" not in page and ">Past<" not in page
    list_html = page.split("data-tag-cards>")[1]
    assert list_html.index("Fake Trip") < list_html.index("Fake Old")               # newest first
    trip = list_html.split(' class="card tag-card')[1]
    old = list_html.split(' class="card tag-card')[2]
    assert "on-calendar" in trip.split(">")[0] and "Fake Trip" in trip
    assert "on-calendar" not in old.split(">")[0] and "Fake Old" in old
    # it follows the calendar
    march = client.get("/tags?month=2026-03").text.split("data-tag-cards>")[1].split(' class="card tag-card')
    assert "on-calendar" not in march[1].split(">")[0] and "on-calendar" in march[2].split(">")[0]


def test_every_page_has_the_light_dark_switch_and_follows_the_cookie(client):
    """The rail's light / dark switch (user's request 2026-10-28): no cookie = the system's theme (no data-theme); the
    ft_theme cookie the switch writes = that theme on <html>, from the first paint; anything else is ignored."""
    page = client.get("/tags").text
    assert page.startswith("<!doctype html>\n<html lang=\"en\">")
    switch = page.split('class="rail-theme"')[1].split("</button>")[0]
    assert 'role="switch" aria-checked="false"' in switch and "data-theme-toggle" in switch and ">Dark mode<" in switch
    assert page.index('class="rail-theme"') < page.index('class="rail-sync-form"')            # above Sync now
    client.cookies.set("ft_theme", "dark")
    dark = client.get("/tags").text
    assert '<html lang="en" data-theme="dark">' in dark and 'aria-checked="true"' in dark.split('class="rail-theme"')[1][:200]
    client.cookies.set("ft_theme", "purple")
    assert '<html lang="en">' in client.get("/tags").text


def test_hovering_a_description_shows_it_with_its_tags(client, db):
    """User's request 2026-11-01: a long one can be cut off, its tags hidden; the hover text has them all."""
    tid = add(db, 1000)
    tags.add(db, [tid], "Fake Hover")
    row = client.get("/transactions?range=custom").text.split(f'id="txn-{tid}"')[1].split("</tr>")[0]
    assert 'title="FAKE SHOP · Fake Hover"' in row
    label = db.execute("SELECT category_label FROM transactions_effective WHERE id = ?", (tid,)).fetchone()[0]
    assert f'<td class="cat-col" title="{label}">'.replace("&", "&amp;") in row             # the category's too



def test_tag_colors(client, db):
    """User's request 2026-11-02: 30-40 predefined, your own added to the list for every tag, or automatic again."""
    colors = [c for _, shades in tags.COLOR_PALETTE for c in shades]
    assert 30 <= len(colors) <= 40 and len(set(colors)) == len(colors)
    assert all(re.fullmatch(r"#[0-9a-f]{6}", c) for c in colors)
    trip, other = tags.create(db, "Fake Trip"), tags.create(db, "Fake Other")
    add_id = add(db, 1000, "2026-07-02", "TRAVEL")
    tags.add(db, [add_id], "Fake Trip")
    page = client.get("/tags?month=2026-07").text
    assert page.count('class="color-choice"') == 2 * len(colors)                 # a picker on each card
    assert 'class="card tag-card tb-1 on-calendar" data-keep-scroll data-tag-text=' in page    # no choice yet: automatic

    client.post(f"/tags/{trip.id}/color", data={"color": colors[0]}, headers=ORIGIN)
    page = client.get("/tags?month=2026-07").text
    assert f'class="card tag-card tb-1 on-calendar" data-tag-color="{colors[0]}"' in page
    bar = page.split('class="cal-bar')[1].split(">")[0]
    assert f'data-tag-color="{colors[0]}"' in bar                                # the calendar's bar follows

    client.post(f"/tags/{trip.id}/color", data={"custom": "#ABCDEF"}, headers=ORIGIN)
    assert tags.custom_colors(db) == ["#abcdef"]
    assert next(t for t in tags.all_tags(db) if t.id == trip.id).color == "#abcdef"
    page = client.get("/tags?month=2026-07").text
    assert page.count('name="color" value="#abcdef"') == 2                       # offered on the other tag too
    client.post(f"/tags/{other.id}/color", data={"custom": colors[1]}, headers=ORIGIN)
    assert tags.custom_colors(db) == ["#abcdef"]                                 # already predefined: not listed twice

    client.post("/tags/colors/remove", data={"remove": "#abcdef"}, headers=ORIGIN)
    assert tags.custom_colors(db) == []
    assert next(t for t in tags.all_tags(db) if t.id == trip.id).color == "#abcdef"   # a tag using it keeps it
    client.post(f"/tags/{trip.id}/color", data={"color": ""}, headers=ORIGIN)
    assert next(t for t in tags.all_tags(db) if t.id == trip.id).color is None
    bad = client.post(f"/tags/{trip.id}/color", data={"color": "red;x"}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in bad.headers["location"]


def test_inactive_tags_leave_pickers_and_go_last_on_the_transactions_filter(client, db):
    """User's request 2026-11-02: latest transaction more than 365 days ago."""
    from datetime import date, timedelta

    today = date.today()
    day = lambda n: (today - timedelta(days=n)).isoformat()                       # noqa: E731
    for name, age in (("Fake Old", 366), ("Fake Edge", 365)):
        tags.add(db, [add(db, 1000, day(age), "TRAVEL")], name)
    tags.add(db, [add(db, 1000, day(900), "TRAVEL"), add(db, 1000, day(5), "TRAVEL")], "Fake Revived")
    tags.create(db, "Fake New")                                                   # no transactions: active
    active, inactive = tags.split_by_activity(db)
    assert [t.name for t in inactive] == ["Fake Old"]
    assert [t.name for t in active] == ["Fake Edge", "Fake New", "Fake Revived"]
    old = inactive[0]

    page = client.get("/transactions?range=all").text
    filter_box = page.split('aria-label="Tags"')[1].split("</details>")[0]
    assert filter_box.index("Fake Revived") < filter_box.index(">Inactive<") < filter_box.index("Fake Old")
    assert "Fake Old" not in page.split('id="bulk-tag-names"')[1].split("</datalist>")[0]
    assert f'<option value="tag:{old.id}">' not in client.get("/budgets").text
    assert "Fake Old" not in client.get("/rules/new").text.split('id="rule-tag-names"')[1].split("</datalist>")[0]

    tid = db.execute("SELECT id FROM transactions ORDER BY id LIMIT 1").fetchone()[0]
    panel = client.get(f"/transactions/{tid}/edit").text
    assert "Fake Old" not in panel.split("<datalist")[1].split("</datalist>")[0]
    # still yours to filter by, and a new transaction with it makes it active again
    ticked = client.get(f"/transactions?range=all&tag={old.id}").text.split('aria-label="Tags"')[1]
    assert "Fake Old" in ticked.split("</summary>")[0]
    tags.add(db, [add(db, 1000, day(1), "TRAVEL")], "Fake Old")
    assert tags.split_by_activity(db)[1] == []


def test_holidays_by_rule_and_observed_days():
    """User's requests 2026-11-02. Dates checked against the published calendar for these years."""
    from datetime import date

    from fintrack import calendar_labels as cl

    def on(year):
        return {name: day for day, name, _ in cl.holidays(year)}

    y2026, y2027 = on(2026), on(2027)
    assert y2026["Thanksgiving"] == date(2026, 11, 26) and y2026["Black Friday"] == date(2026, 11, 27)
    assert y2026["Small Business Saturday"] == date(2026, 11, 28) and y2026["Cyber Monday"] == date(2026, 11, 30)
    assert y2026["Easter"] == date(2026, 4, 5) and y2027["Easter"] == date(2027, 3, 28) and on(2024)["Easter"] == date(2024, 3, 31)
    assert y2026["Mother's Day"] == date(2026, 5, 10) and y2026["Father's Day"] == date(2026, 6, 21)
    assert y2026["Memorial Day"] == date(2026, 5, 25) and y2026["Labor Day"] == date(2026, 9, 7)
    assert y2026["Martin Luther King Jr. Day"] == date(2026, 1, 19) and y2026["Presidents' Day"] == date(2026, 2, 16)
    assert y2026["Columbus Day"] == date(2026, 10, 12)
    assert y2026["Super Saturday"] == date(2026, 12, 19) and on(2027)["Super Saturday"] == date(2027, 12, 18)
    # July 4, 2026 is a Saturday: the Friday before. July 4, 2027 is a Sunday: the Monday after.
    assert y2026["Independence Day"] == date(2026, 7, 4) and y2026["Independence Day (observed)"] == date(2026, 7, 3)
    assert y2027["Independence Day (observed)"] == date(2027, 7, 5)
    assert "Christmas Day (observed)" not in y2026 and y2026["Christmas Day"].weekday() == 4     # a Friday: none needed
    assert not [name for name in y2026 if "observed" in name and name.split(" (")[0] in ("Thanksgiving", "Labor Day")]


def test_observed_new_years_day_lands_in_the_year_before(db):
    from datetime import date

    from fintrack import calendar_labels as cl

    # January 1, 2028 is a Saturday: observed Friday, December 31, 2027, in a view that ends that day
    found = cl.day_labels(db, date(2027, 12, 1), date(2027, 12, 31))
    assert "New Year's Day (observed)" in [l.text for l in found["2027-12-31"]]
    assert "New Year's Eve" in [l.text for l in found["2027-12-31"]]


def test_your_day_labels(client, db):
    from datetime import date

    from fintrack import calendar_labels as cl

    tags.create(db, "Fake Trip")                                                  # the calendar shows with a tag
    client.post("/tags/labels", data={"date": "2026-07-09", "label": "Fake birthday", "yearly": "1"}, headers=ORIGIN)
    client.post("/tags/labels", data={"date": "2026-07-10", "label": "Fake party"}, headers=ORIGIN)
    page = client.get("/tags?month=2026-07").text
    assert '<span class="cal-label custom">Fake birthday</span>' in page and '<span class="cal-label federal">Independence Day</span>' in page
    assert '<span class="cal-label federal">Independence Day (observed)</span>' in page
    assert 'id="day-dialog"' in page and 'data-cal-day="2026-07-09"' in page
    # every year from its date on; one-off stays put
    later = cl.day_labels(db, date(2028, 7, 1), date(2028, 7, 31))
    assert [l.text for l in later["2028-07-09"]] == ["Fake birthday"] and "2028-07-10" not in later
    assert "2025-07-09" not in cl.day_labels(db, date(2025, 7, 1), date(2025, 7, 31))
    # your label comes first on a holiday; February 29 only in leap years
    cl.add(db, "2026-07-04", "Fake cookout")
    assert cl.day_labels(db, date(2026, 7, 4), date(2026, 7, 4))["2026-07-04"][0].text == "Fake cookout"
    cl.add(db, "2028-02-29", "Fake leap", yearly=True)
    assert "2029-02-28" not in cl.day_labels(db, date(2029, 2, 28), date(2029, 2, 28))
    assert "2032-02-29" in cl.day_labels(db, date(2032, 2, 29), date(2032, 2, 29))
    bad = client.post("/tags/labels", data={"date": "nope", "label": "x"}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in bad.headers["location"]
    label_id = db.execute("SELECT id FROM calendar_labels WHERE label = 'Fake party'").fetchone()[0]
    client.post(f"/tags/labels/{label_id}/delete", headers=ORIGIN)
    assert "Fake party" not in client.get("/tags?month=2026-07").text


def test_only_federal_holidays_and_your_labels_are_shaded(client, db):
    """User's request 2026-11-02: shopping days alone have no grey background."""
    tags.create(db, "Fake Trip")
    client.post("/tags/labels", data={"date": "2026-11-27", "label": "Fake party"}, headers=ORIGIN)
    page = client.get("/tags?month=2026-11").text

    def classes(day):
        return page.split(f'data-cal-day="{day}"')[0].rsplit('class="', 1)[1]

    assert "shaded" in classes("2026-11-26")                                      # Thanksgiving
    assert "shaded" in classes("2026-11-27")                                      # Black Friday + your label
    assert "shaded" not in classes("2026-11-30") and "has-label" in classes("2026-11-30")   # Cyber Monday alone


def test_overview_calendar_card(client, db):
    """User's request 2026-11-02: this month by default, arrows, and the card's pages and label flow."""
    from datetime import date

    from fintrack.web.tags import month_card

    card = month_card(db, today=date(2026, 11, 20))
    assert card["month"] == "2026-11" and card["prev"] == "2026-10" and card["next"] == "2026-12"
    assert month_card(db, "2027-01", today=date(2026, 11, 20))["current"] == "2026-11"
    page = client.get("/").text
    assert 'id="overview-calendar"' in page and 'name="back" value="overview"' in page and 'class="countdown"' in page
    fragment = client.get("/overview/calendar?month=2030-06").text
    assert "<html" not in fragment and "June 2030" in fragment and 'data-cal-day="2030-06-16"' in fragment
    assert "Father&#39;s Day" in fragment and ">Today<" in fragment                # Today link on another month
    # adding a label from the card comes back to the card's month
    done = client.post("/tags/labels", data={"date": "2030-06-02", "label": "Fake party", "month": "2030-06", "back": "overview"},
                       headers=ORIGIN, follow_redirects=False)
    assert done.headers["location"] == "/?cal=2030-06"
    assert "Fake party" in client.get("/?cal=2030-06").text


def test_countdowns_default_to_the_next_two_and_can_be_chosen(client, db):
    """User's requests 2026-11-02: tags, holidays and your labels; the other timer moves out of the way."""
    from datetime import date, timedelta

    from fintrack import calendar_labels
    from fintrack.web.tags import choose_countdown, countdowns

    today = date(2026, 11, 20)

    def shown(on=today):
        return [(t["item"]["name"], t["item"]["days"], t["pinned"]) for t in countdowns(db, on)[0]]

    assert shown() == [("Thanksgiving", 6, False), ("Black Friday", 7, False)]     # no events: holidays fill both
    soon = tags.create(db, "Fake Soon")
    tags.set_dates(db, soon.id, "2026-11-22", "2026-11-23")
    begun = tags.create(db, "Fake Begun")
    tags.set_dates(db, begun.id, "2026-11-01", "2026-11-30")                       # already begun: not coming up
    tags.create(db, "Fake Undated")                                                # no dates: never
    calendar_labels.add(db, "2026-11-20", "Fake birthday", yearly=True)
    assert shown() == [("Fake birthday", 0, False), ("Fake Soon", 2, False)]       # the next two, whatever they are
    choices = countdowns(db, today)[1]
    assert list(choices) == ["Events", "Holidays", "Your dates"]
    assert [c["name"] for c in choices["Events"]] == ["Fake Soon"]
    assert [c["name"] for c in choices["Your dates"]] == ["Fake birthday"]
    names = [c["name"] for c in choices["Holidays"]]
    assert names[:2] == ["Thanksgiving", "Black Friday"] and len(names) == len(set(names))   # each once, its next date

    # choosing updates the timer; the automatic one skips it
    choose_countdown(db, 1, "holiday:Halloween", today)
    assert shown()[0] == ("Halloween", 345, True) and shown()[1] == ("Fake birthday", 0, False)
    choose_countdown(db, 2, "holiday:Halloween", today)                            # both: the first keeps it
    assert shown()[0][0] == "Halloween" and shown()[1][0] == "Fake birthday"
    choose_countdown(db, 2, f"tag:{soon.id}", today)
    assert shown() == [("Halloween", 345, True), ("Fake Soon", 2, True)]
    assert countdowns(db, today)[0][0]["blocked"] == f"tag:{soon.id}"              # grayed out for the other
    choose_countdown(db, 2, "holiday:Not coming up", today)                        # ignored
    assert shown()[1][0] == "Fake Soon"

    # a tag moved later stays chosen
    tags.set_dates(db, soon.id, "2026-12-01", "2026-12-02")
    assert shown()[1] == ("Fake Soon", 11, True)
    # a deleted label goes back to automatic
    label_id = db.execute("SELECT id FROM calendar_labels").fetchone()[0]
    choose_countdown(db, 1, f"label:{label_id}", today)
    assert shown()[0] == ("Fake birthday", 0, True)
    calendar_labels.remove(db, label_id)
    assert shown()[0][2] is False
    tags.set_countdown_choice(db, 1, "")
    tags.set_countdown_choice(db, 2, "")

    # through the page: a card back, in the same month, with the list under each title
    tags.set_dates(db, soon.id, (date.today() + timedelta(days=40)).isoformat(), "")
    done = client.post("/overview/countdown", data={"slot": "2", "choice": f"tag:{soon.id}", "month": "2030-06"}, headers=ORIGIN)
    assert "<html" not in done.text and "June 2030" in done.text
    second = done.text.split('class="countdown"')[2]
    assert "Fake Soon in" in second.split("</summary>")[0] and ">40 days</div>" in second
    assert 'aria-pressed="true"' in second and "Next up" in second and "countdown-group" in second
    client.post("/overview/countdown", data={"slot": "2", "choice": ""}, headers=ORIGIN)
    assert tags.countdown_choice(db, 2) is None
    assert client.post("/overview/countdown", data={"slot": "9", "choice": ""}, headers=ORIGIN).status_code == 200


def test_every_countdown_choice_returns_to_automatic_after_its_date(db):
    """User's rule 2026-11-02: holidays and yearly labels too, not only tags and one-off labels."""
    from datetime import date, timedelta

    from fintrack import calendar_labels
    from fintrack.web.tags import choose_countdown, countdowns

    today = date(2026, 11, 20)
    trip = tags.create(db, "Fake Trip")
    tags.set_dates(db, trip.id, "2026-12-01", "2026-12-03")
    calendar_labels.add(db, "2026-11-23", "Fake birthday", yearly=True)
    calendar_labels.add(db, "2026-11-24", "Fake party")
    birthday, party = [r[0] for r in db.execute("SELECT id FROM calendar_labels ORDER BY id")]

    def timer(on):
        t = countdowns(db, on)[0][0]
        return t["item"]["name"], t["pinned"]

    for key, name, day in (("holiday:Thanksgiving", "Thanksgiving", date(2026, 11, 26)),
                           (f"label:{birthday}", "Fake birthday", date(2026, 11, 23)),
                           (f"label:{party}", "Fake party", date(2026, 11, 24)),
                           (f"tag:{trip.id}", "Fake Trip", date(2026, 12, 1))):
        choose_countdown(db, 1, key, today)
        assert timer(today) == (name, True)
        assert timer(day) == (name, True)                                          # still on the day itself
        after = timer(day + timedelta(days=1))
        assert after[1] is False                                                   # the day after: automatic
        assert timer(day + timedelta(days=300))[1] is False                        # and it doesn't come back next year
        assert timer(date(day.year + 1, day.month, day.day))[1] is False


def test_todays_background_covers_the_days_box_on_both_calendars(client, db):
    """User's request 2026-11-02: one box in today's column, in the week's row, before its bars."""
    from datetime import date

    today = date.today()
    tags.create(db, "Fake Trip")
    column = (today.weekday() + 1) % 7 + 1                                        # 1 = Sunday
    for url in ("/tags?month=" + today.strftime("%Y-%m"), "/overview/calendar"):
        page = client.get(url).text
        assert page.count('class="cal-today-box') == 1 and f'class="cal-today-box cs-{column}"' in page
    assert 'class="cal-today-box' not in client.get("/overview/calendar?month=2099-06").text


def test_the_calendar_is_days_only_with_the_year_in_2_rows_of_6(client, db):
    """User's request 2026-11-02: the timeline view and its gear option are gone."""
    tags.add(db, [add(db, 1000, "2026-07-10")], "Fake Trip")
    for view in ("month", "quarter", "year"):
        page = client.get(f"/tags?view={view}&month=2026-07").text
        assert "timeline" not in page.lower() and 'name="style"' not in page and f'data-cal-views data-view="{view}">' in page
    year = client.get("/tags?view=year&month=2026-07").text
    assert year.count('<div class="cal tiny">') == 12



def test_the_year_toggle_can_be_hidden_on_phones(client, db):
    """User's request 2026-11-02: app.css hides .view-year on phones; ui.js moves a year opened there to 3 months."""
    tags.create(db, "Fake Trip")
    page = client.get("/tags?view=year&month=2026-07").text
    assert 'class="view-year"' in page and 'class="view-quarter"' in page and 'data-view="year"' in page


def test_visible_months_have_the_same_number_of_rows():
    """User's request 2026-11-02: drop a 6-row month's thinner end row unless it holds the range's first or last day or today."""
    from datetime import date

    from fintrack.web.tags import month_rows

    away = date(2030, 1, 15)                                                      # today outside: no effect
    def shape(months, today=away):
        plans = month_rows(months, today)
        return {m: (len(w), w[0][0].isoformat(), w[-1][-1].isoformat()) for m, w in plans.items()}

    year = shape([f"2026-{m:02d}" for m in range(1, 13)])
    assert {m: rows for m, (rows, _, _) in year.items() if rows != 5} == {"2026-02": 4}   # all 5 but February
    assert year["2026-08"][1] == "2026-08-02"                                     # Aug 1 alone on a row: dropped
    assert year["2026-05"][2] == "2026-05-30"                                     # tie-free: May 31 alone at the end
    assert year["2026-02"] == (4, "2026-02-01", "2026-02-28")                     # February needs 4: keeps 4
    # the range's first and last day stay: May 2026 first in range keeps May 1-2, drops May 31 instead
    assert shape(["2026-05", "2026-06", "2026-07"])["2026-05"][1] == "2026-04-26"
    # both ends held (a 6-row month alone): it keeps 6
    assert shape(["2026-08"])["2026-08"][0] == 6
    # today is never dropped: Aug 1 today keeps that row, so August drops the last row and stays 5
    plans = shape([f"2026-{m:02d}" for m in range(7, 10)], today=date(2026, 8, 1))
    assert plans["2026-08"][1] == "2026-07-26" and plans["2026-08"][2] == "2026-08-29"


def test_the_pages_draw_the_even_rows(client, db):
    tags.create(db, "Fake Trip")
    page = client.get("/tags?view=year&month=2026-11").text
    august = page.split("<h3 class=\"cal-month\">Aug</h3>")[1].split('<div class="cal tiny">')[0]
    assert 'data-cal-day="2026-08-01"' not in august and august.count('class="cal-week"') == 5


def test_today_link_returns_each_view_to_now(client, db):
    """User's request 2026-11-02: this month; this month plus the next 2; this year. Shown only when away."""
    from datetime import date

    from fintrack.spending import shift_month

    tags.create(db, "Fake Trip")
    now = f"{date.today():%Y-%m}"

    def today_link(url):
        page = client.get(url).text.split('aria-label="Move the calendar"')[1].split("</nav>")[0]
        return page.split('aria-label="Back to today"')[0].rsplit('href="', 1)[1].split('"')[0] if "Back to today" in page else None

    assert today_link("/tags?month=2001-01") == f"/tags?month={now}"
    assert today_link(f"/tags?month={now}") is None
    ahead = shift_month(now, 2)
    assert today_link("/tags?view=quarter&month=2001-01") == f"/tags?view=quarter&amp;month={ahead}"
    assert today_link(f"/tags?view=quarter&month={ahead}") is None
    page = client.get(f"/tags?view=quarter&month={ahead}").text                     # starts with this month
    assert f"<h2>{date.today():%b}" in page
    assert today_link("/tags?view=year&month=2001-06") == f"/tags?view=year&amp;month={now}"
    assert today_link(f"/tags?view=year&month={now[:4]}-01") is None              # any month of this year


def test_each_day_shows_once_across_the_visible_months(client, db):
    """User's request 2026-11-02: Mar 1-6, 2027 at the end of February is blank, as March shows them."""
    import re

    trip = tags.create(db, "Fake Trip")
    tags.set_dates(db, trip.id, "2027-02-26", "2027-03-03")
    page = client.get("/tags?view=quarter&month=2027-03").text.split("data-cal-views")[1]
    months = page.split('<h3 class="cal-month">')[1:]
    feb, mar = months[1], months[2]
    assert 'data-cal-day="2027-03-01"' not in feb and feb.count('class="cal-day cal-blank"') == 6
    assert 'data-cal-day="2027-03-01"' in mar
    # still where the own month isn't shown: Jan 1-2, 2027's row has Dec 27-31, 2026 grayed (the range's edge)
    assert 'data-cal-day="2026-12-27"' in months[0]
    # the bar stops at February's last shown day, square there, and March draws its own
    feb_bars = re.findall(r'class="cal-bar [^"]*"', feb)
    assert len(feb_bars) == 2 and "cs-6 ce-7 open-end" in feb_bars[0]           # Fri 26 - Sat 27, going on
    assert "cs-1 ce-1 open-start open-end" in feb_bars[1]                         # Sun 28 only: Mar 1-3 are March's
    assert mar.count(">Fake Trip</a>") == 1


def test_hidden_days_lose_their_grid_lines(client, db):
    """User's request 2026-11-02: a cover over each run of hidden days, at the week's start or end."""
    from fintrack.web.tags import Week

    days = [{"hidden": h} for h in (True, True, False, False, False, True, True)]
    assert (Week(days).blank_lead, Week(days).blank_tail) == (2, 2)
    assert (Week([{"hidden": False}] * 7).blank_lead, Week([{"hidden": False}] * 7).blank_tail) == (0, 0)
    tags.create(db, "Fake Trip")
    feb = client.get("/tags?view=quarter&month=2027-03").text.split('<h3 class="cal-month">')[2]
    assert '<span class="cal-blank-cover tail cs-2 ce-7">' in feb                   # Mar 1-6 at February's end


def test_a_bar_brings_its_tag_card_to_the_top(client, db):
    """User's request 2026-11-02: Overview opens the Tags page on its month, focused on the tag; the Tags page scrolls to it."""
    from datetime import date

    trip = tags.create(db, "Fake Trip")
    today = date.today()
    tags.set_dates(db, trip.id, today.isoformat(), today.isoformat())
    month = f"{today:%Y-%m}"
    overview = client.get("/overview/calendar").text
    assert f'href="/tags?month={month}&amp;focus={trip.id}" data-focus-tag="{trip.id}"' in overview
    page = client.get(f"/tags?month={month}&focus={trip.id}").text
    assert f'href="#tag-card-{trip.id}" data-focus-tag="{trip.id}"' in page
    assert f'<section id="tag-card-{trip.id}" class="card tag-card' in page


def test_a_tag_to_come_counts_down_after_its_name(client, db):
    from datetime import date, timedelta
    soon = (date.today() + timedelta(days=51)).isoformat()
    tag, _ = tags.add(db, [add(db, 1000, date.today().isoformat())], "Fake Lisbon")
    tags.set_dates(db, tag.id, soon, soon)
    past, _ = tags.add(db, [add(db, 500, "2026-07-01")], "Fake Past")
    tags.set_dates(db, past.id, "2026-07-01", "2026-07-03")
    page = client.get("/tags").text
    head = page.split('Fake Lisbon</a> <span class="tag-countdown">(7.5w away)</span>')[1].split("</h2>")[0]   # 51 days
    assert 'title="Edit Fake Lisbon"' in head                                    # then the pencil (2026-11-04)
    assert page.count('class="tag-countdown"') == 1


def test_countdown_days_then_weeks_then_months_by_halves():
    from datetime import date, timedelta
    from fintrack.web.tags import countdown
    today = date(2026, 11, 4)
    at = lambda n: countdown((today + timedelta(days=n)).isoformat(), today)   # noqa: E731
    assert [at(n) for n in (0, -3, 1, 21, 28)] == ["", "", "1d away", "21d away", "28d away"]
    assert [at(n) for n in (29, 35, 51, 91)] == ["4w away", "5w away", "7.5w away", "13w away"]   # to 3 months
    assert [at(n) for n in (92, 106, 140, 365)] == ["3m away", "3.5m away", "4.5m away", "12m away"]
    assert countdown(None) == ""


# ---------------------------------------------------------------- category budgets are part of the total (2026-11-04)

def test_category_budgets_fit_in_the_total_or_ask_to_raise_it(db):
    tag, _ = tags.add(db, [add(db, 1000)], "Fake Trip")
    tags.set_budget(db, tag.id, "1000")
    tags.set_category_budget(db, tag.id, "TRAVEL", "600")
    with pytest.raises(tags.BudgetTooSmall) as asked:
        tags.set_category_budget(db, tag.id, "FOOD_AND_DRINK", "500")              # 1,100 > 1,000
    assert (asked.value.needed, asked.value.total) == (110000, 100000)
    assert not db.execute("SELECT 1 FROM tag_category_budgets WHERE category_key = 'FOOD_AND_DRINK'").fetchone()
    tags.set_category_budget(db, tag.id, "FOOD_AND_DRINK", "500", raise_total=True)
    assert db.execute("SELECT budget_cents FROM tags WHERE id = ?", (tag.id,)).fetchone()[0] == 110000
    with pytest.raises(tags.TagError, match="can't be less"):
        tags.set_budget(db, tag.id, "900")
    tags.set_category_budget(db, tag.id, "TRAVEL", "200")                           # lower: fits, nothing asked


def test_the_card_shows_budgets_and_the_other_categories_against_the_rest(client, db):
    tag, _ = tags.add(db, [add(db, 30000, key="TRAVEL"), add(db, 5000, key="FOOD_AND_DRINK"),
                           add(db, 2000, key="ENTERTAINMENT")], "Fake Trip")
    tags.set_budget(db, tag.id, "1000")
    tags.set_category_budget(db, tag.id, "TRAVEL", "250")                           # over: $300 spent
    [s] = [s for s in tags.summaries(db) if s.tag.id == tag.id]
    assert (s.other_spent_cents, s.other_budget_cents) == (7000, 75000)
    assert sorted(line.category for line in s.other_lines) == ["ENTERTAINMENT", "FOOD_AND_DRINK"]
    card = client.get("/tags").text.split(f'id="tag-card-{tag.id}"')[1].split("</section>")[0]
    assert '<th class="num">Budget</th>' in card and 'aria-label="Over budget">✕</span> $250</td>' in card   # ✕ first
    assert 'class="data compact tag-table with-budget"' in card
    assert '<th class="num">Budget</th><th class="num">Amount</th>' in card                      # budget first
    other = card.split("<th>Other")[1].split("</tr>")[0]
    assert other.startswith(' <span class="muted small">2</span></th>')          # its count (2026-11-04)
    assert other.index("$750") < other.index("$70.00")
    asked = client.post(f"/tags/{tag.id}/category-budget", headers=ORIGIN, follow_redirects=False,
                        data={"category": "FOOD_AND_DRINK", "amount": "800"})                # 1,050 > 1,000
    assert "raise_needed=105000" in asked.headers["location"]
    page = client.get(asked.headers["location"]).text
    assert 'class="tag-raise"' in page and "Raise the budget to $1,050.00?" in page
    client.post(f"/tags/{tag.id}/category-budget", headers=ORIGIN, data={"category": "FOOD_AND_DRINK", "amount": "800", "raise": "1"})
    assert db.execute("SELECT budget_cents FROM tags WHERE id = ?", (tag.id,)).fetchone()[0] == 105000


def test_the_pencil_opens_edit_mode_for_name_budgets_and_delete(client, db):
    tag, _ = tags.add(db, [add(db, 30000, key="TRAVEL")], "Fake Trip")
    tags.set_budget(db, tag.id, "1000")
    tags.set_category_budget(db, tag.id, "TRAVEL", "400")
    card = client.get("/tags").text.split(f'id="tag-card-{tag.id}"')[1].split("</section>")[0]
    assert f'&amp;edit={tag.id}" title="Edit Fake Trip"' in card.split("</h2>")[0]     # in the heading, after the name
    assert 'class="icon-edit tag-edit" data-keep-place' in card                 # the page keeps its place (2026-11-04)
    assert 'name="name"' not in card and 'title="Delete Fake Trip"' not in card and 'class="cell-input"' not in card
    assert '<tr class="group tag-total"><th>Total <span class="muted small">1</span></th>' in card and "30% used" in card
    assert card.index("<th>Total") < card.index(">Travel</a>")                 # the total first (2026-11-04)
    assert '>Travel</a> <span class="muted small">1</span></td>' in card and "$1,000" in card   # each row's count
    card = client.get(f"/tags?edit={tag.id}").text.split(f'id="tag-card-{tag.id}"')[1].split("</section>")[0]
    assert f'name="name" form="rename-{tag.id}" value="Fake Trip"' in card and 'title="Delete Fake Trip"' in card
    assert '<h2 class="tag-title-edit">' in card and 'class="tag-when"' not in card     # no count in edit mode
    assert f'form="cb-{tag.id}-1" value="$400.00"' in card and f'form="total-{tag.id}"' in card and ">Done</a>" in card
    assert f'id="add-{tag.id}"' in card and "Add a category budget…" in card
    renamed = client.post(f"/tags/{tag.id}/rename", headers=ORIGIN, follow_redirects=False,
                          data={"name": "Fake Journey", "month": "2026-10", "edit": str(tag.id)})
    assert renamed.headers["location"] == f"/tags?month=2026-10&edit={tag.id}"
    assert tags.tag_by_name(db, "Fake Journey") is not None


def test_a_search_box_filters_the_cards(client, db):
    tags.add(db, [add(db, 1000)], "Fake Trip")
    page = client.get("/tags").text
    ribbon = page.split('<header class="tabbar">')[1].split("</header>")[0]
    assert ribbon.index('data-tag-search') < ribbon.index('aria-label="Calendar view"')    # left of the view toggle
    assert 'data-tag-text="fake trip"' in page and "data-tag-none hidden" in page
    trip, _ = tags.add(db, [add(db, 500)], "Fake Getaway")
    tags.set_dates(db, trip.id, "2026-08-28", "2026-09-03")                    # its range: Start and End (2026-11-04)
    assert 'data-tag-text="fake getaway aug 28 august 2026 sep 3 september 2026"' in client.get("/tags").text
