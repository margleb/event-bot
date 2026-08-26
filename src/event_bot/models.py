# src/event_bot/models.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


def format_group_size(minimum: int | None, maximum: int | None) -> str:
    """«2–4», «от 2», «до 4» или «не указан».

    Живёт здесь, потому что нужен в двух местах: в профиле
    (profile_service) и в карточке участника (db).
    """
    if minimum is not None and maximum is not None:
        return f"{minimum}–{maximum}"
    if minimum is not None:
        return f"от {minimum}"
    if maximum is not None:
        return f"до {maximum}"
    return "не указан"


# Модели Pydantic здесь работают в двух ролях: это и схема ответа
# для OpenAI (structured output), и обычные объекты для передачи данных.
# description у полей видит модель — это часть промпта.
class Profile(BaseModel):
    """Профиль пользователя"""
    interests: list[str] = Field(
        default_factory=list,
        description="Интересы человека простыми словами",
    )
    avoid: list[str] = Field(
        default_factory=list,
        description="Что человеку явно не нравится",
    )
    days: list[str] | None = Field(
        default=None,
        description='Дни недели, например ["fri", "sat"]',
    )
    budget_rub: int | None = Field(
        default=None,
        description="Максимальный бюджет в рублях",
    )
    preferred_group_size_min: int | None = Field(
        default=None,
        description="Предпочтительный минимальный размер компании",
    )
    preferred_group_size_max: int | None = Field(
        default=None,
        description="Предпочтительный максимальный размер компании",
    )


class Event(BaseModel):
    """Мероприятие"""
    # None у события, которое ещё не сохранено в базу;
    # у прочитанного из SQLite id всегда есть
    id: Optional[int] = None
    title: str
    description: str
    city: str
    address: str
    date: datetime
    price_min: Optional[int] = None
    price_max: Optional[int] = None
    tags: list[str] = Field(default_factory=list)
    venue: str

    def get_price_display(self) -> str:
        """Человекочитаемая цена. 0 и None трактуются как «бесплатно»."""
        # `or 0` схлопывает None и 0 в один случай
        low = self.price_min or 0
        high = self.price_max or 0

        if low == 0 and high == 0:
            return "Бесплатно"
        if low == 0:
            return f"до {high} ₽"
        if high == 0 or high == low:
            return f"{low} ₽"
        return f"{low}–{high} ₽"


# Другой участник события — то, что видит пользователь в списке «Кто идёт».
# Username и телефон сюда намеренно не попадают. Telegram ID хранится только
# для callback_data кнопок и не используется форматтером карточки.
class Companion(BaseModel):
    """Открывшийся участник того же события"""
    # Нужен только для callback_data кнопок. В текст карточки не выводится.
    user_id: int
    name: str
    # пересечение интересов: считается относительно того, кто смотрит
    common_interests: list[str] = Field(default_factory=list)
    group_size_min: Optional[int] = None
    group_size_max: Optional[int] = None


class ConnectionRequest(BaseModel):
    """Запрос на знакомство с данными для служебных уведомлений"""
    id: int
    event_id: int
    event_title: str
    from_user: int
    to_user: int
    from_name: str
    to_name: str
    from_username: Optional[str] = None
    to_username: Optional[str] = None
    common_interests: list[str] = Field(default_factory=list)


# Отметка + само событие: то, что возвращает db.get_user_intents
class UserIntent(BaseModel):
    """Отметка пользователя по конкретному мероприятию"""
    event: Event
    status: str
    visible: bool = False
    # спрашивали ли уже согласие на видимость по этому событию
    visibility_asked: bool = False
