"""Manual and connected accounts alike (user's rule 2026-11-14, docs/parity-plan.md): the same activity in a connected
and a manual account of each kind must come out the same everywhere it's shown or counted. Fake data only."""

from datetime import date

import pytest

from fintrack import balances, contributions, investment_activity, items, manual, spending
from fintrack.imports import engine

CARD_FILE = """Date,Description,Amount,Category
2026-09-02,FAKE GROCER,42.00,Groceries
2026-09-05,FAKE DINER,25.00,Dining
2026-09-20,FAKE PAYMENT THANK YOU,-60.00,
"""
ACTIVITY_FILE = """Date,Action,Symbol,Description,Quantity,Price,Amount
09/15/2026,Contribution,FAKEX,FAKE TARGET FUND,10,50.00,500.00
09/16/2026,Dividend,FAKEX,FAKE TARGET FUND,,,12.00
09/16/2026,Reinvestment,FAKEX,FAKE TARGET FUND,0.2,60.00,-12.00
"""
ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def pairs(conn):
    """{kind: (connected account id, manual account id)}, each pair given the same file."""
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    for acc_id, kind, subtype in ((1, "credit", None), (2, "investment", "401k")):
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, mask, type, subtype, classification)"
                     " VALUES (?, 1, ?, ?, '0000', ?, ?, ?)",
                     (acc_id, f"acc-{acc_id}", f"Fake connected {kind}", kind, subtype, items.classify(kind)))
    conn.commit()
    card = manual.create_account(conn, "Fake manual credit", "credit", "0", "2026-09-01")
    k401 = manual.create_account(conn, "Fake manual investment", "investment", "0", "2026-09-01")
    conn.execute("UPDATE accounts SET mask = '0000' WHERE id = ?", (card,))
    conn.execute("UPDATE accounts SET subtype = '401k' WHERE id = ?", (k401,))
    conn.commit()
    for account in (1, card):
        engine.apply(conn, engine.prepare(conn, account, CARD_FILE.encode(), "fake.csv", sign=1))
    for account in (2, k401):
        engine.apply(conn, engine.prepare(conn, account, ACTIVITY_FILE.encode(), "fake.csv"))
    return {"credit": (1, card), "investment": (2, k401)}


def strip(rows, *drop):
    return [{k: v for k, v in dict(r).items() if k not in drop} for r in rows]


def test_a_card_is_a_card(conn, pairs):
    connected, mine = pairs["credit"]
    shown = [strip(conn.execute("""SELECT date, description, amount_cents, category, kind FROM transactions_effective
                                   WHERE account_id = ? ORDER BY date""", (a,)).fetchall()) for a in (connected, mine)]
    assert shown[0] == shown[1] and len(shown[0]) == 3
    by_card = {c.account_id: (c.spending_cents, sorted(str(line) for line in c.lines))
               for c in spending.card_spending(conn, "2026-09-01", "2026-09-30")}
    assert by_card[connected] == by_card[mine]                                     # Credit cards page: the same


def test_an_investment_account_is_an_investment_account(conn, pairs):
    connected, mine = pairs["investment"]
    sql, params = investment_activity.as_transactions(conn)
    shown = [strip(conn.execute(f"SELECT date, description, amount_cents, category, kind FROM ({sql})"
                                " WHERE account_id = ? ORDER BY date, amount_cents", params + [a]).fetchall())
             for a in (connected, mine)]
    assert shown[0] == shown[1] and len(shown[0]) == 4                             # contribution, buy, dividend, buy
    flow = {f.month: f for f in contributions.between(conn, date(2026, 9, 1), date(2026, 9, 30), to_bank=False)}["2026-09"]
    assert flow.by_account["retirement"][connected] == flow.by_account["retirement"][mine] == 50000
    activity = balances.recent_activity(conn, "retirement", "2026-09-01", "2026-09-30", limit=50)
    seen = {name: sorted((a.date, a.info, a.amount_cents) for a in activity if a.source == name)
            for name in ("Fake connected investment", "Fake manual investment")}
    assert seen["Fake connected investment"] == seen["Fake manual investment"] and seen["Fake manual investment"]


def test_their_pages_show_the_same(client, conn, pairs):
    for connected, mine in pairs.values():
        pages = [client.get(f"/accounts/{a}").text for a in (connected, mine)]
        for page in pages:
            assert 'id="transactions"' in page and "Import a file" in page
        rows = [p.split('id="transactions"')[1].count("<tr") for p in pages]
        assert rows[0] == rows[1]


@pytest.fixture
def client(settings, conn):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher, ensure_key
    from fintrack.web.app import create_app
    from tests.fakes import FakeGateway

    ensure_key(settings.home)
    return TestClient(create_app(settings, gateway_factory=lambda s: FakeGateway(), cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")
