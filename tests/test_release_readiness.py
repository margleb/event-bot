from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import event_bot.db as db
import event_bot.webapp as webapp_module
from event_bot.admin_dashboard import build_admin_dashboard
from event_bot.event_group_notifications import dispatch_event_group_notifications
from event_bot.webapp import app
from event_bot.web_auth import authenticated_user
from tests.test_webapp import BOT_TOKEN, auth_headers


def test_company_uses_exact_preferred_size_and_ranks_shared_interests(
    user_factory,
    event_factory,
):
    event_id = event_factory(title="Событие на четверых")
    first = user_factory(
        user_id=1,
        interests=["Кино", "Джаз"],
        group_size_min=4,
        group_size_max=4,
    )[0]
    similar = user_factory(
        user_id=2,
        interests=["Кино"],
        group_size_min=4,
        group_size_max=4,
    )[0]

    _, group_id, _ = db.join_event_group(first, event_id)
    _, joined_group_id, _ = db.join_event_group(similar, event_id)

    assert joined_group_id == group_id
    group = db.get_event_group(group_id, first)
    assert group is not None
    assert group["minimum_members"] == group["maximum_members"] == 4
    assert group["status"] == "forming"


@pytest.mark.asyncio
async def test_company_reminder_and_experience_prompt_are_sent_once(
    user_factory,
    event_factory,
    monkeypatch,
):
    monkeypatch.setenv("MINIAPP_URL", "https://bot.example/r/app?build=1")
    current = datetime.now(ZoneInfo("Europe/Moscow")).replace(microsecond=0)
    event_id = event_factory(
        title="Кино с компанией",
        date=current.replace(tzinfo=None) + timedelta(hours=12),
    )
    first = user_factory(user_id=10)[0]
    second = user_factory(user_id=11)[0]
    _, group_id, _ = db.join_event_group(first, event_id)
    db.join_event_group(second, event_id)
    bot = SimpleNamespace(send_message=AsyncMock())

    assert await dispatch_event_group_notifications(bot, now=current) == 2
    assert await dispatch_event_group_notifications(bot, now=current) == 0
    assert bot.send_message.await_count == 2
    assert "уже завтра" in bot.send_message.await_args_list[0].args[1]

    after = current + timedelta(hours=16)
    assert await dispatch_event_group_notifications(bot, now=after) == 2
    assert await dispatch_event_group_notifications(bot, now=after) == 0
    assert bot.send_message.await_count == 4
    assert "Удалось сходить на мероприятие" in bot.send_message.await_args_list[2].args[1]
    assert db.save_event_experience_feedback(group_id, first, "met")


def test_report_block_privacy_and_account_deletion(
    user_factory,
    event_factory,
    monkeypatch,
):
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(webapp_module, "_notify_event_company", AsyncMock(return_value=0))
    monkeypatch.setattr(webapp_module, "_notify_admins_about_report", AsyncMock())
    event_id = event_factory(title="Безопасная компания")
    first = user_factory(user_id=20, username="firstsafe")[0]
    second = user_factory(user_id=21, username="secondsafe")[0]

    with TestClient(app) as client:
        client.post(f"/r/api/events/{event_id}/company", headers=auth_headers(first))
        group_response = client.post(
            f"/r/api/events/{event_id}/company", headers=auth_headers(second)
        )
        group = group_response.json()["event_group"]
        target = next(member for member in group["members"] if not member["is_me"])
        report = client.post(
            f"/r/api/event-groups/{group['id']}/members/{target['member_key']}/report",
            headers=auth_headers(second),
            json={"reason": "unsafe", "details": "Агрессивное общение"},
        )
        blocked = client.post(
            f"/r/api/event-groups/{group['id']}/members/{target['member_key']}/block",
            headers=auth_headers(second),
        )
        privacy = client.get("/r/privacy")
        deleted = client.delete("/r/api/account", headers=auth_headers(second))

    assert report.status_code == 200
    assert blocked.json() == {"status": "blocked"}
    assert privacy.status_code == 200
    assert "Конфиденциальность" in privacy.text
    assert deleted.json() == {"status": "deleted"}
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM user_reports").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM users WHERE telegram_id = 21").fetchone()[0] == 0


def test_acquisition_funnel_and_source_health_are_in_dashboard(
    user_factory,
    event_factory,
    monkeypatch,
):
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "99")
    user_id = user_factory(user_id=30)[0]
    assert db.record_user_acquisition(user_id, "club_singles")
    assert not db.record_user_acquisition(user_id, "another_campaign")
    event_id = event_factory(title="Событие из рекламы")
    db.save_intent(user_id, event_id, "interested")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE user_acquisition SET first_seen_at = ? WHERE user_id = ?",
            ((now - timedelta(hours=2)).strftime(db.DB_DATETIME_FORMAT), user_id),
        )
        conn.executemany(
            "INSERT INTO usage_events (user_id, event_name, source, created_at) VALUES (?, ?, ?, ?)",
            [
                (user_id, "command.start", "bot", (now - timedelta(hours=2)).strftime(db.DB_DATETIME_FORMAT)),
                (user_id, "miniapp.open", "miniapp", (now - timedelta(hours=1)).strftime(db.DB_DATETIME_FORMAT)),
            ],
        )
    db.record_source_sync_run(
        "test",
        "success",
        fetched=8,
        added=1,
        updated=7,
        started_at=now - timedelta(minutes=2),
        finished_at=now,
    )

    report = build_admin_dashboard(7, now=now)

    assert report["funnel"][0]["users"] == 1
    assert next(item for item in report["campaigns"] if item["campaign"] == "club_singles")["profiled"] == 1
    health = next(item for item in report["source_health"] if item["source_id"] == "test")
    assert health["health"] == "success"
    assert health["active_events"] == 1


def test_account_deletion_also_clears_unfinished_onboarding(temp_db):
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO usage_events (user_id, event_name, source) VALUES (77, 'miniapp.open', 'miniapp')"
        )
        conn.execute(
            "INSERT INTO user_acquisition (user_id, campaign) VALUES (77, 'test_link')"
        )

    assert db.delete_user_data(77)
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM usage_events WHERE user_id = 77").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM user_acquisition WHERE user_id = 77").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_admin_suspension_blocks_miniapp_but_can_be_lifted(
    user_factory,
    monkeypatch,
):
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    user_id = user_factory(user_id=88)[0]
    assert db.suspend_user(user_id, 99, "нарушение правил")
    assert db.is_user_suspended(user_id)
    headers = auth_headers(user_id)
    with pytest.raises(Exception) as error:
        await authenticated_user(headers["X-Telegram-Init-Data"])
    assert getattr(error.value, "status_code", None) == 403
    assert db.lift_user_suspension(user_id)
    user = await authenticated_user(headers["X-Telegram-Init-Data"])
    assert user.id == user_id
