from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from event_bot.handlers import (
    FeedbackFlow,
    handle_unknown_text,
    receive_feedback,
    start_feedback,
)
from event_bot.keyboards import main_menu_keyboard, miniapp_tab_url


def test_miniapp_tab_url_preserves_build_and_replaces_tab():
    url = "https://bot.example/r/app?build=42&tab=feed"

    assert miniapp_tab_url(url, "group") == (
        "https://bot.example/r/app?build=42&tab=group"
    )


def test_main_menu_opens_each_current_miniapp_section():
    keyboard = main_menu_keyboard("https://bot.example/r/app?build=42")

    assert keyboard.inline_keyboard[0][0].web_app.url.endswith("build=42&tab=feed")
    assert keyboard.inline_keyboard[1][0].web_app.url.endswith("build=42&tab=my")
    assert keyboard.inline_keyboard[1][1].web_app.url.endswith("build=42&tab=group")
    assert keyboard.inline_keyboard[2][0].web_app.url.endswith("build=42&tab=profile")
    assert keyboard.inline_keyboard[3][0].callback_data == "feedback:start"


@pytest.mark.asyncio
async def test_unknown_text_redirects_to_app_instead_of_building_profile(monkeypatch):
    monkeypatch.setenv("MINIAPP_URL", "https://bot.example/r/app?build=42")
    message = SimpleNamespace(answer=AsyncMock())

    await handle_unknown_text(message)

    message.answer.assert_awaited_once()
    assert "находятся в приложении" in message.answer.await_args.args[0]
    assert message.answer.await_args.kwargs["reply_markup"] is not None


@pytest.mark.asyncio
async def test_feedback_is_an_explicit_cancellable_state(temp_db, monkeypatch):
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "")
    state = SimpleNamespace(set_state=AsyncMock(), clear=AsyncMock())
    message = SimpleNamespace(
        text="Добавьте фильтр по району",
        from_user=SimpleNamespace(id=7, first_name="Мария", username="maria"),
        bot=SimpleNamespace(),
        answer=AsyncMock(),
    )

    await start_feedback(message, state)
    state.set_state.assert_awaited_once_with(FeedbackFlow.waiting_message)
    assert (
        message.answer.await_args.kwargs["reply_markup"]
        .inline_keyboard[0][0]
        .callback_data
        == "feedback:cancel"
    )

    message.answer.reset_mock()
    await receive_feedback(message, state)

    state.clear.assert_awaited_once()
    assert "передано команде" in message.answer.await_args.args[0]
