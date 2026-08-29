"""HTTPS API и статика Telegram Mini App.

Nginx проксирует публичный префикс /r на этот процесс. Каждый API-запрос
содержит подписанную Telegram строку initData; одного user_id от клиента
недостаточно для авторизации.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, field_validator, model_validator

from event_bot.db import (
    INTENT_STATUSES,
    PARTICIPATING_STATUSES,
    assign_user_to_interest_group,
    find_events,
    get_digest_schedule,
    get_event,
    get_group_matching_enabled,
    get_user_interest_group,
    get_user_intent,
    get_user_intents,
    get_user_profile,
    get_user_profile_embeddings,
    init_db,
    save_intent,
    save_user_profile,
    set_digest_schedule,
    set_group_matching_enabled,
    set_intent_visibility,
    update_user_identity,
)
from event_bot.embedding_provider import (
    EmbeddingProvider,
    profile_embedding_text,
    vector_to_blob,
)
from event_bot.group_notifications import notify_group_assignment
from event_bot.models import Event, Profile, UserIntent, format_group_size


BASE_PATH = "/r"
STATIC_DIR = Path(__file__).resolve().parent / "web"
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
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramUser:
    id: int
    first_name: str
    username: str | None = None
    photo_url: str | None = None


class ProfileUpdate(BaseModel):
    interests: list[str] = Field(min_length=1, max_length=12)
    avoid: list[str] = Field(default_factory=list, max_length=12)
    days: list[str] = Field(default_factory=list, max_length=7)
    budget_rub: int | None = Field(default=None, ge=0, le=1_000_000)
    preferred_group_size_min: int | None = Field(default=None, ge=1, le=100)
    preferred_group_size_max: int | None = Field(default=None, ge=1, le=100)
    digest_weekday: int | None = Field(default=None, ge=0, le=6)
    group_matching_enabled: bool = False

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
        if self.group_matching_enabled:
            if (
                self.preferred_group_size_max is not None
                and self.preferred_group_size_max < 3
            ):
                raise ValueError("для группы выберите компанию минимум от 3 человек")
            if (
                self.preferred_group_size_min is not None
                and self.preferred_group_size_min > 5
            ):
                raise ValueError("группы собираются максимум из 5 человек")
        return self


class IntentUpdate(BaseModel):
    status: Literal["interested", "going", "not_going"]


class VisibilityUpdate(BaseModel):
    visible: bool


def _max_auth_age() -> int:
    try:
        return max(60, int(os.getenv("MINIAPP_AUTH_MAX_AGE_SECONDS", "86400")))
    except ValueError:
        return 86400


def validate_init_data(init_data: str, bot_token: str, *, now: int | None = None) -> TelegramUser:
    """Проверяет HMAC Telegram и возвращает только доверенные данные юзера."""
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as error:
        raise ValueError("некорректный initData") from error
    if not pairs or len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("некорректный initData")

    values = dict(pairs)
    received_hash = values.pop("hash", "")
    if not received_hash:
        raise ValueError("подпись отсутствует")

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(values.items())
    )
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash.lower()):
        raise ValueError("неверная подпись")

    current_time = int(time.time()) if now is None else now
    try:
        auth_date = int(values["auth_date"])
    except (KeyError, ValueError) as error:
        raise ValueError("дата авторизации отсутствует") from error
    if auth_date > current_time + 60 or current_time - auth_date > _max_auth_age():
        raise ValueError("данные запуска устарели")

    try:
        raw_user = json.loads(values["user"])
        user_id = int(raw_user["id"])
        first_name = str(raw_user["first_name"]).strip()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("данные пользователя отсутствуют") from error
    if user_id <= 0 or not first_name:
        raise ValueError("некорректный пользователь")

    username = raw_user.get("username")
    photo_url = raw_user.get("photo_url")
    return TelegramUser(
        id=user_id,
        first_name=first_name[:128],
        username=str(username)[:64] if username else None,
        photo_url=str(photo_url)[:2048] if photo_url else None,
    )


async def authenticated_user(
    init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
) -> TelegramUser:
    bot_token = os.getenv("BOT_TOKEN", "")
    if not bot_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Bot is not configured")
    if not init_data:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Open this app from Telegram")
    try:
        return validate_init_data(init_data, bot_token)
    except ValueError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error


def _profile_payload(profile: Profile | None) -> dict[str, object] | None:
    if profile is None:
        return None
    payload = profile.model_dump(mode="json")
    payload["days"] = ProfileUpdate.unique_days(profile.days or [])
    return payload


def _event_payload(event: Event, intent: UserIntent | None = None) -> dict[str, object]:
    return {
        "id": event.id,
        "title": event.title,
        "description": event.description,
        "city": event.city,
        "address": event.address,
        "date": event.date.isoformat(),
        "end_date": event.end_date.isoformat() if event.end_date else None,
        "price": event.get_price_display(),
        "tags": event.tags,
        "venue": event.venue,
        "source_url": event.source_url,
        "intent": intent.status if intent else None,
        "visible": intent.visible if intent else False,
    }


def _group_payload(user_id: int, profile: Profile | None) -> dict[str, object] | None:
    if profile is None:
        return None
    view = get_user_interest_group(user_id, profile)
    if view is None:
        return None
    group = view.group
    return {
        "id": group.id,
        "title": group.title,
        "status": group.status,
        "topics": group.topics,
        "member_count": group.member_count,
        "minimum_members": group.minimum_members,
        "maximum_members": group.maximum_members,
        "members": [
            {
                "name": "Вы" if member.user_id == user_id else member.name,
                "is_me": member.user_id == user_id,
                "common_interests": member.common_interests,
                "group_size": format_group_size(
                    member.group_size_min,
                    member.group_size_max,
                ),
            }
            for member in view.members
        ],
    }


def _bootstrap(user: TelegramUser) -> dict[str, object]:
    profile = get_user_profile(user.id)
    if profile is not None:
        update_user_identity(user.id, user.first_name, user.username)
    intents = get_user_intents(user.id) if profile is not None else []
    intents_by_event = {intent.event.id: intent for intent in intents}
    recommendations: list[Event] = []
    if profile is not None:
        embeddings = get_user_profile_embeddings(user.id)
        recommendations = find_events(
            profile,
            profile_embedding=embeddings[0],
            profile_embedding_model=embeddings[1],
            avoid_embedding=embeddings[2],
            avoid_embedding_model=embeddings[3],
            limit=20,
        )
    return {
        "user": {
            "id": user.id,
            "first_name": user.first_name,
            "username": user.username,
            "photo_url": user.photo_url,
        },
        "profile": _profile_payload(profile),
        "digest_weekday": get_digest_schedule(user.id) if profile else None,
        "group_matching_enabled": get_group_matching_enabled(user.id),
        "group": _group_payload(user.id, profile),
        "events": [
            _event_payload(event, intents_by_event.get(event.id))
            for event in recommendations
        ],
        "my_events": [_event_payload(intent.event, intent) for intent in intents],
    }


async def _embed_profile(profile: Profile) -> tuple[bytes | None, str | None, bytes | None, str | None]:
    api_key = os.getenv("OPENAI_API_KEY")
    positive_text = profile_embedding_text(profile.interests)
    negative_text = profile_embedding_text(profile.avoid)
    if not api_key or not positive_text:
        return None, None, None, None

    client = AsyncOpenAI(api_key=api_key)
    provider = EmbeddingProvider(client)
    try:
        texts = [positive_text] + ([negative_text] if negative_text else [])
        vectors = await provider.embed(texts)
        return (
            vector_to_blob(vectors[0]),
            provider.model,
            vector_to_blob(vectors[1]) if negative_text else None,
            provider.model if negative_text else None,
        )
    except Exception:
        logger.exception("Не удалось обновить эмбеддинг профиля из Mini App")
        return None, None, None, None
    finally:
        await client.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Event Bot Mini App",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.mount(f"{BASE_PATH}/app/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://telegram.org; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
        "connect-src 'self'; frame-ancestors https://web.telegram.org https://*.telegram.org"
    )
    if request.url.path.startswith(f"{BASE_PATH}/api"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get(f"{BASE_PATH}/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(f"{BASE_PATH}/app", include_in_schema=False)
@app.get(f"{BASE_PATH}/app/", include_in_schema=False)
def miniapp() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get(f"{BASE_PATH}/api/bootstrap")
def bootstrap(user: Annotated[TelegramUser, Depends(authenticated_user)]) -> dict[str, object]:
    return _bootstrap(user)


@app.put(f"{BASE_PATH}/api/profile")
async def update_profile(
    payload: ProfileUpdate,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, object]:
    profile = Profile(
        interests=payload.interests,
        avoid=payload.avoid,
        days=payload.days or None,
        budget_rub=payload.budget_rub,
        preferred_group_size_min=payload.preferred_group_size_min,
        preferred_group_size_max=payload.preferred_group_size_max,
    )
    embeddings = await _embed_profile(profile)
    save_user_profile(
        user.id,
        profile,
        user.first_name,
        user.username,
        profile_embedding=embeddings[0],
        profile_embedding_model=embeddings[1],
        avoid_embedding=embeddings[2],
        avoid_embedding_model=embeddings[3],
    )
    set_digest_schedule(user.id, payload.digest_weekday)
    set_group_matching_enabled(user.id, payload.group_matching_enabled)
    assignment = (
        assign_user_to_interest_group(user.id)
        if payload.group_matching_enabled
        else None
    )
    if assignment is not None and assignment.notify_user_ids:
        bot = Bot(token=os.environ["BOT_TOKEN"])
        try:
            await notify_group_assignment(bot, assignment)
        finally:
            await bot.session.close()
    return _bootstrap(user)


@app.put(f"{BASE_PATH}/api/events/{{event_id}}/intent")
def update_intent(
    event_id: int,
    payload: IntentUpdate,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, object]:
    if payload.status not in INTENT_STATUSES or get_event(event_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    if get_user_profile(user.id) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Complete your profile first")
    save_intent(user.id, event_id, payload.status)
    intent = get_user_intent(user.id, event_id)
    assert intent is not None
    return _event_payload(intent.event, intent)


@app.put(f"{BASE_PATH}/api/events/{{event_id}}/visibility")
def update_visibility(
    event_id: int,
    payload: VisibilityUpdate,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, object]:
    intent = get_user_intent(user.id, event_id)
    if intent is None or intent.status not in PARTICIPATING_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Choose the event first")
    if not set_intent_visibility(user.id, event_id, payload.visible):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    updated = get_user_intent(user.id, event_id)
    assert updated is not None
    return _event_payload(updated.event, updated)


@app.exception_handler(HTTPException)
async def http_error(_request: Request, error: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"detail": error.detail},
    )


def main() -> None:
    import uvicorn

    uvicorn.run(
        "event_bot.webapp:app",
        host="0.0.0.0",
        port=int(os.getenv("MINIAPP_PORT", "8080")),
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )


if __name__ == "__main__":
    main()
