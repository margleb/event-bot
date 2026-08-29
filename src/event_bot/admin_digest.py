import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

from event_bot.analytics import (
    build_daily_admin_report,
    format_daily_admin_report,
    get_admin_ids,
)
from event_bot.db import mark_admin_report_sent, was_admin_report_sent


MOSCOW_TZ = ZoneInfo("Europe/Moscow")
logger = logging.getLogger(__name__)


def admin_report_hour() -> int:
    """Час ежедневной сводки по Москве; по умолчанию 10:00."""
    try:
        hour = int(os.getenv("ADMIN_DAILY_REPORT_HOUR_MSK", "10"))
    except ValueError:
        return 10
    return hour if 0 <= hour <= 23 else 10


async def dispatch_daily_admin_reports(
    bot: Bot,
    *,
    now: datetime | None = None,
) -> int:
    """Отправляет каждому администратору не более одной сводки в день."""
    current = now or datetime.now(MOSCOW_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=MOSCOW_TZ)
    else:
        current = current.astimezone(MOSCOW_TZ)
    if current.hour < admin_report_hour():
        return 0

    report_date = current.date()
    pending_admins = [
        admin_id
        for admin_id in sorted(get_admin_ids())
        if not was_admin_report_sent(admin_id, report_date)
    ]
    if not pending_admins:
        return 0

    text = format_daily_admin_report(build_daily_admin_report(now=current))
    delivered = 0
    for admin_id in pending_admins:
        try:
            await bot.send_message(
                admin_id,
                text,
                parse_mode=ParseMode.HTML,
            )
            delivered += 1
        except TelegramAPIError:
            logger.warning(
                "Не удалось доставить ежедневную сводку администратору %s",
                admin_id,
            )
        finally:
            # Не повторяем попытку каждую минуту, если бот заблокирован.
            mark_admin_report_sent(admin_id, report_date)
    return delivered


async def run_admin_report_scheduler(
    bot: Bot,
    *,
    interval_seconds: int = 60,
) -> None:
    """Фоновый цикл ежедневных админ-сводок."""
    while True:
        try:
            await dispatch_daily_admin_reports(bot)
        except Exception:
            logger.exception("Ошибка цикла ежедневной админ-сводки")
        await asyncio.sleep(interval_seconds)
