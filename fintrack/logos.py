"""Merchant logos (user's request and approval 2026-10-08).

Plaid's transaction data carries a logo address per transaction (logo_url; sync.logo_fields). The
image itself is a plain file on Plaid's logo servers, so fintrack downloads each one ONCE, on the
server, into FINTRACK_HOME/logos/, and the browser only ever loads it from fintrack (/logos/...):
the CSP stays `default-src 'self'`, nothing about what you view reaches Plaid, and logos work
offline. This module is the only code that downloads logos.

Safety: only https addresses on ALLOWED_HOSTS are fetched; the answer must be a PNG (signature
checked) no larger than MAX_BYTES; files are named by a hash of the address (nothing from Plaid
reaches a file name) with private permissions. Failures are remembered (logo_files.status) and
retried after RETRY_FAILED_DAYS, so a broken address doesn't slow every sync.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
import sqlite3
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

log = logging.getLogger(__name__)

ALLOWED_HOSTS = {"plaid-merchant-logos.plaid.com", "plaid-counterparty-logos.plaid.com"}
MAX_BYTES = 256 * 1024              # Plaid's logos are 100x100 PNGs, a few KB
PER_RUN = 200                       # downloads per sync at most; the rest follow next time
TIMEOUT_SECONDS = 5
PARALLEL = 4
RETRY_FAILED_DAYS = 7
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def logos_dir(home: Path) -> Path:
    path = home / "logos"
    path.mkdir(mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def file_name(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest() + ".png"


def allowed(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS


def _http_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "fintrack"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:   # noqa: S310 (allowlisted https)
        return response.read(MAX_BYTES + 1)


def _download(url: str, fetch: Callable[[str], bytes]) -> bytes | None:
    try:
        data = fetch(url)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        log.warning("Logo unavailable (%s)", type(exc).__name__)
        return None
    if len(data) > MAX_BYTES or not data.startswith(PNG_SIGNATURE):
        log.warning("Logo refused: not a small PNG")
        return None
    return data


def fetch_missing(conn: sqlite3.Connection, home: Path, fetch: Callable[[str], bytes] = _http_get,
                  limit: int = PER_RUN) -> tuple[int, int]:
    """Download logos that transactions point to (logo_url, and transfer_logo_url for transfers) and fintrack doesn't have yet (or whose earlier
    attempt failed more than RETRY_FAILED_DAYS ago). Returns (saved, failed)."""
    wanted = [r[0] for r in conn.execute(
        f"""
        WITH urls AS (SELECT logo_url AS url FROM transactions WHERE logo_url IS NOT NULL
                      UNION SELECT transfer_logo_url FROM transactions WHERE transfer_logo_url IS NOT NULL)
        SELECT u.url FROM urls u
        LEFT JOIN logo_files f ON f.url = u.url
        WHERE f.url IS NULL OR (f.status = 'failed' AND f.fetched_at < datetime('now', '-{RETRY_FAILED_DAYS} days'))
        """)]
    wanted = [u for u in wanted if allowed(u)][:limit]
    if not wanted:
        return 0, 0
    folder = logos_dir(home)
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        results = list(pool.map(lambda u: (u, _download(u, fetch)), wanted))
    saved = failed = 0
    with conn:
        for url, data in results:
            if data is None:
                failed += 1
                conn.execute("INSERT INTO logo_files (url, file, status) VALUES (?, NULL, 'failed')"
                             " ON CONFLICT (url) DO UPDATE SET status = 'failed', fetched_at = CURRENT_TIMESTAMP", (url,))
                continue
            name = file_name(url)
            path = folder / name
            path.write_bytes(data)
            os.chmod(path, 0o600)
            saved += 1
            conn.execute("INSERT INTO logo_files (url, file, status) VALUES (?, ?, 'ok')"
                         " ON CONFLICT (url) DO UPDATE SET file = excluded.file, status = 'ok',"
                         " fetched_at = CURRENT_TIMESTAMP", (url, name))
    if saved or failed:
        log.info("Logos: %d saved, %d unavailable", saved, failed)
    return saved, failed


def lookup(conn: sqlite3.Connection) -> tuple[dict[str, str], dict[str, str]]:
    """({logo_url: file}, {merchant_entity_id: file}) for the logos fintrack has. The second lets a
    transaction without its own logo use one from the same merchant (e.g. older transactions)."""
    by_url = {r[0]: r[1] for r in conn.execute("SELECT url, file FROM logo_files WHERE status = 'ok'")}
    by_merchant = {r[0]: r[1] for r in conn.execute(
        """SELECT t.merchant_entity_id, MAX(f.file) FROM transactions t
           JOIN logo_files f ON f.url = t.logo_url AND f.status = 'ok'
           WHERE t.merchant_entity_id IS NOT NULL GROUP BY t.merchant_entity_id""")}
    return by_url, by_merchant


def transfer_logos(conn: sqlite3.Connection, investment_accounts: dict[int, int] | None = None) -> dict[int, str]:
    """{transaction id: logo file} for transfers (kind 'transfer') whose other side fintrack knows (user's request
    2026-10-20): money in shows where it came from, money out where it went. First the account on the other side, by
    its institution's logo (institution_logo_files):

        1. a money-lent account you or a rule linked it to   (purpose_links)
        2. the other half of a matched transfer               (transfer_pair_id; a card payment shows the card's bank)
        3. the investment / retirement account it paired with, or a destination rule names (contributions)

    else the bank Plaid names as its counterparty (transfer_logo_url). Transfers with neither fall back to the row's
    usual logo, then its category's icon (_txn_row.html). `investment_accounts`: contributions.transfer_accounts already
    worked out by the caller (it's the slow part)."""
    from fintrack import contributions        # imported here: contributions imports visuals, which is heavier

    institution = institution_logo_files(conn)
    other: dict[int, int] = {}
    if investment_accounts is None:
        investment_accounts = contributions.transfer_accounts(conn)
    for tid, account_id in investment_accounts.items():
        other.setdefault(tid, account_id)
    for r in conn.execute("""SELECT t.id, p.account_id FROM transactions t JOIN transactions p ON p.id = t.transfer_pair_id
                             WHERE t.is_transfer = 1"""):
        other[r[0]] = r[1]                                           # a matched pair beats the brokerage matching
    for r in conn.execute("SELECT transaction_id, account_id FROM purpose_links WHERE account_id IS NOT NULL"):
        other[r[0]] = r[1]                                           # your link (or a rule's) beats both
    files = {tid: institution[a] for tid, a in other.items() if a in institution}
    for r in conn.execute("""SELECT t.id, f.file FROM transactions t
                             JOIN logo_files f ON f.url = t.transfer_logo_url AND f.status = 'ok'"""):
        files.setdefault(r[0], r[1])
    if not files:
        return {}
    transfers = {r[0] for r in conn.execute("SELECT id FROM transactions_effective WHERE kind = 'transfer'")}
    return {tid: f for tid, f in files.items() if tid in transfers}


# ---------------------------------------------------------------- one-time backfill

def backfill(conn: sqlite3.Connection, gateway, cipher, home: Path, dry_run: bool = False,
             today=None) -> list[dict]:
    """Fill in logo_url / merchant_entity_id / transfer_logo_url for transactions synced before fintrack kept them:
    /transactions/get over the history Plaid keeps, for every active Item with Transactions.
    Only those logo fields of transactions fintrack already has are touched (never categories,
    amounts or your edits). `dry_run` writes nothing and only counts. Then downloads the images."""
    from datetime import date, timedelta

    from fintrack import items, sync
    from fintrack.plaid_client import TRANSACTION_HISTORY_DAYS, PlaidError

    today = today or date.today()
    report = []
    for item in items.active_items(conn):
        if "transactions" not in (items.item_products(item) or set()):
            continue
        row = {"item": item["id"], "fetched": 0, "with_logo": 0, "with_merchant_id": 0, "with_bank_logo": 0,
               "updated": 0, "error": None}
        try:
            found = gateway.get_transactions(cipher.decrypt(item["access_token_enc"]),
                                             today - timedelta(days=TRANSACTION_HISTORY_DAYS), today)
        except PlaidError as err:
            row["error"] = err.code
            report.append(row)
            continue
        row["fetched"] = len(found)
        with conn:
            for t in found:
                logo, entity = sync.logo_fields(t)
                bank_logo = sync.transfer_logo_field(t)
                row["with_logo"] += bool(logo)
                row["with_merchant_id"] += bool(entity)
                row["with_bank_logo"] += bool(bank_logo)
                if dry_run or not (logo or entity or bank_logo):
                    continue
                row["updated"] += conn.execute(
                    """UPDATE transactions SET logo_url = COALESCE(?, logo_url),
                                               merchant_entity_id = COALESCE(?, merchant_entity_id),
                                               transfer_logo_url = COALESCE(?, transfer_logo_url)
                       WHERE plaid_transaction_id = ?
                         AND (logo_url IS NOT ? OR merchant_entity_id IS NOT ? OR transfer_logo_url IS NOT ?)""",
                    (logo, entity, bank_logo, t["transaction_id"], logo, entity, bank_logo)).rowcount
            if dry_run:
                conn.rollback()
        report.append(row)
    if not dry_run:
        fetch_missing(conn, home)
    return report


# ---------------------------------------------------------------- institution logos (Balances page, 2026-10-13)

RETRY_INSTITUTION_DAYS = 30


def fetch_institution_logos(conn: sqlite3.Connection, home: Path, gateway) -> tuple[int, int]:
    """Each connected institution's logo, once (gateway.get_institution_logo: Plaid's free /institutions/get_by_id;
    no Item, no bank data). Saved as <sha256 of the image>.png in the logos folder (mode 600), refused unless it's a
    PNG no larger than MAX_BYTES. Institutions with no logo, or whose call failed, are asked again after
    RETRY_INSTITUTION_DAYS. Never raises: a logo is never worth failing a sync. Returns (saved, without)."""
    from fintrack.plaid_client import PlaidError

    wanted = [r[0] for r in conn.execute(
        f"""
        SELECT DISTINCT i.institution_id FROM items i
        LEFT JOIN institution_logos l ON l.institution_id = i.institution_id
        WHERE i.institution_id IS NOT NULL
          AND (l.institution_id IS NULL
               OR (l.status <> 'ok' AND l.fetched_at < datetime('now', '-{RETRY_INSTITUTION_DAYS} days')))
        """)]
    saved = without = 0
    for institution_id in wanted:
        try:
            encoded = gateway.get_institution_logo(institution_id)
        except (PlaidError, OSError, ValueError) as exc:
            log.warning("Institution logo unavailable (%s)", type(exc).__name__)
            with conn:
                conn.execute("INSERT INTO institution_logos (institution_id, file, status) VALUES (?, NULL, 'failed')"
                             " ON CONFLICT (institution_id) DO UPDATE SET file = NULL, status = 'failed',"
                             " fetched_at = CURRENT_TIMESTAMP", (institution_id,))
            without += 1
            continue
        status = _save_logo(conn, home, institution_id, encoded)
        saved += status == "ok"
        without += status != "ok"
    if wanted:
        log.info("Institution logos: %d saved, %d without", saved, without)
    return saved, without


def institution_logo_files(conn: sqlite3.Connection) -> dict[int, str]:
    """account id -> its institution's saved logo file (accounts of institutions without one are left out): a
    connected account's institution, or the institution a manual account's name was matched to (institution_searches)."""
    files = {r[0]: r[1] for r in conn.execute(
        """SELECT a.id, l.file FROM accounts a JOIN items i ON i.id = a.item_id
           JOIN institution_logos l ON l.institution_id = i.institution_id WHERE l.status = 'ok'""")}
    files.update({r[0]: r[1] for r in conn.execute(
        """SELECT a.id, l.file FROM accounts a
           JOIN institution_searches s ON s.query = lower(trim(a.manual_institution)) AND s.status = 'ok'
           JOIN institution_logos l ON l.institution_id = s.institution_id AND l.status = 'ok'
           WHERE a.is_manual = 1""")})
    return files


def _save_logo(conn: sqlite3.Connection, home: Path, institution_id: str, encoded: str | None) -> str:
    """Save a base64 logo for an institution (a small PNG, else refused); record it. Returns the status."""
    status, name = "none", None
    if encoded:
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            data = b""
        if len(data) <= MAX_BYTES and data.startswith(PNG_SIGNATURE):
            name = hashlib.sha256(data).hexdigest() + ".png"
            path = logos_dir(home) / name
            path.write_bytes(data)
            os.chmod(path, 0o600)
            status = "ok"
        else:
            log.warning("Institution logo refused: not a small PNG")
    with conn:
        conn.execute("INSERT INTO institution_logos (institution_id, file, status) VALUES (?, ?, ?)"
                     " ON CONFLICT (institution_id) DO UPDATE SET file = excluded.file, status = excluded.status,"
                     " fetched_at = CURRENT_TIMESTAMP", (institution_id, name, status))
    return status


def find_manual_institution_logos(conn: sqlite3.Connection, home: Path, gateway, only: str | None = None) -> int:
    """Manual accounts' institutions (user's request 2026-10-13): each typed name not searched yet (or searched without
    a match more than RETRY_INSTITUTION_DAYS ago) goes to Plaid's /institutions/search (free, no Item); an exact name
    match wins, else the first result; its logo is saved like a connected institution's. `only` = just this name (the
    account page, when you save it). Never raises. Returns how many names found a match."""
    from fintrack.plaid_client import PlaidError

    names = [only.strip().lower()] if only and only.strip() else [r[0] for r in conn.execute(
        f"""SELECT DISTINCT lower(trim(a.manual_institution)) FROM accounts a
            LEFT JOIN institution_searches s ON s.query = lower(trim(a.manual_institution))
            WHERE a.is_manual = 1 AND trim(COALESCE(a.manual_institution, '')) <> ''
              AND (s.query IS NULL OR (s.status <> 'ok' AND s.checked_at < datetime('now', '-{RETRY_INSTITUTION_DAYS} days')))""")]
    found = 0
    for query in names:
        try:
            results = gateway.search_institutions(query)
            status = "ok" if results else "none"
        except (PlaidError, OSError, ValueError) as exc:
            log.warning("Institution search unavailable (%s)", type(exc).__name__)
            results, status = [], "failed"
        match = next((r for r in results if r["name"].strip().lower() == query), results[0] if results else None)
        if match:
            _save_logo(conn, home, match["institution_id"], match.get("logo"))
            found += 1
        with conn:
            conn.execute("INSERT INTO institution_searches (query, institution_id, status) VALUES (?, ?, ?)"
                         " ON CONFLICT (query) DO UPDATE SET institution_id = excluded.institution_id,"
                         " status = excluded.status, checked_at = CURRENT_TIMESTAMP",
                         (query, match["institution_id"] if match else None, status))
    return found
