# Inline-клавиатуры — кнопки под сообщением. У каждой кнопки есть
# callback_data: эту строку Telegram присылает обратно при нажатии,
# по ней в handlers.py находится нужный обработчик.
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo


DIGEST_WEEKDAY_BUTTONS = (
    ("Пн", 0),
    ("Вт", 1),
    ("Ср", 2),
    ("Чт", 3),
    ("Пт", 4),
    ("Сб", 5),
    ("Вс", 6),
)


def miniapp_keyboard(url: str) -> InlineKeyboardMarkup:
    """Кнопка запуска визуального приложения внутри Telegram."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✨ Открыть подборку",
                    web_app=WebAppInfo(url=url),
                )
            ]
        ]
    )


def profile_keyboard() -> InlineKeyboardMarkup:
    """Кнопки под распознанным профилем."""
    # список списков = строки кнопок; тут одна строка из двух кнопок
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Верно",
                    callback_data="profile_confirm",
                ),
                InlineKeyboardButton(
                    text="✏️ Исправить",
                    callback_data="profile_edit",
                ),
            ]
        ]
    )


def digest_weekday_keyboard() -> InlineKeyboardMarkup:
    """День еженедельной персональной подборки и отключение рассылки."""
    buttons = [
        InlineKeyboardButton(
            text=label,
            callback_data=f"digest:set:{weekday}",
        )
        for label, weekday in DIGEST_WEEKDAY_BUTTONS
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            buttons[:4],
            buttons[4:],
            [
                InlineKeyboardButton(
                    text="🔕 Не присылать",
                    callback_data="digest:off",
                )
            ],
        ]
    )


def intent_keyboard(event_id: int) -> InlineKeyboardMarkup:
    """Кнопки отметки под карточкой события.

    id события подставляется прямо в callback_data — иначе при
    нажатии будет непонятно, о каком из пяти событий речь.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🤔 Интересно",
                    callback_data=f"intent:interested:{event_id}",
                ),
                InlineKeyboardButton(
                    text="🙋 Иду",
                    callback_data=f"intent:going:{event_id}",
                ),
                InlineKeyboardButton(
                    text="🚫 Не подходит",
                    callback_data=f"intent:not_going:{event_id}",
                ),
            ],
            # вторая строка клавиатуры
            [people_button(event_id)],
        ]
    )


def people_button(event_id: int) -> InlineKeyboardButton:
    """Кнопка «Кто идёт» — она нужна и под карточкой события, и в /my."""
    return InlineKeyboardButton(
        text="👥 Кто идёт",
        callback_data=f"people:list:{event_id}",
    )


def visibility_toggle_button(event_id: int, visible: bool) -> InlineKeyboardButton:
    """Переключатель видимости: показывает действие, а не текущее состояние.

    Видно сейчас — предлагаем скрыться, и наоборот.
    """
    if visible:
        return InlineKeyboardButton(
            text="🙈 Скрыть меня",
            callback_data=f"toggle:hide:{event_id}",
        )
    return InlineKeyboardButton(
        text="👀 Показывать меня",
        callback_data=f"toggle:show:{event_id}",
    )


def visibility_keyboard(event_id: int) -> InlineKeyboardMarkup:
    """«Да» / «Нет» на вопрос о видимости по конкретному событию."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да",
                    callback_data=f"visible:yes:{event_id}",
                ),
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=f"visible:no:{event_id}",
                ),
            ]
        ]
    )


def intent_card_keyboard(event_id: int, visible: bool) -> InlineKeyboardMarkup:
    """Кнопки под карточкой отметки: переключатель видимости и «Кто идёт».

    Используется в /my и вместо повторного вопроса о видимости.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [visibility_toggle_button(event_id, visible)],
            [people_button(event_id)],
        ]
    )


def show_me_keyboard(event_id: int) -> InlineKeyboardMarkup:
    """Единственная кнопка «Показывать меня».

    Показывается вместо списка участников тому, кто сам ещё не открылся.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[[visibility_toggle_button(event_id, visible=False)]]
    )


def companion_keyboard(event_id: int, to_user: int) -> InlineKeyboardMarkup:
    """Действия с кандидатом; id остаются только внутри callback_data."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👋 Познакомиться",
                    callback_data=f"req:send:{event_id}:{to_user}",
                ),
                InlineKeyboardButton(
                    text="🚫 Заблокировать",
                    callback_data=f"block:add:{to_user}",
                ),
            ]
        ]
    )


def request_response_keyboard(request_id: int) -> InlineKeyboardMarkup:
    """Ответ адресата на входящий запрос."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принять",
                    callback_data=f"req:accept:{request_id}",
                ),
                InlineKeyboardButton(
                    text="Отклонить",
                    callback_data=f"req:reject:{request_id}",
                ),
            ]
        ]
    )
