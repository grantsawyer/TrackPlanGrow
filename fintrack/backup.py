"""Dated database backups.

Uses SQLite's online backup API rather than copying the file, so a backup
taken while the app or a sync is running is still consistent.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


def backup_database(db_path: Path, backups_dir: Path, now: datetime | None = None) -> Path:
    if not db_path.exists():
        raise FileNotFoundError(f"No database at {db_path}")
    backups_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    # e.g. fintrack-production-2026-10-02_093000.db (stem carries the environment)
    target = backups_dir / f"{db_path.stem}-{stamp}.db"

    source = sqlite3.connect(db_path)
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()
    target.chmod(0o600)
    return target
