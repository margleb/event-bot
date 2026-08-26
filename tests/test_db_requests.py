import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
import event_bot.db as db
from event_bot.handlers import _send_message_safely
from event_bot.models import Profile


class ConnectionRequestDbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.DB_PATH
        db.DB_PATH = Path(self.temp_dir.name) / "test.db"
        db.init_db()
        db.seed_events()

        with db.get_connection() as conn:
            self.event_id = conn.execute(
                "SELECT id FROM events ORDER BY id LIMIT 1"
            ).fetchone()["id"]

        interests = {
            1: ["музыка", "кино"],
            2: ["музыка", "театр"],
            3: ["музыка"],
            4: ["кино"],
            5: ["театр"],
            6: ["выставки"],
            7: ["спорт"],
        }
        for user_id, user_interests in interests.items():
            db.save_user_profile(
                user_id,
                Profile(interests=user_interests),
                f"User {user_id}",
                f"user{user_id}",
            )
            db.save_intent(user_id, self.event_id, "going")
            db.set_intent_visibility(user_id, self.event_id, True)

    def tearDown(self) -> None:
        db.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_request_is_idempotent_and_rejected_request_cannot_repeat(self) -> None:
        result, request = db.create_connection_request(self.event_id, 1, 2)
        self.assertEqual(result, "created")
        self.assertIsNotNone(request)

        duplicate_result, duplicate = db.create_connection_request(
            self.event_id, 1, 2
        )
        self.assertEqual(duplicate_result, "already")
        self.assertEqual(duplicate.id, request.id)

        self.assertTrue(db.reject_connection_request(request.id, 2))
        rejected_result, _ = db.create_connection_request(self.event_id, 1, 2)
        self.assertEqual(rejected_result, "already")

        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT status FROM requests WHERE from_user = 1 AND to_user = 2"
            ).fetchall()
        self.assertEqual([row["status"] for row in rows], ["rejected"])

        companions = db.find_companions(
            self.event_id,
            1,
            db.get_user_profile(1),
        )
        self.assertNotIn(2, [companion.user_id for companion in companions])

    def test_accept_returns_contacts_only_after_transition(self) -> None:
        result, request = db.create_connection_request(self.event_id, 1, 2)
        self.assertEqual(result, "created")

        notification = db.format_request_notification(request)
        self.assertNotIn("@user1", notification)
        self.assertNotIn("tg://user?id=", notification)
        self.assertIn("User 1", notification)
        self.assertIn("музыка", notification)

        accepted, accepted_request = db.accept_connection_request(request.id, 2)
        self.assertEqual(accepted, "accepted")
        self.assertEqual(accepted_request.from_username, "user1")
        self.assertEqual(accepted_request.to_username, "user2")
        self.assertIn(
            "@user2",
            db.format_contact_message(
                accepted_request.to_name,
                accepted_request.to_user,
                accepted_request.to_username,
                accepted_request.event_title,
            ),
        )

        repeated, repeated_request = db.accept_connection_request(request.id, 2)
        self.assertEqual(repeated, "already")
        self.assertIsNone(repeated_request)

    def test_block_is_symmetric_and_rejects_pending_requests(self) -> None:
        result, request = db.create_connection_request(self.event_id, 1, 2)
        self.assertEqual(result, "created")
        inverse_result, inverse_request = db.create_connection_request(
            self.event_id, 2, 1
        )
        self.assertEqual(inverse_result, "created")
        self.assertTrue(db.block_user(2, 1))

        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT id, status FROM requests WHERE id IN (?, ?)",
                (request.id, inverse_request.id),
            ).fetchall()
        self.assertEqual({row["status"] for row in rows}, {"rejected"})

        profile_1 = db.get_user_profile(1)
        profile_2 = db.get_user_profile(2)
        companions_1 = db.find_companions(self.event_id, 1, profile_1)
        companions_2 = db.find_companions(self.event_id, 2, profile_2)
        self.assertNotIn(2, [item.user_id for item in companions_1])
        self.assertNotIn(1, [item.user_id for item in companions_2])

        blocked_result, _ = db.create_connection_request(self.event_id, 1, 2)
        self.assertEqual(blocked_result, "already")
        blocked_inverse_result, _ = db.create_connection_request(
            self.event_id, 2, 1
        )
        self.assertEqual(blocked_inverse_result, "already")

    def test_sixth_request_in_last_24_hours_is_rejected(self) -> None:
        for to_user in range(2, 7):
            result, _ = db.create_connection_request(
                self.event_id,
                1,
                to_user,
            )
            self.assertEqual(result, "created")

        result, request = db.create_connection_request(self.event_id, 1, 7)
        self.assertEqual(result, "limit")
        self.assertIsNone(request)

        with db.get_connection() as conn:
            amount = conn.execute(
                "SELECT COUNT(*) AS amount FROM requests WHERE from_user = 1"
            ).fetchone()["amount"]
        self.assertEqual(amount, 5)

    def test_requests_older_than_24_hours_do_not_use_daily_limit(self) -> None:
        for to_user in range(2, 7):
            result, _ = db.create_connection_request(
                self.event_id,
                1,
                to_user,
            )
            self.assertEqual(result, "created")

        with db.get_connection() as conn:
            conn.execute(
                """
                UPDATE requests
                SET created_at = datetime('now', '-25 hours')
                WHERE from_user = 1
                """
            )

        result, request = db.create_connection_request(self.event_id, 1, 7)
        self.assertEqual(result, "created")
        self.assertIsNotNone(request)


class RequestMigrationTests(unittest.TestCase):
    def test_init_db_upgrades_previous_schema_without_losing_users(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.db"
            with sqlite3.connect(path) as conn:
                conn.execute(
                    """
                    CREATE TABLE users (
                        telegram_id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL DEFAULT '',
                        interests TEXT NOT NULL DEFAULT '[]',
                        avoid TEXT NOT NULL DEFAULT '[]',
                        days TEXT NOT NULL DEFAULT '[]',
                        budget_rub INTEGER,
                        group_size_min INTEGER,
                        group_size_max INTEGER,
                        created_at TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO users (telegram_id, name) VALUES (1, 'Alice')"
                )
                conn.execute(
                    """
                    CREATE TABLE intents (
                        user_id INTEGER NOT NULL,
                        event_id INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        visible INTEGER NOT NULL DEFAULT 0,
                        visibility_asked INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                        PRIMARY KEY (user_id, event_id)
                    )
                    """
                )

            original_db_path = db.DB_PATH
            db.DB_PATH = path
            try:
                db.init_db()
                with db.get_connection() as conn:
                    user_columns = {
                        row["name"]
                        for row in conn.execute("PRAGMA table_info(users)")
                    }
                    tables = {
                        row["name"]
                        for row in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        )
                    }
                    name = conn.execute(
                        "SELECT name FROM users WHERE telegram_id = 1"
                    ).fetchone()["name"]
            finally:
                db.DB_PATH = original_db_path

        self.assertIn("username", user_columns)
        self.assertTrue({"requests", "blocks"}.issubset(tables))
        self.assertEqual(name, "Alice")


class SafeNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_forbidden_notification_does_not_escape_handler(self) -> None:
        bot = AsyncMock()
        method = SendMessage(chat_id=2, text="notification")
        bot.send_message.side_effect = TelegramForbiddenError(
            method,
            "bot was blocked by the user",
        )

        sent = await _send_message_safely(bot, 2, "notification")

        self.assertFalse(sent)
        bot.send_message.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
