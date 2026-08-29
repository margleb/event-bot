# src/event_bot/db.py
import json
import logging
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime
from html import escape
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

import numpy as np

from event_bot.embedding_provider import vector_from_blob
from event_bot.models import (
    Companion,
    ConnectionRequest,
    Event,
    GroupAssignment,
    GroupConnectionRequest,
    GroupEventInvite,
    GroupMessage,
    InterestGroup,
    InterestGroupView,
    Profile,
    UserIntent,
    format_group_size,
)
from event_bot.source_branding import source_brand

# Файл базы лежит в data/ в корне проекта: три parent — это
# db.py -> event_bot -> src -> корень
DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "bot.db"

# Формат, в котором даты лежат в SQLite: он сравним с datetime('now')
DB_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

logger = logging.getLogger(__name__)

# Сколько карточек максимум отправляем за один /find
MAX_EVENTS = 5

# Насколько сильно нежелательные интересы понижают semantic score.
AVOID_SIMILARITY_WEIGHT = 0.5

# Сколько других участников показываем по кнопке «Кто идёт»
MAX_COMPANIONS = 5

# Постоянные группы активируются после набора трёх совместимых участников
# и больше пяти человек не разрастаются.
GROUP_MIN_MEMBERS = 3
GROUP_MAX_MEMBERS = 5

# Не больше пяти новых запросов за скользящие 24 часа.
DAILY_REQUEST_LIMIT = 5

# Статусы, при которых человек считается участником события:
# только они попадают в список «Кто идёт»
PARTICIPATING_STATUSES = ("interested", "going")

# Показываем, если профиль сохранён до появления колонки name
UNKNOWN_NAME = "Без имени"

# Допустимые статусы отметки: то, что приходит из callback_data,
# сверяется с этим списком перед записью в базу
INTENT_STATUSES = ("interested", "going", "not_going")

# Подписи статусов для сообщений бота
INTENT_STATUS_LABELS = {
    "interested": "интересно",
    "going": "иду",
    "not_going": "не подходит",
}

WEEKDAYS_RU = ("понедельник", "вторник", "среда", "четверг",
               "пятница", "суббота", "воскресенье")

PROFILE_WEEKDAYS = {
    "mon": 0,
    "monday": 0,
    "пн": 0,
    "понедельник": 0,
    "tue": 1,
    "tuesday": 1,
    "вт": 1,
    "вторник": 1,
    "wed": 2,
    "wednesday": 2,
    "ср": 2,
    "среда": 2,
    "thu": 3,
    "thursday": 3,
    "чт": 3,
    "четверг": 3,
    "fri": 4,
    "friday": 4,
    "пт": 4,
    "пятница": 4,
    "sat": 5,
    "saturday": 5,
    "сб": 5,
    "суббота": 5,
    "sun": 6,
    "sunday": 6,
    "вс": 6,
    "воскресенье": 6,
}


@contextmanager
def get_connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Соединение с SQLite: коммитит при успехе, откатывает при ошибке."""
    db_path = db_path or DB_PATH
    # каталог data/ может отсутствовать при первом запуске
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    # с этой строкой строки результата ведут себя как словарь: row["title"]
    conn.row_factory = sqlite3.Row
    try:
        # тело with выполняется здесь; вернулись без ошибки — сохраняем
        yield conn
        conn.commit()
    except Exception:
        # любая ошибка внутри with откатывает всё, что успели записать
        conn.rollback()
        raise
    finally:
        conn.close()


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    """Добавляет колонку, если её ещё нет: простая миграция старых баз."""
    # PRAGMA table_info возвращает описание всех колонок таблицы
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    """Создаёт таблицы и индексы, если их ещё нет.

    Вызывается при каждом старте бота (app.py): IF NOT EXISTS делает
    повторный вызов безопасным, данные не теряются.
    """
    with get_connection() as conn:
        conn.execute(
            """
            -- Профили: одна строка на пользователя.
            -- Списки (интересы, дни) хранятся строкой JSON — в SQLite
            -- нет типа «массив», а отдельная таблица тут избыточна.
            CREATE TABLE IF NOT EXISTS users (
                telegram_id    INTEGER PRIMARY KEY,
                -- имя из Telegram (first_name): единственное, что показываем
                -- другим участникам
                name           TEXT NOT NULL DEFAULT '',
                -- username используется только после взаимного согласия
                username       TEXT,
                interests      TEXT NOT NULL DEFAULT '[]',
                avoid          TEXT NOT NULL DEFAULT '[]',
                days           TEXT NOT NULL DEFAULT '[]',
                budget_rub     INTEGER,
                group_size_min INTEGER,
                group_size_max INTEGER,
                profile_embedding       BLOB,
                profile_embedding_model TEXT,
                avoid_embedding         BLOB,
                avoid_embedding_model   TEXT,
                digest_weekday INTEGER,
                digest_enabled INTEGER NOT NULL DEFAULT 0,
                last_digest_date TEXT,
                created_at     TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS interest_groups (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                status       TEXT NOT NULL DEFAULT 'forming',
                topics       TEXT NOT NULL DEFAULT '[]',
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                activated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS interest_group_members (
                group_id  INTEGER NOT NULL,
                user_id   INTEGER NOT NULL,
                joined_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (group_id, user_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS group_connection_requests (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id   INTEGER NOT NULL,
                from_user  INTEGER NOT NULL,
                to_user    INTEGER NOT NULL,
                status     TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (group_id, from_user, to_user)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS interest_group_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id   INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                message    TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS group_event_invites (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id   INTEGER NOT NULL,
                event_id   INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (group_id, event_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS group_event_invite_responses (
                invite_id  INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                status     TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (invite_id, user_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                event_name TEXT NOT NULL,
                source     TEXT NOT NULL,
                metadata   TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                name        TEXT NOT NULL DEFAULT '',
                username    TEXT,
                message     TEXT NOT NULL,
                source      TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'new',
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                answered_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_report_deliveries (
                admin_id      INTEGER NOT NULL,
                report_date   TEXT NOT NULL,
                delivered_at  TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (admin_id, report_date)
            )
            """
        )
        conn.execute(
            """
            -- Одноразовый мягкий опрос тех, кто перестал пользоваться ботом.
            -- Наличие строки защищает от повторной отправки после рестартов.
            CREATE TABLE IF NOT EXISTS inactivity_feedback_prompts (
                user_id         INTEGER PRIMARY KEY,
                prompt_sent_at  TEXT NOT NULL DEFAULT (datetime('now')),
                delivery_status TEXT NOT NULL DEFAULT 'pending',
                response_code   TEXT,
                responded_at    TEXT
            )
            """
        )
        conn.execute(
            """
            -- Мероприятия. date хранится текстом "ГГГГ-ММ-ДД ЧЧ:ММ:СС",
            -- такой формат корректно сравнивается с datetime('now')
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                description TEXT NOT NULL,
                city        TEXT NOT NULL,
                address     TEXT NOT NULL,
                date        TEXT NOT NULL,
                end_date    TEXT,
                price_min   INTEGER,
                price_max   INTEGER,
                price_text  TEXT,
                is_free     INTEGER,
                tags        TEXT NOT NULL DEFAULT '[]',
                venue       TEXT NOT NULL,
                source_id   TEXT,
                external_id TEXT,
                source_url  TEXT,
                fetched_at  TEXT,
                status      TEXT,
                embedding       BLOB,
                embedding_model TEXT,
                content_hash    TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            -- Запросы не удаляются после ответа: UNIQUE одновременно
            -- обеспечивает идемпотентность и запрещает повтор после отказа.
            CREATE TABLE IF NOT EXISTS requests (
                id         INTEGER PRIMARY KEY,
                event_id   INTEGER NOT NULL,
                from_user  INTEGER NOT NULL,
                to_user    INTEGER NOT NULL,
                status     TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (event_id, from_user, to_user)
            )
            """
        )
        conn.execute(
            """
            -- Блокировка глобальна: не привязана к событию.
            CREATE TABLE IF NOT EXISTS blocks (
                blocker    INTEGER NOT NULL,
                blocked    INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (blocker, blocked)
            )
            """
        )
        conn.execute(
            """
            -- Отметки «пойду / интересно / не подходит».
            -- Составной первичный ключ = одна строка на пару
            -- пользователь + событие, поэтому повторное нажатие обновляет
            -- существующую запись, а не плодит новые.
            CREATE TABLE IF NOT EXISTS intents (
                user_id    INTEGER NOT NULL,
                event_id   INTEGER NOT NULL,
                status     TEXT NOT NULL,
                -- согласие показывать себя другим; по умолчанию 0
                visible    INTEGER NOT NULL DEFAULT 0,
                -- спрашивали ли уже про видимость по этому событию:
                -- вопрос задаётся один раз, дальше — кнопка-переключатель
                visibility_asked INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, event_id)
            )
            """
        )
        # CREATE TABLE IF NOT EXISTS не трогает уже существующую таблицу,
        # поэтому новые колонки досоздаём отдельно — иначе база, сделанная
        # прошлой версией бота, останется без name и visibility_asked
        _add_column_if_missing(conn, "users", "name", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "users", "username", "TEXT")
        _add_column_if_missing(conn, "users", "profile_embedding", "BLOB")
        _add_column_if_missing(conn, "users", "profile_embedding_model", "TEXT")
        _add_column_if_missing(conn, "users", "avoid_embedding", "BLOB")
        _add_column_if_missing(conn, "users", "avoid_embedding_model", "TEXT")
        _add_column_if_missing(conn, "users", "digest_weekday", "INTEGER")
        _add_column_if_missing(
            conn, "users", "digest_enabled", "INTEGER NOT NULL DEFAULT 0"
        )
        _add_column_if_missing(conn, "users", "last_digest_date", "TEXT")
        _add_column_if_missing(
            conn,
            "users",
            "group_matching_enabled",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _add_column_if_missing(
            conn, "intents", "visibility_asked", "INTEGER NOT NULL DEFAULT 0"
        )
        # Поля импорта nullable: строки старой схемы должны сохраниться как есть.
        _add_column_if_missing(conn, "events", "end_date", "TEXT")
        _add_column_if_missing(conn, "events", "price_text", "TEXT")
        _add_column_if_missing(conn, "events", "is_free", "INTEGER")
        _add_column_if_missing(conn, "events", "source_id", "TEXT")
        _add_column_if_missing(conn, "events", "external_id", "TEXT")
        _add_column_if_missing(conn, "events", "source_url", "TEXT")
        _add_column_if_missing(conn, "events", "fetched_at", "TEXT")
        _add_column_if_missing(conn, "events", "status", "TEXT")
        _add_column_if_missing(conn, "events", "embedding", "BLOB")
        _add_column_if_missing(conn, "events", "embedding_model", "TEXT")
        _add_column_if_missing(conn, "events", "content_hash", "TEXT")

        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telegram_id "
            "ON users(telegram_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_city_date "
            "ON events(city, date)"
        )
        # Разные источники могут прислать одно название на одну дату — это не
        # дубль. Идентичность задаёт только пара источник + внешний id.
        conn.execute("DROP INDEX IF EXISTS idx_events_title_date")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_source_external "
            "ON events(source_id, external_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_status_city_date "
            "ON events(status, city, date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_requests_from_created "
            "ON requests(from_user, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_requests_pair_status "
            "ON requests(from_user, to_user, status)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_group_members_user "
            "ON interest_group_members(user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_groups_status "
            "ON interest_groups(status, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_group_connection_pair "
            "ON group_connection_requests(group_id, from_user, to_user, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_group_messages_group_created "
            "ON interest_group_messages(group_id, created_at, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_group_invites_group_created "
            "ON group_event_invites(group_id, created_at, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_events_created "
            "ON usage_events(created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_events_user_created "
            "ON usage_events(user_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_events_name_created "
            "ON usage_events(event_name, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_status_created "
            "ON feedback_messages(status, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_inactivity_feedback_sent "
            "ON inactivity_feedback_prompts(prompt_sent_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_inactivity_feedback_response "
            "ON inactivity_feedback_prompts(response_code, responded_at)"
        )


def get_inactive_feedback_user_ids(
    inactive_before: datetime,
    *,
    excluded_user_ids: set[int] | None = None,
    limit: int = 50,
) -> list[int]:
    """Возвращает давно неактивных пользователей, которых ещё не опрашивали."""
    cutoff = inactive_before
    if cutoff.tzinfo is not None:
        cutoff = cutoff.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    cutoff_text = cutoff.strftime(DB_DATETIME_FORMAT)
    excluded = sorted(excluded_user_ids or set())
    excluded_sql = (
        f" AND activity.user_id NOT IN ({','.join('?' for _ in excluded)})"
        if excluded
        else ""
    )
    amount = max(1, min(limit, 200))
    with get_connection() as conn:
        rows = conn.execute(
            """
            WITH known_activity AS (
                SELECT telegram_id AS user_id, created_at FROM users
                UNION ALL
                SELECT user_id, created_at FROM usage_events
            ), activity AS (
                SELECT user_id, MAX(created_at) AS last_activity
                FROM known_activity
                GROUP BY user_id
            )
            SELECT activity.user_id
            FROM activity
            LEFT JOIN inactivity_feedback_prompts AS prompt
              ON prompt.user_id = activity.user_id
            WHERE activity.last_activity <= ?
              AND prompt.user_id IS NULL
            """
            + excluded_sql
            + """
            ORDER BY activity.last_activity, activity.user_id
            LIMIT ?
            """,
            (cutoff_text, *excluded, amount),
        ).fetchall()
    return [int(row["user_id"]) for row in rows]


def claim_inactivity_feedback_prompt(user_id: int) -> bool:
    """Резервирует одноразовую отправку до обращения к Telegram API."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO inactivity_feedback_prompts
                (user_id, delivery_status)
            VALUES (?, 'pending')
            """,
            (user_id,),
        )
    return cursor.rowcount > 0


def mark_inactivity_feedback_delivery(user_id: int, status: str) -> None:
    """Фиксирует результат доставки, сохраняя защиту от повторной отправки."""
    if status not in {"sent", "failed"}:
        raise ValueError("Недопустимый статус доставки")
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE inactivity_feedback_prompts
            SET delivery_status = ?
            WHERE user_id = ? AND delivery_status = 'pending'
            """,
            (status, user_id),
        )


def save_inactivity_feedback_response(user_id: int, response_code: str) -> bool:
    """Сохраняет первый ответ на одноразовый опрос."""
    allowed = {"no_events", "confusing", "not_now", "other"}
    if response_code not in allowed:
        return False
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE inactivity_feedback_prompts
            SET response_code = ?, responded_at = datetime('now')
            WHERE user_id = ?
              AND delivery_status = 'sent'
              AND response_code IS NULL
            """,
            (response_code, user_id),
        )
    return cursor.rowcount > 0


def save_user_profile(
    telegram_id: int,
    profile: Profile,
    name: str,
    username: str | None = None,
    *,
    profile_embedding: bytes | None = None,
    profile_embedding_model: str | None = None,
    avoid_embedding: bytes | None = None,
    avoid_embedding_model: str | None = None,
) -> None:
    """Сохраняет профиль пользователя (UPSERT по telegram_id).

    name — first_name из Telegram: имя нужно для карточки участника.
    username хранится для выдачи контакта только после принятия запроса.
    """
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO users
                (telegram_id, name, username, interests, avoid, days,
                 budget_rub, group_size_min, group_size_max,
                 profile_embedding, profile_embedding_model,
                 avoid_embedding, avoid_embedding_model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            -- UPSERT: если строка с таким telegram_id уже есть,
            -- вместо ошибки обновляем её поля (excluded — то, что пытались
            -- вставить)
            ON CONFLICT(telegram_id) DO UPDATE SET
                name           = excluded.name,
                username       = excluded.username,
                interests      = excluded.interests,
                avoid          = excluded.avoid,
                days           = excluded.days,
                budget_rub     = excluded.budget_rub,
                group_size_min = excluded.group_size_min,
                group_size_max = excluded.group_size_max,
                profile_embedding       = excluded.profile_embedding,
                profile_embedding_model = excluded.profile_embedding_model,
                avoid_embedding         = excluded.avoid_embedding,
                avoid_embedding_model   = excluded.avoid_embedding_model
            """,
            (
                telegram_id,
                name,
                username,
                # ensure_ascii=False, чтобы в базе была кириллица,
                # а не \u04XX
                json.dumps(profile.interests, ensure_ascii=False),
                json.dumps(profile.avoid, ensure_ascii=False),
                json.dumps(profile.days or [], ensure_ascii=False),
                profile.budget_rub,
                profile.preferred_group_size_min,
                profile.preferred_group_size_max,
                profile_embedding,
                profile_embedding_model,
                avoid_embedding,
                avoid_embedding_model,
            ),
        )


def update_user_identity(
    telegram_id: int,
    name: str,
    username: str | None,
) -> None:
    """Освежает Telegram-имя у уже подтверждённого профиля."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE users
            SET name = ?, username = ?
            WHERE telegram_id = ?
            """,
            (name, username, telegram_id),
        )


def get_user_profile(telegram_id: int) -> Profile | None:
    """Возвращает сохранённый профиль или None, если его нет."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()

    if row is None:
        return None

    # обратное преобразование: строки JSON -> списки Python
    return Profile(
        interests=json.loads(row["interests"]),
        avoid=json.loads(row["avoid"]),
        days=json.loads(row["days"]) or None,
        budget_rub=row["budget_rub"],
        preferred_group_size_min=row["group_size_min"],
        preferred_group_size_max=row["group_size_max"],
    )


def get_user_profile_embeddings(
    telegram_id: int,
) -> tuple[bytes | None, str | None, bytes | None, str | None]:
    """Положительный и отрицательный векторы подтверждённого профиля."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT profile_embedding, profile_embedding_model,
                   avoid_embedding, avoid_embedding_model
            FROM users
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        ).fetchone()

    if row is None:
        return None, None, None, None
    return (
        row["profile_embedding"],
        row["profile_embedding_model"],
        row["avoid_embedding"],
        row["avoid_embedding_model"],
    )


def set_digest_schedule(telegram_id: int, weekday: int | None) -> bool:
    """Включает рассылку в день 0..6 или отключает её через None."""
    if weekday is not None and weekday not in range(7):
        raise ValueError("weekday должен быть от 0 до 6")

    with get_connection() as conn:
        if weekday is None:
            cursor = conn.execute(
                """
                UPDATE users
                SET digest_enabled = 0, digest_weekday = NULL
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            )
        else:
            cursor = conn.execute(
                """
                UPDATE users
                SET digest_enabled = 1,
                    last_digest_date = CASE
                        WHEN digest_weekday = ? THEN last_digest_date
                        ELSE NULL
                    END,
                    digest_weekday = ?
                WHERE telegram_id = ?
                """,
                (weekday, weekday, telegram_id),
            )
    return cursor.rowcount > 0


def get_digest_schedule(telegram_id: int) -> int | None:
    """День активной еженедельной рассылки или None."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT digest_weekday, digest_enabled
            FROM users
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        ).fetchone()
    if row is None or not row["digest_enabled"]:
        return None
    return row["digest_weekday"]


def get_due_digest_user_ids(weekday: int, sent_on: date) -> list[int]:
    """Пользователи, которым подборка ещё не отправлялась в эту дату."""
    if weekday not in range(7):
        raise ValueError("weekday должен быть от 0 до 6")
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT telegram_id
            FROM users
            WHERE digest_enabled = 1
              AND digest_weekday = ?
              AND (last_digest_date IS NULL OR last_digest_date != ?)
            ORDER BY telegram_id
            """,
            (weekday, sent_on.isoformat()),
        ).fetchall()
    return [row["telegram_id"] for row in rows]


def mark_digest_sent(telegram_id: int, sent_on: date) -> None:
    """Фиксирует доставку, чтобы рестарт не создал повторную рассылку."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE users
            SET last_digest_date = ?
            WHERE telegram_id = ?
            """,
            (sent_on.isoformat(), telegram_id),
        )


def was_admin_report_sent(admin_id: int, report_date: date) -> bool:
    """Проверяет, была ли администратору отправлена сводка в эту дату."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM admin_report_deliveries
            WHERE admin_id = ? AND report_date = ?
            """,
            (admin_id, report_date.isoformat()),
        ).fetchone()
    return row is not None


def mark_admin_report_sent(admin_id: int, report_date: date) -> None:
    """Фиксирует отправку ежедневной сводки и защищает её от дублей."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO admin_report_deliveries (admin_id, report_date)
            VALUES (?, ?)
            """,
            (admin_id, report_date.isoformat()),
        )


def get_group_matching_enabled(telegram_id: int) -> bool:
    """Дал ли пользователь согласие на постоянный групповой подбор."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT group_matching_enabled FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
    return bool(row["group_matching_enabled"]) if row is not None else False


def _group_from_row(row: sqlite3.Row) -> InterestGroup:
    return InterestGroup(
        id=row["id"],
        status=row["status"],
        topics=json.loads(row["topics"]),
        member_count=row["member_count"],
        minimum_members=GROUP_MIN_MEMBERS,
        maximum_members=GROUP_MAX_MEMBERS,
    )


def _group_row(
    conn: sqlite3.Connection,
    group_id: int,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT g.*, COUNT(gm.user_id) AS member_count
        FROM interest_groups g
        LEFT JOIN interest_group_members gm ON gm.group_id = g.id
        WHERE g.id = ?
        GROUP BY g.id
        """,
        (group_id,),
    ).fetchone()


def _recompute_group_topics(conn: sqlite3.Connection, group_id: int) -> list[str]:
    """Три самые частые темы участников, с исходным регистром первого автора."""
    rows = conn.execute(
        """
        SELECT u.interests
        FROM interest_group_members gm
        JOIN users u ON u.telegram_id = gm.user_id
        WHERE gm.group_id = ?
        ORDER BY gm.joined_at, gm.user_id
        """,
        (group_id,),
    ).fetchall()
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    for row in rows:
        try:
            interests = json.loads(row["interests"])
        except (TypeError, json.JSONDecodeError):
            continue
        for interest in interests:
            if not isinstance(interest, str) or not interest.strip():
                continue
            value = interest.strip()
            key = value.casefold()
            counts[key] += 1
            display.setdefault(key, value)

    shared = [key for key, amount in counts.items() if amount >= 2]
    source = shared or list(counts)
    ordered = sorted(source, key=lambda key: (-counts[key], display[key].casefold()))
    topics = [display[key] for key in ordered[:3]]
    conn.execute(
        "UPDATE interest_groups SET topics = ? WHERE id = ?",
        (json.dumps(topics, ensure_ascii=False), group_id),
    )
    return topics


def _effective_group_capacity(conn: sqlite3.Connection, group_id: int) -> int:
    rows = conn.execute(
        """
        SELECT u.group_size_max
        FROM interest_group_members gm
        JOIN users u ON u.telegram_id = gm.user_id
        WHERE gm.group_id = ?
          AND u.group_size_max IS NOT NULL
        """,
        (group_id,),
    ).fetchall()
    limits = [row["group_size_max"] for row in rows if row["group_size_max"] >= 3]
    return min([GROUP_MAX_MEMBERS, *limits])


def _leave_group_in_connection(
    conn: sqlite3.Connection,
    telegram_id: int,
) -> bool:
    row = conn.execute(
        "SELECT group_id FROM interest_group_members WHERE user_id = ?",
        (telegram_id,),
    ).fetchone()
    if row is None:
        return False
    group_id = row["group_id"]
    conn.execute(
        "DELETE FROM group_event_invite_responses "
        "WHERE user_id = ? AND invite_id IN "
        "(SELECT id FROM group_event_invites WHERE group_id = ?)",
        (telegram_id, group_id),
    )
    conn.execute(
        """
        UPDATE group_connection_requests
        SET status = 'rejected'
        WHERE group_id = ? AND status = 'pending'
          AND (from_user = ? OR to_user = ?)
        """,
        (group_id, telegram_id, telegram_id),
    )
    conn.execute(
        "DELETE FROM interest_group_members WHERE user_id = ?",
        (telegram_id,),
    )
    amount = conn.execute(
        "SELECT COUNT(*) AS amount FROM interest_group_members WHERE group_id = ?",
        (group_id,),
    ).fetchone()["amount"]
    if amount == 0:
        conn.execute(
            "DELETE FROM group_event_invite_responses "
            "WHERE invite_id IN (SELECT id FROM group_event_invites WHERE group_id = ?)",
            (group_id,),
        )
        conn.execute("DELETE FROM group_event_invites WHERE group_id = ?", (group_id,))
        conn.execute(
            "DELETE FROM interest_group_messages WHERE group_id = ?",
            (group_id,),
        )
        conn.execute(
            "DELETE FROM group_connection_requests WHERE group_id = ?",
            (group_id,),
        )
        conn.execute("DELETE FROM interest_groups WHERE id = ?", (group_id,))
    else:
        _recompute_group_topics(conn, group_id)
        if amount < GROUP_MIN_MEMBERS:
            conn.execute(
                """
                UPDATE interest_groups
                SET status = 'forming', activated_at = NULL
                WHERE id = ?
                """,
                (group_id,),
            )
    return True


def set_group_matching_enabled(telegram_id: int, enabled: bool) -> bool:
    """Меняет согласие; отключение одновременно удаляет членство."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE users
            SET group_matching_enabled = ?
            WHERE telegram_id = ?
            """,
            (int(enabled), telegram_id),
        )
        if cursor.rowcount and not enabled:
            _leave_group_in_connection(conn, telegram_id)
    return cursor.rowcount > 0


def leave_interest_group(telegram_id: int) -> bool:
    """Добровольно выходит из постоянной группы, не удаляя профиль."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return _leave_group_in_connection(conn, telegram_id)


def assign_user_to_interest_group(telegram_id: int) -> GroupAssignment | None:
    """Атомарно вступает в лучшую совместимую группу или создаёт новую."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute(
            """
            SELECT telegram_id, interests, group_size_min, group_size_max,
                   group_matching_enabled
            FROM users
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        ).fetchone()
        if user is None or not user["group_matching_enabled"]:
            return None

        existing = conn.execute(
            "SELECT group_id FROM interest_group_members WHERE user_id = ?",
            (telegram_id,),
        ).fetchone()
        if existing is not None:
            _recompute_group_topics(conn, existing["group_id"])
            row = _group_row(conn, existing["group_id"])
            return (
                GroupAssignment(group=_group_from_row(row), joined=False)
                if row is not None
                else None
            )

        try:
            raw_interests = json.loads(user["interests"])
        except (TypeError, json.JSONDecodeError):
            raw_interests = []
        interests = {
            value.strip().casefold()
            for value in raw_interests
            if isinstance(value, str) and value.strip()
        }
        if not interests:
            return None

        rows = conn.execute(
            """
            SELECT g.*, COUNT(gm.user_id) AS member_count
            FROM interest_groups g
            LEFT JOIN interest_group_members gm ON gm.group_id = g.id
            GROUP BY g.id
            HAVING member_count < ?
            ORDER BY CASE g.status WHEN 'forming' THEN 0 ELSE 1 END,
                     member_count DESC, g.created_at, g.id
            """,
            (GROUP_MAX_MEMBERS,),
        ).fetchall()

        candidates: list[tuple[int, int, int, int]] = []
        for row in rows:
            try:
                topics = {
                    value.strip().casefold()
                    for value in json.loads(row["topics"])
                    if isinstance(value, str) and value.strip()
                }
            except (TypeError, json.JSONDecodeError):
                continue
            overlap = len(interests & topics)
            if overlap == 0:
                continue
            target_size = row["member_count"] + 1
            user_max = user["group_size_max"]
            if user_max is not None and target_size > user_max:
                continue
            if target_size > _effective_group_capacity(conn, row["id"]):
                continue
            blocked = conn.execute(
                """
                SELECT 1
                FROM interest_group_members gm
                JOIN blocks b
                  ON (b.blocker = ? AND b.blocked = gm.user_id)
                  OR (b.blocker = gm.user_id AND b.blocked = ?)
                WHERE gm.group_id = ?
                LIMIT 1
                """,
                (telegram_id, telegram_id, row["id"]),
            ).fetchone()
            if blocked is not None:
                continue
            forming_bonus = 1 if row["status"] == "forming" else 0
            candidates.append(
                (overlap, forming_bonus, row["member_count"], row["id"])
            )

        if candidates:
            group_id = max(candidates, key=lambda item: item[:3])[3]
        else:
            topics = [
                value.strip()
                for value in raw_interests
                if isinstance(value, str) and value.strip()
            ][:3]
            cursor = conn.execute(
                "INSERT INTO interest_groups (topics) VALUES (?)",
                (json.dumps(topics, ensure_ascii=False),),
            )
            group_id = cursor.lastrowid

        conn.execute(
            "INSERT INTO interest_group_members (group_id, user_id) VALUES (?, ?)",
            (group_id, telegram_id),
        )
        _recompute_group_topics(conn, group_id)
        row = _group_row(conn, group_id)
        if row is None:
            return None

        newly_activated = (
            row["status"] == "forming"
            and row["member_count"] >= GROUP_MIN_MEMBERS
        )
        if newly_activated:
            conn.execute(
                """
                UPDATE interest_groups
                SET status = 'active', activated_at = datetime('now')
                WHERE id = ?
                """,
                (group_id,),
            )
            row = _group_row(conn, group_id)

        group = _group_from_row(row)
        notify_user_ids: list[int] = []
        if newly_activated or group.status == "active":
            notify_user_ids = [
                item["user_id"]
                for item in conn.execute(
                    """
                    SELECT user_id
                    FROM interest_group_members
                    WHERE group_id = ?
                    ORDER BY joined_at, user_id
                    """,
                    (group_id,),
                ).fetchall()
            ]
        return GroupAssignment(
            group=group,
            newly_activated=newly_activated,
            joined=True,
            notify_user_ids=notify_user_ids,
        )


def get_user_interest_group(
    telegram_id: int,
    profile: Profile | None = None,
) -> InterestGroupView | None:
    """Постоянная группа пользователя и её участники."""
    profile = profile or get_user_profile(telegram_id)
    if profile is None:
        return None
    with get_connection() as conn:
        membership = conn.execute(
            "SELECT group_id FROM interest_group_members WHERE user_id = ?",
            (telegram_id,),
        ).fetchone()
        if membership is None:
            return None
        group_row = _group_row(conn, membership["group_id"])
        if group_row is None:
            return None
        rows = conn.execute(
            """
            SELECT u.telegram_id, u.name, u.interests,
                   u.group_size_min, u.group_size_max
            FROM interest_group_members gm
            JOIN users u ON u.telegram_id = gm.user_id
            WHERE gm.group_id = ?
            ORDER BY gm.joined_at, gm.user_id
            """,
            (membership["group_id"],),
        ).fetchall()

    mine = {interest.casefold() for interest in profile.interests}
    members: list[Companion] = []
    for row in rows:
        try:
            their_interests = json.loads(row["interests"])
        except (TypeError, json.JSONDecodeError):
            their_interests = []
        members.append(
            Companion(
                user_id=row["telegram_id"],
                name=row["name"].strip() or UNKNOWN_NAME,
                common_interests=[
                    interest
                    for interest in their_interests
                    if isinstance(interest, str) and interest.casefold() in mine
                ],
                group_size_min=row["group_size_min"],
                group_size_max=row["group_size_max"],
            )
        )
    return InterestGroupView(group=_group_from_row(group_row), members=members)


def _row_to_event(row: sqlite3.Row) -> Event:
    """Строка таблицы events -> модель Event."""
    return Event(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        city=row["city"],
        address=row["address"],
        date=datetime.strptime(row["date"], DB_DATETIME_FORMAT),
        end_date=(
            datetime.strptime(row["end_date"], DB_DATETIME_FORMAT)
            if row["end_date"]
            else None
        ),
        price_min=row["price_min"],
        price_max=row["price_max"],
        price_text=row["price_text"],
        is_free=(bool(row["is_free"]) if row["is_free"] is not None else None),
        tags=json.loads(row["tags"]),
        venue=row["venue"],
        source_id=row["source_id"],
        external_id=row["external_id"],
        source_url=row["source_url"],
        fetched_at=row["fetched_at"],
        status=row["status"],
    )


def get_interest_group_member_ids(group_id: int) -> list[int]:
    """Telegram ID участников группы для служебных уведомлений."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT user_id
            FROM interest_group_members
            WHERE group_id = ?
            ORDER BY joined_at, user_id
            """,
            (group_id,),
        ).fetchall()
    return [row["user_id"] for row in rows]


def _active_group_id(conn: sqlite3.Connection, user_id: int) -> int | None:
    row = conn.execute(
        """
        SELECT gm.group_id
        FROM interest_group_members gm
        JOIN interest_groups g ON g.id = gm.group_id
        WHERE gm.user_id = ? AND g.status = 'active'
        """,
        (user_id,),
    ).fetchone()
    return row["group_id"] if row is not None else None


_GROUP_REQUEST_QUERY = """
    SELECT r.id, r.group_id, r.from_user, r.to_user, r.status,
           g.topics,
           sender.name AS from_name,
           sender.username AS from_username,
           sender.interests AS from_interests,
           recipient.name AS to_name,
           recipient.username AS to_username,
           recipient.interests AS to_interests
    FROM group_connection_requests r
    JOIN interest_groups g ON g.id = r.group_id
    JOIN users sender ON sender.telegram_id = r.from_user
    JOIN users recipient ON recipient.telegram_id = r.to_user
    WHERE r.id = ?
"""


def _row_to_group_connection_request(row: sqlite3.Row) -> GroupConnectionRequest:
    try:
        sender_interests = {
            value.casefold()
            for value in json.loads(row["from_interests"])
            if isinstance(value, str)
        }
        recipient_interests = json.loads(row["to_interests"])
    except (TypeError, json.JSONDecodeError):
        sender_interests, recipient_interests = set(), []
    try:
        topics = json.loads(row["topics"])
    except (TypeError, json.JSONDecodeError):
        topics = []
    return GroupConnectionRequest(
        id=row["id"],
        group_id=row["group_id"],
        group_title=" · ".join(topics) or "Новые знакомства",
        from_user=row["from_user"],
        to_user=row["to_user"],
        from_name=row["from_name"].strip() or UNKNOWN_NAME,
        to_name=row["to_name"].strip() or UNKNOWN_NAME,
        from_username=row["from_username"],
        to_username=row["to_username"],
        common_interests=[
            value
            for value in recipient_interests
            if isinstance(value, str) and value.casefold() in sender_interests
        ],
        status=row["status"],
    )


def _get_group_connection_request(
    conn: sqlite3.Connection,
    request_id: int,
) -> GroupConnectionRequest | None:
    row = conn.execute(_GROUP_REQUEST_QUERY, (request_id,)).fetchone()
    return _row_to_group_connection_request(row) if row is not None else None


def get_group_connection_state(
    group_id: int,
    viewer_id: int,
    member_id: int,
) -> tuple[str, GroupConnectionRequest | None]:
    """Состояние знакомства: available/pending_sent/pending_received/connected."""
    if viewer_id == member_id:
        return "self", None
    with get_connection() as conn:
        eligible = conn.execute(
            """
            SELECT COUNT(*) AS amount
            FROM interest_group_members
            WHERE group_id = ? AND user_id IN (?, ?)
            """,
            (group_id, viewer_id, member_id),
        ).fetchone()["amount"]
        if eligible != 2:
            return "unavailable", None
        blocked = conn.execute(
            """
            SELECT 1 FROM blocks
            WHERE (blocker = ? AND blocked = ?)
               OR (blocker = ? AND blocked = ?)
            """,
            (viewer_id, member_id, member_id, viewer_id),
        ).fetchone()
        if blocked is not None:
            return "unavailable", None
        row = conn.execute(
            """
            SELECT id, from_user, to_user, status
            FROM group_connection_requests
            WHERE group_id = ?
              AND ((from_user = ? AND to_user = ?)
                OR (from_user = ? AND to_user = ?))
            ORDER BY CASE status
                       WHEN 'accepted' THEN 0
                       WHEN 'pending' THEN 1
                       ELSE 2
                     END,
                     id DESC
            LIMIT 1
            """,
            (group_id, viewer_id, member_id, member_id, viewer_id),
        ).fetchone()
        if row is None:
            return "available", None
        request = _get_group_connection_request(conn, row["id"])
        if row["status"] == "rejected":
            return "rejected", request
        if row["status"] == "accepted":
            return "connected", request
        if row["from_user"] == viewer_id:
            return "pending_sent", request
        return "pending_received", request


def create_group_connection_request(
    group_id: int,
    from_user: int,
    to_user: int,
) -> tuple[str, GroupConnectionRequest | None]:
    """Создаёт запрос между двумя участниками активной постоянной группы."""
    if from_user == to_user:
        return "unavailable", None
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if _active_group_id(conn, from_user) != group_id:
            return "unavailable", None
        eligible = conn.execute(
            "SELECT 1 FROM interest_group_members WHERE group_id = ? AND user_id = ?",
            (group_id, to_user),
        ).fetchone()
        if eligible is None:
            return "unavailable", None
        blocked = conn.execute(
            """
            SELECT 1 FROM blocks
            WHERE (blocker = ? AND blocked = ?)
               OR (blocker = ? AND blocked = ?)
            """,
            (from_user, to_user, to_user, from_user),
        ).fetchone()
        if blocked is not None:
            return "blocked", None

        existing = conn.execute(
            """
            SELECT id, from_user, status
            FROM group_connection_requests
            WHERE group_id = ?
              AND ((from_user = ? AND to_user = ?)
                OR (from_user = ? AND to_user = ?))
            ORDER BY id DESC
            LIMIT 1
            """,
            (group_id, from_user, to_user, to_user, from_user),
        ).fetchone()
        if existing is not None:
            request = _get_group_connection_request(conn, existing["id"])
            if existing["status"] == "accepted":
                return "connected", request
            if existing["status"] == "pending":
                return (
                    "incoming" if existing["from_user"] == to_user else "already",
                    request,
                )
            return "rejected", request

        sent_last_day = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM requests
               WHERE from_user = ? AND created_at >= datetime('now', '-24 hours'))
              +
              (SELECT COUNT(*) FROM group_connection_requests
               WHERE from_user = ? AND created_at >= datetime('now', '-24 hours'))
              AS amount
            """,
            (from_user, from_user),
        ).fetchone()["amount"]
        if sent_last_day >= DAILY_REQUEST_LIMIT:
            return "limit", None

        cursor = conn.execute(
            """
            INSERT INTO group_connection_requests
                (group_id, from_user, to_user, status)
            VALUES (?, ?, ?, 'pending')
            """,
            (group_id, from_user, to_user),
        )
        return "created", _get_group_connection_request(conn, cursor.lastrowid)


def accept_group_connection_request(
    request_id: int,
    to_user: int,
) -> tuple[str, GroupConnectionRequest | None]:
    """Принимает входящий запрос, если оба пользователя всё ещё в группе."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id, group_id, from_user, to_user, status
            FROM group_connection_requests
            WHERE id = ?
            """,
            (request_id,),
        ).fetchone()
        if row is None or row["to_user"] != to_user:
            return "unavailable", None
        if row["status"] != "pending":
            return "already", _get_group_connection_request(conn, request_id)
        if (
            _active_group_id(conn, row["from_user"]) != row["group_id"]
            or _active_group_id(conn, to_user) != row["group_id"]
        ):
            return "unavailable", None
        blocked = conn.execute(
            """
            SELECT 1 FROM blocks
            WHERE (blocker = ? AND blocked = ?)
               OR (blocker = ? AND blocked = ?)
            """,
            (row["from_user"], to_user, to_user, row["from_user"]),
        ).fetchone()
        if blocked is not None:
            conn.execute(
                "UPDATE group_connection_requests SET status = 'rejected' WHERE id = ?",
                (request_id,),
            )
            return "unavailable", None
        conn.execute(
            "UPDATE group_connection_requests SET status = 'accepted' WHERE id = ?",
            (request_id,),
        )
        return "accepted", _get_group_connection_request(conn, request_id)


def reject_group_connection_request(request_id: int, to_user: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE group_connection_requests
            SET status = 'rejected'
            WHERE id = ? AND to_user = ? AND status = 'pending'
            """,
            (request_id, to_user),
        )
        return cursor.rowcount > 0


def get_group_messages(user_id: int, *, limit: int = 50) -> list[GroupMessage]:
    """Последние сообщения после момента вступления текущего участника."""
    limit = min(max(limit, 1), 100)
    with get_connection() as conn:
        membership = conn.execute(
            """
            SELECT gm.group_id, gm.joined_at
            FROM interest_group_members gm
            JOIN interest_groups g ON g.id = gm.group_id
            WHERE gm.user_id = ? AND g.status = 'active'
            """,
            (user_id,),
        ).fetchone()
        if membership is None:
            return []
        rows = conn.execute(
            """
            SELECT m.id, m.group_id, m.user_id, m.message, m.created_at,
                   u.name AS author_name
            FROM interest_group_messages m
            JOIN users u ON u.telegram_id = m.user_id
            WHERE m.group_id = ? AND m.created_at >= ?
            ORDER BY m.created_at DESC, m.id DESC
            LIMIT ?
            """,
            (membership["group_id"], membership["joined_at"], limit),
        ).fetchall()
    return [
        GroupMessage(
            id=row["id"],
            group_id=row["group_id"],
            user_id=row["user_id"],
            author_name=row["author_name"].strip() or UNKNOWN_NAME,
            text=row["message"],
            created_at=row["created_at"],
        )
        for row in reversed(rows)
    ]


def create_group_message(
    user_id: int,
    message: str,
) -> tuple[str, GroupMessage | None]:
    """Пишет в активную группу с небольшим антиспам-лимитом."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        group_id = _active_group_id(conn, user_id)
        if group_id is None:
            return "unavailable", None
        recent = conn.execute(
            """
            SELECT COUNT(*) AS amount
            FROM interest_group_messages
            WHERE user_id = ? AND created_at >= datetime('now', '-1 minute')
            """,
            (user_id,),
        ).fetchone()["amount"]
        if recent >= 6:
            return "limit", None
        cursor = conn.execute(
            """
            INSERT INTO interest_group_messages (group_id, user_id, message)
            VALUES (?, ?, ?)
            """,
            (group_id, user_id, message),
        )
        row = conn.execute(
            """
            SELECT m.id, m.group_id, m.user_id, m.message, m.created_at,
                   u.name AS author_name
            FROM interest_group_messages m
            JOIN users u ON u.telegram_id = m.user_id
            WHERE m.id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
        return "created", GroupMessage(
            id=row["id"],
            group_id=row["group_id"],
            user_id=row["user_id"],
            author_name=row["author_name"].strip() or UNKNOWN_NAME,
            text=row["message"],
            created_at=row["created_at"],
        )


def _group_invite_from_row(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    viewer_id: int,
) -> GroupEventInvite:
    responses = conn.execute(
        """
        SELECT r.user_id, r.status, u.name
        FROM group_event_invite_responses r
        JOIN users u ON u.telegram_id = r.user_id
        WHERE r.invite_id = ?
        ORDER BY r.updated_at, r.user_id
        """,
        (row["invite_id"],),
    ).fetchall()
    my_response = next(
        (item["status"] for item in responses if item["user_id"] == viewer_id),
        None,
    )
    return GroupEventInvite(
        id=row["invite_id"],
        group_id=row["group_id"],
        event=_row_to_event(row),
        created_by=row["created_by"],
        creator_name=row["creator_name"].strip() or UNKNOWN_NAME,
        created_at=row["invite_created_at"],
        my_response=my_response,
        going_names=[
            item["name"].strip() or UNKNOWN_NAME
            for item in responses
            if item["status"] == "going"
        ],
        declined_count=sum(item["status"] == "declined" for item in responses),
    )


_GROUP_INVITE_QUERY = """
    SELECT e.*, gi.id AS invite_id, gi.group_id, gi.created_by,
           gi.created_at AS invite_created_at,
           creator.name AS creator_name
    FROM group_event_invites gi
    JOIN events e ON e.id = gi.event_id
    JOIN users creator ON creator.telegram_id = gi.created_by
"""


def get_group_event_invites(
    user_id: int,
    *,
    limit: int = 10,
) -> list[GroupEventInvite]:
    limit = min(max(limit, 1), 25)
    with get_connection() as conn:
        group_id = _active_group_id(conn, user_id)
        if group_id is None:
            return []
        rows = conn.execute(
            _GROUP_INVITE_QUERY
            + """
              WHERE gi.group_id = ?
                AND e.status = 'active'
                AND COALESCE(e.end_date, e.date) > datetime('now', 'localtime')
              ORDER BY gi.created_at DESC, gi.id DESC
              LIMIT ?
            """,
            (group_id, limit),
        ).fetchall()
        return [_group_invite_from_row(conn, row, user_id) for row in rows]


def create_group_event_invite(
    user_id: int,
    event_id: int,
) -> tuple[str, GroupEventInvite | None]:
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        group_id = _active_group_id(conn, user_id)
        if group_id is None:
            return "unavailable", None
        event = conn.execute(
            """
            SELECT 1 FROM events
            WHERE id = ? AND status = 'active'
              AND COALESCE(end_date, date) > datetime('now', 'localtime')
            """,
            (event_id,),
        ).fetchone()
        if event is None:
            return "event_unavailable", None
        existing = conn.execute(
            "SELECT id FROM group_event_invites WHERE group_id = ? AND event_id = ?",
            (group_id, event_id),
        ).fetchone()
        if existing is None:
            cursor = conn.execute(
                """
                INSERT INTO group_event_invites (group_id, event_id, created_by)
                VALUES (?, ?, ?)
                """,
                (group_id, event_id, user_id),
            )
            invite_id = cursor.lastrowid
            result = "created"
        else:
            invite_id = existing["id"]
            result = "already"
        conn.execute(
            """
            INSERT INTO group_event_invite_responses (invite_id, user_id, status)
            VALUES (?, ?, 'going')
            ON CONFLICT(invite_id, user_id) DO UPDATE SET
                status = 'going', updated_at = datetime('now')
            """,
            (invite_id, user_id),
        )
        row = conn.execute(
            _GROUP_INVITE_QUERY + " WHERE gi.id = ?",
            (invite_id,),
        ).fetchone()
        return result, _group_invite_from_row(conn, row, user_id)


def respond_group_event_invite(
    user_id: int,
    invite_id: int,
    response: str,
) -> tuple[str, GroupEventInvite | None]:
    if response not in {"going", "declined"}:
        return "unavailable", None
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            _GROUP_INVITE_QUERY
            + """
              JOIN interest_group_members gm ON gm.group_id = gi.group_id
              JOIN interest_groups g ON g.id = gi.group_id
              WHERE gi.id = ? AND gm.user_id = ? AND g.status = 'active'
            """,
            (invite_id, user_id),
        ).fetchone()
        if row is None:
            return "unavailable", None
        conn.execute(
            """
            INSERT INTO group_event_invite_responses (invite_id, user_id, status)
            VALUES (?, ?, ?)
            ON CONFLICT(invite_id, user_id) DO UPDATE SET
                status = excluded.status, updated_at = datetime('now')
            """,
            (invite_id, user_id, response),
        )
        return "updated", _group_invite_from_row(conn, row, user_id)


def _score(event: Event, interests: set[str], avoid: set[str]) -> int:
    """Насколько событие подходит: совпадения тегов минус нежелательные."""
    tags = {tag.lower() for tag in event.tags}
    # & — пересечение множеств, то есть общие теги
    return len(interests & tags) - len(avoid & tags)


def _decode_embedding(blob: bytes | None) -> np.ndarray | None:
    if not blob:
        return None
    try:
        vector = vector_from_blob(blob)
    except ValueError:
        return None
    if vector.ndim != 1 or vector.size == 0 or not np.all(np.isfinite(vector)):
        return None
    return vector


def _rank_by_tags(events: list[Event], profile: Profile) -> list[Event]:
    interests = {tag.lower() for tag in profile.interests}
    avoid = {tag.lower() for tag in profile.avoid}
    return sorted(
        events,
        key=lambda event: (-_score(event, interests, avoid), event.date),
    )


def _preferred_weekday_numbers(days: list[str] | None) -> set[int]:
    if not days:
        return set()
    return {
        PROFILE_WEEKDAYS[day.strip().lower()]
        for day in days
        if day.strip().lower() in PROFILE_WEEKDAYS
    }


def _deduplicate_candidates(
    candidates: list[tuple[Event, sqlite3.Row]],
) -> list[tuple[Event, sqlite3.Row]]:
    """Схлопывает один анонс, попавший сразу из нескольких каталогов."""
    selected: dict[tuple[str, datetime], tuple[Event, sqlite3.Row]] = {}

    def completeness(event: Event) -> tuple[int, int]:
        filled = sum(
            bool(value)
            for value in (
                event.description,
                event.venue,
                event.address,
                event.source_url,
                event.price_text,
                event.tags,
            )
        )
        return filled, len(event.description)

    for candidate in candidates:
        event = candidate[0]
        normalized_title = " ".join(event.title.casefold().split())
        key = (normalized_title, event.date)
        current = selected.get(key)
        if current is None or completeness(event) > completeness(current[0]):
            selected[key] = candidate
    return list(selected.values())


def _rank_by_embeddings(
    candidates: list[tuple[Event, sqlite3.Row]],
    profile_embedding: bytes | None,
    profile_embedding_model: str | None,
    avoid_embedding: bytes | None,
    avoid_embedding_model: str | None,
) -> list[Event] | None:
    """Возвращает semantic ranking или None, если векторы неприменимы."""
    profile_vector = _decode_embedding(profile_embedding)
    if profile_vector is None or not profile_embedding_model:
        return None

    events: list[Event] = []
    vectors: list[np.ndarray] = []
    for event, row in candidates:
        if row["embedding_model"] != profile_embedding_model:
            continue
        event_vector = _decode_embedding(row["embedding"])
        if event_vector is None or event_vector.size != profile_vector.size:
            continue
        events.append(event)
        vectors.append(event_vector)

    if not vectors:
        return None

    matrix = np.vstack(vectors)
    profile_norm = np.linalg.norm(profile_vector)
    event_norms = np.linalg.norm(matrix, axis=1)
    usable = np.isfinite(event_norms) & (event_norms > 0)
    if not np.isfinite(profile_norm) or profile_norm == 0 or not np.any(usable):
        return None

    scores = np.full(len(events), -np.inf, dtype=np.float32)
    scores[usable] = (
        matrix[usable] @ profile_vector / (event_norms[usable] * profile_norm)
    )

    negative_vector = _decode_embedding(avoid_embedding)
    if (
        negative_vector is not None
        and avoid_embedding_model == profile_embedding_model
        and negative_vector.size == profile_vector.size
    ):
        negative_norm = np.linalg.norm(negative_vector)
        if negative_norm > 0:
            negative_similarity = np.zeros(len(events), dtype=np.float32)
            negative_similarity[usable] = (
                matrix[usable] @ negative_vector
                / (event_norms[usable] * negative_norm)
            )
            scores -= AVOID_SIMILARITY_WEIGHT * negative_similarity

    order = sorted(
        np.flatnonzero(usable),
        key=lambda index: (-float(scores[index]), events[index].date),
    )
    return [events[index] for index in order]


def find_events(
    profile: Profile,
    *,
    profile_embedding: bytes | None = None,
    profile_embedding_model: str | None = None,
    avoid_embedding: bytes | None = None,
    avoid_embedding_model: str | None = None,
    limit: int = MAX_EVENTS,
) -> list[Event]:
    """Жёстко фильтрует в SQL, затем ранжирует векторами или тегами."""
    # Запрос собираем по частям: условие по бюджету добавляется,
    # только если бюджет вообще указан в профиле
    query = [
        "SELECT * FROM events",
        # active включает и будущие, и уже начавшиеся, но ещё не завершённые
        "WHERE city = 'Москва'"
        " AND status = 'active'"
        " AND COALESCE(end_date, date) > datetime('now', 'localtime')",
    ]
    params: list[object] = []

    if profile.budget_rub is not None:
        # Бесплатность берём из отдельного флага источника. Событие с
        # неизвестной произвольной ценой не выдаём как якобы бесплатное.
        query.append(
            "AND (is_free = 1 OR (price_min IS NOT NULL AND price_min <= ?"
            " AND (price_max <= ? OR price_max IS NULL OR price_max = 0)))"
        )
        # значения подставляются через ?, а не форматированием строки —
        # так не бывает SQL-инъекций
        params.extend([profile.budget_rub, profile.budget_rub])

    with get_connection() as conn:
        rows = conn.execute(" ".join(query), params).fetchall()

    preferred_weekdays = _preferred_weekday_numbers(profile.days)
    candidates: list[tuple[Event, sqlite3.Row]] = []
    for row in rows:
        try:
            event = _row_to_event(row)
            if preferred_weekdays and event.date.weekday() not in preferred_weekdays:
                continue
            candidates.append((event, row))
        except (TypeError, ValueError, json.JSONDecodeError):
            # Одна повреждённая строка не должна ломать /find для всех
            # пользователей. Импорт валидирует новые даты отдельно.
            logger.exception("Пропущено повреждённое событие id=%s", row["id"])
    candidates = _deduplicate_candidates(candidates)
    semantic = _rank_by_embeddings(
        candidates,
        profile_embedding,
        profile_embedding_model,
        avoid_embedding,
        avoid_embedding_model,
    )
    if semantic is not None:
        return semantic[:limit]

    # Нет вектора профиля, модель не совпала или каталог ещё не проиндексирован:
    # используем прежний алгоритм и не оставляем пользователя без результата.
    fallback = _rank_by_tags([event for event, _ in candidates], profile)
    return fallback[:limit]


def get_event(event_id: int) -> Event | None:
    """Одно мероприятие по id для действий из Mini App."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
    if row is None:
        return None
    try:
        return _row_to_event(row)
    except (TypeError, ValueError, json.JSONDecodeError):
        logger.exception("Повреждено событие id=%s", event_id)
        return None


def _format_source_link(event: Event) -> str:
    """Прямая атрибуция источника для любой карточки события."""
    if not event.source_url:
        return ""
    source_url = escape(event.source_url, quote=True)
    brand = source_brand(event.source_id)
    source_name = escape(brand.name)
    source_mark = escape(brand.mark)
    return (
        f"<b>{source_mark} {source_name}</b> · "
        f'<a href="{source_url}">открыть источник ↗</a>'
    )


def format_event_card(event: Event, index: int) -> str:
    """Карточка мероприятия для отправки в Telegram (parse_mode=HTML)."""
    # weekday(): 0 — понедельник, отсюда порядок в WEEKDAYS_RU
    weekday = WEEKDAYS_RU[event.date.weekday()]
    when = f"{event.date.strftime('%d.%m.%Y %H:%M')} ({weekday})"
    tags = ", ".join(event.tags) if event.tags else "—"
    place = ", ".join(part for part in (event.venue, event.address) if part)
    place_line = f"📍 {escape(place)}\n" if place else ""
    source_link = _format_source_link(event)
    source = f"\n{source_link}" if source_link else ""

    # escape() экранирует < > & в тексте события: иначе Telegram примет
    # их за разметку и не отправит сообщение
    return (
        f"🎯 <b>{index}. {escape(event.title)}</b>\n"
        f"{place_line}"
        f"🏙️ {escape(event.city)}\n"
        f"📅 {when}\n"
        f"💰 {escape(event.get_price_display())}\n"
        f"🏷️ {escape(tags)}\n\n"
        f"📝 {escape(event.description)}"
        f"{source}"
    )


def upsert_source_events(
    events: list[Event],
    *,
    now: datetime | None = None,
) -> tuple[int, int, int]:
    """Атомарно сохраняет импорт: (добавлено, обновлено, ошибок).

    Сбой отдельной строки откатывается до savepoint и не мешает остальным.
    Транзакция начинается только после полной загрузки ответа источника.
    """
    current_datetime = now or datetime.now(ZoneInfo("Europe/Moscow")).replace(
        tzinfo=None
    )
    current = current_datetime.strftime(DB_DATETIME_FORMAT)
    added = 0
    updated = 0
    errors = 0

    with get_connection() as conn:
        # События с уже прошедшей выбранной датой больше не участвуют в /find.
        conn.execute(
            """
            UPDATE events
            SET status = 'past'
            WHERE source_id IS NOT NULL
              AND status = 'active'
              AND COALESCE(end_date, date) <= ?
            """,
            (current,),
        )
        existing = {
            (row["source_id"], row["external_id"])
            for row in conn.execute(
                """
                SELECT source_id, external_id
                FROM events
                WHERE source_id IS NOT NULL AND external_id IS NOT NULL
                """
            )
        }

        for event in events:
            key = (event.source_id, event.external_id)
            conn.execute("SAVEPOINT import_one_event")
            try:
                if not all(key):
                    raise ValueError("у нормализованного события нет source id")
                conn.execute(
                    """
                    INSERT INTO events
                        (title, description, city, address, date, end_date,
                         price_min, price_max, price_text, is_free, tags, venue,
                         source_id, external_id, source_url, fetched_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id, external_id) DO UPDATE SET
                        title       = excluded.title,
                        description = excluded.description,
                        city        = excluded.city,
                        address     = excluded.address,
                        date        = excluded.date,
                        end_date    = excluded.end_date,
                        price_min   = excluded.price_min,
                        price_max   = excluded.price_max,
                        price_text  = excluded.price_text,
                        is_free     = excluded.is_free,
                        tags        = excluded.tags,
                        venue       = excluded.venue,
                        source_url  = excluded.source_url,
                        fetched_at  = excluded.fetched_at,
                        status      = excluded.status
                    """,
                    (
                        event.title,
                        event.description,
                        event.city,
                        event.address,
                        event.date.strftime(DB_DATETIME_FORMAT),
                        (
                            event.end_date.strftime(DB_DATETIME_FORMAT)
                            if event.end_date
                            else None
                        ),
                        event.price_min,
                        event.price_max,
                        event.price_text,
                        int(event.is_free) if event.is_free is not None else None,
                        json.dumps(event.tags, ensure_ascii=False),
                        event.venue,
                        event.source_id,
                        event.external_id,
                        event.source_url,
                        event.fetched_at,
                        event.status,
                    ),
                )
            except Exception:
                conn.execute("ROLLBACK TO SAVEPOINT import_one_event")
                conn.execute("RELEASE SAVEPOINT import_one_event")
                errors += 1
                continue
            conn.execute("RELEASE SAVEPOINT import_one_event")

            if key in existing:
                updated += 1
            else:
                added += 1
                existing.add(key)

    return added, updated, errors


def delete_legacy_events() -> int:
    """Удаляет старые события без источника и зависящие от них записи."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS amount FROM events WHERE source_id IS NULL"
        ).fetchone()
        amount = row["amount"]
        conn.execute(
            """
            DELETE FROM intents
            WHERE event_id IN (SELECT id FROM events WHERE source_id IS NULL)
            """
        )
        conn.execute(
            """
            DELETE FROM requests
            WHERE event_id IN (SELECT id FROM events WHERE source_id IS NULL)
            """
        )
        conn.execute("DELETE FROM events WHERE source_id IS NULL")
    return amount


def save_intent(user_id: int, event_id: int, status: str) -> None:
    """Сохраняет отметку по событию (UPSERT по user_id + event_id).

    Согласие на видимость не трогаем: новая строка получает visible = 0,
    существующая сохраняет своё значение. Исключение — not_going,
    при нём видимость сбрасывается.
    """
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO intents (user_id, event_id, status, visible, updated_at)
            -- новая отметка всегда создаётся с visible = 0
            VALUES (?, ?, ?, 0, datetime('now'))
            -- строка на эту пару уже есть -> обновляем её
            ON CONFLICT(user_id, event_id) DO UPDATE SET
                status     = excluded.status,
                -- excluded.visible здесь всегда 0, поэтому не берём его:
                -- при not_going ставим 0, иначе оставляем то согласие,
                -- которое человек дал раньше (intents.visible)
                visible    = CASE
                                 WHEN excluded.status = 'not_going' THEN 0
                                 ELSE intents.visible
                             END,
                updated_at = excluded.updated_at
            """,
            (user_id, event_id, status),
        )


def set_intent_visibility(user_id: int, event_id: int, visible: bool) -> bool:
    """Меняет видимость у существующей отметки.

    Возвращает False, если отметки по этому событию ещё нет.
    """
    with get_connection() as conn:
        # именно UPDATE, а не UPSERT: согласие не должно создавать
        # отметку по событию, которое человек не отмечал
        cursor = conn.execute(
            """
            UPDATE intents
            SET visible = ?, updated_at = datetime('now')
            WHERE user_id = ? AND event_id = ?
            """,
            # в SQLite нет типа bool, пишем 1/0
            (int(visible), user_id, event_id),
        )
        # rowcount — сколько строк изменил UPDATE; 0 значит «отметки нет»
        return cursor.rowcount > 0


# Общая часть запросов об отметках: JOIN подтягивает к отметке данные
# события, поэтому одним запросом получаем и статус, и название с датой
_INTENTS_QUERY = """
    SELECT e.*, i.status AS intent_status,
           i.visible AS intent_visible,
           i.visibility_asked AS intent_visibility_asked
    FROM intents i
    JOIN events e ON e.id = i.event_id
    WHERE i.user_id = ?
"""


def _row_to_intent(row: sqlite3.Row) -> UserIntent:
    """Строка запроса выше -> модель UserIntent."""
    return UserIntent(
        # в row лежат и колонки события, и статус с видимостью
        event=_row_to_event(row),
        status=row["intent_status"],
        # 1/0 из SQLite обратно в True/False
        visible=bool(row["intent_visible"]),
        visibility_asked=bool(row["intent_visibility_asked"]),
    )


def get_user_intents(user_id: int) -> list[UserIntent]:
    """Отметки пользователя вместе с событиями, ближайшие сверху."""
    with get_connection() as conn:
        rows = conn.execute(
            _INTENTS_QUERY + " ORDER BY e.date", (user_id,)
        ).fetchall()

    return [_row_to_intent(row) for row in rows]


def get_user_intent(user_id: int, event_id: int) -> UserIntent | None:
    """Одна отметка пользователя по конкретному событию.

    Нужна, чтобы перерисовать карточку после переключения видимости
    и чтобы проверить право смотреть список участников.
    """
    with get_connection() as conn:
        row = conn.execute(
            _INTENTS_QUERY + " AND i.event_id = ?", (user_id, event_id)
        ).fetchone()

    return _row_to_intent(row) if row is not None else None


def mark_visibility_asked(user_id: int, event_id: int) -> None:
    """Помечает, что про видимость по этому событию уже спрашивали."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE intents
            SET visibility_asked = 1
            WHERE user_id = ? AND event_id = ?
            """,
            (user_id, event_id),
        )


def format_intent_card(intent: UserIntent) -> str:
    """Строка события для /my (parse_mode=HTML)."""
    event = intent.event
    weekday = WEEKDAYS_RU[event.date.weekday()]
    # .get с запасным значением: если в базе окажется незнакомый статус,
    # покажем его как есть, а не упадём
    status = INTENT_STATUS_LABELS.get(intent.status, intent.status)
    visibility = "показываю другим" if intent.visible else "не показываю"
    source_link = _format_source_link(event)
    source = f"\n{source_link}" if source_link else ""

    return (
        f"🎯 <b>{escape(event.title)}</b>\n"
        f"📅 {event.date.strftime('%d.%m.%Y %H:%M')} ({weekday})\n"
        f"👀 {status} · {visibility}"
        f"{source}"
    )


def is_open_participant(intent: UserIntent | None) -> bool:
    """Открылся ли человек на этом событии.

    Ровно тот же критерий, по которому люди попадают в список «Кто идёт».
    Одна функция на оба случая = правило взаимности: видит список только
    тот, кого в этом списке видят другие.
    """
    return (
        intent is not None
        and intent.status in PARTICIPATING_STATUSES
        and intent.visible
    )


def find_companions(
    event_id: int,
    user_id: int,
    profile: Profile,
) -> list[Companion]:
    """Другие открывшиеся участники события.

    Право смотреть список проверяется отдельно (is_open_participant),
    здесь только выборка. profile нужен, чтобы посчитать общие интересы
    относительно того, кто смотрит.
    """
    # плейсхолдеры под IN (...) строим по числу статусов
    statuses = ", ".join("?" * len(PARTICIPATING_STATUSES))

    with get_connection() as conn:
        # Проверяем взаимность и на уровне доменной функции, а не только в
        # Telegram-хендлере. Так прямой вызов из другого интерфейса не сможет
        # показать список тому, кто сам не открылся на этом событии.
        viewer = conn.execute(
            f"""
            SELECT 1
            FROM intents
            WHERE event_id = ?
              AND user_id = ?
              AND status IN ({statuses})
              AND visible = 1
            """,
            (event_id, user_id, *PARTICIPATING_STATUSES),
        ).fetchone()
        if viewer is None:
            return []

        rows = conn.execute(
            f"""
            -- Внутренний JOIN: строка в users появляется только при
            -- подтверждении профиля, поэтому человек без профиля
            -- в список не попадёт
            SELECT u.telegram_id, u.name, u.interests,
                   u.group_size_min, u.group_size_max
            FROM intents i
            JOIN users u ON u.telegram_id = i.user_id
            WHERE i.event_id = ?
              AND i.status IN ({statuses})
              -- показываем только тех, кто сам разрешил себя показывать
              AND i.visible = 1
              -- себя самого в списке не показываем
              AND i.user_id != ?
              -- Блокировка в любую сторону скрывает пару у обоих.
              AND NOT EXISTS (
                  SELECT 1
                  FROM blocks b
                  WHERE (b.blocker = ? AND b.blocked = i.user_id)
                     OR (b.blocker = i.user_id AND b.blocked = ?)
              )
              -- Любой уже созданный исходящий запрос по этому событию
              -- (включая отклонённый) навсегда убирает кнопку повтора.
              AND NOT EXISTS (
                  SELECT 1
                  FROM requests r
                  WHERE r.event_id = i.event_id
                    AND r.from_user = ?
                    AND r.to_user = i.user_id
              )
            -- скоринга нет, нужен просто стабильный порядок
            ORDER BY i.updated_at, i.user_id
            LIMIT ?
            """,
            (
                event_id,
                *PARTICIPATING_STATUSES,
                user_id,
                user_id,
                user_id,
                user_id,
                MAX_COMPANIONS,
            ),
        ).fetchall()

    # интересы смотрящего — для пересечения; регистр не важен
    mine = {interest.lower() for interest in profile.interests}

    companions = []
    for row in rows:
        their_interests = json.loads(row["interests"])
        companions.append(
            Companion(
                user_id=row["telegram_id"],
                # у профилей, сохранённых до появления колонки name,
                # имя пустое — тогда честно пишем «Без имени»
                name=row["name"].strip() or UNKNOWN_NAME,
                common_interests=[
                    interest
                    for interest in their_interests
                    if interest.lower() in mine
                ],
                group_size_min=row["group_size_min"],
                group_size_max=row["group_size_max"],
            )
        )
    return companions


def format_companion_card(companion: Companion, index: int) -> str:
    """Карточка участника (parse_mode=HTML).

    telegram id нужен клавиатуре, но намеренно не попадает в текст.
    """
    common = ", ".join(companion.common_interests) or "пока не совпали"
    group_size = format_group_size(
        companion.group_size_min,
        companion.group_size_max,
    )

    return (
        f"👤 <b>{index}. {escape(companion.name)}</b>\n"
        f"🤝 Общие интересы: {escape(common)}\n"
        f"👥 Компания: {escape(group_size)}"
    )


_REQUEST_QUERY = """
    SELECT r.id, r.event_id, r.from_user, r.to_user, r.status,
           e.title AS event_title,
           sender.name AS from_name,
           sender.username AS from_username,
           sender.interests AS from_interests,
           recipient.name AS to_name,
           recipient.username AS to_username,
           recipient.interests AS to_interests
    FROM requests r
    JOIN events e ON e.id = r.event_id
    JOIN users sender ON sender.telegram_id = r.from_user
    JOIN users recipient ON recipient.telegram_id = r.to_user
    WHERE r.id = ?
"""


def _row_to_connection_request(row: sqlite3.Row) -> ConnectionRequest:
    """Строка запроса с профилями обеих сторон -> служебная модель."""
    sender_interests = {
        interest.lower() for interest in json.loads(row["from_interests"])
    }
    common_interests = [
        interest
        for interest in json.loads(row["to_interests"])
        if interest.lower() in sender_interests
    ]
    return ConnectionRequest(
        id=row["id"],
        event_id=row["event_id"],
        event_title=row["event_title"],
        from_user=row["from_user"],
        to_user=row["to_user"],
        from_name=row["from_name"].strip() or UNKNOWN_NAME,
        to_name=row["to_name"].strip() or UNKNOWN_NAME,
        from_username=row["from_username"],
        to_username=row["to_username"],
        common_interests=common_interests,
    )


def _get_connection_request(
    conn: sqlite3.Connection,
    request_id: int,
) -> ConnectionRequest | None:
    row = conn.execute(_REQUEST_QUERY, (request_id,)).fetchone()
    return _row_to_connection_request(row) if row is not None else None


def create_connection_request(
    event_id: int,
    from_user: int,
    to_user: int,
) -> tuple[str, ConnectionRequest | None]:
    """Атомарно создаёт запрос.

    Результат: created / already / blocked / limit / unavailable.
    Проверки выполняются в одной write-транзакции, поэтому два быстрых
    нажатия не создадут две строки и не пройдут лимит одновременно.
    """
    if from_user == to_user:
        return "unavailable", None

    statuses = ", ".join("?" * len(PARTICIPATING_STATUSES))
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")

        existing = conn.execute(
            """
            SELECT id FROM requests
            WHERE event_id = ? AND from_user = ? AND to_user = ?
            """,
            (event_id, from_user, to_user),
        ).fetchone()
        if existing is not None:
            return "already", _get_connection_request(conn, existing["id"])

        blocked = conn.execute(
            """
            SELECT 1 FROM blocks
            WHERE (blocker = ? AND blocked = ?)
               OR (blocker = ? AND blocked = ?)
            """,
            (from_user, to_user, to_user, from_user),
        ).fetchone()
        if blocked is not None:
            return "blocked", None

        # Нельзя обойти список поддельным callback: обе стороны должны быть
        # открыты и участвовать именно в этом событии.
        eligible = conn.execute(
            f"""
            SELECT 1
            FROM intents sender
            JOIN intents recipient ON recipient.event_id = sender.event_id
            JOIN users su ON su.telegram_id = sender.user_id
            JOIN users ru ON ru.telegram_id = recipient.user_id
            WHERE sender.event_id = ?
              AND sender.user_id = ?
              AND recipient.user_id = ?
              AND sender.status IN ({statuses})
              AND recipient.status IN ({statuses})
              AND sender.visible = 1
              AND recipient.visible = 1
            """,
            (
                event_id,
                from_user,
                to_user,
                *PARTICIPATING_STATUSES,
                *PARTICIPATING_STATUSES,
            ),
        ).fetchone()
        if eligible is None:
            return "unavailable", None

        sent_last_day = conn.execute(
            """
            SELECT COUNT(*) AS amount
            FROM requests
            WHERE from_user = ?
              AND created_at >= datetime('now', '-24 hours')
            """,
            (from_user,),
        ).fetchone()["amount"]
        if sent_last_day >= DAILY_REQUEST_LIMIT:
            return "limit", None

        cursor = conn.execute(
            """
            INSERT INTO requests
                (event_id, from_user, to_user, status, created_at)
            VALUES (?, ?, ?, 'pending', datetime('now'))
            """,
            (event_id, from_user, to_user),
        )
        request = _get_connection_request(conn, cursor.lastrowid)
        return "created", request


def accept_connection_request(
    request_id: int,
    to_user: int,
) -> tuple[str, ConnectionRequest | None]:
    """Принимает только свой pending-запрос и возвращает контакты сторон."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status, from_user, to_user FROM requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if row is None or row["to_user"] != to_user:
            return "unavailable", None
        if row["status"] != "pending":
            return "already", None

        blocked = conn.execute(
            """
            SELECT 1 FROM blocks
            WHERE (blocker = ? AND blocked = ?)
               OR (blocker = ? AND blocked = ?)
            """,
            (row["from_user"], to_user, to_user, row["from_user"]),
        ).fetchone()
        if blocked is not None:
            conn.execute(
                "UPDATE requests SET status = 'rejected' WHERE id = ?",
                (request_id,),
            )
            return "already", None

        conn.execute(
            "UPDATE requests SET status = 'accepted' WHERE id = ?",
            (request_id,),
        )
        return "accepted", _get_connection_request(conn, request_id)


def reject_connection_request(request_id: int, to_user: int) -> bool:
    """Отклоняет только свой pending-запрос; отправителю ничего не шлётся."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE requests
            SET status = 'rejected'
            WHERE id = ? AND to_user = ? AND status = 'pending'
            """,
            (request_id, to_user),
        )
        return cursor.rowcount > 0


def block_user(blocker: int, blocked: int) -> bool:
    """Блокирует глобально и отклоняет pending-запросы в обе стороны."""
    if blocker == blocked:
        return False

    with get_connection() as conn:
        # Не создаём блок для несуществующего пользователя из поддельного
        # callback_data.
        exists = conn.execute(
            "SELECT 1 FROM users WHERE telegram_id = ?",
            (blocked,),
        ).fetchone()
        if exists is None:
            return False

        conn.execute(
            """
            INSERT OR IGNORE INTO blocks (blocker, blocked, created_at)
            VALUES (?, ?, datetime('now'))
            """,
            (blocker, blocked),
        )
        conn.execute(
            """
            UPDATE requests
            SET status = 'rejected'
            WHERE status = 'pending'
              AND ((from_user = ? AND to_user = ?)
                OR (from_user = ? AND to_user = ?))
            """,
            (blocker, blocked, blocked, blocker),
        )
        conn.execute(
            """
            UPDATE group_connection_requests
            SET status = 'rejected'
            WHERE status = 'pending'
              AND ((from_user = ? AND to_user = ?)
                OR (from_user = ? AND to_user = ?))
            """,
            (blocker, blocked, blocked, blocker),
        )
        return True


def format_request_notification(request: ConnectionRequest) -> str:
    """Уведомление адресату без username и Telegram ID."""
    common = ", ".join(request.common_interests) or "пока не совпали"
    return (
        f"👋 <b>{escape(request.from_name)}</b> хочет познакомиться\n"
        f"🤝 Общие интересы: {escape(common)}\n"
        f"🎯 Событие: {escape(request.event_title)}"
    )


def format_contact_message(
    name: str,
    user_id: int,
    username: str | None,
    event_title: str,
) -> str:
    """Контакт, который формируется только после принятия запроса."""
    if username:
        contact = f"@{escape(username.lstrip('@'))}"
    else:
        contact = f'<a href="tg://user?id={user_id}">{escape(name)}</a>'
    return (
        "Знакомство взаимно ✅\n"
        f"Контакт: {contact}\n"
        f"Событие: {escape(event_title)}"
    )
