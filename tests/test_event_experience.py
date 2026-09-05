from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message

import event_bot.db as db
from event_bot.admin_dashboard import build_admin_dashboard
from event_bot.event_experience import DETAILS_BY_OUTCOME
from event_bot.handlers import handle_event_experience, handle_event_experience_detail
from event_bot.keyboards import event_experience_keyboard, event_experience_detail_keyboard


@pytest.fixture
def membership(user_factory, event_factory):
    user_id = user_factory()[0]
    event_id = event_factory()
    _, group_id, _ = db.join_event_group(user_id, event_id)
    return group_id, user_id


def feedback_row(group_id, user_id):
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM event_experience_feedback WHERE group_id=? AND user_id=?",
            (group_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def make_callback(data, user_id):
    message = AsyncMock(spec=Message)
    message.text = 'Удалось сходить на мероприятие «Тест <1>»?'
    message.edit_text = AsyncMock()
    message.answer = AsyncMock()
    return SimpleNamespace(
        data=data, from_user=SimpleNamespace(id=user_id), message=message, answer=AsyncMock(),
    )


def test_attendance_keyboard_has_three_clear_answers():
    buttons = [b for row in event_experience_keyboard(7).inline_keyboard for b in row]
    assert [b.text for b in buttons] == [
        "🤝 Да, с компанией", "👤 Да, один/одна", "❌ Нет, не ходил(а)",
    ]
    assert [b.callback_data for b in buttons] == [
        "event_experience:7:met", "event_experience:7:solo", "event_experience:7:not_attended",
    ]


def test_every_followup_is_optional_and_callback_fits_telegram():
    for outcome in DETAILS_BY_OUTCOME:
        buttons = [b for row in event_experience_detail_keyboard(2**63-1, outcome).inline_keyboard for b in row]
        assert buttons[-1].text == "Пропустить"
        assert all(len(b.callback_data.encode()) <= 64 for b in buttons)


@pytest.mark.parametrize("outcome", ["met", "solo", "not_attended", "no_show", "unsafe"])
def test_new_and_legacy_answers_are_preserved(membership, outcome):
    group_id, user_id = membership
    assert db.save_event_experience_feedback(group_id, user_id, outcome)
    assert feedback_row(group_id, user_id)["outcome"] == outcome
    assert feedback_row(group_id, user_id)["detail"] is None


def test_detail_does_not_replace_attendance_and_duplicates_are_idempotent(membership):
    group_id, user_id = membership
    assert db.save_event_experience_feedback(group_id, user_id, "not_attended")
    assert db.save_event_experience_detail(group_id, user_id, "not_attended", "no_company")
    original = feedback_row(group_id, user_id)
    assert original["outcome"] == "not_attended"
    assert db.save_event_experience_feedback(group_id, user_id, "not_attended")
    assert db.save_event_experience_detail(group_id, user_id, "not_attended", "skip")
    assert feedback_row(group_id, user_id) == original
    assert db.save_event_experience_feedback(group_id, user_id, "solo")
    assert feedback_row(group_id, user_id)["detail"] is None
    assert not db.save_event_experience_detail(group_id, user_id, "not_attended", "plans_changed")


def test_invalid_foreign_or_out_of_order_answers_are_rejected(membership):
    group_id, user_id = membership
    assert not db.save_event_experience_detail(group_id, user_id, "solo", "no_show")
    assert not db.save_event_experience_feedback(group_id, user_id+100, "met")
    assert not db.save_event_experience_feedback(group_id, user_id, "unknown")
    assert db.save_event_experience_feedback(group_id, user_id, "not_attended")
    assert not db.save_event_experience_detail(group_id, user_id, "not_attended", "no_show")
    assert not db.save_event_experience_detail(group_id, user_id+100, "not_attended", "skip")
    assert not db.save_event_experience_detail(group_id, user_id, "unknown", "skip")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["met", "solo", "not_attended"])
async def test_first_answer_is_saved_before_optional_question(membership, outcome):
    group_id, user_id = membership
    callback = make_callback(f"event_experience:{group_id}:{outcome}", user_id)
    await handle_event_experience(callback)
    row = feedback_row(group_id, user_id)
    assert row["outcome"] == outcome
    assert row["detail"] is None
    sent = callback.message.edit_text.await_args
    assert "Ответ необязателен" in sent.args[0]
    assert "&lt;1&gt;" in sent.args[0]
    assert sent.kwargs["reply_markup"].inline_keyboard[-1][0].text == "Пропустить"
    callback.message.answer.assert_not_awaited()
    callback.answer.assert_awaited_once_with("Ответ сохранён")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,detail", [
    ("not_attended", "skip"), ("not_attended", "plans_changed"),
    ("solo", "no_show"), ("met", "unsafe"),
])
async def test_followup_or_skip_finishes_without_extra_messages(membership, outcome, detail):
    group_id, user_id = membership
    db.save_event_experience_feedback(group_id, user_id, outcome)
    callback = make_callback(f"event_detail:{group_id}:{outcome}:{detail}", user_id)
    await handle_event_experience_detail(callback)
    row = feedback_row(group_id, user_id)
    assert row["outcome"] == outcome
    assert row["detail"] == (None if detail == "skip" else detail)
    sent = callback.message.edit_text.await_args
    assert sent.kwargs["reply_markup"] is None
    assert ("/feedback" in sent.args[0]) == (detail == "unsafe")
    callback.message.answer.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["no_show", "unsafe"])
async def test_old_buttons_still_work(membership, outcome):
    group_id, user_id = membership
    callback = make_callback(f"event_experience:{group_id}:{outcome}", user_id)
    await handle_event_experience(callback)
    assert feedback_row(group_id, user_id)["outcome"] == outcome
    assert callback.message.edit_text.await_args.kwargs["reply_markup"] is None


def test_schema_upgrade_preserves_existing_answers(temp_db):
    with db.get_connection() as conn:
        conn.execute("ALTER TABLE event_experience_feedback DROP COLUMN detail")
        conn.execute("INSERT INTO event_experience_feedback VALUES (1, 2, 'no_show', '2026-09-01 12:00:00')")
    db.init_db()
    db.init_db()
    assert feedback_row(1, 2) == {
        "group_id": 1, "user_id": 2, "outcome": "no_show", "detail": None,
        "created_at": "2026-09-01 12:00:00",
    }


def test_dashboard_separates_absence_and_details_from_legacy_answers(membership, monkeypatch):
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "99")
    group_id, user_id = membership
    now = datetime.now(timezone.utc)
    with db.get_connection() as conn:
        conn.executemany(
            "INSERT INTO event_experience_feedback (group_id,user_id,outcome,detail) VALUES (?,?,?,?)",
            [(group_id, user_id, "not_attended", "plans_changed"),
             (group_id, 22, "solo", "no_show"), (group_id, 23, "no_show", None),
             (group_id, 99, "met", "good"), (group_id, 98, "met", "good")],
        )
        conn.execute("INSERT INTO research_participants (campaign,user_id,participant_code) VALUES ('ux_test',98,'UX-98')")
        conn.execute(
            "UPDATE event_experience_feedback SET created_at=?",
            ((now-timedelta(hours=1)).strftime(db.DB_DATETIME_FORMAT),),
        )
    report = build_admin_dashboard(7, now=now)
    outcomes = {r["outcome"]: r for r in report["company_outcomes"]}
    assert set(outcomes) == {"not_attended", "solo", "no_show"}
    assert outcomes["not_attended"]["label"] == "Не ходили"
    assert outcomes["no_show"]["label"].endswith("(старый опрос)")
    details = report["company_outcome_details"]
    assert {(r["outcome"], r["detail"], r["amount"]) for r in details} == {
        ("not_attended", "plans_changed", 1), ("solo", "no_show", 1),
    }
