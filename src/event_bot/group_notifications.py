import logging
import os
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.enums import ParseMode

from event_bot.keyboards import miniapp_keyboard
from event_bot.models import (
    GroupAssignment,
    GroupConnectionRequest,
    GroupEventInvite,
    GroupMessage,
)


logger = logging.getLogger(__name__)


def _group_miniapp_url(url: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}tab=group"


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
                reply_markup=(
                    miniapp_keyboard(_group_miniapp_url(miniapp_url), "👥 Открыть группу")
                    if miniapp_url
                    else None
                ),
            )
            delivered += 1
        except TelegramAPIError:
            logger.warning("Не удалось уведомить участника группы %s", user_id)
    return delivered


def _group_keyboard():
    miniapp_url = os.getenv("MINIAPP_URL", "").strip()
    return (
        miniapp_keyboard(_group_miniapp_url(miniapp_url), "👥 Открыть группу")
        if miniapp_url
        else None
    )


async def notify_group_connection_request(
    bot: Bot,
    request: GroupConnectionRequest,
) -> bool:
    """Сообщает адресату о безопасном запросе на знакомство."""
    common = ", ".join(request.common_interests) or "общая группа"
    try:
        await bot.send_message(
            request.to_user,
            (
                f"👋 {request.from_name} хочет познакомиться\n\n"
                f"Группа: {request.group_title}\n"
                f"Общее: {common}\n\n"
                "Откройте вкладку «Группа», чтобы принять или отклонить запрос."
            ),
            reply_markup=_group_keyboard(),
        )
        return True
    except TelegramAPIError:
        logger.warning("Не удалось отправить групповой запрос пользователю %s", request.to_user)
        return False


def _contact_html(name: str, user_id: int, username: str | None) -> str:
    if username:
        clean_username = username.lstrip("@")
        return f'<a href="https://t.me/{escape(clean_username)}">@{escape(clean_username)}</a>'
    return f'<a href="tg://user?id={user_id}">{escape(name)}</a>'


async def notify_group_connection_accepted(
    bot: Bot,
    request: GroupConnectionRequest,
) -> int:
    """После согласия раскрывает каждой стороне только контакт собеседника."""
    messages = (
        (
            request.from_user,
            _contact_html(request.to_name, request.to_user, request.to_username),
        ),
        (
            request.to_user,
            _contact_html(request.from_name, request.from_user, request.from_username),
        ),
    )
    delivered = 0
    for user_id, contact in messages:
        try:
            await bot.send_message(
                user_id,
                (
                    "Знакомство подтверждено ✅\n"
                    f"Контакт: {contact}\n"
                    f"Группа: {escape(request.group_title)}"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=_group_keyboard(),
            )
            delivered += 1
        except TelegramAPIError:
            logger.warning("Не удалось отправить контакт участнику %s", user_id)
    return delivered


async def notify_group_event_invite(
    bot: Bot,
    invite: GroupEventInvite,
    user_ids: list[int],
) -> int:
    """Уведомляет группу о новом совместном плане."""
    delivered = 0
    when = invite.event.date.strftime("%d.%m в %H:%M")
    for user_id in user_ids:
        if user_id == invite.created_by:
            continue
        try:
            await bot.send_message(
                user_id,
                (
                    f"🎟 {invite.creator_name} предлагает сходить вместе\n\n"
                    f"{invite.event.title}\n"
                    f"{when} · {invite.event.venue or invite.event.address}\n\n"
                    "Ответьте «Иду» или «Не смогу» во вкладке «Группа»."
                ),
                reply_markup=_group_keyboard(),
            )
            delivered += 1
        except TelegramAPIError:
            logger.warning("Не удалось отправить приглашение участнику %s", user_id)
    return delivered


async def notify_group_message(
    bot: Bot,
    message: GroupMessage,
    user_ids: list[int],
) -> int:
    """Доставляет короткое уведомление о новом сообщении группе."""
    preview = message.text if len(message.text) <= 240 else f"{message.text[:237]}…"
    delivered = 0
    for user_id in user_ids:
        if user_id == message.user_id:
            continue
        try:
            await bot.send_message(
                user_id,
                f"💬 {message.author_name} в группе:\n{preview}",
                reply_markup=_group_keyboard(),
            )
            delivered += 1
        except TelegramAPIError:
            logger.warning("Не удалось отправить сообщение группы участнику %s", user_id)
    return delivered
