"""The Sandbox demo data (demo.py): the generated world is consistent, the gateway routes demo Items locally,
and `seed` produces a believable ~$500k net worth through the app's real sync. Everything runs in a temp data
folder with an in-memory keyring and no network."""

import json
from datetime import date

import pytest

from fintrack import demo, networth, sync
from fintrack.plaid_client import PlaidError, PlaidGateway

ANCHOR = date(2026, 10, 9)


@pytest.fixture(scope="module")
def dataset():
    return demo.build_dataset(ANCHOR)


# ---------------------------------------------------------------- the generated world

def test_dataset_is_deterministic(dataset):
    again = demo.build_dataset(ANCHOR)
    assert json.dumps(again.server, sort_keys=True) == json.dumps(dataset.server, sort_keys=True)
    assert again.histories == dataset.histories


def test_account_mix_matches_the_real_setup(dataset):
    accounts = [a for item in dataset.server["items"].values() for a in item["accounts"]]
    kinds = sorted((a["type"], a["subtype"]) for a in accounts)
    assert kinds == sorted([("depository", "checking")] + [("credit", "credit card")] * 4
                           + [("investment", "brokerage"), ("investment", "ira"), ("investment", "roth")])
    assert len(dataset.manual) == 3 and all(line for a in dataset.manual for line in a["lines"])
    assert all(len(a["mask"]) == 4 for a in accounts)               # last 4 only


def test_everything_is_obviously_made_up(dataset):
    names = {t["name"] for item in dataset.server["items"].values() for t in item["transactions"]}
    institutions = {item["institution_name"] for item in dataset.server["items"].values()}
    assert all(n.startswith("Demo") for n in institutions)
    assert "DEMO CORP PAYROLL" in names and "RIVERSIDE PROPERTY MGMT RENT" in names


def test_net_worth_target_is_hit_by_construction(dataset):
    assert abs(dataset.expected_net_worth_cents - demo.TARGET_NET_WORTH_CENTS) < 1_000_00      # within $1,000


def test_august_2026_overspends(dataset):
    """The demo has one month that spends more than it earns, to show savings below zero (Savings page)."""
    income = spending = 0
    for item in dataset.server["items"].values():
        for t in item["transactions"]:
            if t["date"].startswith("2026-08"):
                pfc = t["personal_finance_category"]["primary"]
                if pfc == "INCOME":
                    income -= round(t["amount"] * 100)
                elif pfc not in ("LOAN_PAYMENTS", "TRANSFER_OUT", "TRANSFER_IN") or t["name"].startswith("ATM"):   # ATM cash counts
                    spending += round(t["amount"] * 100)
    assert income > 0 and spending > income
    other = [m for m in ("2026-06", "2026-07", "2026-09")]
    for month in other:                                      # the other months stay within income
        month_spend = sum(round(t["amount"] * 100) for item in dataset.server["items"].values() for t in item["transactions"]
                          if t["date"].startswith(month) and t["personal_finance_category"]["primary"] not in ("INCOME", "LOAN_PAYMENTS", "TRANSFER_OUT", "TRANSFER_IN"))
        assert 0 < month_spend < income


def test_transfers_to_and_from_investments_are_labelled_the_way_plaid_does(dataset):
    from fintrack.contributions import INTO_CODE, OUT_CODE

    checking = dataset.server["items"]["credit_union"]["transactions"]
    into = [t for t in checking if t["personal_finance_category"]["detailed"] == INTO_CODE]
    out = [t for t in checking if t["personal_finance_category"]["detailed"] == OUT_CODE]
    assert len(into) >= 21 + 2 and {t["date"][:7] for t in into} >= {"2026-04", "2026-09"}    # monthly + the April IRA lumps
    assert [t["date"][:7] for t in out] == ["2025-12", "2026-08"] and all(t["amount"] < 0 for t in out)   # money in to checking


def test_investment_side_mirrors_the_bank_transfers_and_adds_payroll_money(dataset):
    brokerage = dataset.server["items"]["brokerage"]
    rows = brokerage["investment_transactions"]
    contributions_in = [t for t in rows if t["subtype"] == "contribution"]
    assert all(t["amount"] < 0 for t in contributions_in)                              # Plaid: cash credited = negative
    checking = dataset.server["items"]["credit_union"]["transactions"]
    from collections import Counter

    bank_out = Counter(round(t["amount"] * 100) for t in checking
                       if t["personal_finance_category"]["detailed"] == "TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS")
    mirrored = Counter(-round(t["amount"] * 100) for t in contributions_in if t["name"] == "ELECTRONIC DEPOSIT CONTRIBUTION")
    assert mirrored and not (mirrored - bank_out)                  # every deposit has a bank transfer of the same amount
    assert any(t["name"].startswith("PAYROLL CONTRIBUTION") for t in contributions_in)   # money the bank never saw
    assert [t["subtype"] for t in rows if t["type"] == "cash" and t["amount"] > 0] == ["withdrawal", "withdrawal"]
    assert {t["type"] for t in rows} >= {"buy", "sell", "cash"}                        # trades and dividends are there to be ignored
    assert len({t["investment_transaction_id"] for t in rows}) == len(rows)


def test_balances_agree_with_the_ledger_and_holdings(dataset):
    items = dataset.server["items"]
    for item_key in ("cards_1", "cards_2"):
        for account in items[item_key]["accounts"]:
            ledger = sum(round(t["amount"] * 100) for t in items[item_key]["transactions"] if t["account_id"] == account["account_id"])
            assert round(account["balances"]["current"] * 100) == ledger and ledger >= 0
    holdings = items["brokerage"]["holdings"]["holdings"]
    for account in items["brokerage"]["accounts"]:
        total = sum(round(h["institution_value"] * 100) for h in holdings if h["account_id"] == account["account_id"])
        assert round(account["balances"]["current"] * 100) == total


def test_card_payments_pair_up_exactly(dataset):
    items = dataset.server["items"]
    checking = [t for t in items["credit_union"]["transactions"] if t["name"].startswith("ONLINE PAYMENT TO DEMO CARD")]
    credits = [t for key in ("cards_1", "cards_2") for t in items[key]["transactions"] if t["amount"] < 0]
    assert len(checking) == len(credits) > 40
    assert sorted((t["date"], t["amount"]) for t in checking) == sorted((t["date"], -t["amount"]) for t in credits)


def test_history_is_continuous_into_today(dataset):
    items = dataset.server["items"]
    checking = items["credit_union"]["accounts"][0]
    series = dataset.histories[checking["account_id"]]
    assert series[0][0] == dataset.start.isoformat() and series[-1][0] == "2026-10-08"
    today_flow = sum(round(t["amount"] * 100) for t in items["credit_union"]["transactions"] if t["date"] == ANCHOR.isoformat())
    assert series[-1][1] - today_flow == round(checking["balances"]["current"] * 100)
    assert 2_000_00 < min(v for _d, v in series) and max(v for _d, v in series) < 60_000_00     # a believable range
    for account in items["brokerage"]["accounts"]:
        assert dataset.histories[account["account_id"]][-1][1] <= round(account["balances"]["current"] * 100) * 1.05


def test_only_recent_card_purchases_are_pending(dataset):
    for key in ("cards_1", "cards_2"):
        for t in dataset.server["items"][key]["transactions"]:
            if t["pending"]:
                assert t["amount"] > 0 and (ANCHOR - date.fromisoformat(t["date"])).days <= 1
    assert not any(t["pending"] for t in dataset.server["items"]["credit_union"]["transactions"])


# ---------------------------------------------------------------- the gateway

class RecordingReal:
    def __init__(self):
        self.calls = []

    def get_accounts(self, access_token):
        self.calls.append(("get_accounts", access_token))
        return ["real"]

    def sync_transactions(self, access_token, cursor, count=500):
        self.calls.append(("sync", access_token))
        return "real"


def test_demo_tokens_are_answered_locally_and_others_go_to_plaid(settings, dataset):
    real = RecordingReal()
    gateway = demo.DemoAwareGateway(settings, real=real, server=demo.DemoServer(dataset.server))
    accounts = gateway.get_accounts(demo.ACCESS_PREFIX + "credit_union")
    assert accounts[0]["name"] == "Demo Checking" and real.calls == []
    assert gateway.get_accounts("access-sandbox-abc") == ["real"] and real.calls == [("get_accounts", "access-sandbox-abc")]
    with pytest.raises(PlaidError) as exc:
        gateway.create_link_token("banking", demo.ACCESS_PREFIX + "credit_union")           # update mode on a demo Item
    assert exc.value.code == "DEMO_ITEM"
    with pytest.raises(PlaidError):
        gateway.sandbox_reset_login(demo.ACCESS_PREFIX + "credit_union")


def test_demo_sync_returns_everything_once_then_nothing(settings, dataset):
    server = demo.DemoServer(dataset.server)
    token = demo.ACCESS_PREFIX + "cards_1"
    first = server.sync_transactions(token, None)
    assert len(first["added"]) == len(dataset.server["items"]["cards_1"]["transactions"]) and not first["has_more"]
    again = server.sync_transactions(token, first["next_cursor"])
    assert again["added"] == [] and again["next_cursor"] == first["next_cursor"]
    with pytest.raises(PlaidError):
        server.get_accounts("demo-access-nope")


def test_production_never_gets_the_demo_gateway(prod_settings, settings):
    assert type(demo.make_gateway(prod_settings)) is PlaidGateway
    assert type(demo.make_gateway(settings)) is demo.DemoAwareGateway


def test_missing_data_file_is_a_clear_error(settings):
    with pytest.raises(PlaidError) as exc:
        demo.DemoAwareGateway(settings).get_accounts(demo.ACCESS_PREFIX + "credit_union")
    assert exc.value.code == "DEMO_DATA_MISSING"


# ---------------------------------------------------------------- seeding a database

@pytest.fixture
def seeded(settings, conn, cipher):
    summary = demo.seed(conn, settings, cipher)
    return conn, summary, settings, cipher


def test_seed_builds_a_500k_sandbox(seeded):
    conn, summary, *_ = seeded
    assert 480_000_00 < summary["net_worth_cents"] < 520_000_00
    assert summary["items"] == 4 and summary["plaid_accounts"] == 8 and summary["manual_accounts"] == 3
    mix = conn.execute("SELECT is_manual, type, COUNT(*) FROM accounts GROUP BY 1, 2 ORDER BY 1, 2").fetchall()
    assert [tuple(r) for r in mix] == [(0, "credit", 4), (0, "depository", 1), (0, "investment", 3), (1, "investment", 3)]
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] > 1_500
    assert conn.execute("SELECT COUNT(*) FROM items WHERE status = 'ok' AND plaid_env = 'sandbox'").fetchone()[0] == 4


def test_seed_goes_through_the_real_pipeline(seeded):
    conn, *_ = seeded
    # card payments were matched as transfers by the real matcher, and paychecks count as income
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE is_transfer = 1").fetchone()[0] > 80
    kinds = {r["kind"]: r["n"] for r in conn.execute("SELECT kind, COUNT(*) n FROM transactions_effective GROUP BY kind")}
    assert kinds["expense"] > 1_000 and kinds["income"] >= 40 and kinds["transfer"] > 80
    # spending lands in several categories, not one
    assert conn.execute("SELECT COUNT(DISTINCT category) FROM transactions_effective WHERE kind = 'expense'").fetchone()[0] >= 10
    # holdings, card statements and recurring items exist
    assert conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] > 20
    assert conn.execute("SELECT COUNT(*) FROM card_statements").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM recurring_items WHERE status = 'confirmed'").fetchone()[0] >= 8
    assert conn.execute("SELECT COUNT(*) FROM recurring_items WHERE status = 'detected'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM budgets").fetchone()[0] >= 8


def test_seed_has_a_year_of_net_worth_history(seeded):
    conn, summary, *_ = seeded
    history = networth.history(conn)
    assert len(history) > 600
    assert history[0].net_cents < history[-1].net_cents                 # it grew...
    # ...by what saving and the market can explain (about $190k over the history), not by 2.7x
    assert 0.55 * summary["net_worth_cents"] < history[0].net_cents < 0.80 * summary["net_worth_cents"]
    assert abs(history[-1].net_cents - summary["net_worth_cents"]) < 10_00


def test_manual_accounts_have_positions_and_values(seeded):
    conn, *_ = seeded
    for row in conn.execute("SELECT * FROM accounts WHERE is_manual = 1"):
        assert row["current_balance_cents"] and row["current_balance_cents"] > 20_000_00
        assert conn.execute("SELECT COUNT(*) FROM manual_positions WHERE account_id = ?", (row["id"],)).fetchone()[0] >= 2
        assert conn.execute("SELECT COUNT(*) FROM manual_valuations WHERE account_id = ?", (row["id"],)).fetchone()[0] > 100


def test_the_transfers_chart_counts_each_contribution_once_on_the_seeded_database(seeded):
    from fintrack import contributions

    conn, *_ = seeded
    assert conn.execute("SELECT COUNT(*) FROM investment_transactions").fetchone()[0] > 60
    flows = {m.month: (m.into_cents, m.out_cents) for m in contributions.monthly(conn, 24)}
    # $1,850 transfer (bank side + the deposit it made, once) + $400 payroll contribution the bank never saw
    assert flows["2026-09"] == (185_000 + 40_000, 0)
    assert flows["2026-04"] == (185_000 + 700_000 + 40_000, 0)                          # plus the yearly Roth IRA contribution
    assert flows["2026-08"] == (185_000 + 40_000, 300_000)                              # the withdrawal pairs up too: once
    assert flows["2025-12"][1] == 120_000


def test_syncing_again_is_quiet(seeded):
    conn, summary, settings, cipher = seeded
    before = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    result = sync.sync_all(conn, demo.DemoAwareGateway(settings), cipher, settings.home)   # reads the saved data file
    assert not result.errors and sum(r.added for r in result.results) == 0
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == before
    assert abs(networth.current_net_worth(conn).net_cents - summary["net_worth_cents"]) < 10_00


def test_data_file_is_private_and_outside_the_repo(seeded):
    *_, settings, _cipher = seeded
    path = demo.data_path(settings.home)
    assert path.exists() and oct(path.stat().st_mode & 0o777) == "0o600"
    assert "finance_tracker" not in str(path)


def test_seed_refuses_a_database_with_data_and_production(seeded, prod_settings, conn):
    conn, _summary, settings, cipher = seeded
    with pytest.raises(demo.DemoError, match="--replace"):
        demo.seed(conn, settings, cipher)
    with pytest.raises(demo.DemoError, match="Sandbox only"):
        demo.seed(conn, prod_settings, cipher)
