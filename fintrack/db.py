"""SQLite access and a minimal migration runner.

Migrations are plain .sql files in fintrack/migrations named NNNN_description.sql.
Each runs once, in order, inside a transaction; applied versions are recorded
in the schema_migrations table. To change the schema, add a new file. Never
edit one that has already been applied.

Money is stored as integer cents everywhere to avoid floating-point drift.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(db_path: Path) -> sqlite3.Connection:
    is_new = not db_path.exists()
    # check_same_thread=False: FastAPI may open a request's connection in a worker
    # thread and use it from the event-loop thread (async routes). Each request
    # has its own connection, used by one piece of code at a time and never
    # shared across requests, so this is safe.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    if is_new:
        db_path.chmod(0o600)  # owner read/write only
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # lets the web app read while a sync writes
    return conn


def revision(conn: sqlite3.Connection) -> int:
    """Counter that rises whenever displayed data changes (triggers in migration 0006)."""
    try:
        row = conn.execute("SELECT n FROM app_revision WHERE id = 1").fetchone()
    except sqlite3.OperationalError:  # migration 0006 not applied yet
        return 0
    return row[0] if row else 0


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def pending_migrations(conn: sqlite3.Connection) -> list[tuple[int, Path]]:
    done = applied_versions(conn)
    found = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = int(path.name.split("_", 1)[0])
        if version not in done:
            found.append((version, path))
    return found


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply pending migrations. Returns the file names applied."""
    applied = []
    for version, path in pending_migrations(conn):
        # executescript() commits any open transaction first, so wrap each
        # file in its own explicit BEGIN/COMMIT to make it all-or-nothing.
        script = path.read_text()
        try:
            conn.executescript(
                f"BEGIN;\n{script}\nINSERT INTO schema_migrations (version) VALUES ({version});\nCOMMIT;"
            )
        except Exception:
            conn.rollback()
            raise
        applied.append(path.name)
    return applied
