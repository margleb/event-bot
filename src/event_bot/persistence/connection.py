"""Low-level SQLite connection policy.

All three application processes use the same database file, so connection
settings must live in one place and be identical everywhere.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DEFAULT_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "bot.db"
DB_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
SQLITE_BUSY_TIMEOUT_MS = 15_000


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a transaction-scoped connection with the shared safety policy."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        db_path,
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
    )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
