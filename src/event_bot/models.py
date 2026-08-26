from pydantic import BaseModel, Field


class Profile(BaseModel):
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
