from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Inline-клавиатуры — кнопки под сообщением. У каждой кнопки есть
# callback_data: эту строку Telegram присылает обратно при нажатии,
# по ней в handlers.py находится нужный обработчик.
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from event_bot.event_experience import (
    ATTENDANCE_LABELS,
    DETAIL_LABELS,
    DETAILS_BY_OUTCOME,
)


def miniapp_keyboard(
    url: str,
    text: str = "✨ Открыть подборку",
) -> InlineKeyboardMarkup:
    """Кнопка запуска визуального приложения внутри Telegram."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text,
                    web_app=WebAppInfo(url=url),
                )
            ]
        ]
    )


def miniapp_tab_url(url: str, tab: str) -> str:
    """Добавляет вкладку к URL, сохраняя build-параметр для сброса кеша."""
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "tab"]
    query.append(("tab", tab))
    return urlunsplit(parts._replace(query=urlencode(query)))


def main_menu_keyboard(url: str) -> InlineKeyboardMarkup:
    """Единое меню бота: работа с сервисом происходит в Mini App."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎟 Открыть афишу",
                    web_app=WebAppInfo(url=miniapp_tab_url(url, "feed")),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ Мои",
                    web_app=WebAppInfo(url=miniapp_tab_url(url, "my")),
                ),
                InlineKeyboardButton(
                    text="👥 Компании",
                    web_app=WebAppInfo(url=miniapp_tab_url(url, "group")),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Профиль и рассылка",
                    web_app=WebAppInfo(url=miniapp_tab_url(url, "profile")),
                )
            ],
            [
                InlineKeyboardButton(
                    text="💬 Написать команде",
                    callback_data="feedback:start",
                )
            ],
        ]
    )


def feedback_cancel_keyboard() -> InlineKeyboardMarkup:
    """Явный выход из режима отправки обратной связи."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="feedback:cancel",
                )
            ]
        ]
    )


def inactivity_feedback_keyboard() -> InlineKeyboardMarkup:
    """Короткий одноразовый опрос без необходимости печатать текст."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Не нашёл подходящего",
                    callback_data="inactive_feedback:no_events",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Не понял, как пользоваться",
                    callback_data="inactive_feedback:confusing",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Сейчас неактуально",
                    callback_data="inactive_feedback:not_now",
                ),
                InlineKeyboardButton(
                    text="Другое",
                    callback_data="inactive_feedback:other",
                ),
            ],
        ]
    )


def event_experience_keyboard(group_id: int) -> InlineKeyboardMarkup:
    """Сначала выясняем факт посещения, не предполагая, что встреча была."""
    prefix = f"event_experience:{group_id}:"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{icon} {ATTENDANCE_LABELS[code]}", callback_data=prefix + code)]
            for code, icon in (("met", "🤝"), ("solo", "👤"), ("not_attended", "❌"))
        ]
    )


def event_experience_detail_keyboard(group_id: int, outcome: str) -> InlineKeyboardMarkup:
    """Необязательное уточнение; основной ответ уже сохранён."""
    prefix = f"event_detail:{group_id}:{outcome}:"
    rows = [
        [InlineKeyboardButton(text=DETAIL_LABELS[code], callback_data=prefix + code)]
        for code in DETAILS_BY_OUTCOME[outcome]
    ]
    rows.append([InlineKeyboardButton(text="Пропустить", callback_data=prefix + "skip")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
