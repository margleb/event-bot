# src/event_bot/handlers.py
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from openai import OpenAIError

from event_bot.db import (
    INTENT_STATUSES,
    INTENT_STATUS_LABELS,
    PARTICIPATING_STATUSES,
    accept_connection_request,
    block_user,
    create_connection_request,
    find_companions,
    find_events,
    format_companion_card,
    format_contact_message,
    format_event_card,
    format_intent_card,
    format_request_notification,
    get_user_intent,
    get_user_intents,
    get_user_profile,
    get_user_profile_embeddings,
    is_open_participant,
    mark_visibility_asked,
    reject_connection_request,
    save_intent,
    save_user_profile,
    set_intent_visibility,
    update_user_identity,
)
from event_bot.embedding_provider import (
    EmbeddingProvider,
    profile_embedding_text,
    vector_to_blob,
)
from event_bot.keyboards import (
    companion_keyboard,
    intent_card_keyboard,
    intent_keyboard,
    profile_keyboard,
    request_response_keyboard,
    show_me_keyboard,
    visibility_keyboard,
)
from event_bot.models import Profile
from event_bot.profile_service import ProfileExtractor, format_profile
from event_bot.storage import ProfileStore


# Router — контейнер обработчиков, в app.py его подключают к Dispatcher.
# Порядок регистрации важен: aiogram отдаёт апдейт ПЕРВОМУ обработчику,
# чей фильтр совпал, поэтому команды объявлены выше перехватчика F.text.
router = Router()
logger = logging.getLogger(__name__)

NO_PROFILE_TEXT = (
    "Профиля пока нет. Расскажи, что тебе интересно, "
    "когда удобно ходить и какой бюджет — я его соберу."
)

# Показывается вместо списка тому, кто сам ещё не открылся:
# смотреть на других, оставаясь невидимым, нельзя
NOT_OPEN_TEXT = (
    "Список видят только те, кто открылся сам.\n"
    "Показывать тебя другим на этом событии?"
)

NO_COMPANIONS_TEXT = "Пока никто больше не открылся. Загляни позже."

REQUEST_SENT_TEXT = "Запрос отправлен, ждём ответа"
REQUEST_ALREADY_TEXT = "Запрос уже отправлен"
REQUEST_LIMIT_TEXT = "На сегодня хватит. Попробуй завтра."
USER_UNAVAILABLE_TEXT = "Этот участник сейчас недоступен."


async def _send_message_safely(
    bot: Bot,
    chat_id: int,
    text: str,
    **kwargs: object,
) -> bool:
    """Шлёт фоновое уведомление и не роняет callback при ошибке Telegram."""
    try:
        await bot.send_message(chat_id, text, **kwargs)
    except TelegramAPIError:
        # Человек мог заблокировать бота. Состояние в БД уже сохранено,
        # откатывать запрос или согласие из-за доставки нельзя.
        return False
    return True


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

    # Векторы живут в SQLite, а не только в памяти, поэтому переживают рестарт.
    embeddings = get_user_profile_embeddings(message.from_user.id)
    events = find_events(
        profile,
        profile_embedding=embeddings[0],
        profile_embedding_model=embeddings[1],
        avoid_embedding=embeddings[2],
        avoid_embedding_model=embeddings[3],
    )
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
            # у «Не подходит» переключать и смотреть нечего — кнопок нет
            reply_markup=(
                intent_card_keyboard(intent.event.id, intent.visible)
                if intent.status in PARTICIPATING_STATUSES
                else None
            ),
        )


# F.text — любое текстовое сообщение, не подошедшее под фильтры выше:
# считаем, что человек рассказывает о своих предпочтениях
@router.message(F.text)
async def handle_profile_text(
    message: Message,
    profile_extractor: ProfileExtractor | None,
    profile_store: ProfileStore,
) -> None:
    if message.text is None or message.from_user is None:
        return

    if profile_extractor is None:
        await message.answer(
            "Сервис разбора профиля сейчас недоступен. Попробуй позже."
        )
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
    embedding_provider: EmbeddingProvider | None,
) -> None:
    user_id = callback.from_user.id
    profile = profile_store.drafts.get(user_id)
    if profile is None:
        await callback.answer(
            "Сначала расскажи о своих предпочтениях.",
            show_alert=True,
        )
        return

    profile_embedding = None
    avoid_embedding = None
    embedding_model = None
    positive_text = profile_embedding_text(profile.interests)
    negative_text = profile_embedding_text(profile.avoid)
    if embedding_provider is not None and positive_text:
        texts = [positive_text]
        if negative_text:
            texts.append(negative_text)
        try:
            vectors = await embedding_provider.embed(texts)
            profile_embedding = vector_to_blob(vectors[0])
            if negative_text:
                avoid_embedding = vector_to_blob(vectors[1])
            embedding_model = embedding_provider.model
        except Exception:
            # Профиль всё равно сохраняется; /find перейдёт на старые теги.
            logger.exception("Не удалось посчитать эмбеддинг профиля %s", user_id)

    # Черновик становится подтверждённым профилем и уходит в SQLite.
    # Имя берём из Telegram: это единственное, что увидят другие участники.
    profile_store.confirmed[user_id] = profile
    save_user_profile(
        user_id,
        profile,
        callback.from_user.first_name,
        callback.from_user.username,
        profile_embedding=profile_embedding,
        profile_embedding_model=(embedding_model if profile_embedding else None),
        avoid_embedding=avoid_embedding,
        avoid_embedding_model=(embedding_model if avoid_embedding else None),
    )

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
async def set_intent(
    callback: CallbackQuery,
    profile_store: ProfileStore,
) -> None:
    parsed = _parse_event_callback(callback.data)
    # статус сверяем со списком допустимых, чтобы в базу не попал мусор
    if parsed is None or parsed[0] not in INTENT_STATUSES:
        await callback.answer()
        return

    status, event_id = parsed
    user_id = callback.from_user.id

    # Без подтверждённого профиля отметка не сохраняется: иначе человек
    # окажется участником, которого нечем показать другим
    if _load_profile(user_id, profile_store) is None:
        await callback.answer("Сначала заполни профиль.", show_alert=True)
        if isinstance(callback.message, Message):
            await callback.message.answer(NO_PROFILE_TEXT)
        return

    # сохраняет отметку пользователя (UPSERT: повторное нажатие обновит строку)
    save_intent(user_id, event_id, status)
    # короткое всплывающее уведомление вместо отдельного сообщения в чате
    await callback.answer(f"Отметил: {INTENT_STATUS_LABELS[status]}")

    # Видимость спрашиваем отдельно и только про это событие.
    # При «Не подходит» спрашивать нечего: save_intent уже сбросил visible в 0
    if status == "not_going" or not isinstance(callback.message, Message):
        return

    intent = get_user_intent(user_id, event_id)
    if intent is None:
        return

    if intent.visibility_asked:
        # вопрос по этому событию уже задавали — больше не спрашиваем,
        # вместо него показываем состояние и кнопку-переключатель
        await callback.message.answer(
            format_intent_card(intent),
            parse_mode=ParseMode.HTML,
            reply_markup=intent_card_keyboard(event_id, intent.visible),
        )
        return

    # спрашиваем один раз и сразу помечаем, что спросили
    mark_visibility_asked(user_id, event_id)
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


# Переключатель «Показывать меня» / «Скрыть меня»: в /my, вместо повторного
# вопроса и в предложении открыться. В отличие от ответа «Да»/«Нет» он
# перерисовывает и текст сообщения, а не только кнопки
@router.callback_query(F.data.startswith("toggle:"))
async def toggle_visibility(callback: CallbackQuery) -> None:
    parsed = _parse_event_callback(callback.data)
    if parsed is None or parsed[0] not in ("show", "hide"):
        await callback.answer()
        return

    action, event_id = parsed
    user_id = callback.from_user.id
    visible = action == "show"

    if not set_intent_visibility(user_id, event_id, visible):
        await callback.answer(
            "Сначала отметь, идёшь ли ты на это событие.",
            show_alert=True,
        )
        return

    # перечитываем отметку, чтобы показать реальное состояние из базы
    intent = get_user_intent(user_id, event_id)
    if isinstance(callback.message, Message) and intent is not None:
        await callback.message.edit_text(
            format_intent_card(intent),
            parse_mode=ParseMode.HTML,
            reply_markup=intent_card_keyboard(event_id, intent.visible),
        )
    await callback.answer(
        "Показываю тебя другим." if visible else "Не показываю тебя другим."
    )


# Кнопка «Кто идёт»
@router.callback_query(F.data.startswith("people:"))
async def show_people(
    callback: CallbackQuery,
    profile_store: ProfileStore,
) -> None:
    parsed = _parse_event_callback(callback.data)
    if parsed is None or parsed[0] != "list":
        await callback.answer()
        return

    _, event_id = parsed
    user_id = callback.from_user.id

    profile = _load_profile(user_id, profile_store)
    if profile is None:
        await callback.answer("Сначала заполни профиль.", show_alert=True)
        return

    if not isinstance(callback.message, Message):
        await callback.answer()
        return

    intent = get_user_intent(user_id, event_id)
    if intent is None or intent.status not in PARTICIPATING_STATUSES:
        await callback.answer(
            "Сначала отметь «Интересно» или «Иду».",
            show_alert=True,
        )
        return

    # ПРАВИЛО ВЗАИМНОСТИ: список видит только тот, кто сам открылся.
    # Проверка — той же функцией, по которой людей отбирают в список,
    # поэтому наблюдателей, которых не видно самих, не бывает
    if not is_open_participant(intent):
        await callback.message.answer(
            NOT_OPEN_TEXT,
            reply_markup=show_me_keyboard(event_id),
        )
        await callback.answer()
        return

    companions = find_companions(event_id, user_id, profile)
    if not companions:
        # ничего не выдумываем: пусто — значит пусто
        await callback.message.answer(NO_COMPANIONS_TEXT)
        await callback.answer()
        return

    await callback.message.answer(f"Открылись {len(companions)}:")
    for index, companion in enumerate(companions, start=1):
        await callback.message.answer(
            format_companion_card(companion, index),
            parse_mode=ParseMode.HTML,
            reply_markup=companion_keyboard(event_id, companion.user_id),
        )
    await callback.answer()


def _parse_candidate_callback(data: str | None) -> tuple[int, int] | None:
    """Разбирает req:send:event_id:to_user."""
    if data is None:
        return None
    parts = data.split(":")
    if (
        len(parts) != 4
        or parts[0:2] != ["req", "send"]
        or not parts[2].isdigit()
        or not parts[3].isdigit()
    ):
        return None
    return int(parts[2]), int(parts[3])


@router.callback_query(F.data.startswith("req:send:"))
async def send_connection_request(
    callback: CallbackQuery,
    bot: Bot,
) -> None:
    parsed = _parse_candidate_callback(callback.data)
    if parsed is None:
        await callback.answer()
        return

    event_id, to_user = parsed
    from_user = callback.from_user.id
    # Сохраняем актуальный username отправителя для возможного контакта,
    # но до принятия нигде его не форматируем.
    update_user_identity(
        from_user,
        callback.from_user.first_name,
        callback.from_user.username,
    )
    result, request = create_connection_request(event_id, from_user, to_user)

    if result == "already":
        await callback.answer(REQUEST_ALREADY_TEXT, show_alert=True)
        return
    if result == "limit":
        await callback.answer(REQUEST_LIMIT_TEXT, show_alert=True)
        return
    if result in ("blocked", "unavailable") or request is None:
        await callback.answer(USER_UNAVAILABLE_TEXT, show_alert=True)
        return

    # Сначала запрос зафиксирован в БД. Если Telegram не доставит сообщение,
    # строка остаётся pending, а обработчик всё равно завершится без падения.
    await _send_message_safely(
        bot,
        request.to_user,
        format_request_notification(request),
        parse_mode=ParseMode.HTML,
        reply_markup=request_response_keyboard(request.id),
    )
    await callback.answer(REQUEST_SENT_TEXT, show_alert=True)


# req:send с четырьмя частями перехватывается обработчиком выше; здесь остаются
# только req:accept:request_id и req:reject:request_id.
@router.callback_query(F.data.startswith("req:"))
async def resolve_connection_request(
    callback: CallbackQuery,
    bot: Bot,
) -> None:
    parsed = _parse_event_callback(callback.data)
    if parsed is None or parsed[0] not in ("accept", "reject"):
        await callback.answer()
        return

    action, request_id = parsed
    user_id = callback.from_user.id
    if action == "reject":
        rejected = reject_connection_request(request_id, user_id)
        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except TelegramAPIError:
                pass
        await callback.answer(
            "Запрос отклонён." if rejected else "Запрос уже обработан.",
            show_alert=True,
        )
        # Защита отказавшего: отправителю здесь не уходит ни одного сообщения.
        return

    # Username адресата берём непосредственно из callback принятия, чтобы
    # отдать отправителю актуальный контакт.
    update_user_identity(
        user_id,
        callback.from_user.first_name,
        callback.from_user.username,
    )
    result, request = accept_connection_request(request_id, user_id)
    if result != "accepted" or request is None:
        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except TelegramAPIError:
                pass
        await callback.answer("Запрос уже обработан.", show_alert=True)
        return

    # Только эта ветка раскрывает контакты. Две отправки независимы:
    # ошибка у одного пользователя не мешает доставить сообщение другому.
    await _send_message_safely(
        bot,
        request.from_user,
        format_contact_message(
            request.to_name,
            request.to_user,
            request.to_username,
            request.event_title,
        ),
        parse_mode=ParseMode.HTML,
    )
    await _send_message_safely(
        bot,
        request.to_user,
        format_contact_message(
            request.from_name,
            request.from_user,
            request.from_username,
            request.event_title,
        ),
        parse_mode=ParseMode.HTML,
    )
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass
    await callback.answer("Запрос принят.", show_alert=True)


@router.callback_query(F.data.startswith("block:"))
async def add_block(callback: CallbackQuery) -> None:
    parsed = _parse_event_callback(callback.data)
    if parsed is None or parsed[0] != "add":
        await callback.answer()
        return

    _, blocked = parsed
    if not block_user(callback.from_user.id, blocked):
        await callback.answer(USER_UNAVAILABLE_TEXT, show_alert=True)
        return

    # Убираем действия с уже заблокированным кандидатом в текущем списке;
    # новые списки отфильтруются симметрично запросом find_companions.
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass
    await callback.answer("Пользователь заблокирован.", show_alert=True)
