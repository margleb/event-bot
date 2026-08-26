# Inline-клавиатуры — кнопки под сообщением. У каждой кнопки есть
# callback_data: эту строку Telegram присылает обратно при нажатии,
# по ней в handlers.py находится нужный обработчик.
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


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
            ]
        ]
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


def hide_me_keyboard(event_id: int) -> InlineKeyboardMarkup:
    """Кнопка под элементом /my.

    Шлёт тот же visible:no, что и ответ «Нет» — отдельный
    обработчик не нужен.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🙈 Скрыть меня",
                    callback_data=f"visible:no:{event_id}",
                ),
            ]
        ]
    )
