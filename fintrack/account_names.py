"""Readable account names.

`accounts.name` is the name every page shows. For Plaid accounts it's derived
from Plaid's own name (kept in `accounts.plaid_name`) by `short_name()` on each
sync, unless you've renamed the account (`name_is_custom = 1`). Manual accounts
keep the name you gave them.

The rules are generic (no per-institution special cases):
  - drop a mask echoed at the end:      "... - ****1234"      -> "..."
  - "Owner - Product - ..." names:      keep the last part     (drops the owner's name)
  - a trailing " Account":              "Roth IRA Account" -> "Roth IRA"
  - generic names ("CREDIT CARD"):      use official_name when it's real text, not a CODE
  - trademark marks:                    "Points®", "AcmeSM Card" -> "Points", "Acme Card"
  - ALL-CAPS names:                     Title Case, keeping short words (ACME, IRA) as-is
"""

from __future__ import annotations

import re
import sqlite3

# Names that only say what kind of account it is.
GENERIC_NAMES = {
    "credit card", "checking", "savings", "brokerage", "account", "card",
    "credit", "loan", "mortgage", "investment", "ira", "401k", "hsa",
}

_MASK_SUFFIX = re.compile(r"\s*[-–]?\s*(?:\*+|x+|\.{2,}|#)\s*\d{2,4}\s*$", re.IGNORECASE)
_MARKS = re.compile(r"[®™℠©]")
_GLUED_SM = re.compile(r"(?<=[a-z])SM\b")      # "AcmeSM Explorer" -> "Acme Explorer"
_CODE = re.compile(r"^[A-Z0-9_]+$")              # "ROTH_IRA", "INDIVIDUAL": an enum, not a name


def _clean(text: str) -> str:
    text = _GLUED_SM.sub("", _MARKS.sub("", text))
    return re.sub(r"\s+", " ", text).strip()


def _title_if_shouting(text: str) -> str:
    """'ACME BANK CLASSIC CHECKING' -> 'ACME BANK Classic Checking'. Mixed-case text is left alone."""
    if not text.isupper():
        return text
    return " ".join(w if len(w) <= 4 else w.capitalize() for w in text.split(" "))


def short_name(plaid_name: str | None, official_name: str | None = None, mask: str | None = None) -> str:
    name = _clean(plaid_name or "")
    if mask:
        # Only strip a trailing number that is this account's mask.
        match = _MASK_SUFFIX.search(name)
        if match and match.group(0).strip().endswith(mask):
            name = name[: match.start()].strip()
    parts = [p.strip() for p in re.split(r"\s+[-–]\s+", name) if p.strip()]
    if len(parts) >= 2:
        name = parts[-1]
    # "Roth IRA Brokerage Account" -> "Roth IRA Brokerage" (but a bare "Account" stays).
    name = re.sub(r"(?<=\S)\s+account$", "", name, flags=re.IGNORECASE)
    if name.lower() in GENERIC_NAMES or not name:
        official = _clean(official_name or "")
        if official and not _CODE.match(official):
            name = official
    name = _title_if_shouting(name)
    return name or "Account"


def rename(conn: sqlite3.Connection, account_id: int, new_name: str) -> None:
    """Set your own name for a Plaid account. Blank = go back to the automatic name."""
    row = conn.execute(
        "SELECT plaid_name, official_name, mask, is_manual FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Unknown account.")
    if row["is_manual"]:
        raise ValueError("Rename manual accounts on their own page.")
    new_name = (new_name or "").strip()
    if len(new_name) > 60:
        raise ValueError("Keep the name to 60 characters or fewer.")
    with conn:
        if new_name:
            conn.execute("UPDATE accounts SET name = ?, name_is_custom = 1 WHERE id = ?", (new_name, account_id))
        else:
            conn.execute("UPDATE accounts SET name = ?, name_is_custom = 0 WHERE id = ?",
                         (auto_name(conn, account_id), account_id))


def unique_names(accounts: list) -> list[str]:
    """Automatic names for one connection's accounts (each with plaid_name / official_name / mask), made distinct
    (user's request 2026-10-14): when two would get the same name (e.g. two cards a bank calls "Fake Rewards"), each
    of those gets " ····<last 4>". Decided over all the connection's accounts, renamed or not, so every caller (sync,
    startup, clearing a name) agrees and nothing flips back and forth."""
    def field(a, key):
        try:
            return a[key]
        except (KeyError, IndexError):
            return None
    bases = [short_name(field(a, "plaid_name") or field(a, "name"), field(a, "official_name"), field(a, "mask"))
             for a in accounts]
    counts = {b: bases.count(b) for b in bases}
    return [f"{b} ····{field(a, 'mask')}" if counts[b] > 1 and field(a, "mask") else b for a, b in zip(accounts, bases)]


def auto_name(conn: sqlite3.Connection, account_id: int) -> str:
    """The automatic name of one Plaid account, distinct within its connection (unique_names)."""
    item_id = conn.execute("SELECT item_id FROM accounts WHERE id = ?", (account_id,)).fetchone()[0]
    rows = conn.execute("SELECT id, plaid_name, official_name, mask FROM accounts WHERE item_id = ? AND plaid_name IS NOT NULL"
                        " ORDER BY id", (item_id,)).fetchall()
    return dict(zip([r["id"] for r in rows], unique_names(rows)))[account_id]


def refresh_auto_names(conn: sqlite3.Connection) -> None:
    """Recompute automatic names for Plaid accounts you haven't renamed (distinct within each connection)."""
    rows = conn.execute(
        "SELECT id, item_id, plaid_name, official_name, mask, name_is_custom FROM accounts"
        " WHERE is_manual = 0 AND plaid_name IS NOT NULL ORDER BY item_id, id"
    ).fetchall()
    autos: dict[int, str] = {}
    for item_id in {r["item_id"] for r in rows}:
        mine = [r for r in rows if r["item_id"] == item_id]
        autos.update(zip([r["id"] for r in mine], unique_names(mine)))
    with conn:
        for r in rows:
            if r["name_is_custom"]:
                continue
            auto = autos[r["id"]]
            # Only write real changes, so the page-revision triggers don't fire for nothing.
            conn.execute("UPDATE accounts SET name = ? WHERE id = ? AND name <> ?", (auto, r["id"], auto))
