"""Merchant logos (logos.py): stored at sync, downloaded once, served by fintrack. Fake data only."""

import os
import stat

import pytest

from fintrack import categories, category_icons, logos, sync, transfers

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 100
LOGO = "https://plaid-merchant-logos.plaid.com/fake_coffee_1.png"
CP_LOGO = "https://plaid-counterparty-logos.plaid.com/fake_app_2.png"


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env) VALUES (1, 'i1', 'sandbox')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification)"
                 " VALUES (1, 1, 'a1', 'Fake Checking', 'depository', 'asset')")
    conn.commit()
    return conn


def txn(tid, **extra):
    return {"transaction_id": tid, "account_id": "a1", "date": "2026-09-02", "amount": 4.5, "name": "FAKE COFFEE",
            "merchant_name": "Fake Coffee", "personal_finance_category": {"primary": "FOOD_AND_DRINK"}, **extra}


def test_logo_fields_prefer_the_transaction_then_a_merchant_counterparty():
    assert sync.logo_fields(txn("x", logo_url=LOGO, merchant_entity_id="m1")) == (LOGO, "m1")
    cp = [{"type": "payment_app", "logo_url": CP_LOGO}, {"type": "merchant", "entity_id": "m2", "logo_url": None}]
    assert sync.logo_fields(txn("x", counterparties=cp)) == (CP_LOGO, "m2")
    assert sync.logo_fields(txn("x", counterparties=[{"type": "financial_institution", "logo_url": CP_LOGO}])) == (None, None)


def test_sync_stores_logo_fields_and_keeps_them(db):
    sync._upsert_transaction(db, 1, txn("t1", logo_url=LOGO, merchant_entity_id="m1"))
    sync._upsert_transaction(db, 1, txn("t1"))                    # a later update without them
    db.commit()
    assert tuple(db.execute("SELECT logo_url, merchant_entity_id FROM transactions").fetchone()) == (LOGO, "m1")


def test_fetch_missing_downloads_once_privately_and_only_from_plaid(db, home):
    sync._upsert_transaction(db, 1, txn("t1", logo_url=LOGO, merchant_entity_id="m1"))
    sync._upsert_transaction(db, 1, txn("t2", logo_url="https://evil.example/x.png"))
    sync._upsert_transaction(db, 1, txn("t3", logo_url=CP_LOGO))
    db.commit()
    calls = []

    def fetch(url):
        calls.append(url)
        return PNG if url == LOGO else b"<html>not a png</html>"

    assert logos.fetch_missing(db, home, fetch=fetch) == (1, 1)              # LOGO saved, CP_LOGO refused
    assert "https://evil.example/x.png" not in calls                         # never fetched
    path = home / "logos" / logos.file_name(LOGO)
    assert path.read_bytes() == PNG
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and stat.S_IMODE(os.stat(home / "logos").st_mode) == 0o700
    assert logos.fetch_missing(db, home, fetch=fetch) == (0, 0) and len(calls) == 2   # nothing refetched
    db.execute("UPDATE logo_files SET fetched_at = datetime('now', '-8 days') WHERE status = 'failed'")
    db.commit()
    assert logos.fetch_missing(db, home, fetch=lambda u: PNG) == (1, 0)     # a failure is retried after 7 days
    assert logos.fetch_missing(db, home, fetch=lambda u: PNG + b"\0" * logos.MAX_BYTES) == (0, 0)


def test_lookup_shares_a_logo_across_the_same_merchant(db, home):
    sync._upsert_transaction(db, 1, txn("t1", logo_url=LOGO, merchant_entity_id="m1"))
    sync._upsert_transaction(db, 1, txn("t2", merchant_entity_id="m1"))   # older: no logo of its own
    db.commit()
    logos.fetch_missing(db, home, fetch=lambda u: PNG)
    by_url, by_merchant = logos.lookup(db)
    assert by_url == {LOGO: logos.file_name(LOGO)} and by_merchant == {"m1": logos.file_name(LOGO)}


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443")


def test_rows_show_the_logo_or_the_category_icon_and_the_route_is_locked_down(client, db, home):
    sync._upsert_transaction(db, 1, txn("t1", logo_url=LOGO, merchant_entity_id="m1"))
    sync._upsert_transaction(db, 1, txn("t2", merchant_entity_id="m1"))
    sync._upsert_transaction(db, 1, {**txn("t3"), "merchant_name": "Zeta Fake", "name": "ZETA"})
    db.commit()
    logos.fetch_missing(db, home, fetch=lambda u: PNG)
    page = client.get("/transactions", params={"range": "custom"}).text
    name = logos.file_name(LOGO)
    assert page.count(f'<img class="merchant-logo" src="/logos/{name}"') == 2           # own + same merchant
    row = page.split('<tr id="txn-3"')[1].split("</tr>")[0]                           # no logo: Food & drink's icon
    assert '<span class="merchant-logo category-icon" aria-hidden="true">' in row and "merchant-initial" not in row
    assert category_icons.BY_NAME["utensils"].svg in row
    served = client.get(f"/logos/{name}")
    assert served.status_code == 200 and served.content == PNG and served.headers["content-type"] == "image/png"
    for bad in ("../.env", "x.png", name.replace(".png", ".svg"), "a" * 64 + ".png"):
        assert client.get(f"/logos/{bad}").status_code == 404


def test_backfill_fills_only_logo_fields_and_dry_run_writes_nothing(db, home, cipher):
    from fintrack.plaid_client import PlaidError

    db.execute("UPDATE items SET access_token_enc = ?, products = '[\"transactions\"]', status = 'ok' WHERE id = 1",
               (cipher.encrypt("access-sandbox-fake"),))
    sync._upsert_transaction(db, 1, txn("t1"))
    db.commit()
    categories.set_transaction_category(db, 1, "TRAVEL")
    categories.set_transaction_description(db, 1, "My coffee")

    class Gateway:
        def __init__(self, error=None):
            self.error = error

        def get_transactions(self, token, start, end):
            if self.error:
                raise self.error
            return [txn("t1", logo_url=LOGO, merchant_entity_id="m1"), txn("unknown-to-us", logo_url=LOGO)]

    dry = logos.backfill(db, Gateway(), cipher, home, dry_run=True)
    assert dry == [{"item": 1, "fetched": 2, "with_logo": 2, "with_merchant_id": 1, "with_bank_logo": 0, "updated": 0,
                    "error": None}]
    assert db.execute("SELECT logo_url FROM transactions").fetchone()[0] is None
    report = logos.backfill(db, Gateway(), cipher, home)
    assert report[0]["updated"] == 1
    row = db.execute("SELECT logo_url, merchant_entity_id, user_category, user_description FROM transactions").fetchone()
    assert tuple(row) == (LOGO, "m1", "TRAVEL", "My coffee")                 # your edits untouched
    assert logos.backfill(db, Gateway(PlaidError("ITEM_LOGIN_REQUIRED", "x")), cipher, home, dry_run=True)[0]["error"] \
        == "ITEM_LOGIN_REQUIRED"


def test_logos_have_their_own_column_left_of_the_description(client, db, home):
    sync._upsert_transaction(db, 1, txn("t1", logo_url=LOGO))
    db.commit()
    logos.fetch_missing(db, home, fetch=lambda u: PNG)
    page = client.get("/transactions", params={"range": "custom"}).text
    head = page.split('<table class="data txns">')[1].split("<thead>")[1].split("</thead>")[0]
    assert head.index(">Date</a>") < head.index('class="logo-col"') < head.index(">Description</a>")
    cells = page.split('<tr id="txn-1"')[1].split("</tr>")[0].split("<td")
    assert 'class="logo-col"' in cells[3] and "merchant-logo" in cells[3] and "merchant-logo" not in cells[4]


# ---------------------------------------------------------------- transfers show their other side (2026-10-20)

BANK_LOGO = "https://plaid-counterparty-logos.plaid.com/fake_bank_3.png"


def test_a_bank_counterparty_logo_is_kept_for_transfers_only():
    cp = [{"type": "financial_institution", "logo_url": BANK_LOGO}, {"type": "merchant", "logo_url": LOGO}]
    assert sync.transfer_logo_field(txn("x", counterparties=cp)) == BANK_LOGO
    assert sync.logo_fields(txn("x", counterparties=cp))[0] == LOGO               # a bank never becomes the row's logo
    assert sync.transfer_logo_field(txn("x", counterparties=[{"type": "payment_app", "logo_url": CP_LOGO}])) is None


@pytest.fixture
def banks(db, home):
    """Fake Checking (1) at fake bank A; Fake Card (2) at B; Fake Brokerage (3) at C; Fake Savings (4) at A again, but
    B and C have saved institution logos and A has none."""
    db.execute("UPDATE items SET institution_id = 'ins_a' WHERE id = 1")
    db.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_id) VALUES (2, 'i2', 'sandbox', 'ins_b'),"
               " (3, 'i3', 'sandbox', 'ins_c')")
    db.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification) VALUES"
               " (2, 2, 'a2', 'Fake Card', 'credit', 'liability'), (3, 3, 'a3', 'Fake Brokerage', 'investment', 'asset'),"
               " (4, 1, 'a4', 'Fake Savings', 'depository', 'asset')")
    db.execute("INSERT INTO institution_logos (institution_id, file, status) VALUES ('ins_b', 'b.png', 'ok'),"
               " ('ins_c', 'c.png', 'ok'), ('ins_a', NULL, 'none')")
    db.commit()
    return db


def add_txn(conn, tid, account, cents, primary, detailed=None, day="2026-09-10", key=None, **logo):
    conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_primary,"
        " plaid_category_detailed, plaid_category_key, transfer_logo_url, logo_url) VALUES (?, ?, ?, ?, 'FAKE', ?, ?, ?, ?, ?)",
        (tid, account, day, cents, primary, detailed, key or primary, logo.get("bank"), logo.get("own")))
    conn.commit()
    return conn.execute("SELECT id FROM transactions WHERE plaid_transaction_id = ?", (tid,)).fetchone()[0]


def test_a_matched_transfer_shows_the_other_accounts_bank(banks):
    pay = add_txn(banks, "pay", 1, 50000, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")   # checking -> card
    got = add_txn(banks, "got", 2, -50000, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
    assert transfers.detect_transfers(banks) == 1
    found = logos.transfer_logos(banks)
    assert found.get(pay) == "b.png"                  # money out: where it went (the card's bank)
    assert got not in found                           # money in came from bank A, which has no logo


def test_without_a_known_account_a_transfer_shows_plaids_bank_counterparty(banks, home):
    sent = add_txn(banks, "sent", 1, 12345, "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER", bank=BANK_LOGO)
    bought = add_txn(banks, "bought", 1, 999, "FOOD_AND_DRINK", bank=BANK_LOGO)    # a purchase: never a bank's logo
    logos.fetch_missing(banks, home, fetch=lambda u: PNG)
    found = logos.transfer_logos(banks)
    assert found == {sent: logos.file_name(BANK_LOGO)} and bought not in found


def test_a_known_account_beats_the_counterparty_and_your_money_lent_link_beats_a_pair(banks, home):
    out = add_txn(banks, "out", 1, 20000, "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER", bank=BANK_LOGO)
    add_txn(banks, "in", 2, -20000, "TRANSFER_IN", "TRANSFER_IN_ACCOUNT_TRANSFER")
    transfers.detect_transfers(banks)
    logos.fetch_missing(banks, home, fetch=lambda u: PNG)
    assert logos.transfer_logos(banks)[out] == "b.png"
    banks.execute("INSERT INTO purpose_links (transaction_id, account_id, source) VALUES (?, 3, 'you')", (out,))
    banks.commit()
    assert logos.transfer_logos(banks)[out] == "c.png"


def test_a_transfer_into_a_brokerage_shows_the_brokerage(banks):
    from fintrack import contributions
    from fintrack.contributions import INTO_CODE

    paired = add_txn(banks, "p", 1, 75000, "TRANSFER_OUT", INTO_CODE)               # the brokerage reports this one
    banks.execute("INSERT INTO investment_transactions (plaid_investment_transaction_id, account_id, date, name, type,"
                  " subtype, amount_cents) VALUES ('d1', 3, '2026-09-11', 'DEPOSIT', 'cash', 'deposit', -75000)")
    ruled = add_txn(banks, "r", 1, 33300, "TRANSFER_OUT", INTO_CODE, day="2026-05-01")   # it doesn't report this one
    banks.execute("UPDATE transactions SET original_description = 'FAKE BROKER REF 4242' WHERE id = ?", (ruled,))
    banks.execute("INSERT INTO destination_rules (account_id, pattern) VALUES (3, 'REF 4242')")
    banks.commit()
    assert contributions.transfer_accounts(banks) == {paired: 3, ruled: 3}
    found = logos.transfer_logos(banks)
    assert found[paired] == found[ruled] == "c.png"


def test_transfer_rows_show_the_other_side_then_the_category_icon(client, banks):
    pay = add_txn(banks, "pay", 1, 50000, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", key="CREDIT_CARD_PAYMENT")
    got = add_txn(banks, "got", 2, -50000, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", key="CREDIT_CARD_PAYMENT")
    transfers.detect_transfers(banks)
    page = client.get("/transactions", params={"range": "custom"}).text
    assert '<img class="merchant-logo" src="/logos/b.png"' in page.split(f'<tr id="txn-{pay}"')[1].split("</tr>")[0]
    row = page.split(f'<tr id="txn-{got}"')[1].split("</tr>")[0]                  # bank A has no logo: the card icon
    assert "<img" not in row and category_icons.BY_NAME["card"].svg in row
