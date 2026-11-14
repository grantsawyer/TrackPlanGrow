"""The Savings page's columns and rate (user's request 2026-10-11): pre-tax contributions on a manual retirement
account (its activity since 2026-11-14, as a connected one's), retirement vs investment savings, net cash flow, and
the pairing rules in contributions.py that feed them. All amounts are made up."""

from datetime import date

import pytest

from fintrack import contributions, items, manual_positions, savings, sync
from fintrack.contributions import INTO_CODE

TODAY = date(2026, 10, 9)
CHECKING, BROKERAGE, IRA, MANUAL_401K = 1, 2, 3, 4
ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name, products)"
                 " VALUES (1, 'i1', 'sandbox', 'Fake Bank', '[\"investments\", \"transactions\"]')")
    for acc_id, account_type, subtype in [(CHECKING, "depository", "checking"), (BROKERAGE, "investment", "brokerage"),
                                          (IRA, "investment", "ira")]:
        conn.execute(
            "INSERT INTO accounts (id, item_id, plaid_account_id, name, type, subtype, classification)"
            " VALUES (?, 1, ?, ?, ?, ?, ?)",
            (acc_id, f"acc-{acc_id}", f"Fake {subtype}", account_type, subtype, items.classify(account_type)))
    conn.execute("INSERT INTO accounts (id, name, type, subtype, classification, is_manual)"
                 " VALUES (?, 'Fake 401k', 'investment', '401k', 'asset', 1)", (MANUAL_401K,))
    conn.commit()
    return conn


_ids = iter(range(1, 100_000))


def bank(conn, cents, day, key, detailed=None, user_category=None):
    """A checking transaction (Plaid sign: positive = money out)."""
    conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key,"
        " plaid_category_detailed, user_category) VALUES (?, ?, ?, ?, 'FAKE', ?, ?, ?)",
        (f"b{next(_ids)}", CHECKING, day, cents, key, detailed, user_category))
    conn.commit()


def inv(account, day, name, subtype, amount):
    """An investment cash transaction as the SDK returns it (Plaid investment sign: negative = cash credited)."""
    return {"investment_transaction_id": f"t{next(_ids)}", "account_id": f"acc-{account}", "security_id": None,
            "date": date.fromisoformat(day), "name": name, "quantity": 0.0, "price": 0.0, "fees": 0.0,
            "amount": amount, "type": "cash", "subtype": subtype, "iso_currency_code": "USD"}


@pytest.fixture
def september(db):
    """One month of made-up activity covering every path."""
    bank(db, -500000, "2026-09-15", "INCOME")                                       # $5,000 paycheck
    bank(db, 200000, "2026-09-10", "FOOD_AND_DRINK")                                # $2,000 spent
    bank(db, 100000, "2026-09-05", "TRANSFER_OUT", detailed=INTO_CODE)              # $1,000 to the brokerage...
    bank(db, -300000, "2026-09-22", "INCOME", detailed="INCOME_SALARY",              # a $3,000 brokerage withdrawal that
         user_category="TRANSFER_IN")                                               # Plaid filed as pay; you: a transfer
    sync.store_investment_transactions(db, 1, [
        inv(BROKERAGE, "2026-09-06", "Funds Received", "deposit", -1000.0),          # ...arrives
        inv(BROKERAGE, "2026-09-19", "Withdrawal", "withdrawal", 3000.0),
        inv(BROKERAGE, "2026-09-19", "Sweep out", "deposit", -3000.0),              # internal cash sweep: ignored
        inv(BROKERAGE, "2026-09-25", "Withdrawal", "withdrawal", 700.0),            # brokerage -> IRA
        inv(IRA, "2026-09-26", "Contribution", "contribution", -700.0),
        inv(IRA, "2026-09-28", "EMPLOYER CONTRIBUTION", "contribution", -250.0),    # seen only inside the IRA
    ])
    # the manual 401(k): a fund tracked through a fake ticker, then a $400 contribution from pay
    db.execute("INSERT INTO ticker_prices (ticker, price_date, price, source) VALUES ('FAKX', '2026-08-01', 40.0, 'you')")
    line = manual_positions.add_line(db, MANUAL_401K, "FAKX", "Fake Index Fund", "fund", "$4,000", "2026-08-01",
                                     mode="opening")
    # its contribution is activity, as a connected 401(k)'s (ledger.py; docs/parity-plan.md, 2026-11-14)
    from fintrack import ledger
    ledger.add(db, MANUAL_401K, "contribution", "2026-09-12", str(line), "", "", "", "400", "Contribution from pay")
    return db


# ---------------------------------------------------------------- pairing (contributions.py)

def test_each_flow_is_classified_by_what_it_pairs_with(september):
    flow = {f.month: f for f in contributions.between(september, date(2026, 9, 1), date(2026, 9, 30))}["2026-09"]
    # the manual 401(k)'s $400 from pay is activity like the IRA's employer money (2026-11-14): seen only inside it
    assert flow.into == {"retirement": 70000 + 25000 + 40000, "investment": 100000}
    assert flow.out == {"retirement": 0, "investment": 300000 + 70000}
    assert flow.bank_net == 100000 - 300000           # the $1,000 in and the $3,000 back, both through checking
    assert flow.outside_into == 25000 + 40000         # new money: the employer's and the 401(k)'s; the $700 move isn't


def test_an_unmatched_transfer_in_checking_is_not_an_investment_flow(db):
    bank(db, -123400, "2026-09-22", "INCOME", detailed="INCOME_SALARY", user_category="TRANSFER_IN")
    flows = contributions.between(db, date(2026, 9, 1), date(2026, 9, 30))
    assert (flows[0].into_cents, flows[0].out_cents, flows[0].bank_net) == (0, 0, 0)


# ---------------------------------------------------------------- the month's columns and rate (savings.py)

def test_columns_add_up_to_the_new_rate(september):
    m = {s.month: s for s in savings.range_series(september, "2026-09-01", "2026-09-30", TODAY)}["2026-09"]
    assert (m.income_cents, m.spending_cents) == (500000, 200000)
    assert m.pretax_cents == 40000 + 25000                               # recorded + employer money seen in the IRA
    assert m.retirement_cents == 40000 + 70000 + 25000
    assert m.investment_cents == 100000 - 70000                          # in from checking, moved on to the IRA
    assert m.net_cash_cents == 500000 - 200000 - 100000                  # the $3,000 back to checking is left out
    assert m.saved_cents == m.retirement_cents + m.investment_cents + m.net_cash_cents == 365000
    assert m.total_income_cents == 565000
    assert m.rate == pytest.approx(365000 / 565000)


def test_without_pretax_or_investment_flows_the_rate_is_unchanged(db):
    bank(db, -500000, "2026-09-15", "INCOME")
    bank(db, 200000, "2026-09-10", "FOOD_AND_DRINK")
    m = savings.range_series(db, "2026-09-01", "2026-09-30", TODAY)[0]
    assert (m.saved_cents, m.rate) == (300000, pytest.approx(0.6))


# ---------------------------------------------------------------- the page

@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_the_page_shows_the_columns_and_no_pretax_calculator(client, september):
    page = client.get("/savings?range=ytd").text
    assert ">Retirement<" in page and ">Net cash<" in page and ">Pre-tax<" in page
    assert "Pre-tax contributions" not in page                          # replaced by recorded events (2026-10-12)
    # no purpose accounts here: the Other assets card and column are hidden (user's request 2026-10-12)
    table = page.split('id="savings-table"')[1].split("</table>")[0]   # the table (a phone-only copy follows it)
    assert table.count(">% inc.</th>") == 4                    # after Spending, Retirement, Investment, Net cash
    assert ">Other assets</th>" not in page and ">Purposes</th>" not in page
    assert 'class="cards n4"' in page and "<h2>Other assets</h2>" not in page and "<h2>Net cash</h2>" in page   # + Net savings
    assert ">35%<" in page                                                # September's spending: 2,000 / 5,650


def test_other_transfers_out_of_checking_reduce_net_cash(september):
    bank(september, 50000, "2026-09-18", "TRANSFER_OUT", detailed="TRANSFER_OUT_TRANSFER_OUT_FROM_APPS")   # $500 Zelle out
    bank(september, -10000, "2026-09-19", "TRANSFER_IN", detailed="TRANSFER_IN_TRANSFER_IN_FROM_APPS")     # $100 back
    bank(september, 80000, "2026-09-20", "CREDIT_CARD_PAYMENT")                                          # card payment: no
    m = {s.month: s for s in savings.range_series(september, "2026-09-01", "2026-09-30", TODAY)}["2026-09"]
    # the paired brokerage withdrawal (a transfer you recategorized) is an investment move, not counted again
    assert m.other_transfers_cents == 50000 - 10000
    assert m.net_cash_cents == 500000 - 200000 - 100000 - 40000
    assert m.saved_cents == 365000 - 40000


def test_retirement_chart_splits_the_retirement_share_by_account_type(september):
    from fintrack import visuals

    series = savings.range_series(september, "2026-09-01", "2026-09-30", TODAY)
    chart = visuals.savings_mix(september, series, "2026-09-01", "2026-09-30", "retirement")
    by_label = {s["label"]: s for s in chart["series"]}
    assert list(by_label) == ["401(k)", "IRA"]                           # fixed alphabetical order
    assert by_label["401(k)"]["dollars"] == [400.0]                      # the recorded contribution
    assert by_label["IRA"]["dollars"] == [700.0 + 250.0]                 # moved in from the brokerage + employer money
    assert by_label["401(k)"]["values"] == [7.1] and by_label["IRA"]["values"] == [16.8]   # of $5,650 total income
    # the stack's top is the table's Retirement % inc.
    assert sum(s["dollars"][0] for s in chart["series"]) * 100 == series[0].retirement_cents
    # the card's big figure: total retirement savings / total income over the period
    assert (chart["saved_cents"], chart["income_cents"]) == (135000, 565000)
    assert chart["average"] == 23.9


def test_investment_chart_splits_the_investment_share_by_account(september):
    from fintrack import visuals

    series = savings.range_series(september, "2026-09-01", "2026-09-30", TODAY)
    chart = visuals.savings_mix(september, series, "2026-09-01", "2026-09-30", "investment")
    assert [s["label"] for s in chart["series"]] == ["Fake Bank"]       # by institution; every flow paired there
    assert chart["series"][0]["dollars"] == [(100000 - 70000) / 100]    # in, moved on to the IRA
    assert chart["saved_cents"] == series[0].investment_cents           # the stack's top = the table's Investment
    assert chart["average"] == round(30000 / 565000 * 100, 1)


def test_an_unmatched_bank_transfer_is_its_own_account_unknown_segment(db):
    from fintrack import visuals

    bank(db, 50000, "2026-09-05", "TRANSFER_OUT", detailed=INTO_CODE)      # no brokerage row to pair with
    bank(db, -500000, "2026-09-15", "INCOME")
    series = savings.range_series(db, "2026-09-01", "2026-09-30", TODAY)
    chart = visuals.savings_mix(db, series, "2026-09-01", "2026-09-30", "investment")
    assert [(s["label"], s["dollars"]) for s in chart["series"]] == [("Account unknown", [500.0])]


def test_net_cash_card_matches_the_table(september):
    from fintrack import visuals

    series = savings.range_series(september, "2026-09-01", "2026-09-30", TODAY)
    chart = visuals.net_cash_mix(series)
    assert [s["label"] for s in chart["series"]] == ["Net cash"]
    assert chart["legend"] is False                                      # the card's title names the one series
    assert chart["saved_cents"] == series[0].net_cash_cents == 200000      # the card's Total = the Net cash column
    assert chart["series"][0]["values"] == [round(200000 / 565000 * 100, 1)]
    assert chart["average"] == round(200000 / 565000 * 100, 1)


def test_day_rows_add_up_to_the_month_and_daily_bars_to_the_average(september):
    from fintrack import visuals

    month = savings.range_series(september, "2026-09-01", "2026-09-30", TODAY)[0]
    days = savings.range_series(september, "2026-09-01", "2026-09-30", TODAY, by_day=True)
    assert len(days) == 30 and days[0].month == "2026-09-01" and days[-1].month == "2026-09-30"
    for field in ("income_cents", "spending_cents", "pretax_cents", "retirement_cents", "investment_cents",
                  "bank_invested_cents", "other_transfers_cents", "net_cash_cents", "saved_cents"):
        assert sum(getattr(d, field) for d in days) == getattr(month, field), field
    flows = {f.month: f for f in contributions.between(september, date(2026, 9, 1), date(2026, 9, 30), by_day=True)}
    assert flows["2026-09-06"].into["investment"] == 100000                # the brokerage deposit, on its own day
    for kind in ("retirement", "investment"):
        monthly = visuals.savings_mix(september, [month], "2026-09-01", "2026-09-30", kind)
        daily = visuals.savings_mix(september, days, "2026-09-01", "2026-09-30", kind, by_day=True)
        assert (daily["saved_cents"], daily["average"]) == (monthly["saved_cents"], monthly["average"])
        total = sum(v or 0 for s in daily["series"] for v in s["values"])
        assert total == pytest.approx(daily["average"], abs=0.1 * len(days))   # each bar rounded to a tenth
    cash = visuals.net_cash_mix(days, by_day=True)
    assert cash["saved_cents"] == month.net_cash_cents and cash["labels"][5] == "2026-09-06"



def test_cards_and_columns_without_amounts_are_hidden(client, db):
    bank(db, -500000, "2026-09-15", "INCOME")
    bank(db, 200000, "2026-09-10", "FOOD_AND_DRINK")
    page = client.get("/savings?range=custom&from=2026-09-01&to=2026-09-30").text
    assert 'class="cards n2"' in page and "<h2>Net cash</h2>" in page                  # Net savings + Net cash always show
    for gone in ("Retirement savings", "Investment savings", "Other assets"):
        assert f"<h2>{gone}</h2>" not in page
    for gone in (">Retirement</th>", ">Investment</th>", ">Other assets</th>", ">Pre-tax</th>"):
        assert gone not in page
    table = page.split('id="savings-table"')[1].split("</table>")[0]   # the table (a phone-only copy follows it)
    assert table.count(">% inc.</th>") == 2                                             # Spending, Net cash



def test_the_savings_view_leaves_out_money_from_investments_to_the_bank(september):
    # user's request 2026-10-12: Savings = saved from income; the $3,000 brokerage -> checking withdrawal is left out of
    # both Investment and Net cash; money into investments still counts
    from fintrack import visuals

    saving = savings.range_series(september, "2026-09-01", "2026-09-30", TODAY)[0]
    assert saving.investment_cents == 100000 - 70000                       # in from checking, moved on to the IRA
    assert saving.net_cash_cents == 500000 - 200000 - 100000               # no +$3,000 from the brokerage
    assert saving.saved_cents == 365000
    chart = visuals.savings_mix(september, [saving], "2026-09-01", "2026-09-30", "investment")
    assert chart["saved_cents"] == saving.investment_cents


def test_savings_and_balances_pages(client, september):
    savings_page = client.get("/savings?range=custom&from=2026-09-01&to=2026-09-30").text
    balances_page = client.get("/accounts?range=custom&from=2026-09-01&to=2026-09-30").text
    assert 'action="/savings"' in savings_page and 'action="/accounts"' in balances_page
    assert "<title>Balances · Track Plan Grow</title>" in balances_page
    assert 'href="/accounts"' in balances_page and 'href="/networth"' in balances_page   # tabs of the Accounts section
    # Balances shows the change in net worth (balances.py, 2026-10-12), not savings rates; the Change in value table is
    # on Net worth since 2026-10-13
    assert "<h2>Net worth</h2>" in balances_page and "Net savings rate" not in balances_page
    assert "<h2>Change in value</h2>" in client.get("/networth?range=custom&from=2026-09-01&to=2026-09-30").text
    assert "Net savings rate" in savings_page and "$300" in savings_page



def test_net_savings_card_adds_up_the_parts(september, client):
    from fintrack import visuals

    series = savings.range_series(september, "2026-09-01", "2026-09-30", TODAY)
    chart = visuals.net_savings_mix(series)
    assert [s["label"] for s in chart["series"]] == ["Retirement", "Investment", "Net cash"]
    assert chart["saved_cents"] == series[0].saved_cents == 365000
    assert sum(s["values"][0] for s in chart["series"]) == pytest.approx(series[0].rate * 100, abs=0.2)  # = Rate
    assert chart["average"] == round(365000 / 565000 * 100, 1)
    # each part in its kind's colour, the same colour as the kind's own card
    assert chart["colors"] == [visuals.KIND_COLORS[k] for k in ("retirement", "investment", "net_cash")]
    assert visuals.savings_mix(september, series, "2026-09-01", "2026-09-30", "investment")["hue"] \
        == chart["colors"][1] and visuals.net_cash_mix(series)["hue"] == chart["colors"][2]
    page = client.get("/savings?range=custom&from=2026-09-01&to=2026-09-30").text
    assert page.index("<h2>Net savings</h2>") < page.index("<h2>Retirement savings</h2>")       # above the cards
