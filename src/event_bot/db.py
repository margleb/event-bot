# src/event_bot/db.py
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Iterator

from event_bot.models import (
    Companion,
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
                price_min   INTEGER,
                price_max   INTEGER,
                tags        TEXT NOT NULL DEFAULT '[]',
                venue       TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
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
        _add_column_if_missing(
            conn, "intents", "visibility_asked", "INTEGER NOT NULL DEFAULT 0"
        )

        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telegram_id "
            "ON users(telegram_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_city_date "
            "ON events(city, date)"
        )
        # Уникальность «название + дата» защищает от дублей при повторном
        # наполнении демо-данными: INSERT OR IGNORE опирается на этот индекс
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_title_date "
            "ON events(title, date)"
        )


def save_user_profile(telegram_id: int, profile: Profile, name: str) -> None:
    """Сохраняет профиль пользователя (UPSERT по telegram_id).

    name — first_name из Telegram: имя нужно, чтобы участнику события
    было что показать другим.
    """
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO users (telegram_id, name, interests, avoid, days,
                               budget_rub, group_size_min, group_size_max)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            -- UPSERT: если строка с таким telegram_id уже есть,
            -- вместо ошибки обновляем её поля (excluded — то, что пытались
            -- вставить)
            ON CONFLICT(telegram_id) DO UPDATE SET
                name           = excluded.name,
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
        price_min=row["price_min"],
        price_max=row["price_max"],
        tags=json.loads(row["tags"]),
        venue=row["venue"],
    )


def _score(event: Event, interests: set[str], avoid: set[str]) -> int:
    """Насколько событие подходит: совпадения тегов минус нежелательные."""
    tags = {tag.lower() for tag in event.tags}
    # & — пересечение множеств, то есть общие теги
    return len(interests & tags) - len(avoid & tags)


def find_events(profile: Profile) -> list[Event]:
    """Подбирает до 5 будущих московских мероприятий под профиль."""
    # Запрос собираем по частям: условие по бюджету добавляется,
    # только если бюджет вообще указан в профиле
    query = [
        "SELECT * FROM events",
        # только будущие события
        "WHERE city = 'Москва' AND date > datetime('now', 'localtime')",
    ]
    params: list[object] = []

    if profile.budget_rub is not None:
        # цена не выше бюджета; 0 и NULL означают «бесплатно»
        query.append(
            "AND COALESCE(price_min, 0) <= ?"
            " AND (price_max <= ? OR price_max IS NULL OR price_max = 0)"
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


def format_event_card(event: Event, index: int) -> str:
    """Карточка мероприятия для отправки в Telegram (parse_mode=HTML)."""
    # weekday(): 0 — понедельник, отсюда порядок в WEEKDAYS_RU
    weekday = WEEKDAYS_RU[event.date.weekday()]
    when = f"{event.date.strftime('%d.%m.%Y %H:%M')} ({weekday})"
    tags = ", ".join(event.tags) if event.tags else "—"

    # escape() экранирует < > & в тексте события: иначе Telegram примет
    # их за разметку и не отправит сообщение
    return (
        f"🎯 <b>{index}. {escape(event.title)}</b>\n"
        f"📍 {escape(event.venue)}\n"
        f"🏙️ {escape(event.city)}, {escape(event.address)}\n"
        f"📅 {when}\n"
        f"💰 {escape(event.get_price_display())}\n"
        f"🏷️ {escape(tags)}\n\n"
        f"📝 {escape(event.description)}"
    )


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

    return (
        f"🎯 <b>{escape(event.title)}</b>\n"
        f"📅 {event.date.strftime('%d.%m.%Y %H:%M')} ({weekday})\n"
        f"👀 {status} · {visibility}"
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
            SELECT u.name, u.interests, u.group_size_min, u.group_size_max
            FROM intents i
            JOIN users u ON u.telegram_id = i.user_id
            WHERE i.event_id = ?
              AND i.status IN ({statuses})
              -- показываем только тех, кто сам разрешил себя показывать
              AND i.visible = 1
              -- себя самого в списке не показываем
              AND i.user_id != ?
            -- скоринга нет, нужен просто стабильный порядок
            ORDER BY i.updated_at, i.user_id
            LIMIT ?
            """,
            (event_id, *PARTICIPATING_STATUSES, user_id, MAX_COMPANIONS),
        ).fetchall()

    # интересы смотрящего — для пересечения; регистр не важен
    mine = {interest.lower() for interest in profile.interests}

    companions = []
    for row in rows:
        their_interests = json.loads(row["interests"])
        companions.append(
            Companion(
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

    Здесь нет и не должно быть username, телефона и telegram id —
    в модель Companion они и не попадают.
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


# Демо-данные: ими наполняется таблица events при первом запуске
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
    # список словарей -> список кортежей в порядке колонок запроса
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
        # executemany выполняет запрос для каждого кортежа,
        # OR IGNORE молча пропускает дубли (см. idx_events_title_date)
        conn.executemany(
            """
            INSERT OR IGNORE INTO events
                (title, description, city, address, date,
                 price_min, price_max, tags, venue)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
