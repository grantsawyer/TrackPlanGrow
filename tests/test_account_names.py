"""Readable account names: automatic shortening and your own names surviving syncs."""

import pytest

from fintrack import account_names, items
from fintrack.account_names import short_name


@pytest.mark.parametrize("plaid, official, mask, expected", [
    # Owner name and echoed mask dropped.
    ("Pat Example - Roth IRA Brokerage Account - ****1234", "ROTH_IRA", "1234", "Roth IRA Brokerage"),
    # Generic name -> official name, trademark marks removed.
    ("CREDIT CARD", "Fake Rewards®", "1111", "Fake Rewards"),
    ("CREDIT CARD", "FakeSM Travel Card", "2222", "Fake Travel Card"),
    # Generic name with only an enum-like official name: keep it, Title Case.
    ("CHECKING", "INDIVIDUAL", "3333", "Checking"),
    # Shouting -> Title Case, short words kept.
    ("FAKE BANK CLASSIC CHECKING", None, "4444", "FAKE BANK Classic Checking"),
    # Already fine: unchanged.
    ("Fake Travel Card", "Fake Travel Card", "5555", "Fake Travel Card"),
    # A trailing number that isn't this account's mask stays.
    ("Plan 2030", None, "9999", "Plan 2030"),
    (None, None, None, "Account"),
])
def test_short_name(plaid, official, mask, expected):
    assert short_name(plaid, official, mask) == expected


def _plaid_account(name, official=None):
    return {"account_id": "acc-1", "name": name, "official_name": official, "mask": "1234",
            "type": "credit", "subtype": "credit card",
            "balances": {"current": 10.0, "available": None, "iso_currency_code": "USD"}}


def _item(conn):
    return conn.execute(
        "INSERT INTO items (plaid_item_id, plaid_env, institution_name) VALUES ('item-1', 'sandbox', 'Fake Bank')"
    ).lastrowid


def test_sync_shortens_and_your_name_survives(conn):
    item_id = _item(conn)
    items.upsert_accounts(conn, item_id, [_plaid_account("CREDIT CARD", "Fake Rewards®")])
    row = conn.execute("SELECT id, name, plaid_name FROM accounts").fetchone()
    assert (row["name"], row["plaid_name"]) == ("Fake Rewards", "CREDIT CARD")

    account_names.rename(conn, row["id"], "  Groceries card ")
    items.upsert_accounts(conn, item_id, [_plaid_account("CREDIT CARD", "Fake Rewards®")])   # next sync
    assert conn.execute("SELECT name FROM accounts").fetchone()["name"] == "Groceries card"

    account_names.rename(conn, row["id"], "")                                                 # back to automatic
    assert conn.execute("SELECT name, name_is_custom FROM accounts").fetchone()[:] == ("Fake Rewards", 0)


def test_refresh_auto_names_skips_custom_and_manual(conn):
    item_id = _item(conn)
    items.upsert_accounts(conn, item_id, [_plaid_account("CREDIT CARD", "Fake Rewards®")])
    conn.execute("UPDATE accounts SET name = 'CREDIT CARD'")              # as stored before migration 0011
    conn.execute("""INSERT INTO accounts (name, type, classification, is_manual)
                  VALUES ('MY CAR', 'other', 'asset', 1)""")
    account_names.refresh_auto_names(conn)
    names = [r[0] for r in conn.execute("SELECT name FROM accounts ORDER BY id")]
    assert names == ["Fake Rewards", "MY CAR"]


def test_rename_rejects_manual_and_long_names(conn):
    item_id = _item(conn)
    items.upsert_accounts(conn, item_id, [_plaid_account("CREDIT CARD")])
    acct_id = conn.execute("SELECT id FROM accounts").fetchone()[0]
    with pytest.raises(ValueError):
        account_names.rename(conn, acct_id, "x" * 61)
    manual_id = conn.execute("""INSERT INTO accounts (name, type, classification, is_manual)
                                VALUES ('Fake Car', 'other', 'asset', 1)""").lastrowid
    with pytest.raises(ValueError):
        account_names.rename(conn, manual_id, "Car")


def _card(acc_id, mask, official="Fake Rewards®"):
    return {"account_id": acc_id, "name": "CREDIT CARD", "official_name": official, "mask": mask, "type": "credit",
            "subtype": "credit card", "balances": {"current": 10.0, "available": None, "iso_currency_code": "USD"}}


def test_same_automatic_names_get_their_last_four(conn):
    """Two cards a bank describes the same way (user's request 2026-10-14): each gets " ····<last 4>"; a different
    card at the same bank keeps its plain name; syncs and the startup refresh agree (nothing flips)."""
    _item(conn)
    batch = [_card("c1", "1111"), _card("c2", "2222"), _card("c3", "3333", official="Fake Travel Card")]
    items.upsert_accounts(conn, 1, batch)
    names = lambda: [r[0] for r in conn.execute("SELECT name FROM accounts ORDER BY plaid_account_id")]   # noqa: E731
    assert names() == ["Fake Rewards ····1111", "Fake Rewards ····2222", "Fake Travel Card"]
    items.upsert_accounts(conn, 1, batch)                                   # a second sync: same names
    account_names.refresh_auto_names(conn)                                  # and the startup refresh
    assert names()[:2] == ["Fake Rewards ····1111", "Fake Rewards ····2222"]
    # your own name wins; clearing it gives back the distinct automatic name
    c1 = conn.execute("SELECT id FROM accounts WHERE plaid_account_id = 'c1'").fetchone()[0]
    account_names.rename(conn, c1, "Fake Gold")
    items.upsert_accounts(conn, 1, batch)
    assert names()[:2] == ["Fake Gold", "Fake Rewards ····2222"]
    account_names.rename(conn, c1, "")
    assert names()[0] == "Fake Rewards ····1111"
