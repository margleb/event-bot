import logging
import os

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from event_bot.keyboards import miniapp_keyboard
from event_bot.models import GroupAssignment


logger = logging.getLogger(__name__)


async def notify_group_assignment(
    bot: Bot,
    assignment: GroupAssignment | None,
) -> int:
    """Уведомляет всех участников только о готовой группе или пополнении."""
    if assignment is None or not assignment.notify_user_ids:
        return 0
    group = assignment.group
    if assignment.newly_activated:
        text = (
            f"✨ Ваша группа собрана!\n\n{group.title}\n"
            f"Участников: {group.member_count}. "
            "Откройте вкладку «Группа», чтобы познакомиться."
        )
    else:
        text = (
            f"👋 В группе новый участник\n\n{group.title}\n"
            f"Теперь вас: {group.member_count}."
        )
    miniapp_url = os.getenv("MINIAPP_URL", "").strip()
    delivered = 0
    for user_id in assignment.notify_user_ids:
        try:
            await bot.send_message(
                user_id,
                text,
                reply_markup=(miniapp_keyboard(miniapp_url) if miniapp_url else None),
            )
            delivered += 1
        except TelegramAPIError:
            logger.warning("Не удалось уведомить участника группы %s", user_id)
    return delivered
