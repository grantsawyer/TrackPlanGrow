"""Milestone 14: search, filters, sort, group edit, rules from a transaction (fake data only)."""

import pytest

from fintrack.web.transaction_filters import parse_search

ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def db(conn):
    """Two fake accounts and six transactions (Plaid sign: positive = money out)."""
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env) VALUES (1, 'i1', 'sandbox')")
    for acc_id, name in [(1, "Fake Checking"), (2, "Fake Card")]:
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification)"
                     " VALUES (?, 1, ?, ?, 'depository', 'asset')", (acc_id, f"a{acc_id}", name))
    rows = [("t1", 1, "2026-09-02", 4250, "BEAN BARN #12", "Bean Barn", "FOOD_AND_DRINK"),
            ("t2", 1, "2026-09-05", 120000, "RENT SEPT", None, "RENT_AND_UTILITIES"),
            ("t3", 2, "2026-09-10", 1999, "STREAMFLIX", "Streamflix", "ENTERTAINMENT"),
            ("t4", 1, "2026-09-15", -250000, "PAYROLL FAKE CO", None, "INCOME"),
            ("t5", 2, "2026-09-20", 875, "MYSTERY 100%_OFF", None, "UNCATEGORIZED"),
            ("t6", 2, "2026-10-01", 4250, "BEAN BARN #12", "Bean Barn", "FOOD_AND_DRINK")]
    conn.executemany("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name,"
                     " merchant_name, plaid_category_key) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    return conn


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def ids(client, **params):
    """The fake transactions shown, in order ("t1".."t6": rows were inserted in that order, so the
    database id N is tN), for these query parameters."""
    import re
    if not {"range", "month", "from", "to"} & set(params):
        params["range"] = "custom"          # a blank Custom range = everything, whatever today's date
    html = client.get("/transactions", params=params).text
    return [f"t{n}" for n in re.findall(r'<tr id="txn-(\d+)"', html)]


def test_parse_search_splits_words_and_amounts():
    assert parse_search("bean 42.50") == (["bean"], [("=", 4250)])
    assert parse_search(">100 <=$1,200.5 coffee") == (["coffee"], [(">", 10000), ("<=", 120050)])
    assert parse_search("#12 100%") == (["#12", "100%"], [])


def test_search_text_amounts_and_more_fields(client):
    assert ids(client, q="bean") == ["t6", "t1"]                        # newest first
    assert ids(client, q="42.50") == ["t6", "t1"]
    assert ids(client, q="2500") == ["t4"]                              # money in matches too
    assert ids(client, q=">1000") == ["t4", "t2"]
    assert ids(client, q="bean >50") == []
    assert ids(client, q="fake card") == ["t6", "t5", "t3"]              # account name
    assert ids(client, q="entertainment") == ["t3"]                      # category name
    assert ids(client, q="100%_") == ["t5"]                              # % and _ are literal


def test_date_range_uncategorized_and_sort(client):
    assert ids(client, **{"from": "2026-09-05", "to": "2026-09-15"}) == ["t4", "t3", "t2"]
    assert ids(client, category="UNCATEGORIZED") == ["t5"]
    assert ids(client, uncat="1") == ["t5"]                              # an older link: the box was removed (2026-10-30)
    assert 'name="uncat"' not in client.get("/transactions").text
    # Biggest money out first; equal amounts (t1, t6) fall back to newest first, in both directions.
    assert ids(client, sort="amount") == ["t2", "t6", "t1", "t3", "t5", "t4"]
    assert ids(client, sort="amount", dir="asc") == ["t4", "t5", "t3", "t6", "t1", "t2"]
    assert ids(client, sort="description", dir="asc")[:2] == ["t6", "t1"]               # Bean Barn
    page = client.get("/transactions", params={"sort": "amount", "q": "bean"}).text
    assert 'href="/transactions?q=bean&amp;sort=amount&amp;dir=asc"' in page              # again = reverse, filters kept
    assert '<input type="hidden" name="sort" value="amount">' in page                       # filtering keeps the sort
    assert "style=" not in page.split("<main>")[1]


# ---------------------------------------------------------------- group edit

def category_of(db, plaid_id):
    return db.execute("SELECT t.category FROM transactions_effective t JOIN transactions x ON x.id = t.id"
                      " WHERE x.plaid_transaction_id = ?", (plaid_id,)).fetchone()[0]


def test_group_edit_selected_rows_then_back_to_automatic(client, db):
    page = client.get("/transactions").text
    assert '<form id="bulk-form" class="bulk-bar"' in page and page.count('class="row-select"') == 6
    r = client.post("/transactions/bulk-category", data={"ids": ["1", "6"], "apply_category": "ENTERTAINMENT",
                                                          "return_url": "/transactions?q=bean"},
                    headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/transactions?q=bean&msg=2+transactions")
    assert category_of(db, "t1") == category_of(db, "t6") == "ENTERTAINMENT" and category_of(db, "t3") == "ENTERTAINMENT"
    client.post("/transactions/bulk-category", data={"ids": ["1"], "apply_category": "__auto__"}, headers=ORIGIN)
    assert category_of(db, "t1") == "FOOD_AND_DRINK" and category_of(db, "t6") == "ENTERTAINMENT"


def test_group_edit_all_matching_uses_only_the_filters(client, db):
    client.post("/transactions/bulk-category", data={"all": "1", "q": "fake card", "apply_category": "TRAVEL"}, headers=ORIGIN)
    assert [category_of(db, t) for t in ("t3", "t5", "t6")] == ["TRAVEL"] * 3
    assert [category_of(db, t) for t in ("t1", "t2", "t4")] == ["FOOD_AND_DRINK", "RENT_AND_UTILITIES", "INCOME"]


def test_group_edit_refusals(client):
    no_category = client.post("/transactions/bulk-category", data={"ids": ["1"]}, headers=ORIGIN, follow_redirects=False)
    assert "error=Choose+a+category" in no_category.headers["location"]
    nothing = client.post("/transactions/bulk-category", data={"apply_category": "TRAVEL"}, headers=ORIGIN, follow_redirects=False)
    assert "error=Select+some+transactions" in nothing.headers["location"]
    outside = client.post("/transactions/bulk-category", data={"ids": ["1"], "apply_category": "TRAVEL",
                                                               "return_url": "https://evil.example/"},
                          headers=ORIGIN, follow_redirects=False)
    assert outside.headers["location"].startswith("/transactions?msg=")          # never leaves the list
    unknown = client.post("/transactions/bulk-category", data={"ids": ["1"], "apply_category": "NOPE"},
                          headers=ORIGIN, follow_redirects=False)
    assert "error=Unknown+category" in unknown.headers["location"]


def test_group_edit_all_matching_with_a_category_filter(client, db):
    """The list's category filter and the category to apply are different fields (bug found while
    building: both were called "category")."""
    client.post("/transactions/bulk-category", data={"all": "1", "category": "FOOD_AND_DRINK", "apply_category": "TRAVEL"},
                headers=ORIGIN)
    assert [category_of(db, t) for t in ("t1", "t6", "t3")] == ["TRAVEL", "TRAVEL", "ENTERTAINMENT"]


# ---------------------------------------------------------------- date range toggle (ribbon)

def test_range_presets():
    from datetime import date

    from fintrack.web.transaction_filters import range_bounds
    d = date(2026, 3, 15)
    assert range_bounds("this-month", d) == ("2026-03-01", "2026-03-15")
    assert range_bounds("last-month", d) == ("2026-02-01", "2026-02-28")
    assert range_bounds("90", d) == ("2025-12-16", "2026-03-15")
    assert range_bounds("ytd", d) == ("2026-01-01", "2026-03-15")
    assert range_bounds("all", d) == ("", "")


def ribbon_of(html):
    return html.split('<header class="tabbar">')[1].split("</header>")[0]


def selected(ribbon):
    """The range the ribbon's dropdown shows as chosen."""
    import re
    return re.search(r'data-value="([\w-]+)" tabindex="-1"\s+aria-selected="true"', ribbon).group(1)


def test_range_dropdown_and_dates_live_in_the_ribbon(client):
    import re
    page = client.get("/transactions").text
    ribbon = ribbon_of(page)
    from datetime import date, timedelta
    assert re.findall(r'role="option" data-value="([\w-]+)"', ribbon) == \
        ["this-month", "last-month", "90", "ytd", "last-year", "365", "custom"]        # Last year after YTD; no All
    # Default: 365 days, with its dates in the fields; Custom isn't listed until you enter a date.
    assert selected(ribbon) == "365" and 'name="range" form="txn-filters" value="365"' in ribbon
    assert f'value="{(date.today() - timedelta(days=364)).isoformat()}"' in ribbon and f'value="{date.today().isoformat()}"' in ribbon
    assert re.search(r'data-value="custom"[^>]*hidden>Custom', ribbon)
    # A custom dropdown, not a <select> (macOS opened lower options upward); the list starts closed.
    # (the range controls only: the + Add a transaction form beside them has selects, 2026-11-04)
    range_part = ribbon.split('id="tx-range"')[1].split('class="pop-button add-txn"')[0]
    assert "<select" not in range_part and re.search(r'role="listbox"[^>]*hidden>', range_part)
    filters = page.split('id="txn-filters"')[1].split("</form>")[0]
    assert 'name="from"' not in filters and 'name="month"' not in page and "tx-dates" not in page
    assert 'hx-select-oob="#tx-range"' in page


def test_a_preset_fills_the_dates_it_applies(client):
    from datetime import date
    ribbon = ribbon_of(client.get("/transactions", params={"range": "ytd"}).text)
    assert selected(ribbon) == "ytd"
    assert f'value="{date.today().year}-01-01"' in ribbon and f'value="{date.today().isoformat()}"' in ribbon


def test_custom_keeps_its_dates(client):
    params = {"range": "custom", "from": "2026-09-05", "to": "2026-09-15"}
    assert ids(client, **params) == ["t4", "t3", "t2"]
    ribbon = ribbon_of(client.get("/transactions", params=params).text)
    assert selected(ribbon) == "custom" and 'value="2026-09-05"' in ribbon and 'value="2026-09-15"' in ribbon
    assert 'data-value="custom" tabindex="-1"\n          aria-selected="true">Custom' in ribbon         # listed once used
    # A preset ignores dates still in the fields (ui.js switches to Custom only when a date is edited).
    assert ids(client, range="365", **{"from": "2026-09-05", "to": "2026-09-15"}) == ids(client, range="365")


def test_old_month_links_open_as_custom(client):
    assert ids(client, month="2026-09") == ["t5", "t4", "t3", "t2", "t1"]          # September only
    ribbon = ribbon_of(client.get("/transactions", params={"month": "2026-09"}).text)
    assert selected(ribbon) == "custom"
    assert 'value="2026-09-01"' in ribbon and 'value="2026-09-30"' in ribbon
    assert ids(client, range="365", month="2026-09") == ids(client, range="365")     # the dropdown wins over the link


def test_custom_dates_are_open_ended_when_blank(client):
    """One date is enough: a blank From = from the earliest transaction, a blank To = to the latest."""
    assert ids(client, range="custom", **{"from": "2026-09-15"}) == ["t6", "t5", "t4"]
    assert ids(client, range="custom", to="2026-09-05") == ["t2", "t1"]
    assert ids(client, range="custom", **{"from": "", "to": ""}) == ["t6", "t5", "t4", "t3", "t2", "t1"]
    ribbon = ribbon_of(client.get("/transactions", params={"range": "custom", "to": "2026-09-05"}).text)
    assert 'name="from" form="txn-filters" value="2026-09-02"' in ribbon          # blank From shows the earliest


def test_last_year_default_and_old_all_bookmarks(client):
    from datetime import date, timedelta

    from fintrack.web.transaction_filters import range_bounds
    assert range_bounds("last-year", date(2026, 10, 8)) == ("2025-01-01", "2025-12-31")
    # No range in the address = the last 365 days; an old "range=all" bookmark falls back to that too.
    for params in ({}, {"range": "all"}):
        ribbon = ribbon_of(client.get("/transactions", params=params).text)
        assert selected(ribbon) == "365"
        assert f'value="{(date.today() - timedelta(days=364)).isoformat()}"' in ribbon


# ---------------------------------------------------------------- row edit panel

def test_rows_open_an_edit_panel_and_hide_not_counted(client, db):
    db.execute("UPDATE transactions SET is_transfer = 1 WHERE id = 3")              # a matched transfer
    db.commit()
    page = client.get("/transactions", params={"range": "custom"}).text
    row = page.split('<tr id="txn-3"')[1].split("</tr>")[0]
    assert 'data-edit-url="/transactions/3/edit"' in row and 'class="link-button row-open"' in row
    assert "<select" not in row and "rule…" not in row and 'class="tag"' not in row                  # no text tags
    assert 'role="img" aria-label="Matched transfer"' in row                                           # an icon instead
    panel = client.get("/transactions/3/edit").text
    assert 'id="edit-panel-3"' in panel and 'name="description"' in panel and 'name="category"' in panel
    assert "Create a rule…" in panel and "/rules/new?from=" in panel and "Not a transfer" in panel and "From the bank: STREAMFLIX" in panel


def test_edit_description_and_category(client, db):
    row = client.post("/transactions/1/edit", data={"description": "  Coffee with Sam  ", "category": "TRAVEL"},
                      headers=ORIGIN).text
    assert row.strip().startswith('<tr id="txn-1"') and ">Coffee with Sam</button>" in row and "Travel" in row
    raw = db.execute("SELECT name, merchant_name, user_description, user_category FROM transactions WHERE id = 1").fetchone()
    assert tuple(raw) == ("BEAN BARN #12", "Bean Barn", "Coffee with Sam", "TRAVEL")       # the bank's text kept
    assert ids(client, q="sam") == ["t1"]                                                   # search finds it
    # Rules still match the bank's text, not your description.
    from fintrack import rules
    found = rules.preview(db, [rules.Condition("description", "contains", ["bean barn"])], [rules.Action("set_category", "TRAVEL")])
    assert found.matches == 2
    back = client.post("/transactions/1/edit", data={"description": "", "category": ""}, headers=ORIGIN).text
    assert ">Bean Barn</button>" in back and "Food" in back                                # blank = the bank's


def test_description_box_is_prefilled_and_unchanged_text_stays_automatic(client, db):
    panel = client.get("/transactions/1/edit").text
    assert 'name="description" value="Bean Barn"' in panel                       # the shown text, editable
    client.post("/transactions/1/edit", data={"description": "Bean Barn", "category": ""}, headers=ORIGIN)
    assert db.execute("SELECT user_description FROM transactions WHERE id = 1").fetchone()[0] is None
    client.post("/transactions/1/edit", data={"description": "Bean Barn downtown", "category": ""}, headers=ORIGIN)
    assert 'name="description" value="Bean Barn downtown"' in client.get("/transactions/1/edit").text


def test_status_icons_replace_text_tags(client, db):
    from fintrack import categories, rules
    db.execute("UPDATE transactions SET pending = 1 WHERE id = 5")
    db.commit()
    rules.add_simple(db, "streamflix", "ENTERTAINMENT")
    categories.set_transaction_category(db, 2, "CREDIT_CARD_PAYMENT")        # a "not counted" kind
    page = client.get("/transactions", params={"range": "custom"}).text
    row = lambda n: page.split(f'<tr id="txn-{n}"')[1].split("</tr>")[0]      # noqa: E731
    assert 'aria-label="Pending" title="Pending"' in row(5)
    assert 'aria-label="Set by a rule" title="Category set by a rule"' in row(3) and "(rule)" not in row(3)
    assert 'aria-label="Not counted" title="Not counted: transfer or card payment"' in row(2)
    assert 'role="img"' not in row(1)                                         # a plain expense: no icons


def test_status_icons_sit_on_the_description_line(client, db):
    """No icon column, and no extra line: the icons follow the description itself (user's request 2026-10-08); the
    bank's text shows only in the edit panel (2026-10-17)."""
    db.execute("UPDATE transactions SET pending = 1 WHERE id = 1")             # Bean Barn: has a 2nd line
    db.commit()
    page = client.get("/transactions", params={"range": "custom"}).text
    assert "status-col" not in page
    cell = page.split('<tr id="txn-1"')[1].split("</tr>")[0].split("<td")[4]       # select, date, logo, description
    assert cell.index(">Bean Barn</button>") < cell.index('aria-label="Pending"')
    assert "BEAN BARN #12" not in cell
    panel = client.get("/transactions/1/edit").text
    assert '<td colspan="7">' in panel and "From the bank: BEAN BARN #12" in panel


def test_purpose_comes_last_in_the_edit_panel():
    """User's request 2026-11-01: only some rows have it, so Tags and Recurring stay in the same place on every row."""
    from pathlib import Path
    import fintrack.web as web
    panel = Path(web.__file__).parent.joinpath("templates/_txn_edit.html").read_text()
    assert panel.index("<span>Tags</span>") < panel.index("<span>Recurring</span>") < panel.index("<span>Reward bonus") < panel.index("<span>Purpose</span>")
    assert panel.index("<span>Purpose</span>") < panel.index("<span>Transfer</span>") < panel.index('class="edit-actions')

