"""Validated request payloads accepted by the Mini App API."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_ALIASES = {
    alias: canonical
    for canonical, aliases in {
        "mon": ("mon", "monday", "пн", "понедельник"),
        "tue": ("tue", "tuesday", "вт", "вторник"),
        "wed": ("wed", "wednesday", "ср", "среда"),
        "thu": ("thu", "thursday", "чт", "четверг"),
        "fri": ("fri", "friday", "пт", "пятница"),
        "sat": ("sat", "saturday", "сб", "суббота"),
        "sun": ("sun", "sunday", "вс", "воскресенье"),
    }.items()
    for alias in aliases
}


class ProfileUpdate(BaseModel):
    interests: list[str] = Field(min_length=1, max_length=12)
    avoid: list[str] = Field(default_factory=list, max_length=12)
    days: list[str] = Field(default_factory=list, max_length=7)
    budget_rub: int | None = Field(default=None, ge=0, le=1_000_000)
    preferred_group_size_min: int | None = Field(default=None, ge=1, le=100)
    preferred_group_size_max: int | None = Field(default=None, ge=1, le=100)
    digest_weekday: int | None = Field(default=None, ge=0, le=6)

    @field_validator("interests", "avoid")
    @classmethod
    def normalize_words(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = " ".join(raw.strip().split())[:60]
            key = value.casefold()
            if value and key not in seen:
                result.append(value)
                seen.add(key)
        return result

    @field_validator("days")
    @classmethod
    def unique_days(cls, values: list[str]) -> list[str]:
        normalized = {
            WEEKDAY_ALIASES[value.strip().casefold()]
            for value in values
            if value.strip().casefold() in WEEKDAY_ALIASES
        }
        return [day for day in WEEKDAYS if day in normalized]

    @model_validator(mode="after")
    def validate_group_size(self) -> "ProfileUpdate":
        if not self.interests:
            raise ValueError("укажите хотя бы один интерес")
        if (
            self.preferred_group_size_min is not None
            and self.preferred_group_size_max is not None
            and self.preferred_group_size_min > self.preferred_group_size_max
        ):
            raise ValueError("минимальный размер компании больше максимального")
        return self


class IntentUpdate(BaseModel):
    status: Literal["interested", "going", "not_going"]


class VisibilityUpdate(BaseModel):
    visible: bool


class GroupMessageCreate(BaseModel):
    message: str = Field(min_length=1, max_length=1000)

    @field_validator("message")
    @classmethod
    def normalize_group_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("сообщение пустое")
        return normalized


class EventGroupRsvpUpdate(BaseModel):
    status: Literal["going", "declined"]


class MeetingPointUpdate(BaseModel):
    meeting_point: str = Field(min_length=2, max_length=240)

    @field_validator("meeting_point")
    @classmethod
    def normalize_meeting_point(cls, value: str) -> str:
        return " ".join(value.strip().split())


class FeedbackCreate(BaseModel):
    message: str = Field(min_length=3, max_length=2000)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if len(normalized) < 3:
            raise ValueError("сообщение слишком короткое")
        return normalized


MINIAPP_TRACK_EVENTS = {
    "tab.feed",
    "tab.my",
    "tab.group",
    "tab.profile",
    "tab.admin",
    "event_details",
    "external_source",
}


class TrackEvent(BaseModel):
    event: str

    @field_validator("event")
    @classmethod
    def allowed_event(cls, value: str) -> str:
        if value not in MINIAPP_TRACK_EVENTS:
            raise ValueError("неизвестное событие")
        return value
