# src/event_bot/handlers.py
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from openai import OpenAIError

from event_bot.db import (
    INTENT_STATUSES,
    INTENT_STATUS_LABELS,
    find_events,
    format_event_card,
    format_intent_card,
    get_user_intents,
    get_user_profile,
    save_intent,
    save_user_profile,
    set_intent_visibility,
)
from event_bot.keyboards import (
    hide_me_keyboard,
    intent_keyboard,
    profile_keyboard,
    visibility_keyboard,
)
from event_bot.models import Profile
from event_bot.profile_service import ProfileExtractor, format_profile
from event_bot.storage import ProfileStore


# Router — контейнер обработчиков, в app.py его подключают к Dispatcher.
# Порядок регистрации важен: aiogram отдаёт апдейт ПЕРВОМУ обработчику,
# чей фильтр совпал, поэтому команды объявлены выше перехватчика F.text.
router = Router()

NO_PROFILE_TEXT = (
    "Профиля пока нет. Расскажи, что тебе интересно, "
    "когда удобно ходить и какой бюджет — я его соберу."
)


def _load_profile(user_id: int, profile_store: ProfileStore) -> Profile | None:
    """Профиль из памяти, а если бот перезапускался — из базы."""
    # ProfileStore живёт в оперативной памяти и обнуляется при рестарте
    profile = profile_store.confirmed.get(user_id)
    if profile is not None:
        return profile

    # в памяти нет — берём из SQLite
    profile = get_user_profile(user_id)
    if profile is not None:
        # и кладём в память, чтобы следующий раз не ходить в базу
        profile_store.confirmed[user_id] = profile
    return profile


# CommandStart() — фильтр, срабатывает только на /start
@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Расскажи свободно, куда ты любишь ходить, "
        "что тебе интересно, когда обычно удобно, "
        "какой бюджет и какая компания комфортна.\n\n"
        "Потом /find — подберу мероприятия, /profile — покажу профиль,\n"
        "/my — что ты уже отметил."
    )


# profile_store сюда подставляет сам aiogram: он передаёт в обработчик
# всё, что отдали в start_polling(...) в app.py, сопоставляя по имени аргумента
@router.message(Command("profile"))
async def show_profile(
    message: Message,
    profile_store: ProfileStore,
) -> None:
    # from_user пустой у сообщений из каналов — такие просто игнорируем
    if message.from_user is None:
        return

    profile = _load_profile(message.from_user.id, profile_store)
    if profile is None:
        await message.answer(NO_PROFILE_TEXT)
        return

    # кладём профиль в черновики: кнопка «Верно» подтверждает именно черновик
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

    # весь подбор и фильтрация — в db.find_events
    events = find_events(profile)
    if not events:
        await message.answer(
            "Не нашлось подходящих мероприятий. "
            "Попробуй изменить профиль через /profile."
        )
        return

    await message.answer(f"Нашёл {len(events)} вариантов:")
    # каждое событие — отдельным сообщением, чтобы к нему прицепить свои кнопки
    for index, event in enumerate(events, start=1):
        await message.answer(
            format_event_card(event, index),
            # HTML нужен из-за тегов <b> в карточке
            parse_mode=ParseMode.HTML,
            # кнопки «Интересно / Иду / Не подходит» именно для этого события
            reply_markup=intent_keyboard(event.id),
        )


@router.message(Command("my"))
async def my_events(message: Message) -> None:
    if message.from_user is None:
        return

    # отметки вместе с данными событий (JOIN в db.get_user_intents)
    intents = get_user_intents(message.from_user.id)
    if not intents:
        await message.answer(
            "Ты пока ничего не отметил. Напиши /find — покажу мероприятия."
        )
        return

    await message.answer("Твои отметки:")
    for intent in intents:
        await message.answer(
            format_intent_card(intent),
            parse_mode=ParseMode.HTML,
            # кнопку «Скрыть меня» показываем, только если сейчас видно
            reply_markup=(
                hide_me_keyboard(intent.event.id) if intent.visible else None
            ),
        )


# F.text — любое текстовое сообщение, не подошедшее под фильтры выше:
# считаем, что человек рассказывает о своих предпочтениях
@router.message(F.text)
async def handle_profile_text(
    message: Message,
    profile_extractor: ProfileExtractor,
    profile_store: ProfileStore,
) -> None:
    if message.text is None or message.from_user is None:
        return

    try:
        # поход в OpenAI: текст -> структурированная модель Profile
        profile = await profile_extractor.extract(message.text)
    except (OpenAIError, ValueError):
        await message.answer(
            "Не получилось разобрать предпочтения. Попробуй ещё раз."
        )
        return

    # в базу пока не пишем — сначала пользователь должен подтвердить
    profile_store.drafts[message.from_user.id] = profile
    await message.answer(
        format_profile(profile),
        reply_markup=profile_keyboard(),
    )


# callback_query — нажатие на inline-кнопку; F.data сверяется с её callback_data
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

    # черновик становится подтверждённым профилем и уходит в SQLite
    profile_store.confirmed[user_id] = profile
    save_user_profile(user_id, profile)

    # callback.message пустой у очень старых сообщений — отсюда проверка типа
    if isinstance(callback.message, Message):
        # убираем кнопки, чтобы нельзя было подтвердить второй раз
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Профиль сохранён ✅\nТеперь напиши /find — подберу мероприятия."
        )
    # обязательный ответ Telegram: без него на кнопке крутятся «часики»
    await callback.answer()


@router.callback_query(F.data == "profile_edit")
async def edit_profile(
    callback: CallbackQuery,
    profile_store: ProfileStore,
) -> None:
    # выбрасываем черновик: следующее сообщение соберёт профиль заново
    profile_store.drafts.pop(callback.from_user.id, None)
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Напиши предпочтения ещё раз — я обновлю профиль."
        )
    await callback.answer()


def _parse_event_callback(data: str | None) -> tuple[str, int] | None:
    """Разбирает callback_data вида prefix:value:event_id.

    Кнопка умеет передать только строку, поэтому id события «зашит» в неё:
    "intent:going:12" -> ("going", 12), "visible:yes:12" -> ("yes", 12).
    """
    if data is None:
        return None

    parts = data.split(":")
    # строка пришла от клиента, так что проверяем формат, а не доверяем ему
    if len(parts) != 3 or not parts[2].isdigit():
        return None

    # префикс (parts[0]) уже отобран фильтром F.data.startswith(...)
    return parts[1], int(parts[2])


# Нажатие «Интересно» / «Иду» / «Не подходит» под карточкой события
@router.callback_query(F.data.startswith("intent:"))
async def set_intent(callback: CallbackQuery) -> None:
    parsed = _parse_event_callback(callback.data)
    # статус сверяем со списком допустимых, чтобы в базу не попал мусор
    if parsed is None or parsed[0] not in INTENT_STATUSES:
        await callback.answer()
        return

    status, event_id = parsed
    # сохраняет отметку пользователя (UPSERT: повторное нажатие обновит строку)
    save_intent(callback.from_user.id, event_id, status)
    # короткое всплывающее уведомление вместо отдельного сообщения в чате
    await callback.answer(f"Отметил: {INTENT_STATUS_LABELS[status]}")

    # Видимость спрашиваем отдельно и только про это событие.
    # При «Не подходит» спрашивать нечего: save_intent уже сбросил visible в 0
    if status == "not_going" or not isinstance(callback.message, Message):
        return

    # именно ОТДЕЛЬНОЕ сообщение: согласие не приклеено к выбору статуса
    await callback.message.answer(
        "Показывать вас другим, кто собирается на это событие?",
        reply_markup=visibility_keyboard(event_id),
    )


# Ответ «Да» / «Нет» на вопрос о видимости и кнопка «Скрыть меня» в /my —
# у них одинаковый callback_data, поэтому обработчик общий
@router.callback_query(F.data.startswith("visible:"))
async def set_visibility(callback: CallbackQuery) -> None:
    parsed = _parse_event_callback(callback.data)
    if parsed is None or parsed[0] not in ("yes", "no"):
        await callback.answer()
        return

    answer, event_id = parsed
    # visible = 1 только здесь, и только если человек сам нажал «Да»
    visible = answer == "yes"

    # False = строки отметки нет; согласие само по себе её не создаёт
    if not set_intent_visibility(callback.from_user.id, event_id, visible):
        await callback.answer(
            "Сначала отметь, идёшь ли ты на это событие.",
            show_alert=True,
        )
        return

    # убираем кнопки: на вопрос уже ответили
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer(
        "Показываю тебя другим." if visible else "Не показываю тебя другим."
    )
