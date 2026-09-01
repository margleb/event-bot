# src/event_bot/handlers.py
import os
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from event_bot.analytics import (
    build_admin_report,
    create_feedback,
    format_admin_report,
    format_feedback_item,
    get_feedback,
    get_recent_feedback,
    is_admin,
    mark_feedback_answered,
    notify_admins,
)
from event_bot.db import (
    INTENT_STATUSES,
    INTENT_STATUS_LABELS,
    PARTICIPATING_STATUSES,
    accept_connection_request,
    block_user,
    create_connection_request,
    find_companions,
    format_companion_card,
    format_contact_message,
    format_intent_card,
    format_request_notification,
    get_user_intent,
    get_user_profile,
    get_recent_user_reports,
    is_open_participant,
    is_user_suspended,
    mark_visibility_asked,
    reject_connection_request,
    record_user_acquisition,
    resolve_user_report,
    lift_user_suspension,
    suspend_user,
    save_inactivity_feedback_response,
    save_event_experience_feedback,
    save_intent,
    set_digest_schedule,
    set_intent_visibility,
    update_user_identity,
)
from event_bot.digest import DIGEST_WEEKDAY_LABELS
from event_bot.inactivity_feedback import INACTIVITY_FEEDBACK_LABELS
from event_bot.keyboards import (
    companion_keyboard,
    feedback_cancel_keyboard,
    intent_card_keyboard,
    main_menu_keyboard,
    miniapp_keyboard,
    miniapp_tab_url,
    request_response_keyboard,
    show_me_keyboard,
    visibility_keyboard,
)
from event_bot.models import Profile


# Router — контейнер обработчиков, в app.py его подключают к Dispatcher.
# Порядок регистрации важен: aiogram отдаёт апдейт ПЕРВОМУ обработчику,
# чей фильтр совпал, поэтому команды объявлены выше перехватчика F.text.
router = Router()


class FeedbackFlow(StatesGroup):
    waiting_message = State()

NO_PROFILE_TEXT = (
    "Профиля пока нет. Откройте приложение и укажите интересы, "
    "удобные дни и бюджет."
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


def _load_profile(user_id: int) -> Profile | None:
    """Профиль хранится в SQLite и редактируется только через Mini App."""
    return get_user_profile(user_id)


def _miniapp_url(tab: str) -> str:
    url = os.getenv("MINIAPP_URL", "").strip()
    return miniapp_tab_url(url, tab) if url else ""


async def _redirect_to_app(
    message: Message,
    tab: str,
    text: str,
    button_text: str,
) -> None:
    url = _miniapp_url(tab)
    await message.answer(
        text,
        reply_markup=(miniapp_keyboard(url, button_text) if url else None),
    )


# CommandStart() — фильтр, срабатывает только на /start
@router.message(CommandStart())
async def start(
    message: Message,
    state: FSMContext,
    command: CommandObject,
) -> None:
    await state.clear()
    if message.from_user is not None:
        if is_user_suspended(message.from_user.id):
            await message.answer(
                "Доступ к сервису ограничен. Если вы считаете это ошибкой, "
                "отправьте апелляцию командой /feedback."
            )
            return
        record_user_acquisition(message.from_user.id, command.args)
    miniapp_url = os.getenv("MINIAPP_URL", "").strip()
    await message.answer(
        "Мск.Митап подбирает мероприятия по вашим интересам и помогает "
        "найти людей, с которыми можно на них сходить.\n\n"
        "Выберите, что хотите открыть:",
        reply_markup=(main_menu_keyboard(miniapp_url) if miniapp_url else None),
    )


@router.message(Command("help"))
async def show_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    miniapp_url = os.getenv("MINIAPP_URL", "").strip()
    await message.answer(
        "В приложении четыре раздела:\n"
        "• «Афиша» — подборка мероприятий;\n"
        "• «Мои» — сохранённые события;\n"
        "• «Компания» — люди, которые собираются вместе;\n"
        "• «Профиль» — интересы и день еженедельной подборки.\n\n"
        "Если что-то не работает, нажмите «Написать команде».",
        reply_markup=(main_menu_keyboard(miniapp_url) if miniapp_url else None),
    )


# Старые команды остаются как переходы, чтобы сохранённые подсказки и ссылки
# не сломались. В меню Telegram они больше не показываются.
@router.message(Command("profile"))
async def show_profile(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _redirect_to_app(
        message,
        "profile",
        "Профиль, предпочтения и расписание подборки теперь находятся в приложении.",
        "⚙️ Открыть профиль",
    )


@router.message(Command("find"))
async def find(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _redirect_to_app(
        message,
        "feed",
        "Афиша и поиск компании теперь работают в приложении.",
        "🎟 Открыть афишу",
    )


@router.message(Command("my"))
async def my_events(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _redirect_to_app(
        message,
        "my",
        "Сохранённые мероприятия находятся в разделе «Мои».",
        "⭐ Открыть мои мероприятия",
    )


@router.message(Command("schedule"))
async def show_digest_schedule(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _redirect_to_app(
        message,
        "profile",
        "День еженедельной подборки настраивается в профиле приложения.",
        "⚙️ Настроить рассылку",
    )


@router.message(Command("group"))
async def show_event_companies(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()
    await _redirect_to_app(
        message,
        "group",
        "Компании теперь собираются вокруг конкретных мероприятий. "
        "Откройте раздел «Компания», чтобы увидеть свои группы.",
        "👥 Открыть мои компании",
    )


@router.message(Command("feedback"))
async def start_feedback(message: Message, state: FSMContext) -> None:
    """Явно включает единственный оставшийся диалоговый сценарий бота."""
    await state.set_state(FeedbackFlow.waiting_message)
    await message.answer(
        "Напишите одним сообщением идею, вопрос или описание проблемы. "
        "Я передам его команде бота.",
        reply_markup=feedback_cancel_keyboard(),
    )


@router.message(Command("admin"))
async def show_admin_report(message: Message) -> None:
    if message.from_user is None:
        return
    if not is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только администратору.")
        return
    await message.answer(
        format_admin_report(build_admin_report()),
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("feedbacks"))
async def show_feedbacks(message: Message) -> None:
    if message.from_user is None:
        return
    if not is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только администратору.")
        return
    items = get_recent_feedback()
    if not items:
        await message.answer("Обращений пока нет.")
        return
    await message.answer(
        "💬 <b>Последние обращения</b>\n"
        "Новые показаны первыми. Ответ: <code>/reply ID текст</code>",
        parse_mode=ParseMode.HTML,
    )
    for item in items:
        await message.answer(
            format_feedback_item(item),
            parse_mode=ParseMode.HTML,
        )


@router.message(Command("reply"))
async def reply_to_feedback(message: Message) -> None:
    if message.from_user is None:
        return
    if not is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только администратору.")
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].strip():
        await message.answer("Формат: /reply ID текст ответа")
        return
    item = get_feedback(int(parts[1]))
    if item is None:
        await message.answer("Обращение с таким ID не найдено.")
        return
    delivered = await _send_message_safely(
        message.bot,
        item.user_id,
        "💬 Ответ команды Мск.Митап:\n\n" + parts[2].strip()[:3000],
    )
    if delivered:
        mark_feedback_answered(item.id)
        await message.answer(f"Ответ на обращение #{item.id} отправлен.")
    else:
        await message.answer("Не удалось доставить ответ: пользователь недоступен.")


@router.message(Command("reports"))
async def show_user_reports(message: Message) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только администратору.")
        return
    reports = get_recent_user_reports()
    if not reports:
        await message.answer("Новых жалоб нет.")
        return
    labels = {
        "spam": "спам",
        "harassment": "оскорбления/домогательства",
        "unsafe": "небезопасное поведение",
        "other": "другое",
    }
    for report in reports:
        details = f"\nКомментарий: {escape(str(report['details']))}" if report["details"] else ""
        await message.answer(
            f"🚨 <b>Жалоба #{report['id']}</b>\n"
            f"От: {escape(str(report['reporter_name'] or report['reporter_id']))}\n"
            f"На: {escape(str(report['reported_name'] or report['reported_id']))}\n"
            f"Событие: {escape(str(report['event_title'] or '—'))}\n"
            f"Причина: {escape(labels.get(str(report['reason']), str(report['reason'])))}"
            f"{details}\nЗакрыть: <code>/reportdone {report['id']}</code>"
            f"\nОграничить: <code>/ban {report['reported_id']} причина</code>",
            parse_mode=ParseMode.HTML,
        )


@router.message(Command("reportdone"))
async def close_user_report(message: Message) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только администратору.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /reportdone ID")
        return
    if resolve_user_report(int(parts[1])):
        await message.answer(f"Жалоба #{parts[1]} закрыта.")
    else:
        await message.answer("Новая жалоба с таким ID не найдена.")


@router.message(Command("ban"))
async def ban_user(message: Message) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только администратору.")
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: /ban TELEGRAM_ID причина")
        return
    reason = parts[2] if len(parts) == 3 else ""
    if suspend_user(int(parts[1]), message.from_user.id, reason):
        await message.answer(f"Доступ пользователя {parts[1]} к Mini App ограничен.")
    else:
        await message.answer("Пользователь не найден или действие недопустимо.")


@router.message(Command("unban"))
async def unban_user(message: Message) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только администратору.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /unban TELEGRAM_ID")
        return
    if lift_user_suspension(int(parts[1])):
        await message.answer(f"Доступ пользователя {parts[1]} восстановлен.")
    else:
        await message.answer("Активного ограничения для этого ID нет.")


@router.callback_query(F.data == "feedback:start")
async def start_feedback_from_button(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.set_state(FeedbackFlow.waiting_message)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Напишите одним сообщением идею, вопрос или описание проблемы. "
            "Я передам его команде бота.",
            reply_markup=feedback_cancel_keyboard(),
        )
    await callback.answer()


@router.message(Command("cancel"))
async def cancel_dialog(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено. Чтобы вернуться в меню, отправьте /start.")


@router.callback_query(F.data == "feedback:cancel")
async def cancel_feedback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Отправка сообщения отменена.")
    await callback.answer("Отменено")


@router.message(FeedbackFlow.waiting_message, F.text)
async def receive_feedback(message: Message, state: FSMContext) -> None:
    if message.text is None or message.from_user is None:
        return
    try:
        item = create_feedback(
            message.from_user.id,
            message.from_user.first_name,
            message.from_user.username,
            message.text,
            "bot",
        )
    except ValueError as error:
        await message.answer(
            str(error) + ". Попробуйте ещё раз или нажмите «Отмена».",
            reply_markup=feedback_cancel_keyboard(),
        )
        return
    await state.clear()
    await notify_admins(message.bot, item)
    await message.answer(
        f"Спасибо! Обращение #{item.id} передано команде. "
        "Ответ придёт сюда, в Telegram."
    )


# Обычный текст больше не запускает скрытое заполнение профиля.
@router.message(F.text)
async def handle_unknown_text(message: Message) -> None:
    miniapp_url = os.getenv("MINIAPP_URL", "").strip()
    await message.answer(
        "Настройки профиля и подбор событий находятся в приложении. "
        "Для сообщения команде используйте /feedback.",
        reply_markup=(main_menu_keyboard(miniapp_url) if miniapp_url else None),
    )


# Кнопки под старыми сообщениями не зависают, а ведут в актуальный профиль.
@router.callback_query(F.data == "profile_confirm")
@router.callback_query(F.data == "profile_edit")
async def redirect_legacy_profile(callback: CallbackQuery) -> None:
    url = _miniapp_url("profile")
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Профиль теперь редактируется в приложении.",
            reply_markup=(
                miniapp_keyboard(url, "⚙️ Открыть профиль") if url else None
            ),
        )
    await callback.answer("Откройте профиль в приложении")


@router.callback_query(F.data.startswith("digest:"))
async def set_weekly_digest(callback: CallbackQuery) -> None:
    """Сохраняет выбранный день или отключает автоматическую рассылку."""
    data = callback.data or ""
    weekday: int | None
    if data == "digest:off":
        weekday = None
    else:
        parts = data.split(":")
        if (
            len(parts) != 3
            or parts[:2] != ["digest", "set"]
            or not parts[2].isdigit()
        ):
            await callback.answer()
            return
        weekday = int(parts[2])
        if weekday not in range(7):
            await callback.answer()
            return

    if not set_digest_schedule(callback.from_user.id, weekday):
        await callback.answer("Сначала заполни профиль.", show_alert=True)
        return

    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        if weekday is None:
            text = "Еженедельная подборка отключена. Включить её можно через /schedule."
        else:
            text = (
                "Готово: буду присылать подборку по "
                f"{DIGEST_WEEKDAY_LABELS[weekday]} после 10:00 по Москве."
            )
        await callback.message.answer(text)
    await callback.answer()


@router.callback_query(F.data.startswith("inactive_feedback:"))
async def handle_inactivity_feedback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Принимает один короткий ответ; текст просим только для «Другое»."""
    parts = (callback.data or "").split(":", maxsplit=1)
    code = parts[1] if len(parts) == 2 else ""
    if code not in INACTIVITY_FEEDBACK_LABELS:
        await callback.answer()
        return

    saved = save_inactivity_feedback_response(callback.from_user.id, code)
    if not saved:
        await callback.answer("Ответ уже сохранён")
        return

    await callback.answer("Спасибо!")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Спасибо, ответ сохранён. Больше напоминать об этом не будем."
        )
        if code == "other":
            await state.set_state(FeedbackFlow.waiting_message)
            await callback.message.answer(
                "Если удобно, напишите одним сообщением, что можно улучшить. "
                "Я передам его команде.",
                reply_markup=feedback_cancel_keyboard(),
            )


@router.callback_query(F.data.startswith("event_experience:"))
async def handle_event_experience(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit():
        await callback.answer()
        return
    saved = save_event_experience_feedback(
        int(parts[1]),
        callback.from_user.id,
        parts[2],
    )
    if not saved:
        await callback.answer("Ответ не сохранён", show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        text = "Спасибо, это поможет улучшить подбор компаний."
        if parts[2] == "unsafe":
            text += " Если нужен ответ команды, напишите /feedback."
        await callback.message.answer(text)
    await callback.answer("Ответ сохранён")


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
    if _load_profile(user_id) is None:
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
) -> None:
    parsed = _parse_event_callback(callback.data)
    if parsed is None or parsed[0] != "list":
        await callback.answer()
        return

    _, event_id = parsed
    user_id = callback.from_user.id

    profile = _load_profile(user_id)
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
