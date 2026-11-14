"""Item lifecycle: connect, repair, refresh, remove.

This is where the Production Item budget is enforced:

* A new-Item Link session in Production requires (a) Items remaining and
  (b) the explicit confirmation phrase. Consent is written to `link_sessions`
  BEFORE Plaid is asked for a link token.
* Duplicates are refused BEFORE the public token is exchanged (Plaid only
  creates the Item when an access token is requested).
* Once the token is exchanged the Item exists at Plaid and counts against the
  budget, so `complete_create` always records it, even without a matching session.
* Repairs use Link update mode, which never creates an Item.
* Removal keeps Production rows forever (the slot is not refunded).
"""

from __future__ import annotations

import json
import logging
import sqlite3

from fintrack import account_names
from fintrack.config import Settings
from fintrack.crypto import TokenCipher
from fintrack.item_budget import production_item_budget
from fintrack.money import to_cents
from fintrack.plaid_client import LINK_KINDS, PlaidError, PlaidGateway

log = logging.getLogger(__name__)

# What you must type to start a Production Link session that creates an Item.
CONSENT_PHRASE = "CONNECT"

LIABILITY_TYPES = {"credit", "loan"}


class ConsentError(Exception):
    """A Production Link session was refused (no consent or no Items left)."""


class DuplicateItemError(Exception):
    """Link succeeded for an institution/login that's already connected.
    Raised BEFORE the public token is exchanged, so no Item is created."""


# ---------------------------------------------------------------- reads

def active_items(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM items WHERE status != 'removed' ORDER BY institution_name"
    ).fetchall()


def removed_items(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM items WHERE status = 'removed' ORDER BY removed_at DESC"
    ).fetchall()


def accounts_for_item(conn: sqlite3.Connection, item_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM accounts WHERE item_id = ? AND is_hidden = 0 ORDER BY type, name", (item_id,)
    ).fetchall()


def get_item(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise LookupError(f"No item {item_id}")
    return row


def connected_institution_ids(conn: sqlite3.Connection) -> list[str]:
    """Institutions with an active Item, used to warn before connecting a duplicate."""
    return [
        r[0] for r in conn.execute(
            "SELECT DISTINCT institution_id FROM items WHERE status != 'removed' AND institution_id IS NOT NULL"
        )
    ]


# ---------------------------------------------------------------- connect a new Item

def start_create_session(
    conn: sqlite3.Connection,
    settings: Settings,
    gateway: PlaidGateway,
    kind: str,
    consent_phrase: str = "",
) -> tuple[int, str]:
    """Check consent, log the session, and return (session_id, link_token)."""
    if kind not in LINK_KINDS:
        raise ValueError(f"Unknown connection kind {kind!r}")

    if settings.is_production:
        budget = production_item_budget(conn, settings.production_item_limit)
        if budget.exhausted:
            raise ConsentError(
                f"All {budget.limit} Production Items are used. Plaid's Trial plan allows no more."
            )
        if consent_phrase.strip() != CONSENT_PHRASE:
            raise ConsentError(f"Type {CONSENT_PHRASE} to confirm using a Production Item.")
        cur = conn.execute(
            "INSERT INTO link_sessions (purpose, plaid_env, kind, consented_at, items_used_at_consent)"
            " VALUES ('create', 'production', ?, CURRENT_TIMESTAMP, ?)",
            (kind, budget.used),
        )
        log.warning("Production Link session %s consented (%s of %s Items used)",
                    cur.lastrowid, budget.used, budget.limit)
    else:
        cur = conn.execute(
            "INSERT INTO link_sessions (purpose, plaid_env, kind) VALUES ('create', 'sandbox', ?)", (kind,)
        )
    conn.commit()
    return cur.lastrowid, gateway.create_link_token(kind=kind)


def check_duplicate(
    conn: sqlite3.Connection,
    institution_id: str | None,
    link_accounts: list[dict],
    allow_duplicate: bool,
) -> None:
    """Refuse a Link result that duplicates an active Item, before any exchange.

    Plaid's guidance: a duplicate Item is only created if an access token is
    requested, so compare onSuccess metadata (institution, account name + mask)
    with existing Items first and simply don't exchange duplicates.

    * Same institution as an active Item -> refused unless `allow_duplicate`
      (you said this is a second, separate login).
    * Same institution AND an identical account (name + mask) -> always refused:
      that's the same login again, whatever the checkbox says.
    """
    if not institution_id:
        return
    existing = conn.execute(
        "SELECT id, institution_name FROM items WHERE status != 'removed' AND institution_id = ?",
        (institution_id,),
    ).fetchall()
    if not existing:
        return

    name = existing[0]["institution_name"] or "This institution"
    known_accounts = {
        (row["name"], row["mask"])
        for row in conn.execute(
            f"SELECT name, mask FROM accounts WHERE is_hidden = 0 AND item_id IN"
            f" ({','.join('?' * len(existing))})",
            [row["id"] for row in existing],
        )
    }
    incoming = {(a.get("name"), a.get("mask")) for a in link_accounts}
    if known_accounts & incoming:
        raise DuplicateItemError(
            f"{name} is already connected with the same accounts, so this is the same login. "
            "Nothing was saved and no Item was used. To fix a broken login, use Reconnect on the Accounts page."
        )
    if not allow_duplicate:
        raise DuplicateItemError(
            f"{name} is already connected. Nothing was saved and no Item was used. "
            "To fix a broken login, use Reconnect. If this really is a second, separate login, "
            "tick that box on the Connect page and try again."
        )


def complete_create(
    conn: sqlite3.Connection,
    settings: Settings,
    gateway: PlaidGateway,
    cipher: TokenCipher,
    session_id: int | None,
    public_token: str,
    institution_id: str | None = None,
    link_accounts: list[dict] | None = None,
    allow_duplicate: bool = False,
) -> dict:
    """Check for duplicates, then exchange the public token and store the new
    Item and its accounts.

    `institution_id` and `link_accounts` ([{name, mask}]) come from Link's
    onSuccess metadata. Raises DuplicateItemError without exchanging when the
    login is already connected.

    Returns {'item_id', 'institution_name', 'duplicate'}; `duplicate` is True when
    you deliberately added a second login at an already-connected institution.
    """
    check_duplicate(conn, institution_id, link_accounts or [], allow_duplicate)

    existing_institutions = set(connected_institution_ids(conn))
    exchanged = gateway.exchange_public_token(public_token)

    # Record the Item immediately, before any further call can fail. It now
    # exists at Plaid and (in Production) counts against the budget.
    cur = conn.execute(
        "INSERT INTO items (plaid_item_id, plaid_env, access_token_enc) VALUES (?, ?, ?)",
        (exchanged["item_id"], settings.plaid_env, cipher.encrypt(exchanged["access_token"])),
    )
    item_id = cur.lastrowid
    session = conn.execute(
        "SELECT id FROM link_sessions WHERE id = ? AND purpose = 'create' AND completed_at IS NULL",
        (session_id,),
    ).fetchone() if session_id else None
    if session:
        conn.execute(
            "UPDATE link_sessions SET completed_at = CURRENT_TIMESTAMP, item_id = ? WHERE id = ?",
            (item_id, session["id"]),
        )
    else:
        log.warning("Item %s was created without a matching Link session", item_id)
    conn.commit()

    # Institution details and accounts. Failures here leave the Item recorded
    # with an error you can retry via "Refresh".
    access_token = exchanged["access_token"]
    institution_id = institution_name = None
    try:
        plaid_item = gateway.get_item(access_token)
        institution_id = plaid_item.get("institution_id")
        institution_name = gateway.get_institution_name(institution_id) if institution_id else None
        conn.execute(
            "UPDATE items SET institution_id = ?, institution_name = ?, products = ? WHERE id = ?",
            (institution_id, institution_name, products_json(plaid_item), item_id),
        )
        upsert_accounts(conn, item_id, gateway.get_accounts(access_token))
        mark_ok(conn, item_id)
    except PlaidError as err:
        record_error(conn, item_id, err)
    conn.commit()

    return {
        "item_id": item_id,
        "institution_name": institution_name or "Unknown institution",
        "duplicate": institution_id in existing_institutions,
    }


def products_json(plaid_item: dict) -> str:
    """The Item's enabled products from /item/get, as a sorted JSON array."""
    products = {str(p) for p in (plaid_item.get("products") or [])}
    products |= {str(p) for p in (plaid_item.get("billed_products") or [])}
    return json.dumps(sorted(products))


def item_products(item: sqlite3.Row) -> set[str] | None:
    """Products recorded for an Item, or None if not recorded yet."""
    return set(json.loads(item["products"])) if item["products"] else None


# ---------------------------------------------------------------- accounts

def classify(account_type: str) -> str:
    """Credit cards and loans are liabilities; everything else is an asset."""
    return "liability" if account_type in LIABILITY_TYPES else "asset"


def upsert_accounts(conn: sqlite3.Connection, item_id: int, accounts: list[dict]) -> None:
    """Insert new accounts and update existing ones (matched by Plaid account_id).

    `accounts` is the Item's full list from /accounts/get. Accounts no longer in it
    (you stopped sharing them via "Add or remove accounts") are hidden, keeping
    their history; accounts shared again reappear.
    """
    # Automatic names, distinct within this connection (account_names.unique_names; user's request 2026-10-14)
    auto_names = dict(zip([acct["account_id"] for acct in accounts], account_names.unique_names(
        [{"plaid_name": acct.get("name") or "Account", "official_name": acct.get("official_name"),
          "mask": str(acct["mask"])[-4:] if acct.get("mask") else None} for acct in accounts])))
    for acct in accounts:
        account_type = str(acct.get("type") or "other")
        balances = acct.get("balances") or {}
        mask = str(acct["mask"])[-4:] if acct.get("mask") else None  # last 4 only, always
        plaid_name = acct.get("name") or "Account"
        conn.execute(
            """
            INSERT INTO accounts (
                item_id, plaid_account_id, name, plaid_name, official_name, mask, type, plaid_type, subtype,
                classification, current_balance_cents, available_balance_cents,
                iso_currency_code, balance_updated_at, reported_balance_cents
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT (plaid_account_id) DO UPDATE SET
                -- A name you set yourself survives syncs; otherwise follow Plaid's.
                name = CASE WHEN accounts.name_is_custom = 1 THEN accounts.name ELSE excluded.name END,
                plaid_name = excluded.plaid_name,
                official_name = excluded.official_name,
                mask = excluded.mask,
                -- With a type you gave it (account_types.py, migration 0032), that decides type and
                -- classification; Plaid's type is still recorded.
                type = CASE WHEN accounts.type_kind IS NULL THEN excluded.type ELSE accounts.type END,
                plaid_type = excluded.plaid_type,
                subtype = excluded.subtype,
                classification = CASE WHEN accounts.type_kind IS NULL THEN excluded.classification
                                      ELSE accounts.classification END,
                current_balance_cents = excluded.current_balance_cents,
                -- What the institution reported; live_values.py adds the market move since (investment accounts).
                reported_balance_cents = excluded.current_balance_cents,
                available_balance_cents = excluded.available_balance_cents,
                iso_currency_code = excluded.iso_currency_code,
                balance_updated_at = CURRENT_TIMESTAMP
            """,
            (
                item_id,
                acct["account_id"],
                auto_names[acct["account_id"]],
                plaid_name,
                acct.get("official_name"),
                mask,
                account_type,
                account_type,
                str(acct["subtype"]) if acct.get("subtype") else None,
                classify(account_type),
                to_cents(balances.get("current")),
                to_cents(balances.get("available")),
                balances.get("iso_currency_code"),
                to_cents(balances.get("current")),            # reported_balance_cents
            ),
        )

    # Shared-account visibility. Skipped for an empty list so a glitch can't hide everything.
    shared = [acct["account_id"] for acct in accounts]
    if shared:
        placeholders = ",".join("?" * len(shared))
        conn.execute(
            f"UPDATE accounts SET is_hidden = CASE WHEN plaid_account_id IN ({placeholders}) THEN 0 ELSE 1 END"
            " WHERE item_id = ?",
            [*shared, item_id],
        )


def refresh_accounts(conn: sqlite3.Connection, gateway: PlaidGateway, cipher: TokenCipher, item_id: int) -> None:
    """Re-read accounts and cached balances via the free /accounts/get."""
    item = get_item(conn, item_id)
    try:
        upsert_accounts(conn, item_id, gateway.get_accounts(cipher.decrypt(item["access_token_enc"])))
        mark_ok(conn, item_id)
    except PlaidError as err:
        record_error(conn, item_id, err)
    conn.commit()


# ---------------------------------------------------------------- status

def mark_ok(conn: sqlite3.Connection, item_id: int) -> None:
    conn.execute(
        "UPDATE items SET status = 'ok', error_code = NULL, error_message = NULL WHERE id = ?", (item_id,)
    )


def record_error(conn: sqlite3.Connection, item_id: int, err: PlaidError) -> None:
    status = "login_required" if err.needs_login else "error"
    conn.execute(
        "UPDATE items SET status = ?, error_code = ?, error_message = ? WHERE id = ?",
        (status, err.code, err.message, item_id),
    )
    log.info("Item %s: %s", item_id, err.code)


# ---------------------------------------------------------------- repair (update mode)

def start_update_session(
    conn: sqlite3.Connection, settings: Settings, gateway: PlaidGateway, cipher: TokenCipher, item_id: int,
    account_selection: bool = False,
) -> tuple[int, str]:
    """Update-mode Link token for an existing Item. Never creates an Item.

    `account_selection=True` lets you add or remove which accounts this Item
    shares (e.g. a card you didn't select the first time), instead of spending
    another Item on a new connection.
    """
    item = get_item(conn, item_id)
    if item["status"] == "removed":
        raise ValueError("This Item was removed and can't be repaired.")
    cur = conn.execute(
        "INSERT INTO link_sessions (purpose, plaid_env, item_id) VALUES ('update', ?, ?)",
        (settings.plaid_env, item_id),
    )
    conn.commit()
    return cur.lastrowid, gateway.create_link_token(
        access_token=cipher.decrypt(item["access_token_enc"]), account_selection=account_selection
    )


def complete_update(
    conn: sqlite3.Connection, gateway: PlaidGateway, cipher: TokenCipher, session_id: int
) -> int:
    """After update mode succeeds: same Item, same access token. Clear the error and refresh."""
    session = conn.execute(
        "SELECT * FROM link_sessions WHERE id = ? AND purpose = 'update'", (session_id,)
    ).fetchone()
    if session is None:
        raise LookupError(f"No update session {session_id}")
    conn.execute("UPDATE link_sessions SET completed_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))
    conn.commit()
    refresh_accounts(conn, gateway, cipher, session["item_id"])
    return session["item_id"]


# ---------------------------------------------------------------- remove

def remove_item(
    conn: sqlite3.Connection, gateway: PlaidGateway, cipher: TokenCipher, item_id: int, typed_name: str
) -> None:
    """Revoke at Plaid, wipe the token, hide the accounts. Production rows stay forever."""
    item = get_item(conn, item_id)
    expected = (item["institution_name"] or "").strip().lower()
    if not expected or typed_name.strip().lower() != expected:
        raise ValueError("Type the institution name exactly to confirm removal.")

    if item["access_token_enc"]:
        try:
            gateway.remove_item(cipher.decrypt(item["access_token_enc"]))
        except PlaidError as err:
            # Already gone at Plaid: fine, finish the local cleanup.
            if err.code not in {"ITEM_NOT_FOUND", "INVALID_ACCESS_TOKEN"}:
                raise

    conn.execute(
        "UPDATE items SET status = 'removed', removed_at = CURRENT_TIMESTAMP,"
        " access_token_enc = NULL, transactions_cursor = NULL WHERE id = ?",
        (item_id,),
    )
    # Accounts and their history are kept for past net-worth records, just hidden.
    conn.execute("UPDATE accounts SET is_hidden = 1 WHERE item_id = ?", (item_id,))
    conn.commit()


# ---------------------------------------------------------------- sandbox testing aid

def simulate_login_required(
    conn: sqlite3.Connection, settings: Settings, gateway: PlaidGateway, cipher: TokenCipher, item_id: int
) -> None:
    if settings.is_production:
        raise RuntimeError("Sandbox-only")
    item = get_item(conn, item_id)
    gateway.sandbox_reset_login(cipher.decrypt(item["access_token_enc"]))
    refresh_accounts(conn, gateway, cipher, item_id)  # now fails with ITEM_LOGIN_REQUIRED
