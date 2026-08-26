from dataclasses import dataclass, field

from event_bot.models import Profile


@dataclass
class ProfileStore:
    """Профили в оперативной памяти: кеш поверх SQLite.

    Экземпляр создаётся один раз в app.py и живёт, пока работает бот;
    после перезапуска оба словаря пустые и данные берутся из базы.
    """

    # распознанные, но ещё не подтверждённые профили

    drafts: dict[int, Profile] = field(default_factory=dict)
    # подтверждённые кнопкой «Верно»; они же лежат в таблице users
    confirmed: dict[int, Profile] = field(default_factory=dict)
