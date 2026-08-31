from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

import event_bot.db as db
from event_bot.digest import dispatch_due_digests


def test_digest_schedule_round_trip_and_duplicate_guard(user_factory):
    user_id, _ = user_factory(interests=["музыка"])
    monday = date(2026, 8, 31)

    assert db.get_digest_schedule(user_id) is None
    assert db.set_digest_schedule(user_id, 0)
    assert db.get_digest_schedule(user_id) == 0
    assert db.get_due_digest_user_ids(0, monday) == [user_id]

    db.mark_digest_sent(user_id, monday)
    assert db.get_due_digest_user_ids(0, monday) == []

    assert db.set_digest_schedule(user_id, None)
    assert db.get_digest_schedule(user_id) is None


def test_digest_schedule_rejects_invalid_weekday(user_factory):
    user_id, _ = user_factory()

    with pytest.raises(ValueError):
        db.set_digest_schedule(user_id, 7)


@pytest.mark.asyncio
async def test_due_digest_is_sent_once_and_contains_ranked_event(
    user_factory,
    event_factory,
    monkeypatch,
):
    monkeypatch.setenv("MINIAPP_URL", "https://bot.example/r/app?build=42")
    user_id, _ = user_factory(interests=["музыка"])
    event_factory(title="Джазовый вечер", tags=["музыка"])
    current = datetime(2026, 8, 31, 10, tzinfo=ZoneInfo("Europe/Moscow"))
    assert db.set_digest_schedule(user_id, current.weekday())
    bot = SimpleNamespace(send_message=AsyncMock())

    sent = await dispatch_due_digests(bot, now=current)
    sent_again = await dispatch_due_digests(bot, now=current)

    assert sent == 1
    assert sent_again == 0
    assert bot.send_message.await_count == 2
    assert "еженедельная подборка" in bot.send_message.await_args_list[0].args[1]
    assert bot.send_message.await_args_list[0].kwargs["reply_markup"].inline_keyboard[
        0
    ][0].web_app.url.endswith("build=42&tab=feed")
    assert "Джазовый вечер" in bot.send_message.await_args_list[1].args[1]
    assert "reply_markup" not in bot.send_message.await_args_list[1].kwargs
