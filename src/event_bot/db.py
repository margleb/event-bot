# src/event_bot/db.py
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Iterator

from event_bot.models import Event, Profile

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "bot.db"

# Формат, в котором даты лежат в SQLite: он сравним с datetime('now')
DB_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

MAX_EVENTS = 5

WEEKDAYS_RU = ("понедельник", "вторник", "среда", "четверг",
               "пятница", "суббота", "воскресенье")


@contextmanager
def get_connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Соединение с SQLite: коммитит при успехе, откатывает при ошибке."""
    db_path = db_path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Создаёт таблицы и индексы, если их ещё нет."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id    INTEGER PRIMARY KEY,
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
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                description TEXT NOT NULL,
                city        TEXT NOT NULL,
                address     TEXT NOT NULL,
                date        TEXT NOT NULL,
                price_min   INTEGER,
                price_max   INTEGER,
                tags        TEXT NOT NULL DEFAULT '[]',
                venue       TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telegram_id "
            "ON users(telegram_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_city_date "
            "ON events(city, date)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_title_date "
            "ON events(title, date)"
        )


def save_user_profile(telegram_id: int, profile: Profile) -> None:
    """Сохраняет профиль пользователя (UPSERT по telegram_id)."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO users (telegram_id, interests, avoid, days,
                               budget_rub, group_size_min, group_size_max)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                interests      = excluded.interests,
                avoid          = excluded.avoid,
                days           = excluded.days,
                budget_rub     = excluded.budget_rub,
                group_size_min = excluded.group_size_min,
                group_size_max = excluded.group_size_max
            """,
            (
                telegram_id,
                json.dumps(profile.interests, ensure_ascii=False),
                json.dumps(profile.avoid, ensure_ascii=False),
                json.dumps(profile.days or [], ensure_ascii=False),
                profile.budget_rub,
                profile.preferred_group_size_min,
                profile.preferred_group_size_max,
            ),
        )


def get_user_profile(telegram_id: int) -> Profile | None:
    """Возвращает сохранённый профиль или None, если его нет."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()

    if row is None:
        return None

    return Profile(
        interests=json.loads(row["interests"]),
        avoid=json.loads(row["avoid"]),
        days=json.loads(row["days"]) or None,
        budget_rub=row["budget_rub"],
        preferred_group_size_min=row["group_size_min"],
        preferred_group_size_max=row["group_size_max"],
    )


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        city=row["city"],
        address=row["address"],
        date=datetime.strptime(row["date"], DB_DATETIME_FORMAT),
        price_min=row["price_min"],
        price_max=row["price_max"],
        tags=json.loads(row["tags"]),
        venue=row["venue"],
    )


def _score(event: Event, interests: set[str], avoid: set[str]) -> int:
    tags = {tag.lower() for tag in event.tags}
    return len(interests & tags) - len(avoid & tags)


def find_events(profile: Profile) -> list[Event]:
    """Подбирает до 5 будущих московских мероприятий под профиль."""
    query = [
        "SELECT * FROM events",
        "WHERE city = 'Москва' AND date > datetime('now', 'localtime')",
    ]
    params: list[object] = []

    if profile.budget_rub is not None:
        query.append(
            "AND COALESCE(price_min, 0) <= ?"
            " AND (price_max <= ? OR price_max IS NULL OR price_max = 0)"
        )
        params.extend([profile.budget_rub, profile.budget_rub])

    with get_connection() as conn:
        rows = conn.execute(" ".join(query), params).fetchall()

    interests = {tag.lower() for tag in profile.interests}
    avoid = {tag.lower() for tag in profile.avoid}

    events = [_row_to_event(row) for row in rows]
    # При равном score показываем то, что раньше по дате
    events.sort(key=lambda e: (-_score(e, interests, avoid), e.date))
    return events[:MAX_EVENTS]


def format_event_card(event: Event, index: int) -> str:
    """Карточка мероприятия для отправки в Telegram (parse_mode=HTML)."""
    weekday = WEEKDAYS_RU[event.date.weekday()]
    when = f"{event.date.strftime('%d.%m.%Y %H:%M')} ({weekday})"
    tags = ", ".join(event.tags) if event.tags else "—"

    return (
        f"🎯 <b>{index}. {escape(event.title)}</b>\n"
        f"📍 {escape(event.venue)}\n"
        f"🏙️ {escape(event.city)}, {escape(event.address)}\n"
        f"📅 {when}\n"
        f"💰 {escape(event.get_price_display())}\n"
        f"🏷️ {escape(tags)}\n\n"
        f"📝 {escape(event.description)}"
    )


SEED_EVENTS: list[dict] = [
    {
        "title": "Трибьют-концерт «Кино»",
        "description": "Живое исполнение главных песен группы «Кино» "
                       "с большим составом музыкантов.",
        "city": "Москва",
        "address": "ул. Пресненский Вал, 6, стр. 1",
        "date": "2026-09-12 20:00:00",
        "price_min": 2500,
        "price_max": 4500,
        "tags": ["музыка", "концерт", "рок", "живая музыка"],
        "venue": "Клуб 16 Тонн",
    },
    {
        "title": "Выставка современного искусства «Город»",
        "description": "Работы современных художников о городской среде "
                       "и жизни мегаполиса.",
        "city": "Москва",
        "address": "Крымский Вал, 9, стр. 32",
        "date": "2026-09-05 12:00:00",
        "price_min": 500,
        "price_max": 800,
        "tags": ["искусство", "выставка", "культура"],
        "venue": "Музей «Гараж»",
    },
    {
        "title": "Stand-up вечер открытого микрофона",
        "description": "Комики обкатывают новый материал: два часа "
                       "свежих шуток и импровизации.",
        "city": "Москва",
        "address": "ул. Трёхгорный Вал, 6",
        "date": "2026-09-13 20:00:00",
        "price_min": 800,
        "price_max": 1500,
        "tags": ["комедия", "стендап", "вечер", "юмор"],
        "venue": "Stand-Up Store Moscow",
    },
    {
        "title": "Лекция «Космос для каждого»",
        "description": "Астрофизик простым языком рассказывает о чёрных "
                       "дырах, экзопланетах и новых телескопах.",
        "city": "Москва",
        "address": "Садовая-Кудринская ул., 5, стр. 1",
        "date": "2026-09-10 19:00:00",
        "price_min": 0,
        "price_max": 0,
        "tags": ["наука", "лекция", "космос", "образование"],
        "venue": "Московский Планетарий",
    },
    {
        "title": "Вечеринка Retrowave 80s",
        "description": "Синтвейв, неон и танцы до утра: диджей-сет "
                       "из хитов восьмидесятых.",
        "city": "Москва",
        "address": "Берсеневская наб., 6, стр. 3",
        "date": "2026-09-19 23:00:00",
        "price_min": 1000,
        "price_max": 2000,
        "tags": ["вечеринка", "танцы", "музыка", "ночь"],
        "venue": "Клуб Gipsy",
    },
    {
        "title": "Мастер-класс по гончарному делу",
        "description": "Первое знакомство с гончарным кругом: лепим чашку "
                       "и забираем её после обжига. Материалы включены.",
        "city": "Москва",
        "address": "ул. Покровка, 31",
        "date": "2026-09-07 14:00:00",
        "price_min": 3000,
        "price_max": 3000,
        "tags": ["мастер-класс", "творчество", "керамика", "хобби"],
        "venue": "Керамическая студия «Глина»",
    },
    {
        "title": "Показ авторского кино и обсуждение",
        "description": "Фильм-лауреат фестивальной программы и разговор "
                       "с киноведом после показа.",
        "city": "Москва",
        "address": "ул. Покровка, 47",
        "date": "2026-09-15 19:30:00",
        "price_min": 400,
        "price_max": 700,
        "tags": ["кино", "культура", "искусство"],
        "venue": "Кинотеатр «Иллюзион»",
    },
    {
        "title": "Йога на крыше",
        "description": "Утренняя практика на открытой площадке с видом "
                       "на город. Подходит новичкам.",
        "city": "Москва",
        "address": "Пресненская наб., 12",
        "date": "2026-09-06 08:00:00",
        "price_min": 1200,
        "price_max": 1800,
        "tags": ["йога", "спорт", "здоровье", "утро"],
        "venue": "Roof Place",
    },
    {
        "title": "Дегустация грузинских вин",
        "description": "Восемь сортов вина с закусками и рассказом "
                       "сомелье о регионах Грузии.",
        "city": "Москва",
        "address": "ул. Мясницкая, 15",
        "date": "2026-09-20 18:00:00",
        "price_min": 3500,
        "price_max": 3500,
        "tags": ["вино", "дегустация", "еда", "вечер"],
        "venue": "Винный бар Big Wine Freaks",
    },
    {
        "title": "Хакатон по искусственному интеллекту",
        "description": "48 часов на прототип AI-продукта в команде, "
                       "менторы и питч перед жюри.",
        "city": "Москва",
        "address": "Ленинградский пр-т, 36, стр. 11",
        "date": "2026-09-21 10:00:00",
        "price_min": 0,
        "price_max": 0,
        "tags": ["технологии", "хакатон", "программирование", "ai"],
        "venue": "Технопарк «Мосхаб»",
    },
    {
        "title": "Фестиваль уличной еды",
        "description": "Тридцать фудтраков, лекторий о еде и живая музыка "
                       "на весь день.",
        "city": "Москва",
        "address": "ул. Крымский Вал, 9",
        "date": "2026-09-14 12:00:00",
        "price_min": 0,
        "price_max": 0,
        "tags": ["еда", "фестиваль", "отдых", "музыка"],
        "venue": "Парк Горького",
    },
    {
        "title": "Спектакль-импровизация",
        "description": "Актёры играют историю по подсказкам зала — "
                       "второго такого показа не будет.",
        "city": "Москва",
        "address": "Малый Гнездниковский пер., 10",
        "date": "2026-09-22 19:30:00",
        "price_min": 1800,
        "price_max": 2800,
        "tags": ["театр", "импровизация", "комедия", "вечер"],
        "venue": "Учебный театр ГИТИС",
    },
    {
        "title": "Экскурсия по крышам Замоскворечья",
        "description": "Прогулка с гидом по историческим кварталам "
                       "и подъём на смотровую площадку.",
        "city": "Москва",
        "address": "ул. Пятницкая, 18",
        "date": "2026-09-08 18:00:00",
        "price_min": 900,
        "price_max": 1400,
        "tags": ["экскурсия", "прогулка", "история", "город"],
        "venue": "Точка сбора у метро «Новокузнецкая»",
    },
    {
        "title": "Настольные игры: вечер знакомств",
        "description": "Ведущий рассадит по столам и объяснит правила — "
                       "приходить одному нормально.",
        "city": "Москва",
        "address": "ул. Большая Дмитровка, 32",
        "date": "2026-09-11 19:00:00",
        "price_min": 500,
        "price_max": 900,
        "tags": ["настольные игры", "знакомства", "вечер", "общение"],
        "venue": "Антикафе «Циферблат»",
    },
]


def seed_events() -> None:
    """Наполняет таблицу events демо-данными. Повторный вызов безопасен."""
    rows = [
        (
            event["title"],
            event["description"],
            event["city"],
            event["address"],
            event["date"],
            event["price_min"],
            event["price_max"],
            json.dumps(event["tags"], ensure_ascii=False),
            event["venue"],
        )
        for event in SEED_EVENTS
    ]

    with get_connection() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO events
                (title, description, city, address, date,
                 price_min, price_max, tags, venue)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
