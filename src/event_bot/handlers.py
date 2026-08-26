from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from openai import OpenAIError

from event_bot.keyboards import profile_keyboard
from event_bot.profile_service import ProfileExtractor, format_profile
from event_bot.storage import ProfileStore


router = Router()


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Расскажи свободно, куда ты любишь ходить, "
        "что тебе интересно, когда обычно удобно, "
        "какой бюджет и какая компания комфортна."
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
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Профиль сохранён ✅")
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
