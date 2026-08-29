from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from event_bot.handlers import handle_profile_text
from event_bot.models import Profile
from event_bot.profile_service import (
    build_profile_input,
    next_clarification_field,
)
from event_bot.storage import ProfileStore


class SequenceExtractor:
    def __init__(self, profiles: list[Profile]) -> None:
        self.profiles = iter(profiles)
        self.inputs: list[str] = []

    async def extract(self, text: str) -> Profile:
        self.inputs.append(text)
        return next(self.profiles)


def test_missing_profile_fields_are_requested_in_useful_order():
    assert next_clarification_field(Profile()) == "interests"
    assert next_clarification_field(Profile(interests=["театр"])) == "days"
    assert (
        next_clarification_field(
            Profile(interests=["театр"]),
            {"days"},
        )
        == "budget"
    )
    assert (
        next_clarification_field(
            Profile(interests=["театр"]),
            {"days", "budget"},
        )
        == "group_size"
    )
    assert (
        next_clarification_field(
            Profile(interests=["театр"]),
            {"days", "budget", "group_size"},
        )
        is None
    )


def test_profile_input_keeps_all_follow_up_answers():
    text = build_profile_input(["Люблю джаз", "По пятницам", "До 2000 рублей"])

    assert "Сообщение пользователя 1: Люблю джаз" in text
    assert "Сообщение пользователя 2: По пятницам" in text
    assert "Сообщение пользователя 3: До 2000 рублей" in text


@pytest.mark.asyncio
async def test_handler_clarifies_nullable_preferences_without_losing_context():
    extractor = SequenceExtractor(
        [
            Profile(interests=["джаз"]),
            Profile(interests=["джаз"]),
            Profile(interests=["джаз"]),
            Profile(interests=["джаз"]),
        ]
    )
    store = ProfileStore()
    message = SimpleNamespace(
        text="Люблю джаз",
        from_user=SimpleNamespace(id=7),
        answer=AsyncMock(),
    )

    await handle_profile_text(message, extractor, store)
    assert store.awaiting_clarification[7] == "days"

    message.text = "Любой день"
    await handle_profile_text(message, extractor, store)
    assert store.awaiting_clarification[7] == "budget"

    message.text = "Любой бюджет"
    await handle_profile_text(message, extractor, store)
    assert store.awaiting_clarification[7] == "group_size"

    message.text = "Без разницы"
    await handle_profile_text(message, extractor, store)

    assert "Люблю джаз" in extractor.inputs[-1]
    assert "Любой день" in extractor.inputs[-1]
    assert "Любой бюджет" in extractor.inputs[-1]
    assert "Без разницы" in extractor.inputs[-1]
    assert 7 not in store.awaiting_clarification
    assert 7 not in store.draft_inputs
    assert message.answer.await_args_list[-1].kwargs["reply_markup"] is not None
