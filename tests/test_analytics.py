from datetime import datetime, timezone

from aiogram.types import CallbackQuery, Chat, Message, User

from event_bot.analytics import (
    build_admin_report,
    classify_bot_event,
    create_feedback,
    format_admin_report,
    get_admin_ids,
    get_feedback,
    get_recent_feedback,
    mark_feedback_answered,
    record_usage,
)


def test_admin_ids_are_strictly_parsed():
    assert get_admin_ids(" 123,invalid,-5,456,123 ") == {123, 456}


def test_usage_report_counts_users_visits_and_features(temp_db):
    record_usage(1, "command.start", "bot")
    record_usage(1, "miniapp.open", "miniapp")
    record_usage(1, "miniapp.tab.feed", "miniapp")
    record_usage(2, "command.find", "bot")
    record_usage(2, "miniapp.tab.feed", "miniapp")

    report = build_admin_report()

    assert report["known_users"] == 2
    assert report["active"]["day"] == 2
    assert report["visits"]["day"] == 2
    assert report["actions_week"] == 5
    assert ("miniapp.tab.feed", 2) in report["top_features"]
    text = format_admin_report(report)
    assert "Активные" in text
    assert "Вкладка «Афиша»" in text


def test_admin_activity_is_excluded_from_user_metrics(temp_db, monkeypatch):
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "1")
    record_usage(1, "command.admin", "bot")
    record_usage(1, "miniapp.open", "miniapp")
    record_usage(2, "command.start", "bot")

    report = build_admin_report()

    assert report["known_users"] == 1
    assert report["active"]["day"] == 1
    assert report["visits"]["day"] == 1
    assert report["actions_week"] == 1


def test_feedback_lifecycle_and_normalization(temp_db):
    item = create_feedback(
        42,
        "Мария",
        "maria_test",
        "  Хочу   фильтр по району Москвы  ",
        "miniapp",
    )

    assert item.message == "Хочу фильтр по району Москвы"
    assert item.status == "new"
    assert get_recent_feedback()[0].id == item.id
    assert build_admin_report()["feedback_new"] == 1

    assert mark_feedback_answered(item.id)
    updated = get_feedback(item.id)
    assert updated is not None
    assert updated.status == "answered"
    assert updated.answered_at is not None


def test_feedback_rejects_empty_message(temp_db):
    try:
        create_feedback(1, "Тест", None, "  ", "bot")
    except ValueError as error:
        assert "от 3" in str(error)
    else:
        raise AssertionError("пустое обращение не должно сохраняться")


def test_bot_event_classification_does_not_store_message_text():
    user = User(id=1, is_bot=False, first_name="Тест")
    message = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=1, type="private"),
        from_user=user,
        text="Мой личный текст про интересы",
    )
    command = message.model_copy(update={"text": "/find now"})
    callback = CallbackQuery(
        id="callback",
        from_user=user,
        chat_instance="chat",
        data="intent:going:15",
    )

    assert classify_bot_event(message) == "bot.profile_text"
    assert classify_bot_event(command) == "command.find"
    assert classify_bot_event(callback) == "callback.intent.going"
