from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import event_bot.db as db
from event_bot.inactivity_feedback import (
    INACTIVITY_FEEDBACK_TEXT,
    dispatch_inactivity_feedback,
)


def add_known_user(user_id: int, created_at: str) -> None:
    with db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO usage_events (user_id, event_name, source, created_at)
            VALUES (?, 'command.start', 'bot', ?)
            """,
            (user_id, created_at),
        )


def test_inactive_users_are_selected_once_and_admins_are_excluded(
    temp_db,
    monkeypatch,
):
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "99")
    add_known_user(1, "2026-08-10 10:00:00")
    add_known_user(2, "2026-08-29 10:00:00")
    add_known_user(99, "2026-08-10 10:00:00")

    cutoff = datetime(2026, 8, 22, 15, tzinfo=ZoneInfo("UTC"))
    assert db.get_inactive_feedback_user_ids(
        cutoff,
        excluded_user_ids={99},
    ) == [1]

    assert db.claim_inactivity_feedback_prompt(1) is True
    assert db.claim_inactivity_feedback_prompt(1) is False
    db.mark_inactivity_feedback_delivery(1, "sent")
    assert db.get_inactive_feedback_user_ids(
        cutoff,
        excluded_user_ids={99},
    ) == []


async def test_dispatch_sends_after_hour_and_never_repeats(temp_db, monkeypatch):
    monkeypatch.setenv("INACTIVITY_FEEDBACK_DAYS", "7")
    monkeypatch.setenv("INACTIVITY_FEEDBACK_HOUR_MSK", "18")
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "")
    add_known_user(7, "2026-08-10 10:00:00")
    bot = SimpleNamespace(send_message=AsyncMock())

    before = datetime(2026, 8, 29, 17, tzinfo=ZoneInfo("Europe/Moscow"))
    assert await dispatch_inactivity_feedback(bot, now=before) == 0
    bot.send_message.assert_not_awaited()

    due = datetime(2026, 8, 29, 18, tzinfo=ZoneInfo("Europe/Moscow"))
    assert await dispatch_inactivity_feedback(bot, now=due) == 1
    bot.send_message.assert_awaited_once()
    args, kwargs = bot.send_message.await_args
    assert args == (7, INACTIVITY_FEEDBACK_TEXT)
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == (
        "inactive_feedback:no_events"
    )

    assert await dispatch_inactivity_feedback(bot, now=due) == 0
    assert bot.send_message.await_count == 1


def test_first_inactivity_response_wins(temp_db):
    assert db.claim_inactivity_feedback_prompt(42)
    db.mark_inactivity_feedback_delivery(42, "sent")

    assert db.save_inactivity_feedback_response(42, "confusing") is True
    assert db.save_inactivity_feedback_response(42, "other") is False
    assert db.save_inactivity_feedback_response(100, "not_now") is False
    assert db.save_inactivity_feedback_response(42, "unknown") is False

    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT response_code, responded_at FROM inactivity_feedback_prompts "
            "WHERE user_id = 42"
        ).fetchone()
    assert row["response_code"] == "confusing"
    assert row["responded_at"] is not None
