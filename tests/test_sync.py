"""Transaction sync: added/modified/removed, cursor persistence, pagination, failures."""

import plistlib
import sys

import pytest

from fintrack import items, schedule, sync
from fintrack.plaid_client import PlaidError
from tests.fakes import page, txn


def connect(conn, settings, gateway, cipher):
    session_id, _ = items.start_create_session(conn, settings, gateway, "banking")
    return items.complete_create(conn, settings, gateway, cipher, session_id, "public-sandbox-fake")["item_id"]


def run_sync(conn, settings, gateway, cipher):
    return sync.sync_all(conn, gateway, cipher, settings.home)


def txns(conn):
    return {r["plaid_transaction_id"]: r for r in conn.execute("SELECT * FROM transactions")}


def cursor(conn, item_id=1):
    return items.get_item(conn, item_id)["transactions_cursor"]


@pytest.fixture
def item(conn, settings, gateway, cipher):
    return connect(conn, settings, gateway, cipher)


# ---------------------------------------------------------------- added / modified / removed

def test_added_transactions_are_stored(conn, settings, gateway, cipher, item):
    gateway.sync_queue = [page(added=[txn("t1", 4.5), txn("t2", -1200.0, name="Payroll", primary="INCOME")])]
    summary = run_sync(conn, settings, gateway, cipher)

    assert summary.results[0].added == 2
    rows = txns(conn)
    assert rows["t1"]["amount_cents"] == 450          # money out stays positive (Plaid sign)
    assert rows["t2"]["amount_cents"] == -120000      # money in is negative
    assert rows["t1"]["date"] == "2026-09-15"         # date object stored as ISO text
    assert rows["t1"]["plaid_category_primary"] == "FOOD_AND_DRINK"
    assert rows["t1"]["plaid_category_confidence"] == "HIGH"
    assert items.get_item(conn, item)["transactions_status"] == "HISTORICAL_UPDATE_COMPLETE"
    assert items.get_item(conn, item)["last_synced_at"] is not None


def test_modified_updates_fields_but_keeps_user_category(conn, settings, gateway, cipher, item):
    gateway.sync_queue = [page(added=[txn("t1", 4.5)], next_cursor="c1")]
    run_sync(conn, settings, gateway, cipher)
    conn.execute("UPDATE transactions SET user_category = 'COFFEE' WHERE plaid_transaction_id = 't1'")
    conn.commit()

    gateway.sync_queue = [page(modified=[txn("t1", 5.25, merchant="Fake Coffee Renamed")], next_cursor="c2")]
    result = run_sync(conn, settings, gateway, cipher).results[0]

    row = txns(conn)["t1"]
    assert result.modified == 1
    assert (row["amount_cents"], row["merchant_name"], row["user_category"]) == (525, "Fake Coffee Renamed", "COFFEE")


def test_removed_pending_is_deleted_but_removed_posted_is_kept_and_flagged(conn, settings, gateway, cipher, item):
    # user's rule 2026-10-22: the bank may withdraw posted transactions and send them again later (removals.py)
    gateway.sync_queue = [page(added=[txn("t1", 4.5), txn("t2", 9.0), txn("p1", 3.0, pending=True)], next_cursor="c1")]
    run_sync(conn, settings, gateway, cipher)
    gateway.sync_queue = [page(removed=["t1", "p1"], next_cursor="c2")]
    summary = run_sync(conn, settings, gateway, cipher)
    assert (summary.results[0].removed, summary.results[0].flagged) == (1, 1)
    assert "1 removed by the bank to review" in summary.describe()
    rows = txns(conn)
    assert set(rows) == {"t1", "t2"} and rows["t1"]["removed_at"] is not None and rows["t2"]["removed_at"] is None
    # Plaid sends t1 again (same id): it wasn't removed after all
    gateway.sync_queue = [page(modified=[txn("t1", 4.5)], next_cursor="c3")]
    run_sync(conn, settings, gateway, cipher)
    assert txns(conn)["t1"]["removed_at"] is None


def test_pending_to_posted_carries_user_category(conn, settings, gateway, cipher, item):
    gateway.sync_queue = [page(added=[txn("pend-1", 20.0, pending=True)], next_cursor="c1")]
    run_sync(conn, settings, gateway, cipher)
    conn.execute("UPDATE transactions SET user_category = 'DINING_OUT' WHERE plaid_transaction_id = 'pend-1'")
    conn.commit()

    # Posted with a tip added: pending removed, posted added pointing back to it.
    gateway.sync_queue = [page(added=[txn("post-1", 24.0, pending_transaction_id="pend-1")],
                               removed=["pend-1"], next_cursor="c2")]
    run_sync(conn, settings, gateway, cipher)

    rows = txns(conn)
    assert set(rows) == {"post-1"}
    assert (rows["post-1"]["amount_cents"], rows["post-1"]["pending"], rows["post-1"]["user_category"]) == (
        2400, 0, "DINING_OUT")


def test_pending_to_posted_carries_a_reward_bonus(conn, settings, gateway, cipher, item):
    gateway.sync_queue = [page(added=[txn("pend-1", 20.0, pending=True)], next_cursor="c1")]
    run_sync(conn, settings, gateway, cipher)
    conn.execute("INSERT INTO transaction_reward_bonuses (transaction_id, amount)"
                 " SELECT id, 5000 FROM transactions WHERE plaid_transaction_id = 'pend-1'")
    conn.commit()
    gateway.sync_queue = [page(added=[txn("post-1", 24.0, pending_transaction_id="pend-1")],
                               removed=["pend-1"], next_cursor="c2")]
    run_sync(conn, settings, gateway, cipher)
    assert [tuple(r) for r in conn.execute("SELECT t.plaid_transaction_id, b.amount FROM transaction_reward_bonuses b"
                                           " JOIN transactions t ON t.id = b.transaction_id")] == [("post-1", 5000)]


def test_pending_to_posted_carries_a_rewards_pair(conn, settings, gateway, cipher, item):
    """A charge partly paid with rewards (migration 0063): its pair moves to the posted version and stays."""
    gateway.sync_queue = [page(added=[txn("pend-1", 50.0, pending=True)], next_cursor="c1")]
    run_sync(conn, settings, gateway, cipher)
    pending = conn.execute("SELECT id, account_id FROM transactions WHERE plaid_transaction_id = 'pend-1'").fetchone()
    for pid, cents in (("manual-part-cost", 15000), ("manual-part-rebate", -15000)):
        conn.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name) VALUES (?, ?, ?, ?, 'Fake')",
                     (pid, pending[1], "2026-10-20", cents))
    cost, rebate = (conn.execute("SELECT id FROM transactions WHERE plaid_transaction_id = ?", (p,)).fetchone()[0]
                    for p in ("manual-part-cost", "manual-part-rebate"))
    conn.execute("UPDATE transactions SET rewards_part_of = ? WHERE id = ?", (pending[0], cost))
    conn.execute("INSERT INTO transaction_rewards_used VALUES (?, 10000, 'points', ?)", (rebate, cost))
    conn.commit()
    gateway.sync_queue = [page(added=[txn("post-1", 50.0, pending_transaction_id="pend-1")],
                               removed=["pend-1"], next_cursor="c2")]
    run_sync(conn, settings, gateway, cipher)
    posted = conn.execute("SELECT id FROM transactions WHERE plaid_transaction_id = 'post-1'").fetchone()[0]
    assert conn.execute("SELECT rewards_part_of FROM transactions WHERE id = ?", (cost,)).fetchone()[0] == posted
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE plaid_transaction_id LIKE 'manual-part-%'").fetchone()[0] == 2


def test_pending_to_posted_carries_your_tags(conn, settings, gateway, cipher, item):
    from fintrack import tags
    gateway.sync_queue = [page(added=[txn("pend-1", 20.0, pending=True)], next_cursor="c1")]
    run_sync(conn, settings, gateway, cipher)
    pending_id = conn.execute("SELECT id FROM transactions WHERE plaid_transaction_id = 'pend-1'").fetchone()[0]
    tags.add(conn, [pending_id], "🏖️ Fake Trip")
    gateway.sync_queue = [page(added=[txn("post-1", 24.0, pending_transaction_id="pend-1")],
                               removed=["pend-1"], next_cursor="c2")]
    run_sync(conn, settings, gateway, cipher)
    assert [tuple(r) for r in conn.execute("SELECT t.plaid_transaction_id, g.name FROM transaction_tags tt"
                                           " JOIN transactions t ON t.id = tt.transaction_id"
                                           " JOIN tags g ON g.id = tt.tag_id")] == [("post-1", "🏖️ Fake Trip")]


FAKE_PLACE = {"address": "1 Fake St", "city": "Faketown", "region": "ZZ", "postal_code": "00000", "country": "US",
              "lat": 40.5, "lon": -75.25, "store_number": "  "}


def location(conn, plaid_id):
    return tuple(conn.execute("SELECT location_address, location_city, location_region, location_postal_code,"
                              " location_country, location_lat, location_lon, location_store_number"
                              " FROM transactions WHERE plaid_transaction_id = ?", (plaid_id,)).fetchone())


def test_location_is_stored_and_kept_when_an_update_leaves_it_out(conn, settings, gateway, cipher, item):
    """Where it happened (migration 0049, user's request 2026-10-28): stored as Plaid sends it, blanks as nothing; an
    update without one keeps it; an online purchase has none."""
    gateway.sync_queue = [page(added=[txn("t1", 9.0, location=FAKE_PLACE), txn("web", 5.0)], next_cursor="c1")]
    run_sync(conn, settings, gateway, cipher)
    assert location(conn, "t1") == ("1 Fake St", "Faketown", "ZZ", "00000", "US", 40.5, -75.25, None)
    assert location(conn, "web") == (None,) * 8
    gateway.sync_queue = [page(modified=[txn("t1", 9.5)], next_cursor="c2")]           # no location this time
    run_sync(conn, settings, gateway, cipher)
    assert location(conn, "t1")[:2] == ("1 Fake St", "Faketown") and txns(conn)["t1"]["amount_cents"] == 950


def test_account_owner_is_stored_and_kept_when_an_update_leaves_it_out(conn, settings, gateway, cipher, item):
    """Who used the card (migration 0051, user's request 2026-10-29): Plaid's account_owner, for an account with
    several cards; blank = nothing; an update without it keeps it."""
    gateway.sync_queue = [page(added=[txn("t1", 9.0, account_owner="FAKE PERSON"), txn("t2", 5.0, account_owner=" ")],
                               next_cursor="c1")]
    run_sync(conn, settings, gateway, cipher)
    owner = lambda pid: conn.execute("SELECT account_owner FROM transactions WHERE plaid_transaction_id = ?",
                                     (pid,)).fetchone()[0]
    assert owner("t1") == "FAKE PERSON" and owner("t2") is None
    gateway.sync_queue = [page(modified=[txn("t1", 9.5)], next_cursor="c2")]
    run_sync(conn, settings, gateway, cipher)
    assert owner("t1") == "FAKE PERSON"


def test_locations_backfill_fills_in_only_missing_parts(conn):
    from datetime import date
    from fintrack import sync as sync_mod
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, access_token_enc, status, products)"
                 " VALUES (1, 'i1', 'sandbox', 'x', 'ok', '[\"transactions\"]')")
    conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, type, classification)"
                 " VALUES (1, 1, 'a1', 'Fake Checking', 'depository', 'asset')")
    for pid, city in (("old", None), ("known", "Kept City")):
        conn.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, location_city)"
                     " VALUES (?, 1, '2026-09-01', 100, 'FAKE', ?)", (pid, city))
    conn.commit()

    class Gateway:
        def get_transactions(self, token, start, end, logos=True, original_description=False):
            assert not logos
            return [{"transaction_id": "old", "location": FAKE_PLACE, "account_owner": "FAKE PERSON"},
                    {"transaction_id": "known", "location": {"city": "Other City", "region": "YY"}},
                    {"transaction_id": "web", "location": {k: None for k in FAKE_PLACE}},
                    {"transaction_id": "not-here", "location": FAKE_PLACE}]

    class Cipher:
        def decrypt(self, value):
            return "token"

    dry = sync_mod.backfill_locations(conn, Gateway(), Cipher(), dry_run=True, today=date(2026, 10, 28))
    assert (dry[0]["with_location"], dry[0]["updated"]) == (3, 0) and location(conn, "old") == (None,) * 8
    report = sync_mod.backfill_locations(conn, Gateway(), Cipher(), today=date(2026, 10, 28))
    assert report[0]["updated"] == 2
    assert location(conn, "old")[:3] == ("1 Fake St", "Faketown", "ZZ")
    assert location(conn, "known")[1:3] == ("Kept City", "YY")                         # known city kept, region filled
    # the card holder (migration 0051) comes with the same fetch; --item limits it to one connection
    assert (report[0]["with_owner"], report[0]["owners_set"]) == (1, 1)
    assert conn.execute("SELECT account_owner FROM transactions WHERE plaid_transaction_id = 'old'").fetchone()[0] == "FAKE PERSON"
    assert sync_mod.backfill_locations(conn, Gateway(), Cipher(), today=date(2026, 10, 28), item_id=2) == []


def test_the_edit_panel_says_where_it_happened():
    from fintrack.web.transaction_edits import location_label

    class Row(dict):
        pass
    full = Row(location_address="1 Fake St", location_city="Faketown", location_region="ZZ", location_postal_code="00000",
               location_country="US", location_store_number="42")
    assert location_label(full) == "Store 42 · 1 Fake St, Faketown, ZZ 00000"
    assert location_label(Row(location_city="Fakeville", location_country="CA")) == "Fakeville, CA"
    assert location_label(Row()) == ""


# ---------------------------------------------------------------- cursor persistence

def test_cursor_saved_and_used_next_time(conn, settings, gateway, cipher, item):
    gateway.sync_queue = [page(added=[txn("t1", 1.0)], next_cursor="cursor-A")]
    run_sync(conn, settings, gateway, cipher)
    assert cursor(conn) == "cursor-A"

    gateway.calls.clear()
    gateway.sync_queue = [page(next_cursor="cursor-B")]
    run_sync(conn, settings, gateway, cipher)
    assert ("sync_transactions", "cursor-A") in gateway.calls  # resumed from the saved cursor
    assert cursor(conn) == "cursor-B"


def test_first_sync_starts_without_cursor(conn, settings, gateway, cipher, item):
    gateway.sync_queue = [page(next_cursor="c1")]
    run_sync(conn, settings, gateway, cipher)
    assert ("sync_transactions", None) in gateway.calls


def test_empty_cursor_from_plaid_keeps_old_cursor(conn, settings, gateway, cipher, item):
    gateway.sync_queue = [page(added=[txn("t1", 1.0)], next_cursor="cursor-A")]
    run_sync(conn, settings, gateway, cipher)
    gateway.sync_queue = [page(next_cursor="", status="NOT_READY")]
    run_sync(conn, settings, gateway, cipher)
    assert cursor(conn) == "cursor-A"
    assert items.get_item(conn, item)["transactions_status"] == "NOT_READY"


# ---------------------------------------------------------------- pagination and failures

def test_multiple_pages_applied_together(conn, settings, gateway, cipher, item):
    gateway.sync_queue = [
        page(added=[txn("t1", 1.0)], next_cursor="p1", has_more=True),
        page(added=[txn("t2", 2.0)], next_cursor="p2", has_more=True),
        page(added=[txn("t3", 3.0)], next_cursor="p3"),
    ]
    run_sync(conn, settings, gateway, cipher)
    assert set(txns(conn)) == {"t1", "t2", "t3"}
    assert cursor(conn) == "p3"
    sync_calls = [c[1] for c in gateway.calls if c[0] == "sync_transactions"]
    assert sync_calls == [None, "p1", "p2"]


def test_mutation_during_pagination_restarts_from_first_cursor(conn, settings, gateway, cipher, item):
    gateway.sync_queue = [page(added=[txn("t0", 1.0)], next_cursor="start")]
    run_sync(conn, settings, gateway, cipher)
    gateway.calls.clear()

    gateway.sync_queue = [
        page(added=[txn("stale", 1.0)], next_cursor="p1", has_more=True),
        PlaidError(sync.MUTATION_DURING_PAGINATION, "changed"),
        page(added=[txn("fresh-1", 1.0)], next_cursor="q1", has_more=True),
        page(added=[txn("fresh-2", 1.0)], next_cursor="q2"),
    ]
    run_sync(conn, settings, gateway, cipher)

    sync_calls = [c[1] for c in gateway.calls if c[0] == "sync_transactions"]
    assert sync_calls == ["start", "p1", "start", "q1"]  # restarted from the original cursor
    assert set(txns(conn)) == {"t0", "fresh-1", "fresh-2"}  # partial "stale" page discarded
    assert cursor(conn) == "q2"


def test_error_midway_writes_nothing_and_keeps_cursor(conn, settings, gateway, cipher, item):
    gateway.sync_queue = [page(added=[txn("t0", 1.0)], next_cursor="start")]
    run_sync(conn, settings, gateway, cipher)

    gateway.sync_queue = [
        page(added=[txn("t1", 1.0)], next_cursor="p1", has_more=True),
        PlaidError("INTERNAL_SERVER_ERROR", "try later"),
    ]
    summary = run_sync(conn, settings, gateway, cipher)

    assert summary.results[0].error == "INTERNAL_SERVER_ERROR"
    assert set(txns(conn)) == {"t0"}
    assert cursor(conn) == "start"
    assert items.get_item(conn, item)["status"] == "error"

    gateway.sync_queue = [page(added=[txn("t1", 1.0)], next_cursor="p1")]  # next sync recovers
    run_sync(conn, settings, gateway, cipher)
    assert items.get_item(conn, item)["status"] == "ok"
    assert set(txns(conn)) == {"t0", "t1"}


def test_login_required_on_one_item_does_not_stop_others(conn, settings, gateway, cipher):
    first = connect(conn, settings, gateway, cipher)
    second = connect(conn, settings, gateway, cipher)  # same fake institution; fine here
    gateway.sync_queue = [PlaidError("ITEM_LOGIN_REQUIRED", "login changed"), page(added=[txn("t1", 1.0)])]
    summary = run_sync(conn, settings, gateway, cipher)

    assert [r.error for r in summary.results] == ["ITEM_LOGIN_REQUIRED", None]
    assert items.get_item(conn, first)["status"] == "login_required"
    assert items.get_item(conn, second)["status"] == "ok"
    assert "First Platypus Bank (ITEM_LOGIN_REQUIRED)" in summary.describe()


# ---------------------------------------------------------------- what gets synced

def test_item_without_transactions_product_is_never_synced(conn, settings, gateway, cipher):
    gateway.products = ["investments"]
    connect(conn, settings, gateway, cipher)
    result = run_sync(conn, settings, gateway, cipher).results[0]

    assert not any(c[0] == "sync_transactions" for c in gateway.calls)  # would add Transactions at Plaid
    assert "no Transactions product" in result.note
    assert ("get_accounts", "access-sandbox-fake-0001") in gateway.calls  # balances still refreshed


def test_products_backfilled_for_older_items(conn, settings, gateway, cipher, item):
    conn.execute("UPDATE items SET products = NULL")
    conn.commit()
    run_sync(conn, settings, gateway, cipher)
    assert items.item_products(items.get_item(conn, item)) == {"transactions"}


def test_transactions_for_unknown_accounts_skipped(conn, settings, gateway, cipher, item):
    gateway.sync_queue = [page(added=[txn("t1", 1.0), txn("t2", 1.0, account_id="acc-mystery")])]
    result = run_sync(conn, settings, gateway, cipher).results[0]
    assert set(txns(conn)) == {"t1"}
    assert result.skipped_unknown_account == 1


def test_removed_items_are_not_synced(conn, settings, gateway, cipher, item):
    items.remove_item(conn, gateway, cipher, item, "First Platypus Bank")
    gateway.calls.clear()
    assert run_sync(conn, settings, gateway, cipher).results == []
    assert gateway.calls == []


def test_concurrent_sync_refused(conn, settings, gateway, cipher, item):
    with sync.sync_lock(settings.home):
        with pytest.raises(sync.SyncInProgress):
            run_sync(conn, settings, gateway, cipher)


# ---------------------------------------------------------------- scheduling

def test_launchd_plist_runs_fintrack_sync_from_venv(settings):
    plist = schedule.build_plist(settings, schedule.parse_times("06:00,18:30"))
    assert plist["Label"] == "com.fintrack.sync"
    # Under caffeinate -i, so a Mac woken for the sync stays awake until it ends.
    assert plist["ProgramArguments"][:2] == ["/usr/bin/caffeinate", "-i"]
    assert plist["ProgramArguments"][3] == "sync"
    assert plist["ProgramArguments"][2].endswith("/fintrack")
    assert plist["ProgramArguments"][2].startswith(str(sys.prefix))  # the venv's command, not the repo
    assert plist["EnvironmentVariables"]["FINTRACK_HOME"] == str(settings.home)
    assert plist["StartCalendarInterval"] == [{"Hour": 6, "Minute": 0}, {"Hour": 18, "Minute": 30}]
    assert plist["StandardOutPath"].startswith(str(settings.home))
    plistlib.dumps(plist)  # serializable


@pytest.mark.parametrize("bad", ["25:00", "6", "06:60", ""])
def test_invalid_schedule_times_rejected(bad):
    with pytest.raises(ValueError):
        schedule.parse_times(bad)
