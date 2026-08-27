# src/event_bot/db.py
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from event_bot.models import (
    Companion,
    ConnectionRequest,
    Event,
    Profile,
    UserIntent,
    format_group_size,
)

# Файл базы лежит в data/ в корне проекта: три parent — это
# db.py -> event_bot -> src -> корень
DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "bot.db"

# Формат, в котором даты лежат в SQLite: он сравним с datetime('now')
DB_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Сколько карточек максимум отправляем за один /find
MAX_EVENTS = 5

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
                created_at     TEXT NOT NULL DEFAULT (datetime('now'))
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


def save_user_profile(
    telegram_id: int,
    profile: Profile,
    name: str,
    username: str | None = None,
) -> None:
    """Сохраняет профиль пользователя (UPSERT по telegram_id).

    name — first_name из Telegram: имя нужно для карточки участника.
    username хранится для выдачи контакта только после принятия запроса.
    """
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO users (telegram_id, name, username, interests, avoid, days,
                               budget_rub, group_size_min, group_size_max)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                group_size_max = excluded.group_size_max
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


def find_events(profile: Profile) -> list[Event]:
    """Подбирает до 5 текущих или будущих московских мероприятий."""
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

    # приводим к нижнему регистру, чтобы «Музыка» и «музыка» совпали
    interests = {tag.lower() for tag in profile.interests}
    avoid = {tag.lower() for tag in profile.avoid}

    # ранжирование делаем в Python: score считать в SQL здесь неудобно
    events = [_row_to_event(row) for row in rows]
    # При равном score показываем то, что раньше по дате
    events.sort(key=lambda e: (-_score(e, interests, avoid), e.date))
    return events[:MAX_EVENTS]


def _format_source_link(event: Event) -> str:
    """Прямая атрибуция источника для любой карточки события."""
    if not event.source_url:
        return ""
    source_url = escape(event.source_url, quote=True)
    source_name = "KudaGo" if event.source_id == "kudago" else event.source_id
    source_name = escape(source_name or "сайт события")
    return f'🔗 <a href="{source_url}">Источник: {source_name}</a>'


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
    SELECT e.*, i.status, i.visible, i.visibility_asked
    FROM intents i
    JOIN events e ON e.id = i.event_id
    WHERE i.user_id = ?
"""


def _row_to_intent(row: sqlite3.Row) -> UserIntent:
    """Строка запроса выше -> модель UserIntent."""
    return UserIntent(
        # в row лежат и колонки события, и статус с видимостью
        event=_row_to_event(row),
        status=row["status"],
        # 1/0 из SQLite обратно в True/False
        visible=bool(row["visible"]),
        visibility_asked=bool(row["visibility_asked"]),
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
