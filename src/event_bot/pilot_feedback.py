"""Manually dispatched, deduplicated pilot questions; never a background campaign."""

import argparse
import asyncio
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ForceReply

from event_bot.analytics import get_admin_ids
from event_bot.db import DB_DATETIME_FORMAT, get_connection


def select_recipients(campaign: str, *, now: datetime | None = None) -> list[dict]:
    current = now or datetime.now(timezone.utc)
    cutoff = (current - timedelta(hours=24)).astimezone(timezone.utc).strftime(DB_DATETIME_FORMAT)
    cooldown = (current - timedelta(days=7)).astimezone(timezone.utc).strftime(DB_DATETIME_FORMAT)
    with get_connection() as conn:
        cohort = {r[0] for r in conn.execute(
            "SELECT user_id FROM user_acquisition WHERE campaign=?", (campaign,),
        )}
        excluded = set(get_admin_ids())
        for sql, params in (
            ("SELECT user_id FROM research_participants", ()),
            ("SELECT user_id FROM user_suspensions WHERE lifted_at IS NULL", ()),
            ("SELECT user_id FROM pilot_feedback_outreach WHERE campaign=? OR created_at>=?", (campaign, cooldown)),
            ("SELECT user_id FROM inactivity_feedback_prompts WHERE prompt_sent_at>=?", (cooldown,)),
            ("SELECT user_id FROM feedback_messages WHERE created_at>=?", (cooldown,)),
            ("SELECT user_id FROM event_group_deliveries WHERE kind='experience_prompt' AND delivered_at>=datetime(?,'+3 hours')", (cooldown,)),
        ):
            excluded.update(r[0] for r in conn.execute(sql, params))
        cohort -= excluded
        opened = {r[0] for r in conn.execute(
            """SELECT ue.user_id FROM usage_events ue
               JOIN user_acquisition ua ON ua.user_id=ue.user_id
               WHERE ua.campaign=? AND ue.event_name='miniapp.open'
                 AND ue.created_at>=ua.first_seen_at""", (campaign,),
        )}
        last_activity = dict(conn.execute("SELECT user_id,MAX(created_at) FROM usage_events GROUP BY user_id"))
        cohort = {u for u in cohort if last_activity.get(u, '9999') < cutoff}
        members = {r[0] for r in conn.execute(
            """SELECT gm.user_id FROM event_group_members gm
               JOIN event_groups eg ON eg.id=gm.group_id WHERE eg.status IN ('active','forming')""",
        )}
        joined = {r[0] for r in conn.execute(
            "SELECT user_id FROM usage_events WHERE event_name='miniapp.event_company.joined'",
        )}
        recipients = {u: {'user_id': u, 'segment': 'no_search'} for u in (cohort & opened)-members-joined}
        for row in conn.execute(
            """SELECT gm.user_id,eg.id,eg.activated_at,e.title FROM event_group_members gm
               JOIN event_groups eg ON eg.id=gm.group_id JOIN events e ON e.id=eg.event_id
               JOIN user_acquisition ua ON ua.user_id=gm.user_id
               WHERE eg.status='active' AND eg.research_campaign IS NULL AND ua.campaign=?
                 AND eg.activated_at>=ua.first_seen_at AND eg.activated_at<?
               ORDER BY eg.activated_at DESC,eg.id DESC""", (campaign, cutoff),
        ):
            user_id, group_id = row['user_id'], row['id']
            if user_id not in cohort or user_id in recipients:
                continue
            messages = conn.execute(
                "SELECT 1 FROM event_group_messages WHERE group_id=? AND user_id=? LIMIT 1", (group_id, user_id),
            ).fetchone()
            connection = conn.execute(
                """SELECT 1 FROM requests req JOIN event_groups eg ON eg.event_id=req.event_id
                   WHERE eg.id=? AND (req.from_user=? OR req.to_user=?) AND req.status='accepted' LIMIT 1""",
                (group_id, user_id, user_id),
            ).fetchone()
            if not messages and not connection:
                recipients[user_id] = {'user_id': user_id, 'segment': 'no_conversation', 'title': row['title']}
    return [recipients[u] for u in sorted(recipients)]


def question_text(recipient: dict) -> str:
    if recipient['segment'] == 'no_search':
        question = (
            "Вы хотели бы найти компанию на мероприятие или пока просто смотрели афишу? "
            "Если хотели, что помешало начать поиск?"
        )
    else:
        question = (
            f"Удалось ли вам связаться с участниками компании на «{escape(recipient['title'][:600])}»? "
            "Если пока нет — что помешало начать общение?"
        )
    return (
        "👋 Это команда Мск.Митап. Поможете понять, насколько сервис вам полезен?\n\n"
        + question
        + "\n\nМожно ответить на это сообщение парой слов. Ответ необязателен — "
        "повторно этот вопрос не пришлём."
    )


def claim_recipient(campaign: str, recipient: dict) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO pilot_feedback_outreach (campaign,user_id,segment) VALUES (?,?,?)",
            (campaign, recipient['user_id'], recipient['segment']),
        )
        return cursor.rowcount > 0


def mark_delivery(campaign: str, user_id: int, status: str, message_id: int | None = None) -> None:
    if status not in {'sent', 'failed', 'unknown'}:
        raise ValueError('Invalid delivery status')
    with get_connection() as conn:
        conn.execute(
            """UPDATE pilot_feedback_outreach SET status=?,message_id=?,
               sent_at=CASE WHEN ?='sent' THEN datetime('now') ELSE NULL END
               WHERE campaign=? AND user_id=? AND status='pending'""",
            (status, message_id, status, campaign, user_id),
        )


def reply_source(user_id: int, message_id: int) -> str | None:
    with get_connection() as conn:
        row = conn.execute(
            """SELECT campaign,segment FROM pilot_feedback_outreach
               WHERE user_id=? AND message_id=? AND status='sent'""", (user_id, message_id),
        ).fetchone()
    return f"pilot:{row['campaign']}:{row['segment']}" if row else None


async def dispatch(bot: Bot, campaign: str, *, limit: int, now: datetime | None = None) -> dict:
    current = now or datetime.now(timezone.utc)
    if not 10 <= current.astimezone(ZoneInfo('Europe/Moscow')).hour < 22:
        raise ValueError('Send only between 10:00 and 22:00 Moscow time')
    recipients = select_recipients(campaign, now=current)
    if not 1 <= limit <= 30 or len(recipients) > limit:
        raise ValueError('Recipient count exceeds the reviewed limit')
    result = Counter()
    segments = Counter()
    for recipient in recipients:
        if not claim_recipient(campaign, recipient):
            continue
        try:
            message = await bot.send_message(
                recipient['user_id'], question_text(recipient), parse_mode='HTML',
                reply_markup=ForceReply(input_field_placeholder='Ваш ответ (необязательно)'),
                disable_notification=True,
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            mark_delivery(campaign, recipient['user_id'], 'failed')
            result['failed'] += 1
        except TelegramAPIError:
            # Delivery may have happened: do not retry an uncertain send.
            mark_delivery(campaign, recipient['user_id'], 'unknown')
            result['unknown'] += 1
            break
        else:
            mark_delivery(campaign, recipient['user_id'], 'sent', message.message_id)
            result['sent'] += 1
            segments[recipient['segment']] += 1
        await asyncio.sleep(0.1)
    return {'delivery': dict(result), 'sent_segments': dict(segments)}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', required=True)
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--limit', type=int, default=15)
    args = parser.parse_args()
    if not args.send:
        print(json.dumps({'eligible_segments': dict(Counter(r['segment'] for r in select_recipients(args.campaign)))}, ensure_ascii=False))
        return
    async with Bot(token=os.environ['BOT_TOKEN']) as bot:
        print(json.dumps(await dispatch(bot, args.campaign, limit=args.limit), ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
