from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import event_bot.db as db
from event_bot.admin_dashboard import build_admin_dashboard


def add_usage_event(
    user_id: int,
    event_name: str,
    created_at: str,
    source: str = "bot",
) -> None:
    with db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO usage_events (user_id, event_name, source, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, event_name, source, created_at),
        )


def test_admin_dashboard_measures_frequency_and_real_usage(temp_db, monkeypatch):
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "99")
    add_usage_event(1, "command.start", "2026-08-28 08:00:00")
    add_usage_event(1, "command.find", "2026-08-28 08:05:00")
    add_usage_event(1, "miniapp.open", "2026-08-29 08:00:00", "miniapp")
    add_usage_event(2, "command.start", "2026-08-29 09:00:00")
    add_usage_event(3, "command.start", "2026-08-20 09:00:00")
    add_usage_event(99, "command.find", "2026-08-29 10:00:00")

    report = build_admin_dashboard(
        7,
        now=datetime(2026, 8, 29, 15, tzinfo=ZoneInfo("Europe/Moscow")),
    )

    summary = report["summary"]
    assert summary["known_users"] == 3
    assert summary["active_users"] == 2
    assert summary["previous_active_users"] == 1
    assert summary["new_users"] == 2
    assert summary["engaged_users"] == 1
    assert summary["returning_users"] == 1
    assert summary["dormant_users"] == 1
    assert summary["visits"] == 3
    assert summary["actions"] == 4
    assert summary["actions_per_active"] == 2.0
    assert summary["active_days_per_user"] == 1.5
    assert report["frequency"]["one_day"] == 1
    assert report["frequency"]["two_three_days"] == 1
    assert len(report["daily"]) == 7
    assert sum(day["actions"] for day in report["daily"]) == 4
    assert report["top_features"][0]["label"] == "Запуск бота"
    assert {source["label"] for source in report["sources"]} == {
        "Telegram-бот",
        "Mini App",
    }


def test_admin_dashboard_rejects_arbitrary_period(temp_db):
    with pytest.raises(ValueError):
        build_admin_dashboard(14)
