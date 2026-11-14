"""Item lifecycle and the Production Item budget gate."""

import sqlite3

import pytest

from fintrack import items
from fintrack.item_budget import production_item_budget
from fintrack.plaid_client import PlaidError


LINK_ACCOUNTS = [{"name": "Fake Checking", "mask": "0000"}, {"name": "Fake Credit Card", "mask": "3333"}]


def connect_one(conn, settings, gateway, cipher, consent="", **link_metadata):
    session_id, _ = items.start_create_session(conn, settings, gateway, "banking", consent)
    return items.complete_create(conn, settings, gateway, cipher, session_id, "public-sandbox-fake", **link_metadata)


def exchanges(gateway):
    return sum(1 for c in gateway.calls if c[0] == "exchange_public_token")


def fill_production_items(conn, n):
    for i in range(n):
        conn.execute(
            "INSERT INTO items (plaid_item_id, plaid_env, status) VALUES (?, 'production', 'removed')",
            (f"old-{i}",),
        )
    conn.commit()


# ---------------------------------------------------------------- consent gate

def test_sandbox_needs_no_consent(conn, settings, gateway):
    session_id, token = items.start_create_session(conn, settings, gateway, "banking")
    assert token.startswith("link-sandbox-fake")
    row = conn.execute("SELECT * FROM link_sessions WHERE id = ?", (session_id,)).fetchone()
    assert (row["purpose"], row["plaid_env"], row["consented_at"]) == ("create", "sandbox", None)


@pytest.mark.parametrize("phrase", ["", "connect", "yes", "CONNECT please"])
def test_production_refused_without_exact_phrase(prod_conn, prod_settings, gateway, phrase):
    with pytest.raises(items.ConsentError):
        items.start_create_session(prod_conn, prod_settings, gateway, "banking", phrase)
    assert gateway.calls == []  # Plaid never asked for a token
    assert prod_conn.execute("SELECT COUNT(*) FROM link_sessions").fetchone()[0] == 0


def test_production_refused_when_budget_exhausted(prod_conn, prod_settings, gateway):
    fill_production_items(prod_conn, 3)  # limit is 3 in prod_settings; removed Items still count
    with pytest.raises(items.ConsentError, match="All 3"):
        items.start_create_session(prod_conn, prod_settings, gateway, "banking", items.CONSENT_PHRASE)
    assert gateway.calls == []


def test_production_consent_is_recorded(prod_conn, prod_settings, gateway):
    fill_production_items(prod_conn, 1)
    session_id, _ = items.start_create_session(prod_conn, prod_settings, gateway, "investments", " CONNECT ")
    row = prod_conn.execute("SELECT * FROM link_sessions WHERE id = ?", (session_id,)).fetchone()
    assert row["consented_at"] is not None
    assert row["items_used_at_consent"] == 1
    assert gateway.calls == [("create_link_token", "investments", None)]


def test_database_rejects_production_session_without_consent(prod_conn):
    with pytest.raises(sqlite3.IntegrityError):
        prod_conn.execute("INSERT INTO link_sessions (purpose, plaid_env) VALUES ('create', 'production')")


def test_link_sessions_cannot_be_deleted(conn, settings, gateway):
    items.start_create_session(conn, settings, gateway, "banking")
    with pytest.raises(sqlite3.IntegrityError, match="audit log"):
        conn.execute("DELETE FROM link_sessions")


def test_unknown_kind_rejected(conn, settings, gateway):
    with pytest.raises(ValueError):
        items.start_create_session(conn, settings, gateway, "crypto")


# ---------------------------------------------------------------- exchange and accounts

def test_complete_create_stores_item_and_accounts(conn, settings, gateway, cipher):
    result = connect_one(conn, settings, gateway, cipher)
    assert result["institution_name"] == "First Platypus Bank"
    assert result["duplicate"] is False

    item = conn.execute("SELECT * FROM items").fetchone()
    assert item["status"] == "ok"
    assert item["plaid_env"] == "sandbox"
    assert b"access-sandbox" not in item["access_token_enc"]  # encrypted at rest
    assert cipher.decrypt(item["access_token_enc"]) == "access-sandbox-fake-0001"

    accounts = {a["plaid_account_id"]: a for a in conn.execute("SELECT * FROM accounts")}
    checking, card = accounts["acc-checking"], accounts["acc-card"]
    assert (checking["classification"], checking["current_balance_cents"], checking["available_balance_cents"]) == (
        "asset", 11000, 10000)
    assert card["classification"] == "liability"
    assert card["mask"] == "3333"                 # last 4 only
    assert card["current_balance_cents"] == 41001  # 410.005 rounds half-up

    session = conn.execute("SELECT * FROM link_sessions").fetchone()
    assert session["completed_at"] is not None and session["item_id"] == item["id"]


def test_no_plaintext_token_anywhere_in_database(conn, settings, gateway, cipher):
    connect_one(conn, settings, gateway, cipher)
    conn.commit()
    raw = settings.db_path.read_bytes()
    wal = settings.db_path.with_name(settings.db_path.name + "-wal")
    if wal.exists():
        raw += wal.read_bytes()
    assert b"access-sandbox-fake" not in raw


def test_item_recorded_even_if_account_fetch_fails(conn, settings, gateway, cipher):
    gateway.accounts_error = PlaidError("INTERNAL_SERVER_ERROR", "try later")
    connect_one(conn, settings, gateway, cipher)
    item = conn.execute("SELECT * FROM items").fetchone()
    assert item is not None
    assert (item["status"], item["error_code"]) == ("error", "INTERNAL_SERVER_ERROR")


def test_production_item_counts_against_budget(prod_conn, prod_settings, gateway, cipher):
    connect_one(prod_conn, prod_settings, gateway, cipher, consent="CONNECT")
    budget = production_item_budget(prod_conn, prod_settings.production_item_limit)
    assert (budget.used, budget.remaining) == (1, 2)


# ---------------------------------------------------------------- duplicate prevention

def test_duplicate_refused_before_exchange(conn, settings, gateway, cipher):
    connect_one(conn, settings, gateway, cipher)
    with pytest.raises(items.DuplicateItemError, match="no Item was used"):
        connect_one(conn, settings, gateway, cipher, institution_id="ins_fake_1", link_accounts=LINK_ACCOUNTS)
    assert exchanges(gateway) == 1  # second public token never exchanged
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_same_accounts_refused_even_when_duplicate_allowed(conn, settings, gateway, cipher):
    connect_one(conn, settings, gateway, cipher)
    with pytest.raises(items.DuplicateItemError, match="same login"):
        connect_one(conn, settings, gateway, cipher, institution_id="ins_fake_1",
                    link_accounts=[{"name": "Fake Checking", "mask": "0000"}], allow_duplicate=True)
    assert exchanges(gateway) == 1


def test_second_separate_login_allowed_when_confirmed(conn, settings, gateway, cipher):
    connect_one(conn, settings, gateway, cipher)
    result = connect_one(conn, settings, gateway, cipher, institution_id="ins_fake_1",
                         link_accounts=[{"name": "Partner Checking", "mask": "9876"}], allow_duplicate=True)
    assert result["duplicate"] is True
    assert exchanges(gateway) == 2


def test_removed_item_does_not_block_reconnecting_institution(conn, settings, gateway, cipher):
    connect_one(conn, settings, gateway, cipher)
    items.remove_item(conn, gateway, cipher, 1, "First Platypus Bank")
    result = connect_one(conn, settings, gateway, cipher, institution_id="ins_fake_1", link_accounts=LINK_ACCOUNTS)
    assert result["duplicate"] is False


def test_production_duplicate_does_not_consume_budget(prod_conn, prod_settings, gateway, cipher):
    connect_one(prod_conn, prod_settings, gateway, cipher, consent="CONNECT")
    with pytest.raises(items.DuplicateItemError):
        connect_one(prod_conn, prod_settings, gateway, cipher, consent="CONNECT",
                    institution_id="ins_fake_1", link_accounts=LINK_ACCOUNTS)
    assert production_item_budget(prod_conn, 3).used == 1


def test_refresh_updates_balances(conn, settings, gateway, cipher):
    connect_one(conn, settings, gateway, cipher)
    gateway_accounts = gateway.get_accounts("x")
    gateway_accounts[0]["balances"]["current"] = 250.0
    items.refresh_accounts(conn, gateway, cipher, 1)
    row = conn.execute("SELECT current_balance_cents FROM accounts WHERE plaid_account_id = 'acc-checking'").fetchone()
    assert row[0] == 25000
    gateway_accounts[0]["balances"]["current"] = 110.0  # restore shared fake data


# ---------------------------------------------------------------- errors and update mode

def test_login_required_error_then_repair_via_update_mode(conn, settings, gateway, cipher):
    connect_one(conn, settings, gateway, cipher)
    items.simulate_login_required(conn, settings, gateway, cipher, 1)
    item = items.get_item(conn, 1)
    assert (item["status"], item["error_code"]) == ("login_required", "ITEM_LOGIN_REQUIRED")

    gateway.calls.clear()
    session_id, _ = items.start_update_session(conn, settings, gateway, cipher, 1)
    # Update mode passes the existing access token and no products: it cannot create an Item.
    assert gateway.calls == [("create_link_token", None, "access-sandbox-fake-0001")]

    gateway.accounts_error = None  # the user re-entered their login in Link
    items.complete_update(conn, gateway, cipher, session_id)
    assert items.get_item(conn, 1)["status"] == "ok"
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1  # still one Item
    assert ("exchange_public_token", "public-sandbox-fake") not in gateway.calls


# ---------------------------------------------------------------- removal

def test_remove_requires_typed_institution_name(conn, settings, gateway, cipher):
    connect_one(conn, settings, gateway, cipher)
    with pytest.raises(ValueError):
        items.remove_item(conn, gateway, cipher, 1, "wrong bank")
    assert items.get_item(conn, 1)["status"] == "ok"
    assert not any(c[0] == "remove_item" for c in gateway.calls)


def test_remove_production_item_keeps_ledger_row(prod_conn, prod_settings, gateway, cipher):
    connect_one(prod_conn, prod_settings, gateway, cipher, consent="CONNECT")
    items.remove_item(prod_conn, gateway, cipher, 1, "first platypus bank")  # case-insensitive

    item = items.get_item(prod_conn, 1)
    assert item["status"] == "removed"
    assert item["access_token_enc"] is None
    assert items.accounts_for_item(prod_conn, 1) == []  # hidden, not deleted
    assert prod_conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2
    assert production_item_budget(prod_conn, 3).used == 1  # still counts
    with pytest.raises(ValueError):
        items.start_update_session(prod_conn, prod_settings, gateway, cipher, 1)


def test_remove_tolerates_item_already_gone_at_plaid(conn, settings, gateway, cipher):
    connect_one(conn, settings, gateway, cipher)
    gateway.remove_error = PlaidError("ITEM_NOT_FOUND", "not found")
    items.remove_item(conn, gateway, cipher, 1, "First Platypus Bank")
    assert items.get_item(conn, 1)["status"] == "removed"


def test_remove_surfaces_other_plaid_errors(conn, settings, gateway, cipher):
    connect_one(conn, settings, gateway, cipher)
    gateway.remove_error = PlaidError("INTERNAL_SERVER_ERROR", "try later")
    with pytest.raises(PlaidError):
        items.remove_item(conn, gateway, cipher, 1, "First Platypus Bank")
    assert items.get_item(conn, 1)["status"] == "ok"


# ---------------------------------------------------------------- add or remove shared accounts

def test_account_selection_update_mode_never_creates_item(conn, settings, gateway, cipher):
    connect_one(conn, settings, gateway, cipher)
    gateway.calls.clear()
    items.start_update_session(conn, settings, gateway, cipher, 1, account_selection=True)
    assert gateway.calls == [("create_link_token", None, "access-sandbox-fake-0001", "account_selection")]
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_unshared_accounts_hidden_and_reshared_reappear(conn, settings, gateway, cipher):
    from tests.fakes import FAKE_ACCOUNTS

    connect_one(conn, settings, gateway, cipher)
    items.upsert_accounts(conn, 1, [FAKE_ACCOUNTS[0]])            # you stopped sharing the card
    hidden = dict(conn.execute("SELECT plaid_account_id, is_hidden FROM accounts").fetchall())
    assert hidden == {"acc-checking": 0, "acc-card": 1}
    items.upsert_accounts(conn, 1, FAKE_ACCOUNTS)                 # shared again
    assert {r[0] for r in conn.execute("SELECT is_hidden FROM accounts")} == {0}


def test_empty_account_list_hides_nothing(conn, settings, gateway, cipher):
    connect_one(conn, settings, gateway, cipher)
    items.upsert_accounts(conn, 1, [])
    assert {r[0] for r in conn.execute("SELECT is_hidden FROM accounts")} == {0}
