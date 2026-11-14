"""Chart data (milestone 13): fixed fake data and fixed dates, so it's deterministic."""

import html
import re
from datetime import date

import pytest

from fintrack import budgets, items, recurring, spending, visuals

TODAY = date(2026, 10, 4)
CHECKING, CARD, BROKERAGE, LOAN = 1, 2, 3, 4


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    for acc_id, name, account_type in [(CHECKING, "Fake Checking", "depository"), (CARD, "Fake Card", "credit"),
                                       (BROKERAGE, "Fake Brokerage", "investment"), (LOAN, "Fake Loan", "loan")]:
        conn.execute(
            "INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification)"
            " VALUES (?, 1, ?, ?, ?, ?)",
            (acc_id, f"acc-{acc_id}", name, account_type, items.classify(account_type)),
        )
    conn.commit()
    return conn


_ids = iter(range(1, 100_000))


def add(conn, account, cents, day, key="FOOD_AND_DRINK"):
    tid = next(_ids)
    conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key)"
        " VALUES (?, ?, ?, ?, 'FAKE', ?)",
        (f"v{tid}", account, day, cents, key),
    )
    conn.commit()


def snapshot(conn, day, account, cents, classification, account_type):
    conn.execute(
        "INSERT INTO balance_snapshots (date, account_id, balance_cents, classification, account_type) VALUES (?, ?, ?, ?, ?)",
        (day, account, cents, classification, account_type),
    )
    conn.commit()


# ---------------------------------------------------------------- spending

def test_cumulative_spending_this_vs_last_month(db):
    add(db, CARD, 1000, "2026-09-01")
    add(db, CARD, 2000, "2026-09-30")
    add(db, CARD, 500, "2026-10-02")
    add(db, CARD, -100, "2026-10-03")              # refund reduces it
    add(db, CHECKING, -500000, "2026-10-01", key="INCOME")   # income isn't spending
    c = visuals.cumulative_spending(db, TODAY)
    assert c["this"] == [0.0, 5.0, 4.0, 4.0]       # days 1..4 only: the rest hasn't happened
    assert len(c["last"]) == 30 and c["last"][0] == 10.0 and c["last"][-1] == 30.0
    assert len(c["days"]) == 31                    # October is longer than September


def test_cumulative_spending_for_a_finished_month_is_drawn_in_full(db):
    add(db, CARD, 1000, "2026-08-31")
    add(db, CARD, 2000, "2026-09-30")
    add(db, CARD, 500, "2026-10-02")
    c = visuals.cumulative_spending(db, TODAY, month="2026-09")
    assert (c["this_month"], c["last_month"]) == ("2026-09", "2026-08")
    assert len(c["this"]) == 30 and c["this"][-1] == 20.0     # all of September, not just through day 4
    assert len(c["last"]) == 31 and c["last"][-1] == 10.0


def test_monthly_spending_counts_only_days_inside_the_range_and_fills_gaps(db):
    add(db, CARD, 1000, "2026-07-05")                    # before the range
    add(db, CARD, 2000, "2026-07-20")
    add(db, CARD, 3000, "2026-09-02")                    # August has nothing
    add(db, CARD, 4000, "2026-10-03")
    add(db, CARD, 5000, "2026-10-20")                    # after the range
    add(db, CHECKING, -9000, "2026-09-03", key="INCOME")  # income isn't spending
    m = visuals.monthly_spending(db, "2026-07-10", "2026-10-08")
    assert m["months"] == ["2026-07", "2026-08", "2026-09", "2026-10"]
    assert m["values"] == [20.0, 0.0, 30.0, 40.0]



def test_period_totals_split_money_in_and_out(db):
    add(db, CARD, 1250, "2026-10-01")
    add(db, CHECKING, -5000, "2026-10-01", key="INCOME")
    add(db, CARD, 300, "2026-09-10")
    by_day = visuals.period_totals(db, "account_hidden = 0 AND date >= ?", ["2026-10-01"], by_day=True)
    assert by_day == {"labels": ["2026-10-01"], "in": [50.0], "out": [12.5], "in_cents": 5000, "out_cents": 1250}
    by_month = visuals.period_totals(db, "account_hidden = 0", [], by_day=False)
    assert by_month["labels"] == ["2026-09", "2026-10"] and by_month["out"] == [3.0, 12.5]


def _label(conn, key):
    return conn.execute("SELECT label FROM categories WHERE key = ?", (key,)).fetchone()[0]


def test_category_rings_nest_subcategories_under_their_category(db):
    from fintrack import categories
    coffee = categories.add_subcategory(db, "FOOD_AND_DRINK", "Fake Coffee")
    bakery = categories.add_subcategory(db, "FOOD_AND_DRINK", "Fake Bakery")
    add(db, CARD, 1000, "2026-10-01")                                # in Food itself
    add(db, CARD, 500, "2026-10-01", key=coffee)
    add(db, CARD, 300, "2026-10-02", key=bakery)
    add(db, CARD, 2000, "2026-10-02", key="GENERAL_MERCHANDISE")
    add(db, CARD, -500, "2026-10-03", key="ENTERTAINMENT")           # only a refund: no slice
    add(db, CHECKING, -9000, "2026-10-03", key="INCOME")             # not spending
    rings = visuals.category_rings(db, "account_hidden = 0", [])
    assert rings["total"] == 38.0 and rings["total_cents"] == 3800
    assert [(g["key"], g["value"], g["slot"], g["direct"]) for g in rings["parents"]] == [
        ("GENERAL_MERCHANDISE", 20.0, "slot-1", 20.0), ("FOOD_AND_DRINK", 18.0, "slot-2", 10.0)]
    assert rings["parents"][1]["label"] == _label(db, "FOOD_AND_DRINK")
    assert rings["parents"][1]["subs"] == [{"key": coffee, "label": "Fake Coffee", "value": 5.0},
                                           {"key": bakery, "label": "Fake Bakery", "value": 3.0}]   # largest first
    income = visuals.category_rings(db, "account_hidden = 0", [], kind="income")
    assert [(g["key"], g["value"]) for g in income["parents"]] == [("INCOME", 90.0)]


def _spend_by_category(db, cents_each):
    keys = [r[0] for r in db.execute(
        "SELECT key FROM categories WHERE parent_key IS NULL AND kind = 'expense' ORDER BY key LIMIT ?", (len(cents_each),))]
    for key, cents in zip(keys, cents_each):
        add(db, CARD, cents, "2026-10-01", key=key)
    return keys


def test_category_rings_other_holds_at_most_five_percent(db):
    """User's request 2026-10-29: the smallest fold into Other only while together they're at most 5% of the total."""
    keys = _spend_by_category(db, [50000, 30000, 10000, 5000, 1500, 1000, 800, 700])     # total $990; 5% = $49.50
    rings = visuals.category_rings(db, "account_hidden = 0", [])
    assert [g["key"] for g in rings["parents"]] == keys[:4] + ["_other"]                 # $15+10+8+7 = $40; +$50 is over
    other = rings["parents"][-1]
    assert (other["label"], other["value"], other["slot"], other["subs"]) == ("Other", 40.0, "slot-other", [])
    assert [m["key"] for m in other["members"]] == keys[4:]                              # largest first
    assert rings["total"] == 990.0


def test_category_rings_past_eight_unfolded_categories_are_gray_not_cycled(db):
    keys = _spend_by_category(db, [1000 * (10 - i) for i in range(10)])                  # $100 ... $10; 5% = $27.50
    rings = visuals.category_rings(db, "account_hidden = 0", [])
    assert [g["key"] for g in rings["parents"]] == keys                                  # only $10 fits: a lone one stays
    assert [g["slot"] for g in rings["parents"]] == [f"slot-{i}" for i in range(1, 9)] + ["slot-other"] * 2
    assert rings["total"] == 550.0


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def _balance(card):
    """The Summary card (a balance since 2026-11-11) as text: its net, then each key row: name, count, share, net."""
    clean = lambda part: " ".join(re.sub(r"<[^>]+>", " ", part).split())  # noqa: E731
    net = clean(re.search(r'class="balance-net-value[^"]*">(.*?)</span>', card, re.S).group(1))
    part = lambda cls, li: clean(re.search(r'<span class="' + cls + r'[^"]*">(.*?)</span>\s*(?:<span class="(?:muted|balance)|$)', li, re.S).group(1))  # noqa: E731
    rows = [[part("balance-name", li), part("muted small balance-of", li), part("balance-amt", li)]
            for li in re.findall(r"<li>(.*?)</li>", card, re.S)]
    return net, rows


def test_the_transactions_page_shows_spending_by_category(client, db):
    import json
    import re
    add(db, CARD, 1200, "2026-10-01", key="GENERAL_MERCHANDISE")
    add(db, CARD, 800, "2026-10-02")
    add(db, CHECKING, -5000, "2026-10-02", key="INCOME")
    # All (user's request 2026-10-31): a Summary in the pie's place, each group's count and total, linking to its view
    every = client.get("/transactions?range=custom&from=2026-10-01&to=2026-10-31").text
    summary = every.split("<h2>Summary</h2>")[1].split("</section>")[0]
    assert 'data-chart="rings"' not in every and 'class="tx-charts both"' in every
    assert every.index("<h2>Summary</h2>") < every.index("<h2>Money in and out</h2>")
    # a balance (2026-11-11; it replaced a table of in / out / net): the net, then each group with something in it,
    # its count, its share of its side (none when alone there) and its net; money in green with a "+"
    assert _balance(summary) == ("+$30", [["Income 1", "", "+$50"], ["Spending 2", "", "$20"]])    # money out: no minus
    assert ">Net in</span>" in summary
    assert 'class="balance-net-value ok"' in summary and 'class="balance-amt ok"' in summary
    assert 'class="meter-fill balance-seg balance-gap"' in summary          # the net, dashed, on the shorter bar
    # within a group both ways: a refund lowers Spending's net; a transfer between two shown accounts nets to $0
    add(db, CARD, -300, "2026-10-03")
    add(db, CHECKING, 7000, "2026-10-04", key="TRANSFER_OUT")
    add(db, CARD, -7000, "2026-10-05", key="TRANSFER_IN")
    both = client.get("/transactions?range=custom&from=2026-10-01&to=2026-10-31").text
    assert _balance(both.split("<h2>Summary</h2>")[1].split("</section>")[0]) == (
        "+$33", [["Income 1", "", "+$50"], ["Spending 3", "", "$17"], ["Transfers 2", "", "$0"]])
    # the money in / out filter still works from an address (the card no longer offers it)
    refunds = client.get("/transactions?range=custom&from=2026-10-01&to=2026-10-31&kind=expense&money=in").text
    assert 'data-bulk-total="1"' in refunds and 'name="money" value="in"' in refunds
    assert 'name="money"' not in client.get("/transactions?money=bogus").text    # junk: ignored
    db.execute("DELETE FROM transactions WHERE amount_cents IN (-300, 7000, -7000)")
    db.commit()
    assert 'href="/transactions?range=custom&amp;from=2026-10-01&amp;to=2026-10-31&amp;kind=expense">Spending</a>' in summary
    page = client.get("/transactions?range=custom&from=2026-10-01&to=2026-10-31&kind=expense").text
    # the corner: its total and transactions (2026-10-31); no summary line above the charts
    assert "selected>Spending by category</option>" in page and 'data-chart="rings"' in page and ">$20 · 2</span>" in page
    assert " · net " not in page and " · net " not in every
    data = json.loads(re.search(r'id="tx-categories-data">(.*?)</script>', page).group(1))
    assert [g["key"] for g in data["parents"]] == ["GENERAL_MERCHANDISE", "FOOD_AND_DRINK"]
    assert page.index("selected>Spending by category</option>") < page.index("<h2>Spending over time</h2>")   # on the left
    assert 'class="tx-charts both"' in page and 'class="rings-legend small"' in page
    # a slice (or its legend entry) opens the list narrowed to its category, keeping the other filters
    assert data["parents"][0]["url"] == "/transactions?kind=expense&range=custom&from=2026-10-01&to=2026-10-31&category=GENERAL_MERCHANDISE"
    assert f'<a href="{data["parents"][0]["url"].replace("&", "&amp;")}">' in page
    income = client.get("/transactions?range=custom&from=2026-10-01&to=2026-10-31&kind=income").text
    assert "selected>Income by category</option>" in income and 'data-value-label="Received"' in income
    assert 'data-chart="rings"' not in client.get("/transactions?kind=transfer").text


def _chart_data(page, chart_id):
    import json
    import re
    return json.loads(re.search(rf'id="{chart_id}-data">(.*?)</script>', page).group(1))


def test_clicking_the_charts_filters_the_list_and_clicking_again_clears_it(client, db):
    import re
    add(db, CARD, 1200, "2026-09-10", key="GENERAL_MERCHANDISE")
    add(db, CARD, 800, "2026-10-02")
    add(db, CARD, 300, "2026-10-03", key="GENERAL_MERCHANDISE")
    base = "/transactions?kind=expense&range=custom&from=2026-08-01&to=2026-10-31"   # long enough for bars by month
    rings = _chart_data(client.get(base).text, "tx-categories")
    url = next(g["url"] for g in rings["parents"] if g["key"] == "GENERAL_MERCHANDISE")
    assert url == base + "&category=GENERAL_MERCHANDISE"

    # a slice picked: the list narrows, the pie keeps every category (the pick highlighted), its link clears it
    page = client.get(url).text
    assert 'data-bulk-total="2"' in page
    rings = _chart_data(page, "tx-categories")
    assert rings["picked"] == "GENERAL_MERCHANDISE" and len(rings["parents"]) == 2
    assert ">$15 · 2</span>" in page                                        # the corner follows the pick (2026-11-01)
    assert next(g["url"] for g in rings["parents"] if g["key"] == "GENERAL_MERCHANDISE") == base
    assert '<li title="' + _label(db, "GENERAL_MERCHANDISE") + '" class="picked">' in page

    # a bar picked as well: both apply; the bars keep every month (the pick highlighted), its link clears only it
    flow = _chart_data(page, "tx-period")
    assert flow["labels"] == ["2026-09", "2026-10"] and flow["picked"] is None
    both = client.get(flow["urls"][1]).text
    assert 'data-bulk-total="1"' in both and 'name="period" value="2026-10"' in both and "Oct 2026" in both
    flow = _chart_data(both, "tx-period")
    assert flow["labels"] == ["2026-09", "2026-10"] and flow["picked"] == 1
    assert flow["urls"][1] == "/transactions?category=GENERAL_MERCHANDISE&kind=expense&range=custom&from=2026-08-01&to=2026-10-31"
    month = _chart_data(both, "tx-categories")
    assert month["total"] == 11.0                                           # the pie follows the picked month
    # ...in October Food & drink ($8) outranks General merchandise ($3), but each keeps its colour from the whole range
    assert [(g["key"], g["slot"]) for g in month["parents"]] == [("FOOD_AND_DRINK", "slot-2"),
                                                                 ("GENERAL_MERCHANDISE", "slot-1")]
    # group edit's "select all matching" carries the period
    assert '<input type="hidden" name="period" value="2026-10">' in both.split('id="bulk-form"')[1]
    assert 'name="period"' not in client.get(base + "&period=Oct").text                 # not a month or day: ignored


def test_money_in_and_out_shows_its_average_per_month_or_per_day(client, db):
    """User's request 2026-10-30: the card's corner = the average over the range's days (not over the bars), per day
    when the bars are days."""
    add(db, CARD, 122000, "2026-09-10")                                     # $1,220 out
    add(db, CHECKING, -61000, "2026-08-10", key="INCOME")                   # $610 in
    def corner(query):
        page = client.get("/transactions?range=custom" + query).text
        return page.split("<h2>Money in and out</h2>")[1].split("</span>")[0].split(">")[-1]
    # Jun 1 - Sep 30 = 122 days = 4.0 months (days / 30.44): $1,220 x 30.44 / 122 = $304.38
    assert corner("&from=2026-06-01&to=2026-09-30") == "$152 in · $304 out per month"
    # Sep and Aug as days (a 61-day range): $1,220 / 61 = $20 a day
    assert corner("&from=2026-08-01&to=2026-09-30") == "$10 in · $20 out per day"
    assert "for these filters" not in client.get("/transactions?range=custom&from=2026-06-01&to=2026-09-30").text


def test_spending_and_income_views_show_one_total_per_bar_and_the_average(client, db):
    """User's request 2026-10-30: Spending / Income = totals (refunds lower spending), a dashed line at the average
    over the range's days (Jun 1 - Sep 30 = 122 days; a month = 30.44 days)."""
    add(db, CARD, 6100, "2026-08-05")
    add(db, CARD, 12000, "2026-09-10")
    add(db, CARD, -2000, "2026-09-12")                                     # a refund
    for day in ("2026-08-01", "2026-09-01"):
        add(db, CHECKING, -30500, day, key="INCOME")
    june_on = "/transactions?range=custom&from=2026-06-01&to=2026-09-30"
    page = client.get(june_on + "&kind=expense").text
    data = _chart_data(page, "tx-period")
    assert 'data-chart="totals"' in page and "<h2>Spending over time</h2>" in page
    assert (data["labels"], data["values"], data["per"]) == (["2026-08", "2026-09"], [61.0, 100.0], "month")
    assert data["average"] == 40.17 and ">$40 per month</span>" in page    # $161 x 30.44 / 122
    income = client.get(june_on + "&kind=income").text
    assert _chart_data(income, "tx-period")["values"] == [305.0, 305.0] and ">$152 per month</span>" in income
    assert 'data-direction="in"' in income
    days = _chart_data(client.get("/transactions?range=custom&from=2026-09-01&to=2026-09-30&kind=expense").text, "tx-period")
    assert days["per"] == "day" and days["average"] == 3.33                  # $100 / 30 days
    assert 'data-chart="flow"' in client.get(june_on).text                  # All: money in and out, as before


def test_transfers_view_shows_where_the_money_went(client, db):
    """User's request 2026-10-30: a matched pair is one flow between its accounts (counted once); an unmatched transfer
    flows between its account and its category."""
    from fintrack import transfers
    add(db, CHECKING, 50000, "2026-09-03", key="TRANSFER_OUT")
    add(db, CARD, -50000, "2026-09-04", key="TRANSFER_IN")
    add(db, CHECKING, 20000, "2026-09-20", key="TRANSFER_OUT")             # no match
    assert transfers.detect_transfers(db) == 1
    flows = visuals.transfer_flows(db, "account_hidden = 0 AND kind IN ('transfer', 'excluded')", [])
    out_label = _label(db, "TRANSFER_OUT")
    # a category node is keyed by its key, labelled with its name (2026-10-31: the flow filter names it)
    assert flows["flows"] == [{"from": f"L:a{CHECKING}", "to": f"R:a{CARD}", "flow": 500.0, "count": 1},
                              {"from": f"L:a{CHECKING}", "to": "R:cTRANSFER_OUT", "flow": 200.0, "count": 1}]
    assert flows["labels"]["R:cTRANSFER_OUT"] == out_label
    assert flows["slots"][f"L:a{CHECKING}"] == "slot-1" and flows["slots"][f"R:a{CARD}"] == "slot-2"
    assert flows["slots"]["R:cTRANSFER_OUT"] == "slot-other" and flows["total_cents"] == 70000
    assert flows["count"] == 2                                              # a matched pair once
    page = client.get("/transactions?range=custom&from=2026-09-01&to=2026-09-30&kind=transfer").text
    assert 'data-chart="chord"' in page and "<h2>Transfer flows</h2>" in page and 'data-chart="rings"' not in page
    assert ">$700 · 2 transfers</span>" in page
    shown = _chart_data(page, "tx-flows")
    assert [{k: v for k, v in f.items() if k != "url"} for f in shown["flows"]] == flows["flows"]

    # a flow picks its transactions (user's request 2026-10-31): both halves of a pair, or the account's unmatched
    # transfers in that category; the chart keeps every flow, the picked one marked; again (or the tag's ✕) = all
    def listed(html):
        body = html.split("<tbody>")[1].split("</tbody>")[0]
        return sorted(re.findall(r'<td class="num[^"]*">\s*(\+?\$[\d,.]+)', body))
    pair_url, single_url = (f["url"] for f in shown["flows"])
    assert f"flow=a{CHECKING}%3Ea{CARD}" in pair_url
    pair_page = client.get(pair_url).text
    assert listed(pair_page) == ["$500.00", "+$500.00"] and "Fake Checking → Fake Card" in pair_page
    picked = _chart_data(pair_page, "tx-flows")
    assert picked["picked"] == f"a{CHECKING}>a{CARD}" and len(picked["flows"]) == 2
    assert "flow=" not in picked["flows"][0]["url"]                         # its own flow again: cleared
    single_page = client.get(single_url).text
    assert listed(single_page) == ["$200.00"] and f"Fake Checking → {out_label}" in single_page

    # the card's gear (user's request 2026-10-31): a circle by default, or the flow; saved, and back to the same list
    assert 'data-chart="chord"' in page and 'value="circle" data-autosubmit checked' in page
    back = "/transactions?range=custom&from=2026-09-01&to=2026-09-30&kind=transfer"
    done = client.post("/transactions/transfers-chart", data={"style": "flow", "return_url": back},
                       headers={"origin": "https://localhost:8443"}, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == back
    page = client.get(back).text
    assert 'data-chart="sankey"' in page and 'value="flow" data-autosubmit checked' in page
    client.post("/transactions/transfers-chart", data={"style": "bogus", "return_url": "https://elsewhere.invalid/"},
                headers={"origin": "https://localhost:8443"})
    assert 'data-chart="sankey"' in client.get(back).text                   # junk: ignored, and no leaving the app


def test_the_category_checklist_has_the_main_groups(client, db):
    """User's request 2026-10-30: Spending, Transfers (was "Not counted"), Income above their categories; a group with
    all its categories ticked is ticked and named on the button."""
    page = client.get("/transactions").text
    box = page.split('aria-label="Categories"')[1].split("</details>")[0]
    assert [g.split('data-label="')[1].split('"')[0] for g in box.split('class="check-row check-group"')[1:]] == [
        "Spending", "Transfers", "Income"]
    assert 'value="FOOD_AND_DRINK"' in box.split('data-parent="k-transfer"')[0]                 # under Spending
    assert box.split('value="FOOD_AND_DRINK"')[1].split(">")[0].count('data-child-of="k-expense"') == 1
    transfer_keys = [r[0] for r in db.execute("SELECT key FROM categories WHERE parent_key IS NULL AND kind = 'transfer'")]
    picked = client.get("/transactions?category=" + ",".join(transfer_keys)).text
    box = picked.split('aria-label="Categories"')[1].split("</details>")[0]
    assert 'data-checklist-label>Transfers</summary>' in box
    assert '<input type="checkbox" data-parent="k-transfer" data-label="Transfers" checked>' in box


def test_other_picks_its_categories_and_the_category_checklist_takes_several(client, db):
    """User's request 2026-10-29: clicking Other lists every category in it; the category filter is a checklist like the
    accounts' (several categories, a category's subcategories under it, the category without them)."""
    from fintrack import categories
    from urllib.parse import parse_qs, urlencode, urlparse
    keys = _spend_by_category(db, [50000, 30000, 10000, 5000, 1500, 1000, 800, 700])
    base = "/transactions?range=custom&from=2026-10-01&to=2026-10-31&kind=expense"
    rings = _chart_data(client.get(base).text, "tx-categories")
    other = rings["parents"][-1]
    assert parse_qs(urlparse(other["url"]).query)["category"] == [",".join(sorted(keys[4:]))]
    page = client.get(other["url"]).text
    assert 'data-bulk-total="4"' in page
    picked = _chart_data(page, "tx-categories")
    assert picked["dim"] and [g["lit"] for g in picked["parents"]] == [False] * 4 + [True]
    assert '" class="picked">' in page.split('class="rings-legend')[1]                 # Other's legend entry
    button = page.split('aria-label="Categories"')[1].split("data-checklist-label>")[1].split("</summary>")[0]
    import html
    assert sorted(html.unescape(button).split(", ")) == sorted(m["label"] for m in other["members"])   # the four, by name
    assert parse_qs(urlparse(picked["parents"][-1]["url"]).query).get("category") is None   # again: cleared

    # several boxes, as the checklist sends them; a ticked category's own boxes are dropped (it covers them)
    sub = categories.add_subcategory(db, keys[0], "Fake Sub")
    add(db, CARD, 400, "2026-10-02", key=sub)
    def count(query):
        import re
        return int(re.search(r'data-bulk-total="(\d+)"', client.get(base + query).text).group(1))
    assert count(f"&category={keys[0]}") == 2                                           # with its subcategory
    assert count(f"&category={keys[0]}:self") == 1 and count(f"&category={keys[0]}:{sub}") == 1
    assert count(f"&category={keys[0]}&category={keys[0]}:self&category={keys[0]}:{sub}&category={keys[1]}") == 3
    assert count(f"&category={keys[0]}&only=1") == 1                                    # an older link's "only"
    # the address bar after a checklist change: the short form, not every box the form sent
    sent = client.get(base + f"&q=&category={keys[0]}&category={keys[0]}:self&category={keys[0]}:{sub}&category={keys[1]}",
                      headers={"HX-Request": "true"})
    assert sent.headers["HX-Push-Url"] == "/transactions?" + urlencode(
        {"category": ",".join(sorted(keys[:2])), "kind": "expense", "range": "custom", "from": "2026-10-01",
         "to": "2026-10-31"})
    box = client.get(base + f"&category={keys[0]}:{sub}").text.split('aria-label="Categories"')[1].split("</details>")[0]
    assert "data-checklist-label>Fake Sub</summary>" in box
    assert f'value="{keys[0]}:{sub}" data-child-of="{keys[0]}"\n        data-label="Fake Sub" checked>' in box
    assert f'value="{keys[0]}:self" data-child-of="{keys[0]}"' in box and "No subcategory" in box


# ---------------------------------------------------------------- budgets

def test_budget_bars(db):
    budgets.set_budget(db, "FOOD_AND_DRINK", "2026-10", "310", rollover=False)
    budgets.set_budget(db, "TRAVEL", "2026-10", "1000", rollover=False)
    add(db, CARD, 15500, "2026-10-02")                          # food: half the budget
    add(db, CARD, 9900, "2026-10-03", key="GENERAL_MERCHANDISE")   # unbudgeted: no bar
    view = budgets.month_view(db, "2026-10")
    bars = visuals.budget_bars(view)
    assert [b["label"] for b in bars] == ["Travel", "Food & drink"]   # largest budget first
    assert bars[1]["spent"] == 155.0 and bars[1]["budget"] == 310.0


# ---------------------------------------------------------------- recurring & cash flow

def test_recurring_income_and_expenses_by_day(db):
    recurring.save_item(db, None, name="Fake Payroll", amount_text="2000", direction="in", frequency="monthly",
                        next_date="2026-10-06", account_id=CHECKING, category_key=None)
    recurring.save_item(db, None, name="Fake Gym", amount_text="40", direction="out", frequency="monthly",
                        next_date="2026-10-08", account_id=CARD, category_key="PERSONAL_CARE")
    recurring.save_item(db, None, name="Fake Phone", amount_text="60", direction="out", frequency="monthly",
                        next_date="2026-10-01", account_id=CARD, category_key=None)        # overdue -> today
    recurring.save_item(db, None, name="Fake Membership", amount_text="120", direction="out", frequency="annual",
                        next_date="2027-03-01", account_id=CARD, category_key="PERSONAL_CARE")
    recurring.save_item(db, None, name="Fake Lunch Club", amount_text="10", direction="out", frequency="weekly",
                        next_date="2026-10-09", account_id=CARD, category_key=None)
    # by day of this month (user's request 2026-10-16): each item on its dates, past days too; annual items left out
    bills = visuals.recurring_by_day(db, "bill", today=TODAY)
    assert len(bills["days"]) == 31 and bills["days"][0] == "2026-10-01" and bills["days"][-1] == "2026-10-31"
    by_day = {d[8:]: v for d, v in zip(bills["days"], bills["values"]) if v}
    assert by_day == {"01": 60.0, "02": 10.0, "08": 40.0, "09": 10.0, "16": 10.0, "23": 10.0, "30": 10.0}
    assert bills["items"][7] == [{"name": "Fake Gym", "amount": 40.0}]
    assert bills["total_cents"] == 6000 + 4000 + 5 * 1000                       # no Fake Membership (annual)
    income = visuals.recurring_by_day(db, "income", today=TODAY)
    assert income["values"][5] == 2000.0 and income["total_cents"] == 200000


# ---------------------------------------------------------------- net worth

def test_investments_by_type_labels_merge_carry_and_fold(conn):
    """Portfolio chart: category name > readable Plaid subtype > "Other investments"; same-named types
    merge; days without a sync repeat the day before; a 6th+ type folds into Other."""
    from datetime import date, timedelta

    from fintrack import account_types

    d = lambda n: (date(2026, 1, 1) + timedelta(days=n)).isoformat()   # noqa: E731
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env) VALUES (1, 'i1', 'sandbox')")
    accounts = [(1, "401k", 0), (2, "ira", 0), (3, None, 1), (4, "hsa", 0)]
    for acc_id, subtype, manual_ in accounts:
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, subtype, classification, is_manual)"
                     " VALUES (?, ?, ?, 'Fake', 'investment', ?, 'asset', ?)",
                     (acc_id, None if manual_ else 1, None if manual_ else f"a{acc_id}", subtype, manual_))
    conn.commit()
    node = conn.execute("SELECT n.id FROM account_type_nodes n JOIN account_type_nodes p ON p.id = n.parent_id"
                        " WHERE p.name = 'Retirement' AND n.name = ?", ('401(k)',)).fetchone()[0]
    account_types.assign(conn, 3, node)                    # the manual account given the type Retirement › 401(k)
    snaps = [(d(0), 1, 1000), (d(0), 2, 500), (d(0), 3, 200), (d(0), 4, 100),
             (d(2), 1, 1100), (d(2), 2, 500), (d(2), 3, 200), (d(2), 4, 100)]          # d(1): no sync
    conn.executemany("INSERT INTO balance_snapshots (date, account_id, balance_cents, classification, account_type)"
                     " VALUES (?, ?, ?, 'asset', 'investment')", snaps)
    conn.commit()
    data = visuals.investments_by_type(conn)
    assert data["labels"] == [d(0), d(1), d(2)] and data["carried"] == [False, True, False]
    by_label = {s["label"]: s["values"] for s in data["series"]}
    # your type's "401(k)" merges with Plaid's 401k account's "401(k)"
    assert by_label == {"401(k)": [12.0, 12.0, 13.0], "IRA": [5.0, 5.0, 5.0], "HSA": [1.0, 1.0, 1.0]}
    assert [s["label"] for s in data["series"]] == ["401(k)", "IRA", "HSA"]        # largest first

    for n, subtype in enumerate(["brokerage", "roth", "529", "trust"], start=5):   # 7 types -> fold
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, subtype, classification)"
                     " VALUES (?, 1, ?, 'Fake', 'investment', ?, 'asset')", (n, f"a{n}", subtype))
        conn.execute("INSERT INTO balance_snapshots (date, account_id, balance_cents, classification, account_type)"
                     " VALUES (?, ?, 50, 'asset', 'investment')", (d(2), n))
    conn.commit()
    labels = [s["label"] for s in visuals.investments_by_type(conn)["series"]]
    assert len(labels) == 5 and labels[:3] == ["401(k)", "IRA", "HSA"] and labels[-1] == "Other"


def test_category_shares_combines_only_the_last_five_percent(db):
    from fintrack.spending import CategoryLine

    # $100 / 60 / 40 / 20 / 10 / 4 / 3 / 2 / 1 = $240; 5% = $12: the last four ($10 total) combine, $10 does not fit.
    amounts = [100, 60, 40, 20, 10, 4, 3, 2, 1]
    lines = [CategoryLine(f"CAT{i}", f"Category {i}", a * 100, 1) for i, a in enumerate(amounts)]
    lines.append(CategoryLine("REFUNDED", "Refunded", -500, 1))        # nets negative: not a slice
    shares = visuals.category_shares(lines)
    assert [s["key"] for s in shares["slices"]] == ["CAT0", "CAT1", "CAT2", "CAT3", "CAT4", "_other"]
    assert shares["slices"][-1]["value"] == 10.0                       # 4 + 3 + 2 + 1
    assert shares["slots"]["CAT4"] == "slot-5"
    assert shares["slots"]["CAT5"] == shares["slots"]["CAT8"] == shares["slots"]["REFUNDED"] == "slot-other"


def test_category_shares_a_single_small_category_is_not_called_other(db):
    from fintrack.spending import CategoryLine

    lines = [CategoryLine("BIG", "Big", 10000, 1), CategoryLine("MID", "Mid", 5000, 1), CategoryLine("TINY", "Tiny", 100, 1)]
    shares = visuals.category_shares(lines)
    assert [s["key"] for s in shares["slices"]] == ["BIG", "MID", "TINY"]      # $1 is under 5%, but alone
    assert shares["slots"]["TINY"] == "slot-3"


def test_category_shares_never_uses_more_than_eight_colours(db):
    from fintrack.spending import CategoryLine

    lines = [CategoryLine(f"CAT{i}", f"Category {i}", 1000 - i, 1) for i in range(12)]    # all ~equal: no 5% tail
    shares = visuals.category_shares(lines)
    assert [s["key"] for s in shares["slices"]] == [f"CAT{i}" for i in range(8)] + ["_other"]
    assert shares["slots"]["CAT7"] == "slot-8" and shares["slots"]["CAT8"] == "slot-other"


def test_grouped_category_lines_combine_the_gray_ones_into_other(db):
    from fintrack.spending import CategoryLine

    amounts = [100, 60, 40, 20, 10, 4, 3, 2, 1]                         # the last four make up under 5%
    lines = [CategoryLine(f"CAT{i}", f"Category {i}", a * 100, i + 1, a * 50) for i, a in enumerate(amounts)]
    rows = visuals.grouped_category_lines(lines, visuals.category_shares(lines))
    assert [r.category for r in rows] == ["CAT0", "CAT1", "CAT2", "CAT3", "CAT4", "_other"]
    other = rows[-1]
    assert (other.label, other.amount_cents, other.count, other.previous_cents) == ("Other", 1000, 6 + 7 + 8 + 9, 500)
    assert other.change_cents == 500
    assert sum(r.amount_cents for r in rows) == sum(l.amount_cents for l in lines)      # still adds up


def test_grouped_category_lines_leave_a_single_leftover_alone(db):
    from fintrack.spending import CategoryLine

    lines = [CategoryLine("BIG", "Big", 10000, 1), CategoryLine("REFUNDED", "Refunded", -500, 1)]
    rows = visuals.grouped_category_lines(lines, visuals.category_shares(lines))
    assert [r.category for r in rows] == ["BIG", "REFUNDED"]            # one gray category: no "Other" row


def test_monthly_average_is_total_over_the_ranges_length_in_months_not_the_mean_of_the_bars(db):
    # Jul 1 - Dec 31 2026 = 184 days = 6.045 months. Spending: $600 in July, nothing else but a half month in December.
    add(db, CARD, 60000, "2026-07-10")
    add(db, CARD, 30000, "2026-12-05")
    m = visuals.monthly_spending(db, "2026-07-01", "2026-12-31", with_average=True)
    assert m["values"][0] == 600.0 and len(m["months"]) == 6
    assert m["average"] == round(90000 / (184 / spending.AVG_DAYS_PER_MONTH)) / 100          # ~ $148.89, not 900 / 6 = 150
    assert 148.0 < m["average"] < 149.5
    # a whole year is total / 12 (to the cent)
    add(db, CARD, 120000, "2026-03-03")
    year = visuals.monthly_spending(db, "2026-01-01", "2026-12-31", with_average=True)
    assert abs(year["average"] - 2100.0 / 12) < 0.5
    # off unless asked, and None when nothing was spent
    assert visuals.monthly_spending(db, "2026-01-01", "2026-12-31")["average"] is None
    assert visuals.monthly_spending(db, "2020-01-01", "2020-03-31", with_average=True)["average"] is None


def test_trailing_average_is_the_365_day_monthly_average_of_counted_spending(db):
    today = date(2026, 10, 9)
    add(db, CARD, 365250, "2025-10-10")                       # first day of the window (365 days ending today)
    add(db, CARD, 100000, "2025-10-09")                       # one day too old: not counted
    add(db, CARD, -5250, "2026-03-03")                        # a refund reduces it
    add(db, CHECKING, -999999, "2026-05-05", key="INCOME")    # income isn't spending
    expected = round(360000 / (365 / spending.AVG_DAYS_PER_MONTH))
    assert spending.trailing_average_cents(db, today) == expected
    # the same number the chart's average line shows for the 365 days range
    year = visuals.monthly_spending(db, "2025-10-10", "2026-10-09", with_average=True)
    assert year["average"] == expected / 100
    assert spending.trailing_average_cents(db, date(2020, 1, 1)) is None            # nothing spent then


def test_the_card_can_list_the_top_descriptions(client, db):
    """User's requests 2026-11-01: the gear picks the pie or a top 10 list; each with its share of the net total as a
    number and a bar; one filters the list exactly."""
    origin = {"origin": "https://localhost:8443"}
    for n in range(11):
        db.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name,"
                   " plaid_category_key) VALUES (?, ?, '2026-10-02', ?, ?, 'FOOD_AND_DRINK')",
                   (f"d{n}", CARD, 20000 if n == 0 else 10000 - n * 100, f"FAKE TEXT {n}"))
    db.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key)"
               " VALUES ('refund', ?, '2026-10-03', -1000, 'FAKE ONLY REFUND', 'FOOD_AND_DRINK')", (CARD,))
    db.commit()
    base = "/transactions?kind=expense&range=custom&from=2026-10-01&to=2026-10-31"
    assert "selected>Spending by category</option>" in client.get(base).text         # the default

    def choose(by):
        done = client.post("/transactions/breakdown-chart", data={"by": by, "return_url": base}, headers=origin,
                           follow_redirects=False)
        assert done.status_code == 303 and done.headers["location"] == base
    choose("description")
    page = client.get(base).text
    assert "selected>Spending by description</option>" in page and 'name="by" class="title-select" data-autosubmit' in page
    assert 'aria-label="Chart settings"' not in page.split('<ol class="top-list">')[0].split("tx-charts")[1]  # no gear
    assert 'value="merchant"' not in page and 'data-chart="rings"' not in page
    card = page.split('<ol class="top-list">')[1].split("</ol>")[0]
    assert card.count("<li") == 10 and "FAKE TEXT 0" in card and "FAKE TEXT 10" not in card and "REFUND" not in card
    # net total: 200 + (99 + 98 + ... + 90) = 1,145 - 10 refund = $1,135 (FAKE TEXT 0 = 200 / 1,135 = 18%; a bar of that share)
    assert '<span class="top-list-pct">18%</span>' in card and 'data-share="0.1762"' in card
    assert ">$1,135 · 12</span>" in page and 'title="12 transactions"' in page
    url = html.unescape(re.search(r'<a href="([^"]+)"', card).group(1))
    picked = client.get(url).text
    assert 'data-bulk-total="1"' in picked and 'name="description" value="FAKE TEXT 0"' in picked
    lit = picked.split('<ol class="top-list">')[1].split("</ol>")[0]
    assert lit.count('class="picked"') == 1 and lit.count("<li") == 10      # every row stays to pick from
    assert "description=" not in html.unescape(re.search(r'<a href="([^"]+)"', lit).group(1))  # again: cleared
    choose("merchant")                                                      # gone: ignored
    assert "selected>Spending by description</option>" in client.get(base).text


def test_sorting_and_paging_dont_change_what_sizes_the_columns(client, db):
    """User's request 2026-11-01: the hidden row holds the whole filtered list's longest values, whatever is on this page."""
    from fintrack.web import transactions as page
    add(db, CARD, 987654, "2026-10-02", key="GENERAL_MERCHANDISE")
    add(db, CHECKING, 100, "2026-10-03")
    base = "/transactions?range=custom&from=2026-10-01&to=2026-10-31"
    original, page.PAGE_SIZE = page.PAGE_SIZE, 1
    try:
        sizers = {re.search(r'<tr class="txn-sizer".*?</tr>', client.get(base + q).text, re.S).group(0)
                  for q in ("", "&sort=amount", "&sort=amount&dir=asc", "&sort=account", "&page=2")}
    finally:
        page.PAGE_SIZE = original
    assert len(sizers) == 1 and "$9,876.54" in sizers.pop()


def test_the_summary_says_net_out_when_more_went_out(client, db):
    """The Summary's net (2026-11-11): "Net out" and a plain amount when more went out than came in."""
    add(db, CARD, 9000, "2026-10-01", key="GENERAL_MERCHANDISE")
    add(db, CHECKING, -2000, "2026-10-02", key="INCOME")
    page = client.get("/transactions?range=custom&from=2026-10-01&to=2026-10-31").text
    card = page.split("<h2>Summary</h2>")[1].split("</section>")[0]
    assert ">Net out</span>" in card and ">$70</span>" in card and "−" not in card
