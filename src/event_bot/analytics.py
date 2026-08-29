import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware, Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, TelegramObject

from event_bot.db import DB_DATETIME_FORMAT, get_connection


EVENT_NAME_PATTERN = re.compile(r"^[a-z0-9_.:-]{1,80}$")
MOSCOW_TZ = ZoneInfo("Europe/Moscow")

EVENT_LABELS = {
    "command.start": "Запуск бота",
    "command.find": "Подбор событий",
    "command.profile": "Профиль",
    "command.schedule": "Расписание",
    "command.my": "Мои мероприятия",
    "command.group": "Группа",
    "command.feedback": "Обратная связь",
    "bot.profile_text": "Заполнение профиля",
    "callback.profile_confirm": "Подтверждение профиля",
    "callback.profile_edit": "Редактирование профиля",
    "callback.digest": "Настройка подборки",
    "callback.intent.interested": "Отметка «Интересно»",
    "callback.intent.going": "Отметка «Пойду»",
    "callback.intent.not_going": "Отметка «Не подходит»",
    "callback.people": "Просмотр участников",
    "callback.request": "Запрос знакомства",
    "callback.block": "Блокировка",
    "miniapp.open": "Открытие Mini App",
    "miniapp.tab.feed": "Вкладка «Афиша»",
    "miniapp.tab.my": "Вкладка «Мои»",
    "miniapp.tab.group": "Вкладка «Группа»",
    "miniapp.tab.profile": "Вкладка «Профиль»",
    "miniapp.event_details": "Карточка события",
    "miniapp.external_source": "Переход к источнику",
    "miniapp.profile_saved": "Сохранение профиля",
    "miniapp.intent.interested": "Mini App: «Интересно»",
    "miniapp.intent.going": "Mini App: «Пойду»",
    "miniapp.intent.not_going": "Mini App: «Не подходит»",
    "miniapp.visibility": "Настройка видимости",
    "feedback.submitted": "Отправка обратной связи",
}


@dataclass(frozen=True)
class FeedbackItem:
    id: int
    user_id: int
    name: str
    username: str | None
    message: str
    source: str
    status: str
    created_at: str
    answered_at: str | None


def get_admin_ids(raw: str | None = None) -> set[int]:
    """Telegram ID администраторов из запятой-разделённой переменной."""
    value = os.getenv("ADMIN_TELEGRAM_IDS", "") if raw is None else raw
    result: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if item.isdigit() and int(item) > 0:
            result.add(int(item))
    return result


def is_admin(user_id: int) -> bool:
    return user_id in get_admin_ids()


def record_usage(
    user_id: int,
    event_name: str,
    source: str,
    metadata: dict[str, object] | None = None,
) -> None:
    """Пишет техническое событие без текста пользовательского сообщения."""
    if user_id <= 0 or not EVENT_NAME_PATTERN.fullmatch(event_name):
        return
    payload = json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":"))
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO usage_events (user_id, event_name, source, metadata)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, event_name, source[:20], payload[:2000]),
        )


def classify_bot_event(event: TelegramObject) -> str | None:
    if isinstance(event, Message):
        text = (event.text or "").strip()
        if text.startswith("/"):
            command = text.split(maxsplit=1)[0][1:].split("@", maxsplit=1)[0]
            command = re.sub(r"[^a-z0-9_]", "", command.lower())
            return f"command.{command}" if command else None
        return "bot.profile_text" if text else None
    if isinstance(event, CallbackQuery):
        parts = (event.data or "").split(":")
        if not parts or not parts[0]:
            return None
        prefix = parts[0]
        if prefix == "intent" and len(parts) > 1:
            return f"callback.intent.{parts[1]}"
        if prefix in {"visible", "toggle"}:
            return "callback.visibility"
        if prefix == "digest":
            return "callback.digest"
        if prefix == "people":
            return "callback.people"
        if prefix == "req":
            return "callback.request"
        if prefix == "block":
            return "callback.block"
        if prefix == "profile_confirm":
            return "callback.profile_confirm"
        if prefix == "profile_edit":
            return "callback.profile_edit"
        return f"callback.{re.sub(r'[^a-z0-9_]', '', prefix.lower())}"
    return None


class UsageTrackingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = data.get("event_from_user")
        event_name = classify_bot_event(event)
        if user is not None and event_name is not None:
            record_usage(user.id, event_name, "bot")
        return await handler(event, data)


def create_feedback(
    user_id: int,
    name: str,
    username: str | None,
    message: str,
    source: str,
) -> FeedbackItem:
    text = " ".join(message.strip().split())
    if not 3 <= len(text) <= 2000:
        raise ValueError("Сообщение должно содержать от 3 до 2000 символов")
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO feedback_messages
                (user_id, name, username, message, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, name[:128], username[:64] if username else None, text, source),
        )
        feedback_id = cursor.lastrowid
        row = conn.execute(
            "SELECT * FROM feedback_messages WHERE id = ?",
            (feedback_id,),
        ).fetchone()
    assert row is not None
    record_usage(user_id, "feedback.submitted", source)
    return _feedback_from_row(row)


def _feedback_from_row(row) -> FeedbackItem:
    return FeedbackItem(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        username=row["username"],
        message=row["message"],
        source=row["source"],
        status=row["status"],
        created_at=row["created_at"],
        answered_at=row["answered_at"],
    )


def get_feedback(feedback_id: int) -> FeedbackItem | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM feedback_messages WHERE id = ?",
            (feedback_id,),
        ).fetchone()
    return _feedback_from_row(row) if row is not None else None


def get_recent_feedback(limit: int = 10) -> list[FeedbackItem]:
    amount = max(1, min(limit, 30))
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM feedback_messages
            ORDER BY CASE status WHEN 'new' THEN 0 ELSE 1 END,
                     created_at DESC, id DESC
            LIMIT ?
            """,
            (amount,),
        ).fetchall()
    return [_feedback_from_row(row) for row in rows]


def mark_feedback_answered(feedback_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE feedback_messages
            SET status = 'answered', answered_at = datetime('now')
            WHERE id = ?
            """,
            (feedback_id,),
        )
    return cursor.rowcount > 0


def build_admin_report() -> dict[str, object]:
    admin_ids = sorted(get_admin_ids())
    admin_filter = (
        f" AND user_id NOT IN ({','.join('?' for _ in admin_ids)})"
        if admin_ids
        else ""
    )
    with get_connection() as conn:
        if admin_ids:
            placeholders = ",".join("?" for _ in admin_ids)
            known_users = conn.execute(
                f"""
                SELECT COUNT(*) AS amount FROM (
                    SELECT telegram_id AS user_id FROM users
                    WHERE telegram_id NOT IN ({placeholders})
                    UNION
                    SELECT user_id FROM usage_events
                    WHERE user_id NOT IN ({placeholders})
                )
                """,
                (*admin_ids, *admin_ids),
            ).fetchone()["amount"]
        else:
            known_users = conn.execute(
                """
                SELECT COUNT(*) AS amount FROM (
                    SELECT telegram_id AS user_id FROM users
                    UNION
                    SELECT user_id FROM usage_events
                )
                """
            ).fetchone()["amount"]
        active = {}
        for label, modifier in (("day", "-1 day"), ("week", "-7 days"), ("month", "-30 days")):
            active[label] = conn.execute(
                """
                SELECT COUNT(DISTINCT user_id) AS amount
                FROM usage_events
                WHERE created_at >= datetime('now', ?)
                """
                + admin_filter,
                (modifier, *admin_ids),
            ).fetchone()["amount"]
        visits = {}
        for label, modifier in (("day", "-1 day"), ("week", "-7 days")):
            visits[label] = conn.execute(
                """
                SELECT COUNT(*) AS amount
                FROM usage_events
                WHERE created_at >= datetime('now', ?)
                  AND event_name IN ('miniapp.open', 'command.start')
                """
                + admin_filter,
                (modifier, *admin_ids),
            ).fetchone()["amount"]
        actions_week = conn.execute(
            """
            SELECT COUNT(*) AS amount FROM usage_events
            WHERE created_at >= datetime('now', '-7 days')
            """
            + admin_filter,
            admin_ids,
        ).fetchone()["amount"]
        top_features = conn.execute(
            """
            SELECT event_name, COUNT(*) AS amount
            FROM usage_events
            WHERE created_at >= datetime('now', '-7 days')
              AND event_name NOT IN ('command.admin', 'command.feedbacks', 'command.reply')
            """
            + admin_filter
            + """
            GROUP BY event_name
            ORDER BY amount DESC, event_name
            LIMIT 8
            """,
            admin_ids,
        ).fetchall()
        daily_rows = conn.execute(
            """
            SELECT date(created_at) AS day, COUNT(DISTINCT user_id) AS amount
            FROM usage_events
            WHERE created_at >= datetime('now', '-6 days')
            """
            + admin_filter
            + """
            GROUP BY date(created_at)
            """,
            admin_ids,
        ).fetchall()
        feedback = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) AS fresh
            FROM feedback_messages
            """
        ).fetchone()
        groups = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active
            FROM interest_groups
            """
        ).fetchone()
        group_members = conn.execute(
            "SELECT COUNT(*) AS amount FROM interest_group_members"
        ).fetchone()["amount"]
        last_activity = conn.execute(
            "SELECT MAX(created_at) AS value FROM usage_events"
        ).fetchone()["value"]

    days = {row["day"]: row["amount"] for row in daily_rows}
    today = datetime.now(timezone.utc).date()
    daily = [
        ((today - timedelta(days=offset)).isoformat(), days.get((today - timedelta(days=offset)).isoformat(), 0))
        for offset in range(6, -1, -1)
    ]
    return {
        "known_users": known_users,
        "active": active,
        "visits": visits,
        "actions_week": actions_week,
        "top_features": [(row["event_name"], row["amount"]) for row in top_features],
        "daily": daily,
        "feedback_total": feedback["total"] or 0,
        "feedback_new": feedback["fresh"] or 0,
        "groups_total": groups["total"] or 0,
        "groups_active": groups["active"] or 0,
        "group_members": group_members,
        "last_activity": last_activity,
    }


def build_daily_admin_report(
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Статистика за последние 24 часа и предшествующие им сутки."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    current = current.replace(microsecond=0)
    started_at = current - timedelta(days=1)
    previous_started_at = started_at - timedelta(days=1)
    current_text = current.strftime(DB_DATETIME_FORMAT)
    started_text = started_at.strftime(DB_DATETIME_FORMAT)
    previous_started_text = previous_started_at.strftime(DB_DATETIME_FORMAT)

    admin_ids = sorted(get_admin_ids())
    admin_filter = (
        f" AND user_id NOT IN ({','.join('?' for _ in admin_ids)})"
        if admin_ids
        else ""
    )

    def count_events(conn, start: str, end: str) -> int:
        return conn.execute(
            """
            SELECT COUNT(*) AS amount
            FROM usage_events
            WHERE created_at >= ? AND created_at < ?
            """
            + admin_filter,
            (start, end, *admin_ids),
        ).fetchone()["amount"]

    def count_active(conn, start: str, end: str) -> int:
        return conn.execute(
            """
            SELECT COUNT(DISTINCT user_id) AS amount
            FROM usage_events
            WHERE created_at >= ? AND created_at < ?
            """
            + admin_filter,
            (start, end, *admin_ids),
        ).fetchone()["amount"]

    def count_visits(conn, start: str, end: str) -> int:
        return conn.execute(
            """
            SELECT COUNT(*) AS amount
            FROM usage_events
            WHERE created_at >= ? AND created_at < ?
              AND event_name IN ('miniapp.open', 'command.start')
            """
            + admin_filter,
            (start, end, *admin_ids),
        ).fetchone()["amount"]

    with get_connection() as conn:
        if admin_ids:
            placeholders = ",".join("?" for _ in admin_ids)
            known_users = conn.execute(
                f"""
                SELECT COUNT(*) AS amount FROM (
                    SELECT telegram_id AS user_id FROM users
                    WHERE telegram_id NOT IN ({placeholders})
                    UNION
                    SELECT user_id FROM usage_events
                    WHERE user_id NOT IN ({placeholders})
                )
                """,
                (*admin_ids, *admin_ids),
            ).fetchone()["amount"]
        else:
            known_users = conn.execute(
                """
                SELECT COUNT(*) AS amount FROM (
                    SELECT telegram_id AS user_id FROM users
                    UNION
                    SELECT user_id FROM usage_events
                )
                """
            ).fetchone()["amount"]

        active = count_active(conn, started_text, current_text)
        active_previous = count_active(
            conn,
            previous_started_text,
            started_text,
        )
        actions = count_events(conn, started_text, current_text)
        actions_previous = count_events(
            conn,
            previous_started_text,
            started_text,
        )
        visits = count_visits(conn, started_text, current_text)
        visits_previous = count_visits(
            conn,
            previous_started_text,
            started_text,
        )
        new_users = conn.execute(
            """
            SELECT COUNT(*) AS amount
            FROM (
                SELECT user_id, MIN(created_at) AS first_seen
                FROM usage_events
                GROUP BY user_id
            ) AS first_usage
            WHERE first_seen >= ? AND first_seen < ?
            """
            + admin_filter,
            (started_text, current_text, *admin_ids),
        ).fetchone()["amount"]
        top_features = conn.execute(
            """
            SELECT event_name, COUNT(*) AS amount
            FROM usage_events
            WHERE created_at >= ? AND created_at < ?
              AND event_name NOT IN (
                  'command.admin', 'command.feedbacks', 'command.reply'
              )
            """
            + admin_filter
            + """
            GROUP BY event_name
            ORDER BY amount DESC, event_name
            LIMIT 5
            """,
            (started_text, current_text, *admin_ids),
        ).fetchall()
        feedback_received = conn.execute(
            """
            SELECT COUNT(*) AS amount
            FROM feedback_messages
            WHERE created_at >= ? AND created_at < ?
            """
            + admin_filter,
            (started_text, current_text, *admin_ids),
        ).fetchone()["amount"]
        feedback_open = conn.execute(
            "SELECT COUNT(*) AS amount FROM feedback_messages WHERE status = 'new'"
            + admin_filter,
            admin_ids,
        ).fetchone()["amount"]
        groups = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active
            FROM interest_groups
            """
        ).fetchone()
        group_members = conn.execute(
            "SELECT COUNT(*) AS amount FROM interest_group_members"
        ).fetchone()["amount"]

    started_msk = started_at.astimezone(MOSCOW_TZ)
    current_msk = current.astimezone(MOSCOW_TZ)
    return {
        "period": (
            f"{started_msk.strftime('%d.%m %H:%M')}–"
            f"{current_msk.strftime('%d.%m %H:%M')} МСК"
        ),
        "known_users": known_users,
        "active": active,
        "active_previous": active_previous,
        "new_users": new_users,
        "visits": visits,
        "visits_previous": visits_previous,
        "actions": actions,
        "actions_previous": actions_previous,
        "top_features": [
            (row["event_name"], row["amount"]) for row in top_features
        ],
        "feedback_received": feedback_received,
        "feedback_open": feedback_open,
        "groups_total": groups["total"] or 0,
        "groups_active": groups["active"] or 0,
        "group_members": group_members,
    }


def format_daily_admin_report(report: dict[str, object]) -> str:
    """Компактное Telegram-сообщение с ежедневной статистикой."""
    top = report["top_features"]
    top_lines = [
        f"• {escape(EVENT_LABELS.get(name, name))} — <b>{amount}</b>"
        for name, amount in top
    ] or ["• Пока нет данных"]
    return (
        "📈 <b>Ежедневная сводка</b>\n"
        f"<i>{escape(str(report['period']))}</i>\n\n"
        f"👤 Активные: <b>{report['active']}</b> · "
        f"сутками ранее {report['active_previous']}\n"
        f"🆕 Новые пользователи: <b>{report['new_users']}</b>\n"
        f"🚪 Входы: <b>{report['visits']}</b> · "
        f"сутками ранее {report['visits_previous']}\n"
        f"🧭 Действия: <b>{report['actions']}</b> · "
        f"сутками ранее {report['actions_previous']}\n\n"
        "<b>Чем пользовались</b>\n"
        + "\n".join(top_lines)
        + "\n\n"
        f"💬 Обращения: за сутки — <b>{report['feedback_received']}</b> · "
        f"ждут ответа — <b>{report['feedback_open']}</b>\n"
        f"🤝 Группы: готовых — <b>{report['groups_active']}</b> / "
        f"всего — <b>{report['groups_total']}</b> · участников — "
        f"<b>{report['group_members']}</b>\n"
        f"👥 Всего пользователей: <b>{report['known_users']}</b>\n\n"
        "Подробный отчёт: /admin · обращения: /feedbacks"
    )


def format_admin_report(report: dict[str, object]) -> str:
    active = report["active"]
    visits = report["visits"]
    top = report["top_features"]
    daily = report["daily"]
    assert isinstance(active, dict) and isinstance(visits, dict)
    top_lines = [
        f"• {EVENT_LABELS.get(name, name)} — {amount}"
        for name, amount in top
    ] or ["• Пока нет данных"]
    daily_line = " · ".join(f"{day[5:]}: {amount}" for day, amount in daily)
    last_activity = report["last_activity"] or "ещё не было"
    return (
        "📊 <b>Работа бота</b>\n\n"
        f"👥 Пользователей: <b>{report['known_users']}</b>\n"
        f"⚡ Активные: 24ч — <b>{active['day']}</b> · "
        f"7д — <b>{active['week']}</b> · 30д — <b>{active['month']}</b>\n"
        f"🚪 Входы: 24ч — <b>{visits['day']}</b> · 7д — <b>{visits['week']}</b>\n"
        f"🧭 Действий за 7д: <b>{report['actions_week']}</b>\n\n"
        "<b>Популярные функции за 7 дней</b>\n"
        + "\n".join(top_lines)
        + "\n\n<b>DAU за 7 дней</b>\n"
        + daily_line
        + "\n\n"
        f"💬 Обратная связь: новых — <b>{report['feedback_new']}</b> · "
        f"всего — <b>{report['feedback_total']}</b>\n"
        f"🤝 Группы: готовых — <b>{report['groups_active']}</b> / "
        f"всего — <b>{report['groups_total']}</b> · участников — "
        f"<b>{report['group_members']}</b>\n"
        f"🕒 Последняя активность: {escape(str(last_activity))} UTC\n\n"
        "Последние обращения: /feedbacks"
    )


def format_feedback_item(item: FeedbackItem) -> str:
    author = escape(item.name or "Без имени")
    if item.username:
        author += f" (@{escape(item.username)})"
    status = "🆕" if item.status == "new" else "✅"
    return (
        f"{status} <b>#{item.id}</b> · {author} · {escape(item.source)}\n"
        f"{escape(item.message)}\n"
        f"<i>{escape(item.created_at)} UTC</i>"
    )


async def notify_admins(bot: Bot, item: FeedbackItem) -> int:
    delivered = 0
    for admin_id in get_admin_ids():
        try:
            await bot.send_message(
                admin_id,
                "💬 <b>Новое обращение</b>\n\n"
                + format_feedback_item(item)
                + f"\n\nОтветить: <code>/reply {item.id} текст</code>",
                parse_mode=ParseMode.HTML,
            )
            delivered += 1
        except TelegramAPIError:
            continue
    return delivered
