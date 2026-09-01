import asyncio
import logging
import os
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

from event_bot.db import (
    archive_expired_event_groups,
    claim_event_group_delivery,
    get_due_event_group_deliveries,
    mark_event_group_delivery,
)
from event_bot.keyboards import event_experience_keyboard, miniapp_keyboard, miniapp_tab_url


MOSCOW_TZ = ZoneInfo("Europe/Moscow")
logger = logging.getLogger(__name__)


def _company_keyboard() -> object | None:
    url = os.getenv("MINIAPP_URL", "").strip()
    return (
        miniapp_keyboard(miniapp_tab_url(url, "group"), "👥 Открыть компанию")
        if url
        else None
    )


async def dispatch_event_group_notifications(
    bot: Bot,
    *,
    now: datetime | None = None,
) -> int:
    """Напоминает за сутки и спрашивает исход после события, без дублей."""
    current = now or datetime.now(MOSCOW_TZ)
    archive_expired_event_groups()
    sent = 0
    for kind in ("reminder_24h", "experience_prompt"):
        for item in get_due_event_group_deliveries(kind, now=current):
            group_id = int(item["group_id"])
            user_id = int(item["user_id"])
            if not claim_event_group_delivery(group_id, user_id, kind, now=current):
                continue
            try:
                if kind == "reminder_24h":
                    place = item.get("meeting_point") or "откройте чат и договоритесь"
                    await bot.send_message(
                        user_id,
                        "⏰ <b>Напоминание: мероприятие уже завтра</b>\n"
                        f"{escape(str(item['title']))}\n"
                        f"🤝 Место встречи: {escape(str(place))}",
                        parse_mode=ParseMode.HTML,
                        reply_markup=_company_keyboard(),
                    )
                else:
                    await bot.send_message(
                        user_id,
                        "Как прошла встреча на мероприятии «"
                        f"{escape(str(item['title']))}»?",
                        parse_mode=ParseMode.HTML,
                        reply_markup=event_experience_keyboard(group_id),
                    )
            except TelegramAPIError:
                mark_event_group_delivery(group_id, user_id, kind, "failed")
                logger.warning("Не удалось доставить %s для группы %s", kind, group_id)
            else:
                mark_event_group_delivery(group_id, user_id, kind, "sent")
                sent += 1
    return sent


async def run_event_group_notification_scheduler(
    bot: Bot,
    *,
    interval_seconds: int = 300,
) -> None:
    while True:
        try:
            await dispatch_event_group_notifications(bot)
        except Exception:
            logger.exception("Ошибка цикла уведомлений компаний")
        await asyncio.sleep(interval_seconds)
