from abc import ABC, abstractmethod
from typing import Any

from event_bot.models import Event


class SourceFetchError(RuntimeError):
    """Источник не удалось получить целиком, импорт нельзя начинать."""


class EventSource(ABC):
    """Контракт внешнего каталога мероприятий."""

    source_id: str

    @abstractmethod
    def fetch(self) -> list[Any]:
        """Получить все сырые записи нужного временного окна."""

    @abstractmethod
    def normalize(self, raw: dict[str, Any]) -> Event | None:
        """Привести запись к общей модели или отклонить негодную."""
