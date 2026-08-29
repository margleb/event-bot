import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

from event_bot.db import (
    find_events,
    format_event_card,
    get_due_digest_user_ids,
    get_user_profile,
    get_user_profile_embeddings,
    mark_digest_sent,
)
from event_bot.keyboards import intent_keyboard


MOSCOW_TZ = ZoneInfo("Europe/Moscow")
DIGEST_WEEKDAY_LABELS = (
    "понедельникам",
    "вторникам",
    "средам",
    "четвергам",
    "пятницам",
    "субботам",
    "воскресеньям",
)
logger = logging.getLogger(__name__)


def digest_hour() -> int:
    """Час отправки по Москве; значение окружения ограничено диапазоном суток."""
    try:
        hour = int(os.getenv("DIGEST_HOUR_MSK", "10"))
    except ValueError:
        return 10
    return hour if 0 <= hour <= 23 else 10


async def dispatch_due_digests(
    bot: Bot,
    *,
    now: datetime | None = None,
) -> int:
    """Отправляет все подборки, положенные к текущему московскому времени."""
    current = now or datetime.now(MOSCOW_TZ)
    if current.hour < digest_hour():
        return 0

    sent = 0
    sent_on = current.date()
    user_ids = get_due_digest_user_ids(current.weekday(), sent_on)
    for user_id in user_ids:
        profile = get_user_profile(user_id)
        if profile is None:
            mark_digest_sent(user_id, sent_on)
            continue

        embeddings = get_user_profile_embeddings(user_id)
        events = find_events(
            profile,
            profile_embedding=embeddings[0],
            profile_embedding_model=embeddings[1],
            avoid_embedding=embeddings[2],
            avoid_embedding_model=embeddings[3],
        )
        try:
            if not events:
                await bot.send_message(
                    user_id,
                    "На этой неделе подходящих мероприятий не нашлось. "
                    "Профиль можно изменить командой /profile.",
                )
            else:
                await bot.send_message(
                    user_id,
                    "Твоя еженедельная подборка — выбрал лучшее по профилю 👇",
                )
                for index, event in enumerate(events, start=1):
                    await bot.send_message(
                        user_id,
                        format_event_card(event, index),
                        parse_mode=ParseMode.HTML,
                        reply_markup=intent_keyboard(event.id),
                    )
        except TelegramAPIError:
            # Заблокированный бот не должен получать новую попытку каждую минуту.
            logger.warning("Не удалось доставить подборку пользователю %s", user_id)
        finally:
            mark_digest_sent(user_id, sent_on)
        sent += 1
    return sent


async def run_digest_scheduler(bot: Bot, *, interval_seconds: int = 60) -> None:
    """Фоновый цикл рассылки; дата в SQLite защищает от дублей после рестарта."""
    while True:
        try:
            await dispatch_due_digests(bot)
        except Exception:
            logger.exception("Ошибка цикла еженедельных подборок")
        await asyncio.sleep(interval_seconds)
