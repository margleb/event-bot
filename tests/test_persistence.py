import event_bot.db as db


def test_sqlite_connection_policy_is_shared(temp_db):
    with db.get_connection() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]

    assert journal_mode == "wal"
    assert busy_timeout == 15_000
    assert foreign_keys == 1


def test_fresh_schema_contains_only_event_scoped_companies(temp_db):
    with db.get_connection() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert "event_groups" in tables
    assert "event_group_members" in tables
    assert "interest_groups" not in tables
    assert "interest_group_members" not in tables


def test_initialization_preserves_unknown_historical_tables(temp_db):
    with db.get_connection() as conn:
        conn.execute("CREATE TABLE historical_groups (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO historical_groups (id) VALUES (7)")

    db.init_db()

    with db.get_connection() as conn:
        row = conn.execute("SELECT id FROM historical_groups").fetchone()
    assert row["id"] == 7
