"""Syncing transactions and balances from Plaid.

For each active Item:

1. Make sure we know the Item's products (via the free /item/get).
2. Refresh accounts and balances (free /accounts/get). This also picks up
   any account the bank added since last time.
3. If, and only if, the Item has the Transactions product, page through
   /transactions/sync from the stored cursor, collecting every page first.
4. Apply all added/modified/removed changes AND the new cursor in one database
   transaction. If anything fails midway, nothing is written and the old
   cursor stays, so the next sync simply retries the same range.

Pending -> posted: Plaid reports the pending transaction as `removed` and the
posted one as `added` with `pending_transaction_id` pointing back. Any
category you set on the pending transaction is copied to the posted one.

Removed POSTED transactions are not deleted (user's rule 2026-10-22): a bank can withdraw posted transactions and
re-send them later under new ids, sometimes changed. They are kept, flagged `removed_at`, matched to what looks like
their return, and listed for your review (removals.py). Only pending ones are deleted.

Only one sync runs at a time (a file lock in FINTRACK_HOME), so a scheduled
sync and a "Sync now" click can't interleave.
"""

from __future__ import annotations

import fcntl
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

from fintrack import categories, items, loans, networth, prices, purposes, recurring, removals, rules, transfers
from fintrack.crypto import TokenCipher
from fintrack.money import to_cents
from fintrack.plaid_client import PlaidError, PlaidGateway

log = logging.getLogger(__name__)

MUTATION_DURING_PAGINATION = "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"
MAX_PAGINATION_RESTARTS = 3


class SyncInProgress(Exception):
    pass


@dataclass
class ItemSyncResult:
    item_id: int
    institution: str
    added: int = 0
    modified: int = 0
    removed: int = 0          # pending transactions Plaid removed (deleted)
    flagged: int = 0          # posted ones Plaid removed: kept and flagged for review (removals.py)
    skipped_unknown_account: int = 0
    holdings: int = 0
    investment_transactions: int = 0
    transactions_status: str | None = None
    note: str = ""            # e.g. "no Transactions product"
    error: str | None = None  # Plaid error code if the Item failed


@dataclass
class SyncSummary:
    results: list[ItemSyncResult] = field(default_factory=list)

    @property
    def errors(self) -> list[ItemSyncResult]:
        return [r for r in self.results if r.error]

    def describe(self) -> str:
        if not self.results:
            return "Nothing to sync."
        parts = [
            f"{sum(r.added for r in self.results)} new",
            f"{sum(r.modified for r in self.results)} updated",
            f"{sum(r.removed for r in self.results)} removed",
        ]
        flagged = sum(r.flagged for r in self.results)
        if flagged:
            parts.append(f"{flagged} removed by the bank to review")
        text = f"Synced {len(self.results)} connection(s): " + ", ".join(parts) + " transactions."
        if self.errors:
            text += " Errors: " + "; ".join(f"{r.institution} ({r.error})" for r in self.errors) + "."
        return text


# ---------------------------------------------------------------- locking

@contextmanager
def sync_lock(home: Path) -> Iterator[None]:
    """Non-blocking exclusive lock; raises SyncInProgress if another sync holds it."""
    lock_path = home / "sync.lock"
    with open(lock_path, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SyncInProgress("A sync is already running.") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# ---------------------------------------------------------------- fetching

@dataclass
class FetchedChanges:
    added: list[dict]
    modified: list[dict]
    removed: list[dict]
    next_cursor: str
    status: str | None


def fetch_all_pages(gateway: PlaidGateway, access_token: str, start_cursor: str | None) -> FetchedChanges:
    """Collect every page since `start_cursor`.

    If Plaid reports the data changed mid-pagination, restart from
    `start_cursor` (Plaid's documented recovery), discarding partial pages.
    """
    for attempt in range(MAX_PAGINATION_RESTARTS + 1):
        added: list[dict] = []
        modified: list[dict] = []
        removed: list[dict] = []
        cursor = start_cursor
        try:
            while True:
                page = gateway.sync_transactions(access_token, cursor)
                added += page["added"]
                modified += page["modified"]
                removed += page["removed"]
                cursor = page["next_cursor"]
                if not page["has_more"]:
                    return FetchedChanges(added, modified, removed, cursor, page.get("transactions_update_status"))
        except PlaidError as err:
            if err.code == MUTATION_DURING_PAGINATION and attempt < MAX_PAGINATION_RESTARTS:
                log.info("Transactions changed during pagination; restarting (attempt %s)", attempt + 1)
                continue
            raise
    raise AssertionError("unreachable")


# ---------------------------------------------------------------- applying

def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, date) else str(value)


def logo_fields(t: dict) -> tuple[str | None, str | None]:
    """(logo_url, merchant_entity_id) from a Plaid transaction: its own logo, else a merchant-type
    counterparty's (payment apps and banks also have counterparty logos)."""
    logo = t.get("logo_url")
    entity = t.get("merchant_entity_id")
    for cp in t.get("counterparties") or []:
        if not logo and cp.get("logo_url") and str(cp.get("type") or "") in ("merchant", "marketplace", "payment_app"):
            logo = cp["logo_url"]
        if not entity and str(cp.get("type") or "") == "merchant" and cp.get("entity_id"):
            entity = cp["entity_id"]
    return logo, entity


# Plaid's location fields, in the order of the location_* columns (migration 0049).
LOCATION_KEYS = ("address", "city", "region", "postal_code", "country", "lat", "lon", "store_number")
LOCATION_COLUMNS = tuple(f"location_{k}" for k in LOCATION_KEYS)


def location_fields(t: dict) -> tuple:
    """Where the transaction happened (Plaid's `location`, user's request 2026-10-28): one value per LOCATION_KEYS,
    None for anything missing or blank (online purchases have none)."""
    where = t.get("location") or {}
    values = []
    for key in LOCATION_KEYS:
        value = where.get(key)
        if isinstance(value, str):
            value = value.strip() or None
        values.append(value)
    return tuple(values)


def transfer_logo_field(t: dict) -> str | None:
    """The logo of the bank Plaid names as the transaction's counterparty (type `financial_institution`; user's
    request 2026-10-20). Kept apart from logo_url: only transfers show it (logos.transfer_logos), so a purchase never
    shows a bank's logo."""
    for cp in t.get("counterparties") or []:
        if str(cp.get("type") or "") == "financial_institution" and cp.get("logo_url"):
            return cp["logo_url"]
    return None


def _upsert_transaction(conn: sqlite3.Connection, account_id: int, t: dict,
                        split: frozenset[str] | None = None, remaps: dict[str, str] | None = None) -> None:
    """Insert or update one transaction. Never overwrites `user_category`.

    Rules run over everything after a sync (rules.apply_all, in sync_all).
    """
    pfc = t.get("personal_finance_category") or {}
    # Our category for Plaid's: a split category's transactions use the detailed subcategory.
    split = categories.split_set(conn) if split is None else split
    plaid_key = categories.plaid_key(pfc.get("primary"), pfc.get("detailed"), split)
    # A category you deleted: its transactions go where you chose (categories.delete_category, migration 0034)
    remaps = categories.remaps(conn) if remaps is None else remaps
    if plaid_key in remaps:
        plaid_key = remaps[plaid_key]
    elif pfc.get("detailed") in remaps:        # a Plaid subcategory you added on its own, then deleted
        plaid_key = remaps[pfc["detailed"]]
    elif plaid_key == pfc.get("detailed") and plaid_key not in categories.DETAILED_SPLITS.values():
        categories.ensure_subcategory(conn, pfc["primary"], plaid_key)
    conn.execute(
        """
        INSERT INTO transactions (
            plaid_transaction_id, account_id, date, authorized_date, amount_cents,
            iso_currency_code, name, merchant_name, plaid_category_primary,
            plaid_category_detailed, plaid_category_confidence, pending, pending_transaction_id,
            plaid_category_key, logo_url, merchant_entity_id, original_description, transfer_logo_url,
            location_address, location_city, location_region, location_postal_code, location_country,
            location_lat, location_lon, location_store_number, account_owner
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (plaid_transaction_id) DO UPDATE SET
            account_id = excluded.account_id,
            date = excluded.date,
            authorized_date = excluded.authorized_date,
            amount_cents = excluded.amount_cents,
            iso_currency_code = excluded.iso_currency_code,
            name = excluded.name,
            merchant_name = excluded.merchant_name,
            plaid_category_primary = excluded.plaid_category_primary,
            plaid_category_detailed = excluded.plaid_category_detailed,
            plaid_category_confidence = excluded.plaid_category_confidence,
            pending = excluded.pending,
            pending_transaction_id = excluded.pending_transaction_id,
            -- Plaid's OTHER became Uncategorized for new transactions only (2026-10-25): one stored as Other stays Other
            plaid_category_key = CASE WHEN transactions.plaid_category_key = 'OTHER' AND excluded.plaid_category_primary = 'OTHER'
                                      THEN 'OTHER' ELSE excluded.plaid_category_key END,
            -- A logo once known isn't lost if a later update leaves it out.
            logo_url = COALESCE(excluded.logo_url, transactions.logo_url),
            merchant_entity_id = COALESCE(excluded.merchant_entity_id, transactions.merchant_entity_id),
            transfer_logo_url = COALESCE(excluded.transfer_logo_url, transactions.transfer_logo_url),
            -- The raw bank text (destination rules); kept if an update leaves it out.
            original_description = COALESCE(excluded.original_description, transactions.original_description),
            -- Where it happened (migration 0049); each part kept if an update leaves it out.
            location_address = COALESCE(excluded.location_address, transactions.location_address),
            location_city = COALESCE(excluded.location_city, transactions.location_city),
            location_region = COALESCE(excluded.location_region, transactions.location_region),
            location_postal_code = COALESCE(excluded.location_postal_code, transactions.location_postal_code),
            location_country = COALESCE(excluded.location_country, transactions.location_country),
            location_lat = COALESCE(excluded.location_lat, transactions.location_lat),
            location_lon = COALESCE(excluded.location_lon, transactions.location_lon),
            location_store_number = COALESCE(excluded.location_store_number, transactions.location_store_number),
            -- Who used the card (migration 0051); kept if an update leaves it out.
            account_owner = COALESCE(excluded.account_owner, transactions.account_owner),
            -- Plaid sent it again: it wasn't removed after all (migration 0038).
            removed_at = NULL,
            returned_id = NULL,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            t["transaction_id"],
            account_id,
            _iso(t["date"]),
            _iso(t.get("authorized_date")),
            to_cents(t["amount"]),  # Plaid sign: positive = money out
            t.get("iso_currency_code"),
            t.get("name") or "",
            t.get("merchant_name"),
            pfc.get("primary"),
            pfc.get("detailed"),
            pfc.get("confidence_level"),
            1 if t.get("pending") else 0,
            t.get("pending_transaction_id"),
            plaid_key,
            *logo_fields(t),
            t.get("original_description"),
            transfer_logo_field(t),
            *location_fields(t),
            (t.get("account_owner") or "").strip() or None,
        ),
    )
    # Pending -> posted: carry your category over from the pending version.
    if t.get("pending_transaction_id"):
        conn.execute(
            """
            UPDATE transactions
            SET user_category = (SELECT user_category FROM transactions WHERE plaid_transaction_id = ?)
            WHERE plaid_transaction_id = ? AND user_category IS NULL
            """,
            (t["pending_transaction_id"], t["transaction_id"]),
        )
        # ...and a reward bonus you added to it (migration 0042), unless the posted one already has its own
        conn.execute(
            """
            INSERT OR IGNORE INTO transaction_reward_bonuses (transaction_id, amount)
            SELECT posted.id, b.amount
            FROM transactions pending
            JOIN transaction_reward_bonuses b ON b.transaction_id = pending.id
            JOIN transactions posted ON posted.plaid_transaction_id = ?
            WHERE pending.plaid_transaction_id = ?
            """,
            (t["transaction_id"], t["pending_transaction_id"]),
        )
        # ...and its rewards pair, when you said rewards paid part of it (migration 0063, user's request 2026-11-04)
        conn.execute(
            """UPDATE transactions SET rewards_part_of = (SELECT id FROM transactions WHERE plaid_transaction_id = ?)
               WHERE rewards_part_of = (SELECT id FROM transactions WHERE plaid_transaction_id = ?)""",
            (t["transaction_id"], t["pending_transaction_id"]),
        )
        # ...and a category you accepted on the review card (migration 0053), so it doesn't come back when it posts
        conn.execute(
            """
            UPDATE transactions SET category_reviewed = 1
            WHERE plaid_transaction_id = ? AND EXISTS (
                SELECT 1 FROM transactions pending WHERE pending.plaid_transaction_id = ? AND pending.category_reviewed = 1)
            """,
            (t["transaction_id"], t["pending_transaction_id"]),
        )
        # ...and your tags (tags.py, migration 0048)
        conn.execute(
            """
            INSERT OR IGNORE INTO transaction_tags (transaction_id, tag_id)
            SELECT posted.id, tt.tag_id
            FROM transactions pending
            JOIN transaction_tags tt ON tt.transaction_id = pending.id
            JOIN transactions posted ON posted.plaid_transaction_id = ?
            WHERE pending.plaid_transaction_id = ?
            """,
            (t["transaction_id"], t["pending_transaction_id"]),
        )


def apply_changes(conn: sqlite3.Connection, item_id: int, changes: FetchedChanges, result: ItemSyncResult) -> None:
    """Write all changes and the new cursor atomically."""
    account_ids = {
        row["plaid_account_id"]: row["id"]
        for row in conn.execute("SELECT id, plaid_account_id FROM accounts WHERE item_id = ?", (item_id,))
    }
    split = categories.split_set(conn)   # categories split into Plaid subcategories
    remaps = categories.remaps(conn)     # categories you deleted -> where their transactions go
    with conn:  # one transaction: commits on success, rolls back on any exception
        # Upserts before deletes, so a posted transaction can copy the category
        # from its pending version before that pending row is removed.
        for bucket, counter in ((changes.added, "added"), (changes.modified, "modified")):
            for t in bucket:
                account_id = account_ids.get(t["account_id"])
                if account_id is None:
                    result.skipped_unknown_account += 1
                    continue
                _upsert_transaction(conn, account_id, t, split, remaps)
                setattr(result, counter, getattr(result, counter) + 1)

        # Removed (user's rule 2026-10-22): only PENDING transactions are deleted (a pending charge replaced by its
        # posted version). A POSTED one the bank removed is kept and flagged for review, with your category,
        # description and links (removals.py); its return, if any, is found after the sync.
        removed_ids = [(r["transaction_id"],) for r in changes.removed]
        if removed_ids:
            result.removed = sum(conn.execute("DELETE FROM transactions WHERE plaid_transaction_id = ? AND pending = 1",
                                              i).rowcount for i in removed_ids)
            result.flagged = sum(conn.execute("UPDATE transactions SET removed_at = CURRENT_TIMESTAMP"
                                              " WHERE plaid_transaction_id = ? AND pending = 0 AND removed_at IS NULL",
                                              i).rowcount for i in removed_ids)

        conn.execute(
            # Keep the old cursor if Plaid returns an empty one (data not ready).
            "UPDATE items SET transactions_cursor = COALESCE(NULLIF(?, ''), transactions_cursor),"
            " transactions_status = ? WHERE id = ?",
            (changes.next_cursor, changes.status, item_id),
        )


# ---------------------------------------------------------------- holdings

def _has_investment_accounts(conn: sqlite3.Connection, item_id: int) -> bool:
    return conn.execute(
        # Plaid's type too: an investment account you filed under a cash category still has holdings.
        "SELECT 1 FROM accounts WHERE item_id = ? AND 'investment' IN (type, plaid_type) AND is_hidden = 0",
        (item_id,)
    ).fetchone() is not None


def store_holdings(conn: sqlite3.Connection, item_id: int, data: dict) -> int:
    """Replace each investment account's holdings with Plaid's current list."""
    account_ids = {
        row["plaid_account_id"]: row["id"]
        for row in conn.execute("SELECT id, plaid_account_id FROM accounts WHERE item_id = ?", (item_id,))
    }
    securities = {s["security_id"]: s for s in data["securities"]}
    rows = []
    for h in data["holdings"]:
        account_id = account_ids.get(h["account_id"])
        if account_id is None:
            continue
        sec = securities.get(h["security_id"], {})
        rows.append((
            account_id,
            h["security_id"],
            sec.get("name"),
            sec.get("ticker_symbol"),
            str(sec["type"]) if sec.get("type") else None,
            1 if sec.get("is_cash_equivalent") else 0,
            h.get("quantity"),
            h.get("institution_price"),
            to_cents(h.get("institution_value")),
            to_cents(h.get("cost_basis")),
            _iso(h.get("institution_price_as_of")),
        ))
    with conn:
        # Wipe holdings for every account in this Item (Plaid's list is complete for the
        # Item), so positions you sold disappear, then insert the current list. Not limited
        # to type 'investment': holdings on any other account type would otherwise never be
        # cleared, and the lot merge below would add them on top of last sync's every time.
        conn.execute(
            "DELETE FROM holdings WHERE account_id IN (SELECT id FROM accounts WHERE item_id = ?)",
            (item_id,),
        )
        # Price history for the Overview movers (prices.py): one price per security per price date.
        prices.record(conn, [(r[1], r[3], r[2], r[7], r[10]) for r in rows if not r[5]])
        conn.executemany(
            """
            INSERT INTO holdings (account_id, security_id, name, ticker, security_type, is_cash,
                                  quantity, price, value_cents, cost_basis_cents, price_as_of)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            -- Some institutions list one security more than once per account
            -- (e.g. separate tax lots): combine them into one position. The cost basis is
            -- known only if every lot has one (NULL + x = NULL); a partial sum would mislead.
            ON CONFLICT (account_id, security_id) DO UPDATE SET
                quantity = quantity + excluded.quantity,
                value_cents = COALESCE(value_cents, 0) + COALESCE(excluded.value_cents, 0),
                cost_basis_cents = cost_basis_cents + excluded.cost_basis_cents
            """,
            rows,
        )
    return len(rows)


# ---------------------------------------------------------------- investment transactions

INVESTMENT_HISTORY_DAYS = 730          # Plaid keeps up to 24 months
INVESTMENT_OVERLAP_DAYS = 30           # re-read this much each time, so late-posting changes are picked up


def investment_transactions_start(conn: sqlite3.Connection, item_id: int, today: date | None = None) -> date:
    """Where to start reading: the full history the first time, then a month before the newest one we have."""
    today = today or date.today()
    newest = conn.execute(
        "SELECT MAX(t.date) FROM investment_transactions t JOIN accounts a ON a.id = t.account_id WHERE a.item_id = ?",
        (item_id,),
    ).fetchone()[0]
    if newest is None:
        return today - timedelta(days=INVESTMENT_HISTORY_DAYS)
    return max(date.fromisoformat(newest) - timedelta(days=INVESTMENT_OVERLAP_DAYS), today - timedelta(days=INVESTMENT_HISTORY_DAYS))


def store_investment_transactions(conn: sqlite3.Connection, item_id: int, transactions: list[dict]) -> int:
    """Insert or update investment transactions for this Item's accounts. Returns how many were written.

    Plaid's sign: amount > 0 = cash debited from the account (purchase, withdrawal, fee), < 0 = cash credited
    (contribution, dividend, sale). Enum fields arrive as SDK enums, so they're stored as plain text."""
    account_ids = {
        row["plaid_account_id"]: row["id"]
        for row in conn.execute("SELECT id, plaid_account_id FROM accounts WHERE item_id = ?", (item_id,))
    }
    rows = []
    for t in transactions:
        account_id = account_ids.get(t["account_id"])
        if account_id is None:
            continue
        rows.append((
            t["investment_transaction_id"], account_id, _iso(t["date"]), t.get("name") or "", str(t["type"]),
            str(t["subtype"]) if t.get("subtype") else None, t.get("security_id"), t.get("quantity"), t.get("price"),
            to_cents(t["amount"]), to_cents(t.get("fees")), t.get("iso_currency_code"),
        ))
    with conn:
        conn.executemany(
            """
            INSERT INTO investment_transactions (
                plaid_investment_transaction_id, account_id, date, name, type, subtype, security_id, quantity, price,
                amount_cents, fees_cents, iso_currency_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (plaid_investment_transaction_id) DO UPDATE SET
                account_id = excluded.account_id, date = excluded.date, name = excluded.name, type = excluded.type,
                subtype = excluded.subtype, security_id = excluded.security_id, quantity = excluded.quantity,
                price = excluded.price, amount_cents = excluded.amount_cents, fees_cents = excluded.fees_cents,
                iso_currency_code = excluded.iso_currency_code
            -- unchanged rows are not rewritten, so a quiet sync doesn't bump the page revision
            WHERE investment_transactions.account_id IS NOT excluded.account_id OR investment_transactions.date IS NOT excluded.date
               OR investment_transactions.name IS NOT excluded.name OR investment_transactions.type IS NOT excluded.type
               OR investment_transactions.subtype IS NOT excluded.subtype OR investment_transactions.amount_cents IS NOT excluded.amount_cents
               OR investment_transactions.quantity IS NOT excluded.quantity OR investment_transactions.price IS NOT excluded.price
               OR investment_transactions.fees_cents IS NOT excluded.fees_cents
            """,
            rows,
        )
    return len(rows)


# ---------------------------------------------------------------- per Item / all Items

def sync_item(conn: sqlite3.Connection, gateway: PlaidGateway, cipher: TokenCipher, item_id: int,
              investment_transactions: bool = False) -> ItemSyncResult:
    """`investment_transactions`: also read the Item's investment transactions (Settings.sync_investment_transactions:
    off in Production unless you opted in, because Plaid bills them as a separate subscription)."""
    item = items.get_item(conn, item_id)
    result = ItemSyncResult(item_id=item_id, institution=item["institution_name"] or "Unknown institution")
    access_token = cipher.decrypt(item["access_token_enc"])
    try:
        products = items.item_products(item)
        if products is None:  # Items connected before products were recorded
            conn.execute("UPDATE items SET products = ? WHERE id = ?",
                         (items.products_json(gateway.get_item(access_token)), item_id))
            conn.commit()
            products = items.item_products(items.get_item(conn, item_id))

        items.upsert_accounts(conn, item_id, gateway.get_accounts(access_token))
        conn.commit()

        notes = []
        if "transactions" in products:
            changes = fetch_all_pages(gateway, access_token, item["transactions_cursor"])
            apply_changes(conn, item_id, changes, result)
            result.transactions_status = changes.status
        else:
            notes.append("no Transactions product (balances only)")

        if "investments" in products and _has_investment_accounts(conn, item_id):
            try:
                result.holdings = store_holdings(conn, item_id, gateway.get_holdings(access_token))
            except PlaidError as err:
                if err.needs_login:
                    raise
                # Holdings are informational (balances already include them):
                # don't fail the Item's sync over them.
                notes.append(f"holdings unavailable ({err.code})")

        if investment_transactions and "investments" in products and _has_investment_accounts(conn, item_id):
            try:
                found = gateway.get_investment_transactions(
                    access_token, investment_transactions_start(conn, item_id), date.today())
                result.investment_transactions = store_investment_transactions(conn, item_id, found)
            except PlaidError as err:
                if err.needs_login:
                    raise
                notes.append(f"investment transactions unavailable ({err.code})")

        if "liabilities" in products:
            try:
                loans.store_plaid_liabilities(conn, item_id, gateway.get_liabilities(access_token))
            except PlaidError as err:
                if err.needs_login:
                    raise
                notes.append(f"loan terms unavailable ({err.code})")
        result.note = "; ".join(notes)

        items.mark_ok(conn, item_id)
        conn.execute("UPDATE items SET last_synced_at = CURRENT_TIMESTAMP WHERE id = ?", (item_id,))
        conn.commit()
    except PlaidError as err:
        conn.rollback()
        items.record_error(conn, item_id, err)
        conn.commit()
        result.error = err.code
    if result.skipped_unknown_account:
        log.warning("Item %s: skipped %s transactions for unknown accounts", item_id, result.skipped_unknown_account)
    return result


def sync_all(conn: sqlite3.Connection, gateway: PlaidGateway, cipher: TokenCipher, home: Path,
             investment_transactions: bool = False) -> SyncSummary:
    """Sync every active Item. One Item's failure doesn't stop the others. `investment_transactions` is passed
    through to sync_item; callers give it Settings.sync_investment_transactions."""
    summary = SyncSummary()
    with sync_lock(home):
        for item in items.active_items(conn):
            summary.results.append(sync_item(conn, gateway, cipher, item["id"], investment_transactions))
        # Posted transactions the bank removed: find what looks like their return (before transfer matching, so a
        # removed one that came back is no longer counted and can't pair).
        removals.match_returns(conn)
        # What your rules decide for the new rows (a category can change what pairs)
        rules.apply_all(conn)
        # Pair up transfers across all accounts (both halves may come from different Items).
        transfers.detect_transfers(conn)
        # ...and rules' recurring and purpose actions, once transfers are known
        rules.apply_links(conn)
        # Link new transfers to purpose accounts by their rules (e.g. money lent) and rebuild those balances.
        purposes.update(conn)
        # Re-detect recurring income/bills; advances confirmed items' next dates.
        recurring.refresh(conn)
        # Today's net-worth snapshot, from whatever balances we now have
        # (accounts whose Item failed keep their last known balance).
        networth.take_snapshot(conn)
    log.info(summary.describe())
    return summary


def backfill_original_descriptions(conn: sqlite3.Connection, gateway: PlaidGateway, cipher: TokenCipher,
                                   dry_run: bool = False, today: date | None = None) -> list[dict]:
    """Fill in the raw bank text (original_description, migration 0030) for transactions synced before fintrack kept it:
    /transactions/get over the history Plaid keeps, for every active Item with Transactions (read-only at Plaid). Only
    that one field of transactions fintrack already has is written, and only where it's missing or different.
    `dry_run` writes nothing and only counts. Run under the sync lock (cli: `fintrack descriptions --backfill`)."""
    from fintrack.plaid_client import TRANSACTION_HISTORY_DAYS, PlaidError

    today = today or date.today()
    report = []
    for item in items.active_items(conn):
        if "transactions" not in (items.item_products(item) or set()):
            continue
        row = {"item": item["id"], "fetched": 0, "with_text": 0, "updated": 0, "error": None}
        try:
            found = gateway.get_transactions(cipher.decrypt(item["access_token_enc"]),
                                             today - timedelta(days=TRANSACTION_HISTORY_DAYS), today,
                                             logos=False, original_description=True)
        except PlaidError as err:
            row["error"] = err.code
            report.append(row)
            continue
        row["fetched"] = len(found)
        with conn:
            for t in found:
                text = t.get("original_description")
                row["with_text"] += bool(text)
                if dry_run or not text:
                    continue
                row["updated"] += conn.execute(
                    "UPDATE transactions SET original_description = ? WHERE plaid_transaction_id = ?"
                    " AND original_description IS NOT ?", (text, t["transaction_id"], text)).rowcount
            if dry_run:
                conn.rollback()
        report.append(row)
    return report


def backfill_locations(conn: sqlite3.Connection, gateway: PlaidGateway, cipher: TokenCipher,
                       dry_run: bool = False, today: date | None = None, item_id: int | None = None) -> list[dict]:
    """Fill in where transactions happened (location_*, migration 0049; user's request 2026-10-28) for transactions
    synced before fintrack kept it: /transactions/get over the history Plaid keeps, for every active Item with
    Transactions (read-only at Plaid, like backfill_original_descriptions). Only the location columns of transactions
    fintrack already has are written, and only parts that are missing (a known value is never cleared). `dry_run`
    writes nothing and only counts. Run under the sync lock (cli: `fintrack locations --backfill`).
    The same fetch fills in who used the card (`account_owner`, migration 0051; user's request 2026-10-29) where it's
    missing. `item_id`: only that Item (`--item`)."""
    from fintrack.plaid_client import TRANSACTION_HISTORY_DAYS, PlaidError

    today = today or date.today()
    fill = ", ".join(f"{c} = COALESCE({c}, ?)" for c in LOCATION_COLUMNS)
    missing = " OR ".join(f"({c} IS NULL AND ? IS NOT NULL)" for c in LOCATION_COLUMNS)
    report = []
    for item in items.active_items(conn):
        if "transactions" not in (items.item_products(item) or set()) or (item_id and item["id"] != item_id):
            continue
        row = {"item": item["id"], "fetched": 0, "with_location": 0, "updated": 0, "with_owner": 0, "owners_set": 0,
               "error": None}
        try:
            found = gateway.get_transactions(cipher.decrypt(item["access_token_enc"]),
                                             today - timedelta(days=TRANSACTION_HISTORY_DAYS), today, logos=False)
        except PlaidError as err:
            row["error"] = err.code
            report.append(row)
            continue
        row["fetched"] = len(found)
        with conn:
            for t in found:
                owner = (t.get("account_owner") or "").strip() or None
                if owner:
                    row["with_owner"] += 1
                    if not dry_run:
                        row["owners_set"] += conn.execute(
                            "UPDATE transactions SET account_owner = ? WHERE plaid_transaction_id = ?"
                            " AND account_owner IS NULL", (owner, t["transaction_id"])).rowcount
                values = location_fields(t)
                if not any(v is not None for v in values):
                    continue
                row["with_location"] += 1
                if dry_run:
                    continue
                row["updated"] += conn.execute(
                    f"UPDATE transactions SET {fill} WHERE plaid_transaction_id = ? AND ({missing})",
                    (*values, t["transaction_id"], *values)).rowcount
        report.append(row)
    return report
