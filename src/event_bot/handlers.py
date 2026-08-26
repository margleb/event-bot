# src/event_bot/handlers.py
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from openai import OpenAIError

from event_bot.db import (
    find_events,
    format_event_card,
    get_user_profile,
    save_user_profile,
)
from event_bot.keyboards import profile_keyboard
from event_bot.models import Profile
from event_bot.profile_service import ProfileExtractor, format_profile
from event_bot.storage import ProfileStore


router = Router()

NO_PROFILE_TEXT = (
    "Профиля пока нет. Расскажи, что тебе интересно, "
    "когда удобно ходить и какой бюджет — я его соберу."
)


def _load_profile(user_id: int, profile_store: ProfileStore) -> Profile | None:
    """Профиль из памяти, а если бот перезапускался — из базы."""
    profile = profile_store.confirmed.get(user_id)
    if profile is not None:
        return profile

    profile = get_user_profile(user_id)
    if profile is not None:
        profile_store.confirmed[user_id] = profile
    return profile


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Расскажи свободно, куда ты любишь ходить, "
        "что тебе интересно, когда обычно удобно, "
        "какой бюджет и какая компания комфортна.\n\n"
        "Потом /find — подберу мероприятия, /profile — покажу профиль."
    )


@router.message(Command("profile"))
async def show_profile(
    message: Message,
    profile_store: ProfileStore,
) -> None:
    if message.from_user is None:
        return

    profile = _load_profile(message.from_user.id, profile_store)
    if profile is None:
        await message.answer(NO_PROFILE_TEXT)
        return

    profile_store.drafts[message.from_user.id] = profile
    await message.answer(
        format_profile(profile),
        reply_markup=profile_keyboard(),
    )


@router.message(Command("find"))
async def find(
    message: Message,
    profile_store: ProfileStore,
) -> None:
    if message.from_user is None:
        return

    profile = _load_profile(message.from_user.id, profile_store)
    if profile is None:
        await message.answer(NO_PROFILE_TEXT)
        return

    events = find_events(profile)
    if not events:
        await message.answer(
            "Не нашлось подходящих мероприятий. "
            "Попробуй изменить профиль через /profile."
        )
        return

    await message.answer(f"Нашёл {len(events)} вариантов:")
    for index, event in enumerate(events, start=1):
        await message.answer(
            format_event_card(event, index),
            parse_mode=ParseMode.HTML,
        )


@router.message(F.text)
async def handle_profile_text(
    message: Message,
    profile_extractor: ProfileExtractor,
    profile_store: ProfileStore,
) -> None:
    if message.text is None or message.from_user is None:
        return

    try:
        profile = await profile_extractor.extract(message.text)
    except (OpenAIError, ValueError):
        await message.answer(
            "Не получилось разобрать предпочтения. Попробуй ещё раз."
        )
        return

    profile_store.drafts[message.from_user.id] = profile
    await message.answer(
        format_profile(profile),
        reply_markup=profile_keyboard(),
    )


@router.callback_query(F.data == "profile_confirm")
async def confirm_profile(
    callback: CallbackQuery,
    profile_store: ProfileStore,
) -> None:
    user_id = callback.from_user.id
    profile = profile_store.drafts.get(user_id)
    if profile is None:
        await callback.answer(
            "Сначала расскажи о своих предпочтениях.",
            show_alert=True,
        )
        return

    profile_store.confirmed[user_id] = profile
    save_user_profile(user_id, profile)

    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Профиль сохранён ✅\nТеперь напиши /find — подберу мероприятия."
        )
    await callback.answer()


@router.callback_query(F.data == "profile_edit")
async def edit_profile(
    callback: CallbackQuery,
    profile_store: ProfileStore,
) -> None:
    profile_store.drafts.pop(callback.from_user.id, None)
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Напиши предпочтения ещё раз — я обновлю профиль."
        )
    await callback.answer()
