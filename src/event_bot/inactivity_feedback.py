import asyncio
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from event_bot.analytics import get_admin_ids
from event_bot.db import (
    claim_inactivity_feedback_prompt,
    get_inactive_feedback_user_ids,
    mark_inactivity_feedback_delivery,
)
from event_bot.keyboards import inactivity_feedback_keyboard


MOSCOW_TZ = ZoneInfo("Europe/Moscow")
INACTIVITY_FEEDBACK_LABELS = {
    "no_events": "Не нашёл подходящего",
    "confusing": "Не понял, как пользоваться",
    "not_now": "Сейчас неактуально",
    "other": "Другое",
}
INACTIVITY_FEEDBACK_TEXT = (
    "👋 Кажется, бот пока не стал привычкой — это нормально.\n\n"
    "Поможете одним нажатием понять, что помешало? "
    "Больше напоминать об этом не будем."
)
logger = logging.getLogger(__name__)


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if minimum <= value <= maximum else default


def inactivity_feedback_days() -> int:
    return _bounded_env_int("INACTIVITY_FEEDBACK_DAYS", 7, 3, 90)


def inactivity_feedback_hour() -> int:
    return _bounded_env_int("INACTIVITY_FEEDBACK_HOUR_MSK", 18, 0, 23)


def inactivity_feedback_batch_size() -> int:
    return _bounded_env_int("INACTIVITY_FEEDBACK_BATCH_SIZE", 50, 1, 200)


async def dispatch_inactivity_feedback(
    bot: Bot,
    *,
    now: datetime | None = None,
) -> int:
    """Один раз спрашивает давно неактивных пользователей, что им помешало."""
    current = now or datetime.now(MOSCOW_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=MOSCOW_TZ)
    else:
        current = current.astimezone(MOSCOW_TZ)
    if current.hour < inactivity_feedback_hour():
        return 0

    cutoff = current - timedelta(days=inactivity_feedback_days())
    user_ids = get_inactive_feedback_user_ids(
        cutoff,
        excluded_user_ids=get_admin_ids(),
        limit=inactivity_feedback_batch_size(),
    )
    delivered = 0
    for user_id in user_ids:
        # Резервируем до сетевого запроса: рестарт процесса не создаст дубль.
        if not claim_inactivity_feedback_prompt(user_id):
            continue
        try:
            await bot.send_message(
                user_id,
                INACTIVITY_FEEDBACK_TEXT,
                reply_markup=inactivity_feedback_keyboard(),
            )
        except TelegramAPIError:
            mark_inactivity_feedback_delivery(user_id, "failed")
            logger.info(
                "Не удалось доставить опрос неактивному пользователю %s",
                user_id,
            )
        else:
            mark_inactivity_feedback_delivery(user_id, "sent")
            delivered += 1
    return delivered


async def run_inactivity_feedback_scheduler(
    bot: Bot,
    *,
    interval_seconds: int = 300,
) -> None:
    """Фоновый цикл мягкого опроса; таблица в SQLite защищает от дублей."""
    while True:
        try:
            await dispatch_inactivity_feedback(bot)
        except Exception:
            logger.exception("Ошибка цикла опроса неактивных пользователей")
        await asyncio.sleep(interval_seconds)
