"""Idempotent SQLite schema initialization and additive migrations."""

import sqlite3
from pathlib import Path

from event_bot.persistence.connection import connect


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    username TEXT,
    interests TEXT NOT NULL DEFAULT '[]',
    avoid TEXT NOT NULL DEFAULT '[]',
    days TEXT NOT NULL DEFAULT '[]',
    budget_rub INTEGER,
    group_size_min INTEGER,
    group_size_max INTEGER,
    profile_embedding BLOB,
    profile_embedding_model TEXT,
    avoid_embedding BLOB,
    avoid_embedding_model TEXT,
    digest_weekday INTEGER,
    digest_enabled INTEGER NOT NULL DEFAULT 0,
    last_digest_date TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    city TEXT NOT NULL,
    address TEXT NOT NULL,
    date TEXT NOT NULL,
    end_date TEXT,
    price_min INTEGER,
    price_max INTEGER,
    price_text TEXT,
    is_free INTEGER,
    tags TEXT NOT NULL DEFAULT '[]',
    venue TEXT NOT NULL,
    source_id TEXT,
    external_id TEXT,
    source_url TEXT,
    fetched_at TEXT,
    status TEXT,
    embedding BLOB,
    embedding_model TEXT,
    content_hash TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS event_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id),
    status TEXT NOT NULL DEFAULT 'forming',
    target_size INTEGER NOT NULL DEFAULT 3,
    meeting_point TEXT,
    meeting_point_by INTEGER REFERENCES users(telegram_id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    activated_at TEXT
);

CREATE TABLE IF NOT EXISTS event_group_members (
    group_id INTEGER NOT NULL REFERENCES event_groups(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(telegram_id),
    rsvp TEXT,
    joined_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (group_id, user_id)
);

CREATE TABLE IF NOT EXISTS event_group_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES event_groups(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(telegram_id),
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events(id),
    from_user INTEGER NOT NULL REFERENCES users(telegram_id),
    to_user INTEGER NOT NULL REFERENCES users(telegram_id),
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (event_id, from_user, to_user)
);

CREATE TABLE IF NOT EXISTS blocks (
    blocker INTEGER NOT NULL REFERENCES users(telegram_id),
    blocked INTEGER NOT NULL REFERENCES users(telegram_id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (blocker, blocked)
);

CREATE TABLE IF NOT EXISTS intents (
    -- An intent can predate a confirmed profile (legacy Telegram callbacks).
    user_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL REFERENCES events(id),
    status TEXT NOT NULL,
    visible INTEGER NOT NULL DEFAULT 0,
    visibility_asked INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, event_id)
);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    event_name TEXT NOT NULL,
    source TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS feedback_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    username TEXT,
    message TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    answered_at TEXT
);

CREATE TABLE IF NOT EXISTS admin_report_deliveries (
    admin_id INTEGER NOT NULL,
    report_date TEXT NOT NULL,
    delivered_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (admin_id, report_date)
);

CREATE TABLE IF NOT EXISTS inactivity_feedback_prompts (
    user_id INTEGER PRIMARY KEY,
    prompt_sent_at TEXT NOT NULL DEFAULT (datetime('now')),
    delivery_status TEXT NOT NULL DEFAULT 'pending',
    response_code TEXT,
    responded_at TEXT
);

CREATE TABLE IF NOT EXISTS user_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id INTEGER NOT NULL,
    reported_id INTEGER NOT NULL,
    event_group_id INTEGER,
    event_id INTEGER,
    reason TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_suspensions (
    user_id INTEGER PRIMARY KEY,
    reason TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    lifted_at TEXT
);

CREATE TABLE IF NOT EXISTS event_group_deliveries (
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'claimed',
    delivered_at TEXT,
    PRIMARY KEY (group_id, user_id, kind)
);

CREATE TABLE IF NOT EXISTS event_experience_feedback (
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (group_id, user_id)
);

CREATE TABLE IF NOT EXISTS user_acquisition (
    user_id INTEGER PRIMARY KEY,
    campaign TEXT NOT NULL,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS source_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    status TEXT NOT NULL,
    added INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    fetched INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);
"""


ADDITIVE_COLUMNS = {
    "users": {
        "name": "TEXT NOT NULL DEFAULT ''",
        "username": "TEXT",
        "profile_embedding": "BLOB",
        "profile_embedding_model": "TEXT",
        "avoid_embedding": "BLOB",
        "avoid_embedding_model": "TEXT",
        "digest_weekday": "INTEGER",
        "digest_enabled": "INTEGER NOT NULL DEFAULT 0",
        "last_digest_date": "TEXT",
    },
    "intents": {
        "visibility_asked": "INTEGER NOT NULL DEFAULT 0",
    },
    "events": {
        "end_date": "TEXT",
        "price_text": "TEXT",
        "is_free": "INTEGER",
        "source_id": "TEXT",
        "external_id": "TEXT",
        "source_url": "TEXT",
        "fetched_at": "TEXT",
        "status": "TEXT",
        "embedding": "BLOB",
        "embedding_model": "TEXT",
        "content_hash": "TEXT",
    },
    "event_groups": {
        "target_size": "INTEGER",
    },
}


INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_city_date ON events(city, date)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_source_external ON events(source_id, external_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_status_city_date ON events(status, city, date)",
    "CREATE INDEX IF NOT EXISTS idx_requests_from_created ON requests(from_user, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_requests_pair_status ON requests(from_user, to_user, status)",
    "CREATE INDEX IF NOT EXISTS idx_event_groups_event_status ON event_groups(event_id, status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_event_group_members_user ON event_group_members(user_id, joined_at)",
    "CREATE INDEX IF NOT EXISTS idx_event_group_messages_created ON event_group_messages(group_id, created_at, id)",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_created ON usage_events(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_user_created ON usage_events(user_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_name_created ON usage_events(event_name, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_status_created ON feedback_messages(status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_inactivity_feedback_sent ON inactivity_feedback_prompts(prompt_sent_at)",
    "CREATE INDEX IF NOT EXISTS idx_inactivity_feedback_response ON inactivity_feedback_prompts(response_code, responded_at)",
    "CREATE INDEX IF NOT EXISTS idx_user_reports_status_created ON user_reports(status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_user_suspensions_active ON user_suspensions(lifted_at, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_event_group_deliveries_kind ON event_group_deliveries(kind, status, delivered_at)",
    "CREATE INDEX IF NOT EXISTS idx_event_feedback_outcome ON event_experience_feedback(outcome, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_user_acquisition_campaign ON user_acquisition(campaign, first_seen_at)",
    "CREATE INDEX IF NOT EXISTS idx_source_sync_runs_source_finished ON source_sync_runs(source_id, finished_at)",
)


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_schema(db_path: Path) -> None:
    """Create the current schema without deleting historical legacy tables."""
    with connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(SCHEMA)
        for table, columns in ADDITIVE_COLUMNS.items():
            for column, definition in columns.items():
                _add_column_if_missing(conn, table, column, definition)
        conn.execute(
            """
            UPDATE event_groups
            SET target_size = CASE
                WHEN status = 'active' THEN MAX(
                    2,
                    MIN(
                        5,
                        (SELECT COUNT(*) FROM event_group_members gm
                         WHERE gm.group_id = event_groups.id)
                    )
                )
                ELSE 3
            END
            WHERE target_size IS NULL OR target_size < 2 OR target_size > 5
            """
        )
        conn.execute(
            """
            UPDATE event_groups
            SET status = 'forming', activated_at = NULL
            WHERE status = 'active'
              AND (SELECT COUNT(*) FROM event_group_members gm
                   WHERE gm.group_id = event_groups.id) < target_size
            """
        )
        conn.execute("DROP INDEX IF EXISTS idx_events_title_date")
        for statement in INDEXES:
            conn.execute(statement)
