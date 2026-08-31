"""Telegram Mini App request authentication."""

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import parse_qsl

from fastapi import Depends, Header, HTTPException, status

from event_bot.analytics import is_admin


@dataclass(frozen=True)
class TelegramUser:
    id: int
    first_name: str
    username: str | None = None
    photo_url: str | None = None


def _max_auth_age() -> int:
    try:
        return max(60, int(os.getenv("MINIAPP_AUTH_MAX_AGE_SECONDS", "86400")))
    except ValueError:
        return 86400


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    now: int | None = None,
) -> TelegramUser:
    """Validate Telegram's HMAC and return only trusted user fields."""
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


async def authenticated_admin(
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> TelegramUser:
    if not is_admin(user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user
