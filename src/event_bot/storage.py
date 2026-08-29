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
    # Все реплики текущего диалога уточнения. Повторная обработка полного
    # контекста не теряет уже названные интересы, бюджет или дни.
    draft_inputs: dict[int, list[str]] = field(default_factory=dict)
    # Поле, про которое бот спросил последним: ответ может быть «неважно»,
    # поэтому сам факт ответа хранится отдельно от nullable-поля Profile.
    awaiting_clarification: dict[int, str] = field(default_factory=dict)
    clarified_fields: dict[int, set[str]] = field(default_factory=dict)

    def clear_clarification(self, user_id: int) -> None:
        """Завершает или отменяет накопленный диалог уточнения."""
        self.draft_inputs.pop(user_id, None)
        self.awaiting_clarification.pop(user_id, None)
        self.clarified_fields.pop(user_id, None)

    def discard_draft(self, user_id: int) -> None:
        """Удаляет черновик вместе с контекстом уточняющих ответов."""
        self.drafts.pop(user_id, None)
        self.clear_clarification(user_id)
