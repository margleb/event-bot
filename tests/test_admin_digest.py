from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

import event_bot.db as db
from event_bot.admin_digest import dispatch_daily_admin_reports
from event_bot.analytics import build_daily_admin_report, format_daily_admin_report


def add_usage_event(user_id: int, event_name: str, created_at: str) -> None:
    with db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO usage_events (user_id, event_name, source, created_at)
            VALUES (?, ?, 'bot', ?)
            """,
            (user_id, event_name, created_at),
        )


def test_daily_report_compares_two_periods_and_excludes_admins(
    temp_db,
    monkeypatch,
):
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "99")
    add_usage_event(1, "command.start", "2026-08-28 08:00:00")
    add_usage_event(1, "miniapp.open", "2026-08-28 09:00:00")
    add_usage_event(2, "command.find", "2026-08-28 10:00:00")
    add_usage_event(3, "miniapp.open", "2026-08-27 12:00:00")
    add_usage_event(99, "miniapp.open", "2026-08-28 11:00:00")

    report = build_daily_admin_report(
        now=datetime(2026, 8, 29, 10, tzinfo=ZoneInfo("Europe/Moscow")),
    )

    assert report["active"] == 2
    assert report["active_previous"] == 1
    assert report["new_users"] == 2
    assert report["visits"] == 2
    assert report["visits_previous"] == 1
    assert report["actions"] == 3
    assert report["actions_previous"] == 1
    assert report["known_users"] == 3
    assert ("command.find", 1) in report["top_features"]
    text = format_daily_admin_report(report)
    assert "Ежедневная сводка" in text
    assert "Подбор событий" in text
    assert "сутками ранее 1" in text


@pytest.mark.asyncio
async def test_daily_report_is_sent_once_after_configured_hour(
    temp_db,
    monkeypatch,
):
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "99")
    monkeypatch.setenv("ADMIN_DAILY_REPORT_HOUR_MSK", "10")
    bot = SimpleNamespace(send_message=AsyncMock())
    early = datetime(2026, 8, 29, 9, tzinfo=ZoneInfo("Europe/Moscow"))
    due = datetime(2026, 8, 29, 10, tzinfo=ZoneInfo("Europe/Moscow"))

    assert await dispatch_daily_admin_reports(bot, now=early) == 0
    assert await dispatch_daily_admin_reports(bot, now=due) == 1
    assert await dispatch_daily_admin_reports(bot, now=due) == 0
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args[0] == 99
    assert "Ежедневная сводка" in bot.send_message.await_args.args[1]
