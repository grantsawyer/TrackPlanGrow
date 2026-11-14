"""Tracking the Plaid Trial plan's Production Item budget.

The Trial plan allows a fixed number of Production Items (10), and Plaid does
not refund a slot when an Item is removed. Every Production Item this app has
ever created therefore counts, including removed ones. The `items` table keeps
Production rows forever (a trigger blocks deleting them), so counting them gives
the used total.

The Plaid dashboard is the real source of truth. If this database is lost or
restored from an old backup, check the dashboard before connecting anything.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class ItemBudget:
    limit: int
    used: int  # includes removed Items

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit


def production_item_budget(conn: sqlite3.Connection, limit: int) -> ItemBudget:
    (used,) = conn.execute("SELECT COUNT(*) FROM items WHERE plaid_env = 'production'").fetchone()
    return ItemBudget(limit=limit, used=used)
