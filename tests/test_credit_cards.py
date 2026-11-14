"""Analysis → Credit cards (user's request 2026-10-25): each card's spending by category over a date range (default
YTD). All data here is made up."""

import html
import re
from datetime import date

import pytest

from fintrack import items, spending, transfers, visuals

CHECKING, CARD_A, CARD_B, HIDDEN_CARD = 1, 2, 3, 4


@pytest.fixture
def db(conn):
    """One Item with checking, two fake cards and a hidden card."""
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    for acc_id, name, account_type, mask, hidden in [
        (CHECKING, "Fake Checking", "depository", "0001", 0), (CARD_A, "Fake Card A", "credit", "1111", 0),
        (CARD_B, "Fake Card B", "credit", "2222", 0), (HIDDEN_CARD, "Fake Old Card", "credit", "3333", 1),
    ]:
        conn.execute(
            "INSERT INTO accounts (id, item_id, plaid_account_id, name, mask, type, classification, is_hidden)"
            " VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
            (acc_id, f"acc-{acc_id}", name, mask, account_type, items.classify(account_type), hidden),
        )
    conn.commit()
    return conn


_ids = iter(range(1, 10_000))


def _headings(page: str) -> str:
    """The page with each rate group's expander button unwrapped, so a heading reads swatch, rate, definitions."""
    return re.sub(r'<button type="button" class="link-button expander"[^>]*>', "", page).replace("</button></th>", "</th>")


def add(conn, account, amount, day, key, merchant=None):
    tid = next(_ids)
    conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, merchant_name,"
        " plaid_category_primary, plaid_category_key) VALUES (?, ?, ?, ?, 'Fake merchant', ?, ?, ?)",
        (f"t{tid}", account, day, amount, merchant, key, key),
    )
    conn.commit()


@pytest.fixture
def spent(db):
    add(db, CARD_A, 10000, "2026-03-01", "FOOD_AND_DRINK")
    add(db, CARD_A, 2500, "2026-03-02", "FOOD_AND_DRINK")
    add(db, CARD_A, -1500, "2026-03-03", "FOOD_AND_DRINK")          # a refund reduces it
    add(db, CARD_A, 4000, "2026-03-04", "GENERAL_MERCHANDISE")
    add(db, CARD_A, -30000, "2026-03-05", "CREDIT_CARD_PAYMENT")    # paying the card isn't spending
    add(db, CHECKING, 30000, "2026-03-05", "CREDIT_CARD_PAYMENT")
    add(db, CARD_B, 8000, "2026-03-06", "GENERAL_MERCHANDISE")
    add(db, CARD_B, 9000, "2025-12-20", "FOOD_AND_DRINK")           # before the range below
    add(db, CHECKING, 5000, "2026-03-07", "FOOD_AND_DRINK")         # not a card
    add(db, HIDDEN_CARD, 5000, "2026-03-07", "FOOD_AND_DRINK")      # hidden card
    transfers.detect_transfers(db)
    return db


def test_card_spending_by_category(spent):
    cards = spending.card_spending(spent, "2026-01-01", "2026-03-31")
    assert [c.name for c in cards] == ["Fake Card A", "Fake Card B"]                  # visible cards, by name
    a, b = cards
    assert a.mask == "1111" and a.spending_cents == 15000
    assert [(l.category, l.amount_cents, l.count) for l in a.lines] == [("FOOD_AND_DRINK", 11000, 3),
                                                                        ("GENERAL_MERCHANDISE", 4000, 1)]
    assert b.spending_cents == 8000 and [l.category for l in b.lines] == ["GENERAL_MERCHANDISE"]


def test_a_card_with_no_spending_is_still_listed(spent):
    cards = spending.card_spending(spent, "2026-06-01", "2026-06-30")
    assert [(c.name, c.spending_cents, c.lines) for c in cards] == [("Fake Card A", 0, []), ("Fake Card B", 0, [])]


def test_each_card_is_coloured_by_its_own_spending(spent):
    """Like Spending's treemap, per card: colours by size within the card (one colour per category across cards was
    tried first and left up to a third of a card gray)."""
    cards = spending.card_spending(spent, "2026-01-01", "2026-03-31")
    charts = visuals.card_category_charts(cards)
    assert charts[CARD_A]["slots"] == {"FOOD_AND_DRINK": "slot-1", "GENERAL_MERCHANDISE": "slot-2"}
    assert charts[CARD_B]["slots"] == {"GENERAL_MERCHANDISE": "slot-1"}
    assert [s["key"] for s in charts[CARD_A]["slices"]] == ["FOOD_AND_DRINK", "GENERAL_MERCHANDISE"]


@pytest.fixture
def client(settings, spent, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_credit_cards_page(client):
    page = client.get("/credit-cards").text
    tabs = page[page.index('aria-label="Pages"'):page.index("</nav>", page.index('aria-label="Pages"'))]
    # the current tab (a page with ribbon controls: its tab also opens them on phones, so more attributes + a chevron)
    assert re.search(r'aria-current="page" class="active"[^>]*>Credit cards<', tabs)
    assert tabs.index(">Savings<") < tabs.index(">Cash flow<") < tabs.index(">Credit cards<")
    assert 'id="tx-range"' in page and f'name="from" form="txn-filters" value="{date.today().year}-01-01"' in page  # YTD
    custom = client.get("/credit-cards?range=custom&from=2026-01-01&to=2026-03-31").text
    assert "Fake Card A" in custom and "····1111" in custom and "Fake Old Card" not in custom
    assert f'id="cc-{CARD_A}" data-chart="colorbars"' in custom
    assert (f"/transactions?range=custom&amp;from=2026-01-01&amp;to=2026-03-31&amp;account={CARD_A}"
            "&amp;category=FOOD_AND_DRINK") in custom


def test_cards_are_listed_largest_spending_first(client):
    def order(url):
        page = client.get(url).text
        return sorted(["A", "B"], key=lambda c: page.index(f">Fake Card {c}</a>"))
    assert order("/credit-cards?range=custom&from=2026-01-01&to=2026-03-31") == ["A", "B"]    # $150 vs $80
    assert order("/credit-cards?range=custom&from=2025-12-01&to=2025-12-31") == ["B", "A"]    # $0 vs $90


def test_savings_and_cash_flow_are_in_analysis(client):
    for path in ("/savings", "/cash-flow"):
        page = client.get(path).text
        rail = page[:page.index('aria-label="Pages"')]
        assert 'href="/savings"' in rail and "Analysis" in rail                       # the section's first tab
        tabs = page[page.index('aria-label="Pages"'):page.index("</nav>", page.index('aria-label="Pages"'))]
        assert ">Spending</a>" not in tabs and ">Credit cards</a>" in tabs


# ---------------------------------------------------------------- reward estimates (rewards.py, migration 0039)

from fintrack import categories, rewards  # noqa: E402

ORIGIN = {"origin": "https://localhost:8443"}


@pytest.mark.parametrize("text, x100", [("1", 100), ("1.5", 150), ("3x", 300), ("2%", 200), (" 1.25 ", 125),
                                        ("0", 0), ("1.005", 101)])
def test_parse_rate(text, x100):
    assert rewards.parse_rate(text) == x100


@pytest.mark.parametrize("text", ["", "abc", "-1", "101"])
def test_parse_rate_rejects(text):
    with pytest.raises(ValueError):
        rewards.parse_rate(text)


def test_rate_label():
    assert rewards.rate_label("points", 150) == "1.5x" and rewards.rate_label("cash", 200) == "2%"
    assert rewards.rate_label("miles", 125) == "1.25x" and rewards.rate_label("points", 0) == "0x"
    assert rewards.rate_label("points", 1000) == "10x" and rewards.rate_label("cash", 2000) == "20%"   # zeros kept


def test_rate_for_falls_back_to_parent_then_base(spent):
    sub = categories.add_subcategory(spent, "FOOD_AND_DRINK", "Fake coffee")
    card = rewards.CardRewards(CARD_A, "points", 100, [rewards.RewardRate(1, "FOOD_AND_DRINK", None, 300)])
    parents = {r["key"]: r["parent_key"] for r in spent.execute("SELECT key, parent_key FROM categories")}
    assert rewards.rate_for(card, sub, parents) == 300                       # its parent's rate
    card.rates.append(rewards.RewardRate(2, sub, None, 500))
    assert rewards.rate_for(card, sub, parents) == 500                       # its own wins
    assert rewards.rate_for(card, "GENERAL_MERCHANDISE", parents) == 100     # the base rate


def test_estimates_points_and_cash(spent):
    rewards.set_up(spent, CARD_A, "points", 100)
    rewards.set_rate(spent, CARD_A, "FOOD_AND_DRINK", None, 300)
    rewards.set_up(spent, CARD_B, "cash", 150)
    cards = spending.card_spending(spent, "2026-01-01", "2026-03-31")
    est = rewards.estimates(spent, cards, "2026-01-01", "2026-03-31")
    # card A: food $110 net of the $15 refund x 3 = 330 points; merchandise $40 x 1 = 40
    assert est[CARD_A].by_category == {"FOOD_AND_DRINK": 330, "GENERAL_MERCHANDISE": 40}
    assert est[CARD_A].total == 370 and est[CARD_A].unit == "points"
    # card B: $80 at 1.5% = $1.20 = 120 cents of cash back
    assert est[CARD_B].total == 120 and est[CARD_B].unit == "cash"


def test_estimate_rounds_half_up_per_category(db):
    add(db, CARD_A, 1050, "2026-03-01", "FOOD_AND_DRINK")          # $10.50 x 1.5 = 15.75 -> 16
    add(db, CARD_A, 1030, "2026-03-01", "GENERAL_MERCHANDISE")     # $10.30 x 1.5 = 15.45 -> 15
    rewards.set_up(db, CARD_A, "points", 150)
    est = rewards.estimates(db, spending.card_spending(db, "2026-01-01", "2026-03-31"), "2026-01-01", "2026-03-31")[CARD_A]
    assert est.by_category == {"FOOD_AND_DRINK": 16, "GENERAL_MERCHANDISE": 15} and est.total == 31


def test_estimate_groups_categories_by_rate(spent):
    sub = categories.add_subcategory(spent, "FOOD_AND_DRINK", "Fake coffee")
    add(spent, CARD_A, 2000, "2026-03-08", sub)                              # earns its parent's 3x
    add(spent, CARD_A, 500, "2026-03-08", "ENTERTAINMENT")
    rewards.set_up(spent, CARD_A, "points", 100)
    rewards.set_rate(spent, CARD_A, "FOOD_AND_DRINK", None, 300)
    rewards.set_rate(spent, CARD_A, "ENTERTAINMENT", None, 100)                   # same as the base: one 1x group
    est = rewards.estimates(spent, spending.card_spending(spent, "2026-01-01", "2026-03-31"), "2026-01-01", "2026-03-31")[CARD_A]
    assert [g.rate_x100 for g in est.groups] == [300, 100]                   # highest first
    food, rest = est.groups
    assert [l.category for l in food.lines] == ["FOOD_AND_DRINK", sub]       # largest first
    assert [l.earned for l in food.lines] == [330, 60]
    assert (food.spending_cents, food.earned) == (13000, 390)
    assert [l.category for l in rest.lines] == ["GENERAL_MERCHANDISE", "ENTERTAINMENT"]
    assert (rest.spending_cents, rest.earned) == (4500, 45)
    assert sum(g.earned for g in est.groups) == est.total


def test_rewards_only_for_cards_and_after_set_up(spent):
    with pytest.raises(ValueError):
        rewards.set_up(spent, CHECKING, "points", 100)
    with pytest.raises(ValueError):
        rewards.set_up(spent, CARD_A, "stars", 100)
    with pytest.raises(ValueError):
        rewards.set_rate(spent, CARD_A, "FOOD_AND_DRINK", None, 300)              # not set up yet
    rewards.set_up(spent, CARD_A, "points", 100)
    with pytest.raises(ValueError):
        rewards.set_rate(spent, CARD_A, "NO_SUCH_CATEGORY", None, 300)
    assert rewards.estimates(spent, spending.card_spending(spent, "2026-01-01", "2026-03-31"), "2026-01-01", "2026-03-31").keys() == {CARD_A}


def test_reward_rates_on_the_page(client, spent):
    keep = {"range": "custom", "from": "2026-01-01", "to": "2026-03-31"}
    page = _headings(client.get("/credit-cards?range=custom&from=2026-01-01&to=2026-03-31").text)
    assert "Estimate rewards" in page and " Earned</span>" not in page
    r = client.post(f"/credit-cards/{CARD_A}/rewards", data={"unit": "points", "base_rate": "1", **keep},
                    headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and "range=custom" in r.headers["location"] and "from=2026-01-01" in r.headers["location"]
    client.post(f"/credit-cards/{CARD_A}/rates", data={"category": "FOOD_AND_DRINK", "rate": "3x", **keep}, headers=ORIGIN)
    page = _headings(client.get("/credit-cards?range=custom&from=2026-01-01&to=2026-03-31").text)
    assert '>370 <span class="measure-pct">(2.5%)</span>' in page and "Points Earned</span>" in page and ">3x<" in page and "<th class=\"num\">Points</th>" in page
    assert 'aria-hidden="true"></span>3x <span class="rate-for">(' in page and 'aria-hidden="true"></span>1x <span class="rate-for">(' in page    # grouped by rate
    bad = client.post(f"/credit-cards/{CARD_A}/rates", data={"category": "FOOD_AND_DRINK", "rate": "lots", **keep},
                      headers=ORIGIN, follow_redirects=False)
    assert "error=" in bad.headers["location"]
    rate_id = spent.execute("SELECT id FROM card_reward_rates WHERE account_id = ?", (CARD_A,)).fetchone()["id"]
    client.post(f"/credit-cards/{CARD_A}/rates/delete", data={"rate_id": rate_id, **keep}, headers=ORIGIN)
    assert '>150 <span class="measure-pct">(1.0%)</span>' in client.get("/credit-cards?range=custom&from=2026-01-01&to=2026-03-31").text
    client.post(f"/credit-cards/{CARD_A}/rewards/delete", data=keep, headers=ORIGIN)
    assert " Earned</span>" not in client.get("/credit-cards?range=custom&from=2026-01-01&to=2026-03-31").text


# ---------------------------------------------------------------- rates by merchant text (migrations 0040, 0041)

@pytest.fixture
def airfare(db):
    """Card A: airfare at a fake airline, airfare elsewhere, and the fake airline's other spending."""
    sub = categories.add_subcategory(db, "TRAVEL", "Fake airfare")
    add(db, CARD_A, 10000, "2026-03-01", sub, merchant="Fake Air Lines")      # airfare at Fake Air
    add(db, CARD_A, 20000, "2026-03-02", sub, merchant="Other Fake Jet")      # other airfare
    add(db, CARD_A, 1000, "2026-03-03", "FOOD_AND_DRINK", merchant="FAKE AIR lounge")   # Fake Air, not airfare
    add(db, CARD_A, 3000, "2026-03-04", "FOOD_AND_DRINK", merchant="Fake Diner")
    rewards.set_up(db, CARD_A, "points", 100)
    return db, sub


def _estimate(db):
    return rewards.estimates(db, spending.card_spending(db, "2026-01-01", "2026-03-31"), "2026-01-01", "2026-03-31")[CARD_A]


def test_category_at_a_merchant_beats_the_merchant_and_the_category(airfare):
    db, sub = airfare
    rewards.set_rate(db, CARD_A, sub, None, 200)                          # airfare: 2x
    rewards.set_rate(db, CARD_A, None, "fake air", 300)                   # any description containing it: 3x
    rewards.set_rate(db, CARD_A, sub, "Fake Air", 500)                    # airfare at Fake Air: 5x
    est = _estimate(db)
    rows = {(g.rate_x100, l.category, l.merchant): l.earned for g in est.groups for l in g.lines}
    assert rows == {
        (500, sub, "Fake Air"): 500,               # $100 airfare at Fake Air x 5
        (300, "FOOD_AND_DRINK", "fake air"): 30,   # $10 "FAKE AIR lounge" x 3: case doesn't matter
        (200, sub, None): 400,                     # $200 other airfare x 2
        (100, "FOOD_AND_DRINK", None): 30,         # $30 elsewhere at the base 1x
    }
    assert [g.rate_x100 for g in est.groups] == [500, 300, 200, 100] and est.total == 960


def test_merchant_text_matches_the_description_you_see(airfare):
    db, sub = airfare
    db.execute("UPDATE transactions SET user_description = 'Fake Air Lines to Denver' WHERE merchant_name = 'Other Fake Jet'")
    db.commit()
    rewards.set_rate(db, CARD_A, sub, "Fake Air", 500)
    rows = {(g.rate_x100, l.merchant): l.amount_cents for g in _estimate(db).groups for l in g.lines}
    assert rows[(500, "Fake Air")] == 30000                               # your description counts, not Plaid's


def test_the_longest_matching_text_wins_and_parents_count(airfare):
    db, sub = airfare
    rewards.set_rate(db, CARD_A, None, "Fake", 200)
    rewards.set_rate(db, CARD_A, None, "Fake Air", 300)
    rewards.set_rate(db, CARD_A, "TRAVEL", "Fake Air", 400)               # Travel at Fake Air covers airfare there
    card = rewards.all_rewards(db)[CARD_A]
    parents = {r["key"]: r["parent_key"] for r in db.execute("SELECT key, parent_key FROM categories")}
    assert rewards.rate_for(card, sub, parents, "Fake Air Lines") == 400
    assert rewards.rate_for(card, "FOOD_AND_DRINK", parents, "FAKE AIR lounge") == 300   # longer beats "Fake"
    assert rewards.rate_for(card, "FOOD_AND_DRINK", parents, "Fake Diner") == 200
    assert rewards.rate_for(card, "FOOD_AND_DRINK", parents, "Real Diner") == 100


def test_merchant_rates_are_checked_and_replaced(airfare):
    db, sub = airfare
    with pytest.raises(ValueError):
        rewards.set_rate(db, CARD_A, None, "   ", 300)                     # neither
    with pytest.raises(ValueError):
        rewards.set_rate(db, CARD_A, None, "x" * 101, 300)
    rewards.set_rate(db, CARD_A, sub, " Fake  Air ", 300)                 # spaces tidied
    rewards.set_rate(db, CARD_A, sub, "FAKE AIR", 500)                    # the same pair in another case: replaced
    rewards.set_rate(db, CARD_A, sub, None, 200)                          # a different pair: added
    rates = rewards.all_rewards(db)[CARD_A].rates
    assert [(r.category, r.merchant, r.rate_x100) for r in rates] == [(sub, "FAKE AIR", 500), (sub, None, 200)]
    rewards.delete_rate(db, CARD_B, rates[0].id)                          # another card's id: nothing happens
    rewards.delete_rate(db, CARD_A, rates[0].id)
    assert [r.rate_x100 for r in rewards.all_rewards(db)[CARD_A].rates] == [200]


def test_merchant_rates_on_the_page(client, spent):
    keep = {"range": "custom", "from": "2026-01-01", "to": "2026-03-31"}
    url = "/credit-cards?range=custom&from=2026-01-01&to=2026-03-31"
    add(spent, CARD_A, 5000, "2026-03-08", "TRAVEL", merchant="Fake Air Lines")
    client.post(f"/credit-cards/{CARD_A}/rewards", data={"unit": "points", "base_rate": "1", **keep}, headers=ORIGIN)
    page = _headings(client.get(url).text)
    assert 'name="merchant" class="merchant-input"' in page and "Any category" in page
    client.post(f"/credit-cards/{CARD_A}/rates", data={"category": "TRAVEL", "merchant": "Fake Air", "rate": "4",
                                                       **keep}, headers=ORIGIN)
    page = _headings(client.get(url).text)
    assert 'aria-hidden="true"></span>4x <span class="rate-for">(Travel · Fake Air)</span></th>' in page and "Travel · Fake Air</a>" in page and "&amp;only=1&amp;kind=expense&amp;q=%22Fake%20Air%22" in page
    assert '>350 <span class="measure-pct">' in page                       # $50 x 4 + $150 x 1


def _schema_before(tmp_path, version):
    """A fake database with the migrations before `version` applied."""
    from fintrack import db as fdb

    conn = fdb.connect(tmp_path / "fake.db")
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)")
    for v, path in fdb.pending_migrations(conn):
        if v < version:
            conn.executescript(f"BEGIN;\n{path.read_text()}\nINSERT INTO schema_migrations VALUES ({v});\nCOMMIT;")
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, mask, type, classification)"
                 " VALUES (2, 1, 'acc-2', 'Fake Card A', '1111', 'credit', 'liability')")
    conn.execute("INSERT INTO card_rewards (account_id, unit, base_rate_x100) VALUES (2, 'points', 100)")
    return conn


def test_migrations_keep_category_rates(tmp_path):
    from fintrack import db as fdb

    conn = _schema_before(tmp_path, 40)
    conn.execute("INSERT INTO card_reward_rates (account_id, category_key, rate_x100) VALUES (2, 'FOOD_AND_DRINK', 300)")
    conn.commit()
    assert fdb.migrate(conn)[:2] == ["0040_card_reward_merchants.sql", "0041_card_reward_merchant_text.sql"]
    rates = rewards.all_rewards(conn)[2].rates
    assert [(r.category, r.merchant, r.rate_x100) for r in rates] == [("FOOD_AND_DRINK", None, 300)]
    revision = conn.execute("SELECT n FROM app_revision").fetchone()[0]
    rewards.set_rate(conn, 2, "TRAVEL", "Fake Air", 200)    # the rebuilt table still bumps the page revision
    assert conn.execute("SELECT n FROM app_revision").fetchone()[0] > revision
    conn.close()


def test_migration_0041_turns_a_picked_merchant_into_its_name(tmp_path):
    from fintrack import db as fdb

    conn = _schema_before(tmp_path, 41)
    conn.execute("INSERT INTO card_reward_rates (account_id, category_key, merchant_key, merchant_name, rate_x100)"
                 " VALUES (2, 'TRAVEL', 'k:fake air', 'Fake Air', 500)")
    conn.commit()
    assert fdb.migrate(conn)[0] == "0041_card_reward_merchant_text.sql"
    assert [(r.category, r.merchant, r.rate_x100) for r in rewards.all_rewards(conn)[2].rates] == [("TRAVEL", "Fake Air", 500)]
    conn.close()


# ---------------------------------------------------------------- charts by reward rate (2026-10-26)

def test_a_cards_chart_has_a_bar_per_rate(airfare):
    db, sub = airfare
    add(db, CARD_A, 100, "2026-03-09", "ENTERTAINMENT")                   # $1: small, still its own colour
    rewards.set_rate(db, CARD_A, sub, None, 200)
    rewards.set_rate(db, CARD_A, "ENTERTAINMENT", None, 400)
    rewards.set_rate(db, CARD_A, None, "Nowhere Fake", 500)               # no spending: a $0 bar
    label = {r["key"]: r["label"] for r in db.execute("SELECT key, label FROM categories")}
    chart = visuals.card_rate_charts({CARD_A: _estimate(db)}, rewards.rate_label)[CARD_A]
    assert [(p["key"], p["label"], p["value"], p["detail"]) for p in chart["slices"]] == [   # lowest rate first
        ("rate-100", "1x", 40.0, "Everything else"), ("rate-200", "2x", 300.0, "Fake airfare"),
        ("rate-400", "4x", 1.0, label["ENTERTAINMENT"]), ("rate-500", "5x", 0.0, "Any category · Nowhere Fake")]
    assert chart["slots"] == {"rate-500": "slot-1", "rate-400": "slot-2", "rate-200": "slot-3", "rate-100": "slot-4"}


def test_a_rate_has_one_colour_on_every_card(spent):
    rewards.set_up(spent, CARD_A, "points", 100)
    rewards.set_rate(spent, CARD_A, "FOOD_AND_DRINK", None, 300)
    rewards.set_up(spent, CARD_B, "cash", 150)                            # 1.5% only on card B
    rewards.set_rate(spent, CARD_B, "FOOD_AND_DRINK", None, 300)           # 3% = card A's 3x: the same colour
    est = rewards.estimates(spent, spending.card_spending(spent, "2026-01-01", "2026-03-31"), "2026-01-01", "2026-03-31")
    charts = visuals.card_rate_charts(est, rewards.rate_label)
    assert charts[CARD_A]["slots"] == charts[CARD_B]["slots"] == {"rate-300": "slot-1", "rate-150": "slot-2",
                                                                  "rate-100": "slot-3"}
    # the same bars on every card, each named in its card's unit; a rate the card lacks is $0
    assert [(p["label"], p["value"]) for p in charts[CARD_A]["slices"]] == [("1x", 40.0), ("1.5x", 0.0), ("3x", 110.0)]
    assert [(p["label"], p["value"]) for p in charts[CARD_B]["slices"]] == [("1%", 0.0), ("1.5%", 80.0), ("3%", 0.0)]


def test_rates_past_the_palette_go_gray(spent):
    rewards.set_up(spent, CARD_A, "points", 100)
    keys = [r["key"] for r in spent.execute("SELECT key FROM categories WHERE parent_key IS NULL AND kind = 'expense' LIMIT 9")]
    for i, key in enumerate(keys):
        rewards.set_rate(spent, CARD_A, key, None, 200 + i * 100)          # 9 rates + the base = 10
    est = rewards.estimates(spent, spending.card_spending(spent, "2026-01-01", "2026-03-31"), "2026-01-01", "2026-03-31")
    slots = list(visuals.card_rate_charts(est, rewards.rate_label)[CARD_A]["slots"].values())
    assert slots == ["slot-other", "slot-other"] + [f"slot-{i}" for i in range(8, 0, -1)]   # lowest rate first


def test_the_page_charts_rates_for_cards_with_rates(client, spent):
    keep = {"range": "custom", "from": "2026-01-01", "to": "2026-03-31"}
    url = "/credit-cards?range=custom&from=2026-01-01&to=2026-03-31"
    client.post(f"/credit-cards/{CARD_A}/rewards", data={"unit": "points", "base_rate": "1", **keep}, headers=ORIGIN)
    client.post(f"/credit-cards/{CARD_A}/rates", data={"category": "FOOD_AND_DRINK", "rate": "3", **keep}, headers=ORIGIN)
    page = _headings(client.get(url).text)
    assert '"key": "rate-300"' in page and '"key": "rate-100"' in page            # card A: tiles per rate
    assert '<span class="swatch slot-1" aria-hidden="true"></span>3x <span class="rate-for">(' in page
    assert '"key": "GENERAL_MERCHANDISE"' in page                                 # card B: still by category
    assert "Fake Card A spending by reward rate" in page and "Fake Card B spending by category" in page


# ---------------------------------------------------------------- rates with no transactions (2026-10-26)

def test_rates_with_no_transactions_are_listed(spent):
    rewards.set_up(spent, CARD_A, "points", 100)
    rewards.set_rate(spent, CARD_A, "FOOD_AND_DRINK", None, 300)
    rewards.set_rate(spent, CARD_A, None, "Nowhere Fake", 500)
    rewards.set_up(spent, CARD_B, "points", 200)
    day = "2026-03-04"                                                     # card A: only $40 of merchandise
    est = rewards.estimates(spent, spending.card_spending(spent, day, day), day, day)
    label = {r["key"]: r["label"] for r in spent.execute("SELECT key, label FROM categories")}
    assert [(g.rate_x100, g.spending_cents, g.definitions, [l.label for l in g.lines]) for g in est[CARD_A].groups] == [
        (500, 0, ["Any category · Nowhere Fake"], []),                    # rates nothing matched: a heading, no lines
        (300, 0, [label["FOOD_AND_DRINK"]], []),
        (100, 4000, ["Everything else"], [label["GENERAL_MERCHANDISE"]]),
    ]
    assert est[CARD_A].total == 40
    # card B spent nothing that day: its base rate is still listed, empty
    assert [(g.rate_x100, g.spending_cents, g.definitions, g.lines) for g in est[CARD_B].groups] == [
        (200, 0, ["Everything else"], [])]


def test_a_rate_equal_to_the_base_shares_its_heading(spent):
    rewards.set_up(spent, CARD_A, "points", 100)
    rewards.set_rate(spent, CARD_A, "GENERAL_MERCHANDISE", "Fake Shop", 100)
    est = rewards.estimates(spent, spending.card_spending(spent, "2026-01-01", "2026-03-31"), "2026-01-01", "2026-03-31")
    label = spent.execute("SELECT label FROM categories WHERE key = 'GENERAL_MERCHANDISE'").fetchone()["label"]
    assert [g.definitions for g in est[CARD_A].groups] == [[f"{label} · Fake Shop", "everything else"]]


def test_the_page_lists_rates_with_no_spending(client, spent):
    keep = {"range": "custom", "from": "2026-03-04", "to": "2026-03-04"}
    client.post(f"/credit-cards/{CARD_B}/rewards", data={"unit": "points", "base_rate": "2", **keep}, headers=ORIGIN)
    client.post(f"/credit-cards/{CARD_B}/rates", data={"category": "FOOD_AND_DRINK", "rate": "3", **keep}, headers=ORIGIN)
    page = _headings(client.get("/credit-cards?range=custom&from=2026-03-04&to=2026-03-04").text)
    card_b = page[page.index("Fake Card B"):]
    assert "No spending in this range." in card_b                          # no chart for card B...
    assert 'data-chart="colorbars"' not in card_b
    assert 'class="swatch slot-1" aria-hidden="true"></span>3x <span class="rate-for">(' in card_b    # ...but its rates are listed
    assert 'class="swatch slot-2" aria-hidden="true"></span>2x <span class="rate-for">(Everything else)</span></th>' in card_b
    assert "&amp;account=3&amp;category=FOOD_AND_DRINK" not in card_b      # an empty rate isn't a link


# ---------------------------------------------------------------- card fees, exact links, bonuses (2026-10-26)

def test_card_fees_earn_nothing_whatever_the_rates(spent):
    add(spent, CARD_A, 9500, "2026-03-10", "BANK_FEES", merchant="FAKE BANK ANNUAL FEE")
    rewards.set_up(spent, CARD_A, "points", 200)
    rewards.set_rate(spent, CARD_A, None, "Fake Bank", 500)                # would match the fee's description
    cards = spending.card_spending(spent, "2026-01-01", "2026-03-31")
    est = rewards.estimates(spent, cards, "2026-01-01", "2026-03-31")[CARD_A]
    fee_label = spent.execute("SELECT label FROM categories WHERE key = 'BANK_FEES'").fetchone()["label"]
    zero = next(g for g in est.groups if g.rate_x100 == 0)
    assert (zero.spending_cents, zero.earned, zero.definitions) == (9500, 0, [fee_label])
    assert est.by_category["BANK_FEES"] == 0
    assert next(c for c in cards if c.account_id == CARD_A).spending_cents == 24500   # still spending
    assert visuals.card_rate_charts({CARD_A: est}, rewards.rate_label)[CARD_A]["slots"]["rate-0"] == "slot-other"


def test_a_card_without_fees_has_no_0x_group(spent):
    rewards.set_up(spent, CARD_A, "points", 100)
    est = rewards.estimates(spent, spending.card_spending(spent, "2026-01-01", "2026-03-31"), "2026-01-01", "2026-03-31")
    assert [g.rate_x100 for g in est[CARD_A].groups] == [100]


def test_parse_search_terms_phrases_and_exclusions():
    from fintrack.web.transaction_filters import parse_search_terms

    assert parse_search_terms('"Fake Air" -acme -"fake rail" >100 lounge') == (
        ["lounge"], [(">", 10000)], ["acme", "fake rail"], ["Fake Air"])


def test_each_row_links_to_exactly_its_transactions(client, spent):
    from urllib.parse import parse_qs, unquote, urlparse

    sub = categories.add_subcategory(spent, "TRAVEL", "Fake airfare")
    add(spent, CARD_A, 10000, "2026-03-01", sub, merchant="Fake Air Lines")
    add(spent, CARD_A, 20000, "2026-03-02", sub, merchant="Other Fake Jet")
    add(spent, CARD_A, 1000, "2026-03-03", "TRAVEL", merchant="Fake Air lounge")
    add(spent, CARD_A, 3000, "2026-03-04", "TRAVEL", merchant="Fake Train")
    keep = {"range": "custom", "from": "2026-01-01", "to": "2026-03-31"}
    client.post(f"/credit-cards/{CARD_A}/rewards", data={"unit": "points", "base_rate": "1", **keep}, headers=ORIGIN)
    client.post(f"/credit-cards/{CARD_A}/rates", data={"category": "TRAVEL", "merchant": "Fake Air", "rate": "5", **keep},
                headers=ORIGIN)
    client.post(f"/credit-cards/{CARD_A}/rates", data={"category": "", "merchant": "Train", "rate": "2", **keep},
                headers=ORIGIN)                                         # one word: still searched as the description
    page = client.get("/credit-cards?range=custom&from=2026-01-01&to=2026-03-31").text
    table = page[page.index("card-table"):page.index("</table>")]
    links = re.findall(r'<a href="(/transactions\?[^"]+)">[^<]*</a>\s*<span class="muted small">(\d+)</span>', table)
    assert len(links) == 6                     # airfare and Travel each at Fake Air and not, food, merchandise
    for href, count in links:
        url = html.unescape(href)
        listed = client.get(url).text.count('class="txn-row"')
        assert listed == int(count), (unquote(urlparse(url).query), listed, count)
        assert parse_qs(urlparse(url).query)["only"] == ["1"]


def test_parse_bonus():
    assert rewards.parse_bonus("points", "5,000") == 5000 and rewards.parse_bonus("cash", "$25") == 2500
    assert rewards.parse_bonus("cash", "12.345") == 1235                  # half-up
    for unit, text in [("points", "1.5"), ("points", "0"), ("points", "lots"), ("cash", "-5")]:
        with pytest.raises(ValueError):
            rewards.parse_bonus(unit, text)


def _txn_id(db, account, cents):
    return db.execute("SELECT id FROM transactions WHERE account_id = ? AND amount_cents = ?", (account, cents)).fetchone()["id"]


def test_bonuses_add_to_the_estimate_in_their_range(spent):
    with pytest.raises(ValueError):
        rewards.set_bonus(spent, _txn_id(spent, CARD_A, 4000), 1000)      # card not set up
    rewards.set_up(spent, CARD_A, "points", 100)
    rewards.set_bonus(spent, _txn_id(spent, CARD_A, 4000), 1000)          # Mar 4
    rewards.set_bonus(spent, _txn_id(spent, CARD_A, 4000), 2000)          # replaced
    cards = spending.card_spending(spent, "2026-01-01", "2026-03-31")
    est = rewards.estimates(spent, cards, "2026-01-01", "2026-03-31")[CARD_A]
    assert [(b.amount, b.date) for b in est.bonuses] == [(2000, "2026-03-04")]
    assert (est.bonus_total, est.total) == (2000, 150 + 2000)
    later = rewards.estimates(spent, spending.card_spending(spent, "2026-03-05", "2026-03-31"), "2026-03-05", "2026-03-31")
    assert later[CARD_A].bonuses == [] and later[CARD_A].total == 0
    rewards.delete_bonus(spent, _txn_id(spent, CARD_A, 4000))
    assert rewards.estimates(spent, cards, "2026-01-01", "2026-03-31")[CARD_A].total == 150


def test_reward_bonus_in_the_edit_panel(client, spent):
    tid = _txn_id(spent, CARD_A, 4000)
    assert "Reward bonus" not in client.get(f"/transactions/{tid}/edit").text          # card not set up
    keep = {"range": "custom", "from": "2026-01-01", "to": "2026-03-31"}
    client.post(f"/credit-cards/{CARD_A}/rewards", data={"unit": "points", "base_rate": "1", **keep}, headers=ORIGIN)
    assert "Reward bonus" in client.get(f"/transactions/{tid}/edit").text
    bad = client.post(f"/transactions/{tid}/bonus", data={"action": "save", "bonus": "lots"}, headers=ORIGIN)
    assert bad.headers["HX-Retarget"] == f"#bonus-msg-{tid}" and "Enter a bonus" in bad.text
    row = client.post(f"/transactions/{tid}/bonus", data={"action": "save", "bonus": "5,000"}, headers=ORIGIN).text
    assert 'title="Reward bonus: 5,000 points"' in row
    assert 'value="5,000"' in client.get(f"/transactions/{tid}/edit").text
    page = _headings(client.get("/credit-cards?range=custom&from=2026-01-01&to=2026-03-31").text)
    assert '>5,150 <span class="measure-pct">' in page and "Bonuses</th>" in page
    row = client.post(f"/transactions/{tid}/bonus", data={"action": "remove"}, headers=ORIGIN).text
    assert "Reward bonus" not in row


def test_rate_groups_start_collapsed(client, spent):
    keep = {"range": "custom", "from": "2026-01-01", "to": "2026-03-31"}
    client.post(f"/credit-cards/{CARD_A}/rewards", data={"unit": "points", "base_rate": "1", **keep}, headers=ORIGIN)
    page = client.get("/credit-cards?range=custom&from=2026-01-01&to=2026-03-31").text
    keys = re.findall(r'data-toggle-group="(card-[^"]+)" aria-expanded="false"', page)
    assert keys and len(keys) == len(set(keys))                           # one closed expander per group, unique keys
    lines = re.findall(r'<tr class="rate-line more" data-group="([^"]+)">', page)
    assert lines and set(lines) <= set(keys)                              # every category row hidden under its group
    assert page.count("data-toggle-row") == len(keys)


def test_the_table_shows_whole_dollars(client, spent):
    keep = {"range": "custom", "from": "2026-01-01", "to": "2026-03-31"}
    client.post(f"/credit-cards/{CARD_A}/rewards", data={"unit": "cash", "base_rate": "1.5", **keep}, headers=ORIGIN)
    page = client.get("/credit-cards?range=custom&from=2026-01-01&to=2026-03-31").text
    table = page[page.index('class="data breakdown compact card-table"'):]
    assert re.search(r'<t[hd] class="num[^"]*">\$[\d,]+</t[hd]>', table)
    assert not re.search(r'<t[hd] class="num[^"]*">-?\$[\d,]+\.\d\d</t[hd]>', table)   # no cents in Spent or cash back


# ---------------------------------------------------------------- rewards balances (a card's page)

def test_parse_balance():
    assert rewards.parse_balance("points", "52,340") == 52_340
    assert rewards.parse_balance("cash", "$123.456") == 12_346               # half-up to the cent
    assert rewards.parse_balance("miles", "0") == 0                           # an emptied balance is a balance
    for unit, text in [("points", "1.5"), ("points", "-5"), ("cash", "lots"), ("points", "")]:
        with pytest.raises(ValueError):
            rewards.parse_balance(unit, text)


def test_balances_one_per_date_newest_first(db):
    today = date(2026, 3, 31)
    rewards.set_balance(db, CARD_A, "2026-02-01", "points", 1_000, today=today)
    rewards.set_balance(db, CARD_A, "2026-03-01", "points", 2_000, today=today)
    rewards.set_balance(db, CARD_A, "2026-02-01", "points", 1_500, today=today)        # the same date: replaced
    assert [(b.as_of, b.amount) for b in rewards.balances(db, CARD_A)] == [("2026-03-01", 2_000), ("2026-02-01", 1_500)]
    first = rewards.balances(db, CARD_A)[1]
    rewards.set_balance(db, CARD_A, "2026-03-01", "points", 1_750, first.id, today=today)   # moved onto March: replaces it
    assert [(b.id, b.as_of, b.amount) for b in rewards.balances(db, CARD_A)] == [(first.id, "2026-03-01", 1_750)]
    rewards.delete_balance(db, CARD_B, first.id)                              # another card's id: nothing happens
    assert len(rewards.balances(db, CARD_A)) == 1
    rewards.delete_balance(db, CARD_A, first.id)
    assert rewards.balances(db, CARD_A) == []


@pytest.mark.parametrize("account, as_of, unit, message", [
    (CHECKING, "2026-03-01", "points", "credit card"),
    (CARD_A, "2026-04-01", "points", "future"),
    (CARD_A, "March", "points", "date"),
    (CARD_A, "2026-03-01", "stars", "points, miles or cash back"),
])
def test_balance_checks(db, account, as_of, unit, message):
    with pytest.raises(ValueError, match=message):
        rewards.set_balance(db, account, as_of, unit, 100, today=date(2026, 3, 31))


def test_the_card_page_adds_edits_and_deletes_balances(client, spent):
    page = client.get(f"/accounts/{CARD_A}").text
    assert "<h2>Rewards balance</h2>" in page and '<select name="unit" aria-label="Unit">' in page   # no setup: pick
    assert "Rewards balance" not in client.get(f"/accounts/{CHECKING}").text
    client.post(f"/credit-cards/{CARD_A}/rewards", data={"unit": "cash", "base_rate": "1.5", "range": "ytd"},
                headers=ORIGIN)
    page = client.get(f"/accounts/{CARD_A}").text
    assert '<input type="hidden" name="unit" value="cash">' in page                            # the card's unit

    response = client.post(f"/accounts/{CARD_A}/reward-balances",
                           data={"as_of": "2026-03-01", "unit": "cash", "amount": "$84.20"}, headers=ORIGIN,
                           follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith(f"/accounts/{CARD_A}?msg=")
    (b,) = rewards.balances(spent, CARD_A)
    assert (b.as_of, b.unit, b.amount) == ("2026-03-01", "cash", 8_420)
    page = client.get(f"/accounts/{CARD_A}").text
    # now = the entered balance + 1.5% of the spending after Mar 1: 38¢ - 23¢ (a refund, half-up away from 0) + 60¢
    assert '$84.95 cash back</span>' in page and "$84.20 on Mar 1 + $0.75 earned</span>" in page and 'value="84.20"' in page

    client.post(f"/accounts/{CARD_A}/reward-balances/{b.id}",
                data={"as_of": "2026-03-02", "unit": "cash", "amount": "90"}, headers=ORIGIN)
    assert [(x.as_of, x.amount) for x in rewards.balances(spent, CARD_A)] == [("2026-03-02", 9_000)]
    page = client.post(f"/accounts/{CARD_A}/reward-balances",
                       data={"as_of": "2026-03-01", "unit": "cash", "amount": "-1"}, headers=ORIGIN).text
    assert "can&#39;t be negative" in page or "can't be negative" in page
    client.post(f"/accounts/{CARD_A}/reward-balances/{b.id}/delete", headers=ORIGIN)
    assert rewards.balances(spent, CARD_A) == []


# ---------------------------------------------------------------- rewards per transaction and the current balance

def _ids_by_amount(conn, account):
    return {r["amount_cents"]: r["id"] for r in conn.execute("SELECT id, amount_cents FROM transactions WHERE account_id = ?",
                                                             (account,))}


def test_each_transactions_rewards(spent):
    assert rewards.transaction_rewards(spent, CARD_A) == {}                   # not set up
    rewards.set_up(spent, CARD_A, "points", 100)
    rewards.set_rate(spent, CARD_A, "FOOD_AND_DRINK", None, 300)
    ids = _ids_by_amount(spent, CARD_A)
    rewards.set_bonus(spent, ids[4000], 500)
    assert rewards.transaction_rewards(spent, CARD_A) == {
        ids[10000]: 300, ids[2500]: 75, ids[-1500]: -45,                     # 3x; a refund takes its rewards back
        ids[4000]: 40 + 500}                                                  # 1x + its bonus; the payment earns none
    assert rewards.transaction_rewards(spent, CARD_A, after="2026-03-02") == {ids[-1500]: -45, ids[4000]: 540}
    assert rewards.transaction_rewards(spent, CARD_A, [ids[2500], ids[-30000]]) == {ids[2500]: 75}


def test_the_current_balance_adds_the_rewards_since_the_latest(spent):
    assert rewards.current_balance(spent, CARD_A) is None                    # nothing entered
    rewards.set_up(spent, CARD_A, "points", 100)
    rewards.set_balance(spent, CARD_A, "2026-02-01", "points", 1_000, today=date(2026, 3, 31))
    rewards.set_balance(spent, CARD_A, "2026-03-02", "points", 5_000, today=date(2026, 3, 31))   # the day's own count
    cur = rewards.current_balance(spent, CARD_A)
    assert (cur.latest.as_of, cur.earned_since, cur.total) == ("2026-03-02", -15 + 40, 5_025)
    rewards.set_balance(spent, CARD_A, "2026-03-10", "miles", 7_000, today=date(2026, 3, 31))
    assert rewards.current_balance(spent, CARD_A) is None                    # the card earns points, not miles


def test_the_card_page_shows_rewards_and_balances_among_its_transactions(client, spent):
    rewards.set_up(spent, CARD_A, "points", 200)
    rewards.set_balance(spent, CARD_A, "2026-03-02", "points", 5_000)
    rewards.set_balance(spent, CARD_A, "2026-02-01", "points", 1_000)
    page = client.get(f"/accounts/{CARD_A}").text
    table = page[page.index('<table class="data txns"'):]
    assert '<th class="num">Points</th>' in table
    assert '<span class="card-total" title="Your Mar 2 balance + rewards and bonuses since − redemptions since">5,050 points</span>' in page
    # newest first: Mar 5 payment (no rewards), Mar 4, Mar 3, the Mar 2 balance, Mar 2, Mar 1, the Feb 1 balance
    order = ["balance" if "balance" in m else "txn" for m in re.findall(r'<tr id="txn-\d+"|<tr class="balance-line">', table)]
    assert order == ["txn"] * 3 + ["balance"] + ["txn"] * 2 + ["balance"]
    cells = re.findall(r'<td class="num muted">([^<]*)</td>', table)
    assert cells == ["", "80", "-30", "50", "200"]
    assert '<td class="num">5,000</td>' in table and '<td class="num">1,000</td>' in table
    assert "balance-line" not in client.get(f"/accounts/{CARD_B}").text  # another card's balances stay on its page


def test_a_row_returned_after_an_edit_keeps_its_rewards_cell(client, spent):
    rewards.set_up(spent, CARD_A, "points", 100)
    rewards.set_rate(spent, CARD_A, "ENTERTAINMENT", None, 500)
    tid = _ids_by_amount(spent, CARD_A)[4000]
    row = client.post(f"/transactions/{tid}/category", data={"category": "ENTERTAINMENT", "view": "account"},
                      headers=ORIGIN).text
    assert '<td class="num muted">200</td>' in row                          # 5x now
    assert '<td class="num muted">' not in client.post(f"/transactions/{tid}/category",
                                                       data={"category": "ENTERTAINMENT"}, headers=ORIGIN).text
    panel = client.get(f"/transactions/{tid}/edit?view=account").text
    assert 'colspan="6"' in panel


def test_the_credit_cards_page_shows_each_cards_rewards_balance(client, spent):
    url = "/credit-cards?range=custom&from=2026-01-01&to=2026-03-31"
    assert " Available" not in client.get(url).text and "#reward-balances" not in client.get(url).text   # none entered
    rewards.set_balance(spent, CARD_B, "2026-03-01", "miles", 12_000)        # card B: no rates set up
    rewards.set_up(spent, CARD_A, "points", 200)
    rewards.set_balance(spent, CARD_A, "2026-03-02", "points", 5_000)
    page = client.get(url).text
    assert ">5,050</a>" in page and "Points Available</span>" in page    # 5,000 + 2x of the spending since
    assert ">12,000</a>" in page and "Miles Available, Mar 1</span>" in page
    assert f'href="/accounts/{CARD_A}#reward-balances"' in page
