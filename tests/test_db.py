import sqlite3
import stat
from datetime import datetime

import pytest

from fintrack import db
from fintrack.backup import backup_database
from fintrack.item_budget import production_item_budget


def add_item(conn, plaid_item_id, env):
    conn.execute(
        "INSERT INTO items (plaid_item_id, plaid_env, access_token_enc) VALUES (?, ?, ?)",
        (plaid_item_id, env, b"encrypted"),
    )
    conn.commit()


def test_migrations_apply_once(conn):
    assert db.pending_migrations(conn) == []
    assert db.migrate(conn) == []


def test_database_file_is_private(settings, conn):
    mode = stat.S_IMODE(settings.db_path.stat().st_mode)
    assert mode == 0o600


def test_production_items_cannot_be_deleted(conn):
    add_item(conn, "item-prod", "production")
    with pytest.raises(sqlite3.IntegrityError, match="permanent ledger"):
        conn.execute("DELETE FROM items WHERE plaid_item_id = 'item-prod'")


def test_sandbox_items_can_be_deleted(conn):
    add_item(conn, "item-sb", "sandbox")
    conn.execute("DELETE FROM items WHERE plaid_item_id = 'item-sb'")


def test_item_budget_counts_removed_items_and_ignores_sandbox(conn):
    add_item(conn, "p1", "production")
    add_item(conn, "p2", "production")
    add_item(conn, "s1", "sandbox")
    conn.execute("UPDATE items SET status = 'removed', access_token_enc = NULL WHERE plaid_item_id = 'p2'")
    budget = production_item_budget(conn, limit=10)
    assert (budget.used, budget.remaining, budget.exhausted) == (2, 8, False)
    assert production_item_budget(conn, limit=2).exhausted


def test_mask_longer_than_four_rejected(conn):
    add_item(conn, "p1", "sandbox")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO accounts (item_id, plaid_account_id, name, mask, type, classification)"
            " VALUES (1, 'acc', 'Checking', '123456789', 'depository', 'asset')"
        )


def test_backup_creates_dated_private_copy(settings, conn):
    add_item(conn, "p1", "sandbox")
    target = backup_database(settings.db_path, settings.backups_dir, now=datetime(2026, 10, 2, 9, 30, 0))
    assert target.name == "fintrack-sandbox-2026-10-02_093000.db"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    copy = sqlite3.connect(target)
    assert copy.execute("SELECT plaid_item_id FROM items").fetchone()[0] == "p1"
    copy.close()
