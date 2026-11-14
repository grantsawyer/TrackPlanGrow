"""Manual accounts: values, net worth, snapshot history, archive/delete, pages."""

from datetime import date, timedelta

import pytest

from fintrack import db, manual, networth

TODAY = date.today()
D = lambda days_ago: (TODAY - timedelta(days=days_ago)).isoformat()  # noqa: E731


def balance(conn, account_id):
    return conn.execute("SELECT current_balance_cents FROM accounts WHERE id = ?", (account_id,)).fetchone()[0]


def seed_history(conn, *days_ago):
    """Past snapshot days, as earlier syncs would have left them: they contain a
    (fake) Plaid checking account. A snapshot with no accounts writes no rows, so
    that day wouldn't exist."""
    if not conn.execute("SELECT 1 FROM accounts WHERE plaid_account_id = 'acc-baseline'").fetchone():
        conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env) VALUES (99, 'item-baseline', 'sandbox')")
        conn.execute(
            "INSERT INTO accounts (item_id, plaid_account_id, name, type, classification, current_balance_cents)"
            " VALUES (99, 'acc-baseline', 'Fake Checking', 'depository', 'asset', 100000)"
        )
        conn.commit()
    for n in days_ago:
        networth.take_snapshot(conn, TODAY - timedelta(days=n))


def snapshot_rows(conn, account_id):
    return conn.execute(
        "SELECT date, balance_cents FROM balance_snapshots WHERE account_id = ? ORDER BY date", (account_id,)
    ).fetchall()


# ---------------------------------------------------------------- parsing and validation

@pytest.mark.parametrize("text, cents", [("18500", 1850000), ("$18,500", 1850000), ("18,500.55", 1850055), ("0", 0)])
def test_parse_money(text, cents):
    assert manual.parse_money(text) == cents


@pytest.mark.parametrize("text", ["-5", "abc", ""])
def test_parse_money_rejects(text):
    with pytest.raises(manual.ManualError):
        manual.parse_money(text)


def test_future_dates_rejected(conn):
    with pytest.raises(manual.ManualError, match="future"):
        manual.create_account(conn, "Car", "property", "18000", (TODAY + timedelta(days=1)).isoformat())


def test_name_and_kind_required(conn):
    with pytest.raises(manual.ManualError):
        manual.create_account(conn, " ", "property", "1", D(0))
    with pytest.raises(manual.ManualError):
        manual.create_account(conn, "Car", "spaceship", "1", D(0))


# ---------------------------------------------------------------- net worth

def test_assets_add_liabilities_subtract(conn):
    manual.create_account(conn, "Home", "property", "400,000", D(0))
    manual.create_account(conn, "Car loan from family", "other_liability", "5,000", D(0))
    manual.create_account(conn, "Cash jar", "cash", "250", D(0))
    nw = networth.current_net_worth(conn)
    assert (nw.assets_cents, nw.liabilities_cents, nw.net_cents) == (40025000, 500000, 39525000)
    labels = {t.label: t.total_cents for t in nw.by_type}
    assert labels == {"Cash": 25000, "Property": 40000000, "Other liabilities": 500000}


def test_current_value_is_latest_by_date(conn):
    car = manual.create_account(conn, "Car", "property", "18000", D(10))
    manual.add_valuation(conn, car, "17500", D(2))
    assert balance(conn, car) == 1750000
    manual.add_valuation(conn, car, "19000", D(30))      # an older value doesn't become current
    assert balance(conn, car) == 1750000


def test_duplicate_date_rejected(conn):
    car = manual.create_account(conn, "Car", "property", "18000", D(5))
    with pytest.raises(manual.ManualError, match="already a value"):
        manual.add_valuation(conn, car, "17000", D(5))


# ---------------------------------------------------------------- history

def test_backdated_values_fill_existing_snapshot_days(conn):
    seed_history(conn, 10, 6, 3)                         # history from earlier syncs
    car = manual.create_account(conn, "Car", "property", "18000", D(8))
    manual.add_valuation(conn, car, "17000", D(4))
    rows = [tuple(r) for r in snapshot_rows(conn, car)]
    # Day -10 is before the first value: no row. -6 uses the -8 value, -3 and today use -4's.
    assert rows == [(D(6), 1800000), (D(3), 1700000), (D(0), 1700000)]


def test_value_before_first_snapshot_creates_no_new_days(conn):
    seed_history(conn, 2)
    manual.create_account(conn, "Car", "property", "18000", D(100))
    days = [r[0] for r in conn.execute("SELECT DISTINCT date FROM balance_snapshots ORDER BY date")]
    assert days == [D(2), D(0)]


def test_editing_and_deleting_values_rebuild_history(conn):
    seed_history(conn, 5)
    car = manual.create_account(conn, "Car", "property", "18000", D(5))
    newer = conn.execute("SELECT id FROM manual_valuations WHERE account_id = ?", (car,)).fetchone()[0]
    manual.add_valuation(conn, car, "16000", D(1))
    manual.update_valuation(conn, newer, "18500", D(5))
    assert [tuple(r) for r in snapshot_rows(conn, car)] == [(D(5), 1850000), (D(0), 1600000)]

    latest = conn.execute("SELECT id FROM manual_valuations WHERE account_id = ? AND date = ?", (car, D(1))).fetchone()[0]
    manual.delete_valuation(conn, latest)
    assert balance(conn, car) == 1850000
    assert [tuple(r) for r in snapshot_rows(conn, car)] == [(D(5), 1850000), (D(0), 1850000)]


def test_cannot_delete_only_value(conn):
    car = manual.create_account(conn, "Car", "property", "18000", D(0))
    only = conn.execute("SELECT id FROM manual_valuations WHERE account_id = ?", (car,)).fetchone()[0]
    with pytest.raises(manual.ManualError, match="only value"):
        manual.delete_valuation(conn, only)


def test_archive_keeps_history_and_drops_today(conn):
    seed_history(conn, 3)
    car = manual.create_account(conn, "Car", "property", "18000", D(3))
    manual.archive_account(conn, car)
    assert [r[0] for r in snapshot_rows(conn, car)] == [D(3)]      # past kept, today gone
    assert networth.current_net_worth(conn).net_cents == 100000    # only the baseline checking
    manual.archive_account(conn, car, archived=False)
    assert [r[0] for r in snapshot_rows(conn, car)] == [D(3), D(0)]


def test_delete_permanently_needs_name_and_removes_everything(conn):
    seed_history(conn, 3)
    car = manual.create_account(conn, "Car", "property", "18000", D(3))
    with pytest.raises(manual.ManualError):
        manual.delete_account(conn, car, "truck")
    manual.delete_account(conn, car, " car ")
    assert snapshot_rows(conn, car) == []
    assert conn.execute("SELECT COUNT(*) FROM manual_valuations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM accounts WHERE is_manual = 1").fetchone()[0] == 0


def test_changing_kind_moves_it_in_the_breakdown(conn):
    acct = manual.create_account(conn, "Thing", "other_asset", "1000", D(0))
    manual.update_account(conn, acct, "Boat", "property")
    labels = {t.label for t in networth.current_net_worth(conn).by_type}
    assert labels == {"Property"}
    assert manual.kind_of(manual.get_account(conn, acct)) == "property"


def test_sync_never_touches_manual_accounts(conn, settings, gateway, cipher):
    from fintrack import items, sync

    car = manual.create_account(conn, "Car", "property", "18000", D(0))
    session_id, _ = items.start_create_session(conn, settings, gateway, "banking")
    items.complete_create(conn, settings, gateway, cipher, session_id, "public-sandbox-fake")
    sync.sync_all(conn, gateway, cipher, settings.home)
    assert balance(conn, car) == 1800000
    assert (D(0), 1800000) in [tuple(r) for r in snapshot_rows(conn, car)]


def test_revision_rises_on_value_change(conn):
    car = manual.create_account(conn, "Car", "property", "18000", D(1))
    before = db.revision(conn)
    manual.add_valuation(conn, car, "17000", D(0))
    assert db.revision(conn) > before


# ---------------------------------------------------------------- pages

ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def client(settings, conn, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_create_edit_and_delete_via_forms(client, conn):
    bad = client.post("/manual", data={"name": "Car", "kind": "property", "value": "-1", "date": D(0)},
                      headers=ORIGIN, follow_redirects=False)
    assert "/manual/new?" in bad.headers["location"] and "name=Car" in bad.headers["location"]  # input kept

    created = client.post("/manual", data={"name": "Car", "kind": "property", "value": "$18,000", "date": D(0)},
                          headers=ORIGIN, follow_redirects=False)
    account_url = created.headers["location"].split("?")[0]
    assert account_url.startswith("/accounts/")                      # the account's page (2026-10-15)
    account_id = account_url.rsplit("/", 1)[1]
    page = client.get(account_url).text
    assert '<div class="acct-balance-value">$18,000.00</div>' in page and "Property" in page
    assert "<h2>Manage</h2>" in page and 'action="/accounts/' + account_id + '/kind"' in page
    kind = client.post(f"/accounts/{account_id}/kind", data={"kind": "other_asset"}, headers=ORIGIN, follow_redirects=False)
    assert "msg=Kind+saved" in kind.headers["location"]
    client.post(f"/accounts/{account_id}/kind", data={"kind": "property"}, headers=ORIGIN)

    account_url = f"/manual/{account_id}"                                # the forms still post under /manual
    client.post(f"{account_url}/valuations", data={"value": "17,250", "date": D(0)}, headers=ORIGIN)
    assert "There&#39;s already a value" in client.get(
        client.post(f"{account_url}/valuations", data={"value": "17,250", "date": D(0)},
                    headers=ORIGIN, follow_redirects=False).headers["location"]).text

    assert "Car" in client.get("/setup").text and "Property (home, car, ...)" in client.get("/setup").text
    html = client.get("/").text
    assert "$18,000.00" in html   # dashboard works with only manual accounts (no institution text on rows since 2026-10-23)

    client.post(f"{account_url}/delete", data={"typed_name": "Car"}, headers=ORIGIN)
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
