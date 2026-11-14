"""Authorized users' cards (user's request 2026-10-29): one account, several cards. Plaid's `account_owner` on a
transaction (migration 0051) is, for one card issuer, the last 4 of the card used (observed 2026-10-29; fintrack import-csv
stores the download's Card No. there too). A transaction is an **authorized user's** when that value looks like a card's
last 4 (2-4 letters or digits) and isn't the account's own mask; anything else (no value, the account's own card, or a
name, which other banks might send) is the primary card's.

Used by the Transactions page: "(AU)" after the account name, and the account filter's cards under each account
(`cards_by_account`; filter values `<id>:main` / `<id>:<last 4>`, web/transaction_filters.py).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

# The same test in SQL, on transactions_effective's columns (account_owner, account_mask).
AU_SQL = ("(account_owner IS NOT NULL AND length(account_owner) BETWEEN 2 AND 4"
          " AND account_owner NOT GLOB '*[^0-9A-Za-z]*'"
          " AND account_owner <> COALESCE(account_mask, ''))")

_CARD = re.compile(r"[0-9A-Za-z]{2,4}")


def is_card_number(text: str) -> bool:
    """Whether text looks like a card's last 4 (2-4 letters or digits), as banks send it."""
    return bool(_CARD.fullmatch(text or ""))


def is_authorized_user(owner: str | None, account_mask: str | None) -> bool:
    return bool(owner and _CARD.fullmatch(owner) and owner != (account_mask or ""))


@dataclass(frozen=True)
class Card:
    value: str          # the filter's value: "<account id>:main" or "<account id>:<last 4>"
    last4: str | None   # None: the primary card without a mask
    au: bool


def cards_by_account(conn: sqlite3.Connection) -> dict[int, list[Card]]:
    """{account id: its cards, primary first, then each authorized user's by last 4} for visible accounts that have
    authorized users' transactions; other accounts aren't listed (they're one card)."""
    found: dict[int, list[Card]] = {}
    for account_id, mask, owner in conn.execute(
            f"""SELECT account_id, account_mask, account_owner FROM transactions_effective
                WHERE account_hidden = 0 AND {AU_SQL}
                GROUP BY account_id, account_owner ORDER BY account_id, account_owner"""):
        cards = found.setdefault(account_id, [Card(f"{account_id}:main", mask, False)])
        cards.append(Card(f"{account_id}:{owner}", owner, True))
    return found


@dataclass(frozen=True)
class CardSummary:
    card: Card
    count: int                  # its transactions, all time
    spent_cents: int            # spending (kind expense; refunds lower it) since `since`
    last_used: str | None       # its latest transaction date


def card_summaries(conn: sqlite3.Connection, account_id: int, since: str) -> list[CardSummary]:
    """An account's cards with their use (the account page's Cards card, user's request 2026-10-29): the primary card
    (with any row without a card) first, then each authorized user's. Empty for an account with no AU card."""
    cards = cards_by_account(conn).get(account_id)
    if not cards:
        return []
    used = {r[0]: r[1:] for r in conn.execute(
        f"""SELECT CASE WHEN {AU_SQL} THEN account_owner ELSE 'main' END AS card, COUNT(*),
                   COALESCE(SUM(CASE WHEN kind = 'expense' AND date >= ? THEN amount_cents END), 0), MAX(date)
            FROM transactions_effective WHERE account_id = ? GROUP BY card""", (since, account_id))}
    return [CardSummary(c, *(used.get(c.value.split(":", 1)[1]) or (0, 0, None))) for c in cards]
