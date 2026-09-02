# src/event_bot/db.py
import json
import logging
import re
import sqlite3
from datetime import date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

import numpy as np

from event_bot.embedding_provider import vector_from_blob
from event_bot.models import (
    Companion,
    ConnectionRequest,
    Event,
    Profile,
    UserIntent,
    format_group_size,
)
from event_bot.persistence import (
    DB_DATETIME_FORMAT,
    DEFAULT_DB_PATH,
    connect,
    init_schema,
)
from event_bot.source_branding import source_brand

# Файл базы лежит в data/ в корне проекта: три parent — это
# db.py -> event_bot -> src -> корень
DB_PATH = DEFAULT_DB_PATH

logger = logging.getLogger(__name__)

# Сколько карточек максимум отправляем за один /find
MAX_EVENTS = 5

# Насколько сильно нежелательные интересы понижают semantic score.
AVOID_SIMILARITY_WEIGHT = 0.5

# Сколько других участников показываем по кнопке «Кто идёт»
MAX_COMPANIONS = 5

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


def get_connection(db_path=None):
    """Compatibility wrapper; tests may still override ``db.DB_PATH``."""
    return connect(db_path or DB_PATH)


def init_db() -> None:
    """Initialize the current schema at the configured database path."""
    init_schema(DB_PATH)



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
              AND activity.user_id NOT IN (
                  SELECT user_id FROM research_participants
              )
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
    photo_url: str | None = None,
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
                (telegram_id, name, username, photo_url, interests, avoid, days,
                 budget_rub, group_size_min, group_size_max,
                 profile_embedding, profile_embedding_model,
                 avoid_embedding, avoid_embedding_model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            -- UPSERT: если строка с таким telegram_id уже есть,
            -- вместо ошибки обновляем её поля (excluded — то, что пытались
            -- вставить)
            ON CONFLICT(telegram_id) DO UPDATE SET
                name           = excluded.name,
                username       = excluded.username,
                photo_url      = COALESCE(excluded.photo_url, users.photo_url),
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
                photo_url,
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
    photo_url: str | None = None,
) -> None:
    """Освежает Telegram-имя и доступную фотографию профиля."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE users
            SET name = ?, username = ?,
                photo_url = COALESCE(?, photo_url)
            WHERE telegram_id = ?
            """,
            (name, username, photo_url, telegram_id),
        )


ACQUISITION_CAMPAIGN_PATTERN = re.compile(r"^[a-z0-9_-]{1,64}$")


def record_user_acquisition(user_id: int, campaign: str | None) -> bool:
    """Store the first trusted Telegram /start payload for attribution."""
    value = (campaign or "").strip().lower()
    if user_id <= 0 or not ACQUISITION_CAMPAIGN_PATTERN.fullmatch(value):
        return False
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO user_acquisition (user_id, campaign) VALUES (?, ?)",
            (user_id, value),
        )
    return cursor.rowcount > 0


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
        " AND COALESCE(end_date, date) > datetime('now', '+3 hours')",
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


EVENT_GROUP_MIN_MEMBERS = 2
EVENT_GROUP_MAX_MEMBERS = 5


def _normalized_company_preference(
    minimum: int | None,
    maximum: int | None,
) -> tuple[int, int]:
    """Return a supported 2–5 range, including profiles saved by old clients."""
    low = max(EVENT_GROUP_MIN_MEMBERS, min(EVENT_GROUP_MAX_MEMBERS, minimum or 2))
    high = max(EVENT_GROUP_MIN_MEMBERS, min(EVENT_GROUP_MAX_MEMBERS, maximum or 5))
    return (low, high) if low <= high else (EVENT_GROUP_MIN_MEMBERS, EVENT_GROUP_MAX_MEMBERS)


def _preferred_company_size(minimum: int | None, maximum: int | None) -> int:
    # «Неважно» оптимизируем под скорость сбора: два участника
    # уже могут безопасно договариваться внутри закрытой компании.
    if minimum is None and maximum is None:
        return EVENT_GROUP_MIN_MEMBERS
    low, high = _normalized_company_preference(minimum, maximum)
    if low == high:
        return low
    return 3 if low <= 3 <= high else low


def _refresh_event_group_status(conn: sqlite3.Connection, group_id: int) -> int:
    """Recalculate one company's state after a member or event changed."""
    row = conn.execute(
        """
        SELECT eg.target_size,
               COALESCE(e.end_date, datetime(e.date, '+3 hours')) AS effective_end,
               COUNT(gm.user_id) AS member_count
        FROM event_groups eg
        JOIN events e ON e.id = eg.event_id
        LEFT JOIN event_group_members gm ON gm.group_id = eg.id
        WHERE eg.id = ?
        GROUP BY eg.id
        """,
        (group_id,),
    ).fetchone()
    if row is None:
        return 0
    amount = int(row["member_count"])
    if amount == 0:
        conn.execute("DELETE FROM event_group_messages WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM event_group_deliveries WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM event_experience_feedback WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM event_groups WHERE id = ?", (group_id,))
        return 0
    expired = conn.execute(
        "SELECT ? <= datetime('now', '+3 hours') AS value",
        (row["effective_end"],),
    ).fetchone()["value"]
    if expired:
        conn.execute("UPDATE event_groups SET status = 'archived' WHERE id = ?", (group_id,))
    elif amount >= int(row["target_size"]):
        conn.execute(
            """
            UPDATE event_groups
            SET status = 'active', activated_at = COALESCE(activated_at, datetime('now'))
            WHERE id = ?
            """,
            (group_id,),
        )
    else:
        conn.execute(
            "UPDATE event_groups SET status = 'forming', activated_at = NULL WHERE id = ?",
            (group_id,),
        )
    return amount


def _leave_event_group_in_connection(
    conn: sqlite3.Connection,
    group_id: int,
    user_id: int,
) -> bool:
    deleted = conn.execute(
        "DELETE FROM event_group_members WHERE group_id = ? AND user_id = ?",
        (group_id, user_id),
    )
    if deleted.rowcount == 0:
        return False
    conn.execute(
        "DELETE FROM event_group_messages WHERE group_id = ? AND user_id = ?",
        (group_id, user_id),
    )
    conn.execute(
        "DELETE FROM event_group_deliveries WHERE group_id = ? AND user_id = ?",
        (group_id, user_id),
    )
    conn.execute(
        "DELETE FROM event_experience_feedback WHERE group_id = ? AND user_id = ?",
        (group_id, user_id),
    )
    conn.execute(
        """
        UPDATE event_groups
        SET meeting_point = NULL, meeting_point_by = NULL
        WHERE id = ? AND meeting_point_by = ?
        """,
        (group_id, user_id),
    )
    _refresh_event_group_status(conn, group_id)
    return True


def archive_expired_event_groups() -> int:
    """Archive companies after the event end (or three hours after start)."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE event_groups
            SET status = 'archived'
            WHERE status != 'archived'
              AND event_id IN (
                  SELECT id FROM events
                  WHERE COALESCE(end_date, datetime(date, '+3 hours'))
                        <= datetime('now', '+3 hours')
              )
            """
        )
        return cursor.rowcount


def _active_research_campaign_in_connection(
    conn: sqlite3.Connection,
    user_id: int,
) -> str | None:
    row = conn.execute(
        """
        SELECT campaign
        FROM research_participants
        WHERE user_id = ? AND active = 1
        ORDER BY enrolled_at DESC, campaign DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    return str(row["campaign"]) if row is not None else None


def _research_group_filter(
    campaign: str | None,
    alias: str = "eg",
) -> tuple[str, tuple[object, ...]]:
    if campaign is None:
        return f" AND {alias}.research_campaign IS NULL", ()
    return f" AND {alias}.research_campaign = ?", (campaign,)


def find_events_with_open_companies(
    *,
    limit: int = 12,
    research_campaign: str | None = None,
) -> list[Event]:
    """Будущие события, где хотя бы одна компания ещё принимает участников."""
    with get_connection() as conn:
        research_sql, research_params = _research_group_filter(research_campaign)
        rows = conn.execute(
            f"""
            SELECT e.*
            FROM events e
            JOIN (
                SELECT open_groups.event_id, MAX(open_groups.created_at) AS newest_group
                FROM (
                    SELECT eg.id, eg.event_id, eg.created_at
                    FROM event_groups eg
                    JOIN event_group_members gm ON gm.group_id = eg.id
                    WHERE eg.status = 'forming'
                    {research_sql}
                    GROUP BY eg.id
                    HAVING COUNT(gm.user_id) < eg.target_size
                ) AS open_groups
                GROUP BY open_groups.event_id
            ) AS discovery ON discovery.event_id = e.id
            WHERE e.city = 'Москва'
              AND COALESCE(e.status, 'active') = 'active'
              AND COALESCE(e.end_date, e.date) > datetime('now', '+3 hours')
            ORDER BY discovery.newest_group DESC, e.date, e.id
            LIMIT ?
            """,
            (*research_params, max(1, min(limit, 50))),
        ).fetchall()
    events: list[Event] = []
    for row in rows:
        try:
            events.append(_row_to_event(row))
        except (TypeError, ValueError, json.JSONDecodeError):
            logger.exception("Пропущено поврежденное событие с открытой компанией")
    return events


def join_event_group(
    user_id: int,
    event_id: int,
) -> tuple[str, int | None, list[int]]:
    """Присоединяет пользователя к компании именно этого мероприятия.

    Возвращает (joined/already/unavailable, group_id, участники для
    уведомления). Операция сериализована, поэтому два одновременных входа не
    переполнят группу.
    """
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        research_campaign = _active_research_campaign_in_connection(conn, user_id)
        user = conn.execute(
            """
            SELECT u.interests, u.group_size_min, u.group_size_max
            FROM users u
            JOIN events e ON e.id = ?
            WHERE u.telegram_id = ?
              AND COALESCE(e.status, 'active') = 'active'
              AND COALESCE(e.end_date, e.date) > datetime('now', '+3 hours')
            """,
            (event_id, user_id),
        ).fetchone()
        if user is None:
            return "unavailable", None, []

        research_sql, research_params = _research_group_filter(research_campaign)
        existing = conn.execute(
            f"""
            SELECT eg.id
            FROM event_groups eg
            JOIN event_group_members gm ON gm.group_id = eg.id
            WHERE eg.event_id = ? AND gm.user_id = ?
              {research_sql}
            """,
            (event_id, user_id, *research_params),
        ).fetchone()
        if existing is not None:
            member_ids = [
                row["user_id"]
                for row in conn.execute(
                    "SELECT user_id FROM event_group_members WHERE group_id = ? "
                    "ORDER BY joined_at, user_id",
                    (existing["id"],),
                )
            ]
            return "already", existing["id"], member_ids

        preference_min, preference_max = _normalized_company_preference(
            user["group_size_min"], user["group_size_max"]
        )
        try:
            user_interests = {
                value.strip().casefold()
                for value in json.loads(user["interests"])
                if isinstance(value, str) and value.strip()
            }
        except (TypeError, json.JSONDecodeError):
            user_interests = set()

        candidates = conn.execute(
            f"""
            SELECT eg.id, eg.target_size, COUNT(gm.user_id) AS member_count
            FROM event_groups eg
            LEFT JOIN event_group_members gm ON gm.group_id = eg.id
            WHERE eg.event_id = ?
              AND eg.status = 'forming'
              AND eg.target_size BETWEEN ? AND ?
              {research_sql}
              AND NOT EXISTS (
                  SELECT 1
                  FROM event_group_members existing_member
                  JOIN blocks b
                    ON (b.blocker = ? AND b.blocked = existing_member.user_id)
                    OR (b.blocker = existing_member.user_id AND b.blocked = ?)
                  WHERE existing_member.group_id = eg.id
              )
            GROUP BY eg.id
            HAVING COUNT(gm.user_id) < eg.target_size
            ORDER BY eg.created_at, eg.id
            """,
            (
                event_id,
                preference_min,
                preference_max,
                *research_params,
                user_id,
                user_id,
            ),
        ).fetchall()
        if candidates:
            ranked_candidates: list[tuple[int, int, int]] = []
            for candidate in candidates:
                member_rows = conn.execute(
                    """
                    SELECT u.interests
                    FROM event_group_members gm
                    JOIN users u ON u.telegram_id = gm.user_id
                    WHERE gm.group_id = ?
                    """,
                    (candidate["id"],),
                ).fetchall()
                group_interests: set[str] = set()
                for member in member_rows:
                    try:
                        group_interests.update(
                            value.strip().casefold()
                            for value in json.loads(member["interests"])
                            if isinstance(value, str) and value.strip()
                        )
                    except (TypeError, json.JSONDecodeError):
                        continue
                ranked_candidates.append(
                    (
                        len(user_interests & group_interests),
                        int(candidate["member_count"]),
                        int(candidate["id"]),
                    )
                )
            group_id = max(ranked_candidates, key=lambda item: item[:2])[2]
        else:
            target_size = _preferred_company_size(
                user["group_size_min"], user["group_size_max"]
            )
            group_id = conn.execute(
                """
                INSERT INTO event_groups (event_id, target_size, research_campaign)
                VALUES (?, ?, ?)
                """,
                (event_id, target_size, research_campaign),
            ).lastrowid

        conn.execute(
            """
            INSERT INTO event_group_members (group_id, user_id, rsvp)
            VALUES (?, ?, 'going')
            """,
            (group_id, user_id),
        )
        # Поиск компании одновременно означает явное участие и согласие быть
        # видимым только внутри выбранного события.
        conn.execute(
            """
            INSERT INTO intents
                (user_id, event_id, status, visible, visibility_asked, updated_at)
            VALUES (?, ?, 'going', 1, 1, datetime('now'))
            ON CONFLICT(user_id, event_id) DO UPDATE SET
                status = 'going', visible = 1, visibility_asked = 1,
                updated_at = datetime('now')
            """,
            (user_id, event_id),
        )
        member_ids = [
            row["user_id"]
            for row in conn.execute(
                "SELECT user_id FROM event_group_members WHERE group_id = ? "
                "ORDER BY joined_at, user_id",
                (group_id,),
            )
        ]
        _refresh_event_group_status(conn, group_id)
        return "joined", group_id, member_ids


def _event_group_payload_in_connection(
    conn: sqlite3.Connection,
    group_id: int,
    viewer_id: int,
) -> dict[str, object] | None:
    group_row = conn.execute(
        """
        SELECT e.*,
               eg.id AS event_group_id,
               eg.status AS event_group_status,
               eg.target_size,
               eg.meeting_point,
               eg.meeting_point_by,
               eg.created_at AS event_group_created_at
        FROM event_groups eg
        JOIN event_group_members mine
          ON mine.group_id = eg.id AND mine.user_id = ?
        JOIN events e ON e.id = eg.event_id
        WHERE eg.id = ?
        """,
        (viewer_id, group_id),
    ).fetchone()
    if group_row is None:
        return None
    event = _row_to_event(group_row)
    viewer_interests_row = conn.execute(
        "SELECT interests FROM users WHERE telegram_id = ?",
        (viewer_id,),
    ).fetchone()
    viewer_interests = {
        value.casefold()
        for value in json.loads(viewer_interests_row["interests"] or "[]")
        if isinstance(value, str)
    } if viewer_interests_row is not None else set()
    member_rows = conn.execute(
        """
        SELECT gm.user_id, gm.rsvp, gm.joined_at, u.name, u.username,
               u.photo_url,
               u.interests, u.group_size_min, u.group_size_max
        FROM event_group_members gm
        JOIN users u ON u.telegram_id = gm.user_id
        WHERE gm.group_id = ?
        ORDER BY gm.joined_at, gm.user_id
        """,
        (group_id,),
    ).fetchall()
    members: list[dict[str, object]] = []
    for row in member_rows:
        their_interests = json.loads(row["interests"] or "[]")
        members.append(
            {
                "user_id": row["user_id"],
                "name": row["name"].strip() or UNKNOWN_NAME,
                "username": row["username"],
                "photo_url": row["photo_url"],
                "rsvp": row["rsvp"],
                "common_interests": [
                    item
                    for item in their_interests
                    if isinstance(item, str) and item.casefold() in viewer_interests
                ],
                "group_size_min": row["group_size_min"],
                "group_size_max": row["group_size_max"],
            }
        )
    message_rows = conn.execute(
        """
        SELECT m.id, m.user_id, m.message, m.created_at, u.name
        FROM event_group_messages m
        JOIN users u ON u.telegram_id = m.user_id
        WHERE m.group_id = ?
        ORDER BY m.created_at DESC, m.id DESC
        LIMIT 50
        """,
        (group_id,),
    ).fetchall()
    return {
        "id": group_row["event_group_id"],
        "status": group_row["event_group_status"],
        "event": event,
        "meeting_point": group_row["meeting_point"],
        "meeting_point_by": group_row["meeting_point_by"],
        "minimum_members": group_row["target_size"],
        "maximum_members": group_row["target_size"],
        "member_count": len(members),
        "members": members,
        "messages": [
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "name": row["name"].strip() or UNKNOWN_NAME,
                "message": row["message"],
                "created_at": row["created_at"],
            }
            for row in reversed(message_rows)
        ],
    }


def get_event_group(group_id: int, user_id: int) -> dict[str, object] | None:
    with get_connection() as conn:
        return _event_group_payload_in_connection(conn, group_id, user_id)


def get_user_event_groups(user_id: int, *, limit: int = 20) -> list[dict[str, object]]:
    with get_connection() as conn:
        campaign = _active_research_campaign_in_connection(conn, user_id)
        research_sql, research_params = _research_group_filter(campaign)
        rows = conn.execute(
            f"""
            SELECT eg.id
            FROM event_groups eg
            JOIN event_group_members gm ON gm.group_id = eg.id
            JOIN events e ON e.id = eg.event_id
            WHERE gm.user_id = ?
              AND eg.status != 'archived'
              {research_sql}
              AND COALESCE(e.end_date, datetime(e.date, '+3 hours'))
                    > datetime('now', '+3 hours')
            ORDER BY e.date, eg.id
            LIMIT ?
            """,
            (user_id, *research_params, limit),
        ).fetchall()
        return [
            payload
            for row in rows
            if (payload := _event_group_payload_in_connection(conn, row["id"], user_id))
            is not None
        ]


def get_event_company_counts(
    event_ids: list[int],
    *,
    research_campaign: str | None = None,
) -> dict[int, int]:
    if not event_ids:
        return {}
    placeholders = ",".join("?" for _ in event_ids)
    with get_connection() as conn:
        research_sql, research_params = _research_group_filter(research_campaign)
        rows = conn.execute(
            f"""
            SELECT eg.event_id, COUNT(gm.user_id) AS amount
            FROM event_groups eg
            JOIN event_group_members gm ON gm.group_id = eg.id
            WHERE eg.event_id IN ({placeholders})
              AND eg.status = 'forming'
              {research_sql}
            GROUP BY eg.event_id
            HAVING COUNT(gm.user_id) < eg.target_size
            """,
            (*event_ids, *research_params),
        ).fetchall()
    return {row["event_id"]: row["amount"] for row in rows}


def leave_event_group(group_id: int, user_id: int) -> bool:
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return _leave_event_group_in_connection(conn, group_id, user_id)


def set_event_group_rsvp(group_id: int, user_id: int, rsvp: str) -> bool:
    if rsvp not in {"going", "declined"}:
        return False
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT eg.event_id
            FROM event_group_members gm
            JOIN event_groups eg ON eg.id = gm.group_id
            WHERE gm.group_id = ? AND gm.user_id = ?
            """,
            (group_id, user_id),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "UPDATE intents SET status = ?, updated_at = datetime('now') "
            "WHERE user_id = ? AND event_id = ?",
            ("going" if rsvp == "going" else "not_going", user_id, row["event_id"]),
        )
        if rsvp == "declined":
            return _leave_event_group_in_connection(conn, group_id, user_id)
        conn.execute(
            "UPDATE event_group_members SET rsvp = 'going' WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        )
        return True


def set_event_group_meeting_point(group_id: int, user_id: int, value: str) -> bool:
    with get_connection() as conn:
        member = conn.execute(
            "SELECT 1 FROM event_group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        ).fetchone()
        if member is None:
            return False
        conn.execute(
            "UPDATE event_groups SET meeting_point = ?, meeting_point_by = ? WHERE id = ?",
            (value, user_id, group_id),
        )
        return True


def create_event_group_message(
    group_id: int,
    user_id: int,
    message: str,
) -> tuple[str, list[int]]:
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        group = conn.execute(
            """
            SELECT eg.status
            FROM event_groups eg
            JOIN event_group_members gm ON gm.group_id = eg.id
            WHERE eg.id = ? AND gm.user_id = ?
            """,
            (group_id, user_id),
        ).fetchone()
        if group is None or group["status"] != "active":
            return "unavailable", []
        recent = conn.execute(
            """
            SELECT COUNT(*) AS amount
            FROM event_group_messages
            WHERE group_id = ? AND user_id = ?
              AND created_at >= datetime('now', '-60 seconds')
            """,
            (group_id, user_id),
        ).fetchone()["amount"]
        if recent >= 6:
            return "limit", []
        conn.execute(
            "INSERT INTO event_group_messages (group_id, user_id, message) VALUES (?, ?, ?)",
            (group_id, user_id, message),
        )
        member_ids = [
            row["user_id"]
            for row in conn.execute(
                "SELECT user_id FROM event_group_members WHERE group_id = ?",
                (group_id,),
            )
        ]
        return "created", member_ids


EVENT_EXPERIENCE_OUTCOMES = {"met", "no_show", "solo", "unsafe"}
EVENT_GROUP_DELIVERY_KINDS = {"reminder_24h", "experience_prompt"}


def get_due_event_group_deliveries(
    kind: str,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    """Return due per-member notifications that have not been delivered."""
    if kind not in EVENT_GROUP_DELIVERY_KINDS:
        raise ValueError("Неизвестный тип уведомления")
    current = now or datetime.now(ZoneInfo("Europe/Moscow"))
    if current.tzinfo is not None:
        current = current.astimezone(ZoneInfo("Europe/Moscow")).replace(tzinfo=None)
    current_text = current.strftime(DB_DATETIME_FORMAT)
    retry_before = (current - timedelta(hours=6)).strftime(DB_DATETIME_FORMAT)
    if kind == "reminder_24h":
        due_clause = "e.date > ? AND e.date <= datetime(?, '+24 hours') AND eg.status = 'active'"
    else:
        due_clause = (
            "COALESCE(e.end_date, datetime(e.date, '+3 hours')) <= ? "
            "AND COALESCE(e.end_date, datetime(e.date, '+3 hours')) > datetime(?, '-7 days') "
            "AND eg.activated_at IS NOT NULL"
        )
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT eg.id AS group_id, gm.user_id, eg.meeting_point,
                   e.id AS event_id, e.title, e.date, e.venue, e.address
            FROM event_groups eg
            JOIN event_group_members gm ON gm.group_id = eg.id
            JOIN events e ON e.id = eg.event_id
            LEFT JOIN event_group_deliveries d
              ON d.group_id = eg.id AND d.user_id = gm.user_id AND d.kind = ?
            WHERE gm.rsvp = 'going'
              AND {due_clause}
              AND (
                  d.group_id IS NULL
                  OR (d.status IN ('claimed', 'failed') AND d.delivered_at <= ?)
              )
            ORDER BY e.date, eg.id, gm.joined_at
            LIMIT ?
            """,
            (kind, current_text, current_text, retry_before, max(1, min(limit, 500))),
        ).fetchall()
    return [dict(row) for row in rows]


def claim_event_group_delivery(
    group_id: int,
    user_id: int,
    kind: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Atomically claim one delivery; stale failed claims may be retried."""
    if kind not in EVENT_GROUP_DELIVERY_KINDS:
        return False
    current = now or datetime.now(ZoneInfo("Europe/Moscow"))
    if current.tzinfo is not None:
        current = current.astimezone(ZoneInfo("Europe/Moscow")).replace(tzinfo=None)
    current_text = current.strftime(DB_DATETIME_FORMAT)
    retry_before = (current - timedelta(hours=6)).strftime(DB_DATETIME_FORMAT)
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT status, delivered_at FROM event_group_deliveries "
            "WHERE group_id = ? AND user_id = ? AND kind = ?",
            (group_id, user_id, kind),
        ).fetchone()
        if existing is not None and (
            existing["status"] == "sent"
            or not existing["delivered_at"]
            or existing["delivered_at"] > retry_before
        ):
            return False
        conn.execute(
            """
            INSERT INTO event_group_deliveries
                (group_id, user_id, kind, status, delivered_at)
            VALUES (?, ?, ?, 'claimed', ?)
            ON CONFLICT(group_id, user_id, kind) DO UPDATE SET
                status = 'claimed', delivered_at = excluded.delivered_at
            """,
            (group_id, user_id, kind, current_text),
        )
        return True


def mark_event_group_delivery(
    group_id: int,
    user_id: int,
    kind: str,
    delivery_status: str,
) -> None:
    if kind not in EVENT_GROUP_DELIVERY_KINDS or delivery_status not in {"sent", "failed"}:
        return
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE event_group_deliveries
            SET status = ?, delivered_at = datetime('now', '+3 hours')
            WHERE group_id = ? AND user_id = ? AND kind = ?
            """,
            (delivery_status, group_id, user_id, kind),
        )


def save_event_experience_feedback(
    group_id: int,
    user_id: int,
    outcome: str,
) -> bool:
    if outcome not in EVENT_EXPERIENCE_OUTCOMES:
        return False
    with get_connection() as conn:
        member = conn.execute(
            "SELECT 1 FROM event_group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        ).fetchone()
        if member is None:
            return False
        conn.execute(
            """
            INSERT INTO event_experience_feedback (group_id, user_id, outcome)
            VALUES (?, ?, ?)
            ON CONFLICT(group_id, user_id) DO UPDATE SET
                outcome = excluded.outcome, created_at = datetime('now')
            """,
            (group_id, user_id, outcome),
        )
        return True


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


def record_source_sync_run(
    source_id: str,
    sync_status: str,
    *,
    added: int = 0,
    updated: int = 0,
    skipped: int = 0,
    errors: int = 0,
    fetched: int = 0,
    error_message: str | None = None,
    started_at: datetime,
    finished_at: datetime,
) -> int:
    """Persist one importer outcome so source health is visible to admins."""
    if sync_status not in {"success", "warning", "failed"}:
        raise ValueError("Недопустимый статус импорта")

    def utc_text(value: datetime) -> str:
        if value.tzinfo is not None:
            value = value.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        return value.strftime(DB_DATETIME_FORMAT)

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO source_sync_runs
                (source_id, status, added, updated, skipped, errors, fetched,
                 error_message, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id[:40],
                sync_status,
                max(0, added),
                max(0, updated),
                max(0, skipped),
                max(0, errors),
                max(0, fetched),
                (error_message or "")[:1000] or None,
                utc_text(started_at),
                utc_text(finished_at),
            ),
        )
    return int(cursor.lastrowid)


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
        conn.execute(
            """
            DELETE FROM event_group_messages
            WHERE group_id IN (
                SELECT eg.id
                FROM event_groups eg
                JOIN events e ON e.id = eg.event_id
                WHERE e.source_id IS NULL
            )
            """
        )
        conn.execute(
            """
            DELETE FROM event_group_members
            WHERE group_id IN (
                SELECT eg.id
                FROM event_groups eg
                JOIN events e ON e.id = eg.event_id
                WHERE e.source_id IS NULL
            )
            """
        )
        conn.execute(
            """
            DELETE FROM event_groups
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


def get_event_connection_state(
    event_id: int,
    user_id: int,
    target_user_id: int,
) -> tuple[str, ConnectionRequest | None]:
    """Состояние взаимного знакомства участников событийной компании."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, from_user, to_user, status
            FROM requests
            WHERE event_id = ?
              AND ((from_user = ? AND to_user = ?)
                OR (from_user = ? AND to_user = ?))
            ORDER BY CASE status WHEN 'accepted' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                     id DESC
            """,
            (event_id, user_id, target_user_id, target_user_id, user_id),
        ).fetchall()
        if not rows:
            return "available", None
        row = rows[0]
        request = _get_connection_request(conn, row["id"])
        if row["status"] == "accepted":
            return "connected", request
        if row["status"] == "pending":
            return (
                "pending_sent" if row["from_user"] == user_id else "pending_received",
                request,
            )
        if row["from_user"] == user_id:
            return "rejected", request
        return "available", None


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
    """Block globally, reject requests and separate the pair from shared groups."""
    if blocker == blocked:
        return False

    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
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
        shared_group_ids = [
            row["group_id"]
            for row in conn.execute(
                """
                SELECT mine.group_id
                FROM event_group_members mine
                JOIN event_group_members other ON other.group_id = mine.group_id
                WHERE mine.user_id = ? AND other.user_id = ?
                """,
                (blocker, blocked),
            ).fetchall()
        ]
        for group_id in shared_group_ids:
            _leave_event_group_in_connection(conn, group_id, blocker)
        return True


USER_REPORT_REASONS = {"spam", "harassment", "unsafe", "other"}


def create_user_report(
    reporter_id: int,
    reported_id: int,
    group_id: int,
    reason: str,
    details: str = "",
) -> tuple[str, dict[str, object] | None]:
    """Create a rate-limited report only for members of the same company."""
    if reporter_id == reported_id or reason not in USER_REPORT_REASONS:
        return "unavailable", None
    with get_connection() as conn:
        eligible = conn.execute(
            """
            SELECT eg.event_id, e.title AS event_title,
                   reporter.name AS reporter_name,
                   reported.name AS reported_name
            FROM event_groups eg
            JOIN events e ON e.id = eg.event_id
            JOIN event_group_members mine
              ON mine.group_id = eg.id AND mine.user_id = ?
            JOIN event_group_members target
              ON target.group_id = eg.id AND target.user_id = ?
            JOIN users reporter ON reporter.telegram_id = mine.user_id
            JOIN users reported ON reported.telegram_id = target.user_id
            WHERE eg.id = ?
            """,
            (reporter_id, reported_id, group_id),
        ).fetchone()
        if eligible is None:
            return "unavailable", None
        recent = conn.execute(
            """
            SELECT COUNT(*) AS amount
            FROM user_reports
            WHERE reporter_id = ? AND created_at >= datetime('now', '-24 hours')
            """,
            (reporter_id,),
        ).fetchone()["amount"]
        if recent >= 5:
            return "limit", None
        cursor = conn.execute(
            """
            INSERT INTO user_reports
                (reporter_id, reported_id, event_group_id, event_id, reason, details)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                reporter_id,
                reported_id,
                group_id,
                eligible["event_id"],
                reason,
                " ".join(details.strip().split())[:1000],
            ),
        )
        return "created", {
            "id": cursor.lastrowid,
            "reported_id": reported_id,
            "reporter_name": eligible["reporter_name"].strip() or UNKNOWN_NAME,
            "reported_name": eligible["reported_name"].strip() or UNKNOWN_NAME,
            "event_title": eligible["event_title"],
            "reason": reason,
            "details": " ".join(details.strip().split())[:1000],
        }


def get_recent_user_reports(
    *,
    only_new: bool = True,
    limit: int = 10,
) -> list[dict[str, object]]:
    where = "WHERE reports.status = 'new'" if only_new else ""
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT reports.id, reports.reason, reports.details, reports.status,
                   reports.created_at, reports.reporter_id, reports.reported_id,
                   reporter.name AS reporter_name,
                   reported.name AS reported_name,
                   events.title AS event_title
            FROM user_reports reports
            LEFT JOIN users reporter ON reporter.telegram_id = reports.reporter_id
            LEFT JOIN users reported ON reported.telegram_id = reports.reported_id
            LEFT JOIN events ON events.id = reports.event_id
            {where}
            ORDER BY CASE reports.status WHEN 'new' THEN 0 ELSE 1 END,
                     reports.created_at DESC, reports.id DESC
            LIMIT ?
            """,
            (max(1, min(limit, 30)),),
        ).fetchall()
    return [dict(row) for row in rows]


def resolve_user_report(report_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE user_reports SET status = 'resolved' WHERE id = ? AND status = 'new'",
            (report_id,),
        )
    return cursor.rowcount > 0


def is_user_suspended(user_id: int) -> bool:
    with get_connection() as conn:
        return conn.execute(
            "SELECT 1 FROM user_suspensions WHERE user_id = ? AND lifted_at IS NULL",
            (user_id,),
        ).fetchone() is not None


def suspend_user(user_id: int, admin_id: int, reason: str = "") -> bool:
    if user_id <= 0 or admin_id <= 0 or user_id == admin_id:
        return False
    normalized = " ".join(reason.strip().split())[:500]
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute(
            "SELECT 1 FROM users WHERE telegram_id = ?",
            (user_id,),
        ).fetchone()
        if exists is None:
            return False
        conn.execute(
            """
            INSERT INTO user_suspensions (user_id, reason, created_by)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                reason = excluded.reason,
                created_by = excluded.created_by,
                created_at = datetime('now'),
                lifted_at = NULL
            """,
            (user_id, normalized, admin_id),
        )
        conn.execute(
            "UPDATE intents SET visible = 0 WHERE user_id = ?",
            (user_id,),
        )
        conn.execute(
            "UPDATE requests SET status = 'rejected' "
            "WHERE status = 'pending' AND (from_user = ? OR to_user = ?)",
            (user_id, user_id),
        )
        group_ids = [
            row["group_id"]
            for row in conn.execute(
                "SELECT group_id FROM event_group_members WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        ]
        for group_id in group_ids:
            _leave_event_group_in_connection(conn, group_id, user_id)
        return True


def lift_user_suspension(user_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE user_suspensions
            SET lifted_at = datetime('now')
            WHERE user_id = ? AND lifted_at IS NULL
            """,
            (user_id,),
        )
    return cursor.rowcount > 0


def delete_user_data(user_id: int) -> bool:
    """Delete a user's profile, activity and company data in one transaction."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute(
            """
            SELECT 1 FROM users WHERE telegram_id = ?
            UNION SELECT 1 FROM usage_events WHERE user_id = ?
            UNION SELECT 1 FROM feedback_messages WHERE user_id = ?
            UNION SELECT 1 FROM user_acquisition WHERE user_id = ?
            UNION SELECT 1 FROM research_participants WHERE user_id = ?
            LIMIT 1
            """,
            (user_id, user_id, user_id, user_id, user_id),
        ).fetchone()
        group_ids = [
            row["group_id"]
            for row in conn.execute(
                "SELECT group_id FROM event_group_members WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        ]
        for group_id in group_ids:
            _leave_event_group_in_connection(conn, group_id, user_id)
        conn.execute("DELETE FROM requests WHERE from_user = ? OR to_user = ?", (user_id, user_id))
        conn.execute("DELETE FROM blocks WHERE blocker = ? OR blocked = ?", (user_id, user_id))
        conn.execute("DELETE FROM intents WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM usage_events WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM feedback_messages WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM inactivity_feedback_prompts WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM event_group_deliveries WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM event_experience_feedback WHERE user_id = ?", (user_id,))
        conn.execute(
            "DELETE FROM user_reports WHERE reporter_id = ? OR reported_id = ?",
            (user_id, user_id),
        )
        conn.execute("DELETE FROM user_acquisition WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM research_sessions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM research_participants WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM admin_report_deliveries WHERE admin_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE telegram_id = ?", (user_id,))
        return exists is not None


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
