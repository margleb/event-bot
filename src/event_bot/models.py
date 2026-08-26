# src/event_bot/models.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


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
        low = self.price_min or 0
        high = self.price_max or 0

        if low == 0 and high == 0:
            return "Бесплатно"
        if low == 0:
            return f"до {high} ₽"
        if high == 0 or high == low:
            return f"{low} ₽"
        return f"{low}–{high} ₽"
