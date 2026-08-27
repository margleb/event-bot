import json
import re
import socket
import time
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from event_bot.models import Event
from event_bot.sources.base import EventSource, SourceFetchError


MOSCOW_TZ = ZoneInfo("Europe/Moscow")
KUDAGO_EVENTS_URL = "https://kudago.com/public-api/v1.4/events/"
KUDAGO_FIELDS = (
    "id",
    "title",
    "dates",
    "place",
    "description",
    "price",
    "is_free",
    "categories",
    "site_url",
)

# Слаги получены из /public-api/v1.4/event-categories/. Значения — русские
# интересы в том же регистронезависимом формате, который сравнивает find_events.
CATEGORY_TAGS: dict[str, tuple[str, ...]] = {
    "business-events": ("бизнес",),
    "cinema": ("кино",),
    "concert": ("музыка", "концерт"),
    "education": ("образование", "обучение"),
    "entertainment": ("развлечения",),
    "exhibition": ("выставка", "искусство"),
    "fashion": ("мода",),
    "festival": ("фестиваль",),
    "holiday": ("праздник",),
    "kids": ("дети",),
    "other": ("разное",),
    "party": ("вечеринка", "музыка", "танцы"),
    "photo": ("фотография",),
    "quest": ("квест",),
    "recreation": ("активный отдых", "спорт"),
    "shopping": ("шопинг",),
    "social-activity": ("благотворительность",),
    "stock": ("акции", "скидки"),
    "theater": ("театр", "спектакль"),
    "tour": ("экскурсия", "прогулка", "история"),
    "wellness-and-health": ("здоровье", "красота"),
    "yarmarki-razvlecheniya-yarmarki": ("ярмарка",),
}

_PRICE_NUMBER_RE = re.compile(
    r"(?<!\d)(?:\d{1,3}(?:[\s\u00a0]\d{3})+|\d+)(?!\d)"
)


class KudaGoSource(EventSource):
    """Постраничный клиент и нормализатор публичного KudaGo API."""

    source_id = "kudago"

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        retries: int = 2,
        page_pause: float = 0.25,
        retry_pause: float = 0.5,
        now: datetime | None = None,
        opener: Callable[..., Any] = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.page_pause = page_pause
        self.retry_pause = retry_pause
        self.now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.opener = opener
        self.sleeper = sleeper
        self.fetched_at = self.now.isoformat(timespec="seconds")

    def _request_page(self, url: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with self.opener(url, timeout=self.timeout) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict):
                    raise SourceFetchError("KudaGo вернул ответ неверного формата")
                return payload
            except HTTPError as error:
                if error.code < 500:
                    raise SourceFetchError(
                        f"KudaGo API ответил HTTP {error.code}"
                    ) from error
                last_error = error
            except (URLError, TimeoutError, socket.timeout, OSError) as error:
                last_error = error
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise SourceFetchError("KudaGo вернул некорректный JSON") from error

            if attempt < self.retries:
                self.sleeper(self.retry_pause * (2**attempt))

        raise SourceFetchError(
            f"KudaGo API недоступен после {self.retries + 1} попыток: "
            f"{last_error}"
        ) from last_error

    def fetch(self) -> list[Any]:
        until = self.now + timedelta(days=30)
        base_params: dict[str, str | int] = {
            "lang": "ru",
            "location": "msk",
            "actual_since": int(self.now.timestamp()),
            "actual_until": int(until.timestamp()),
            "fields": ",".join(KUDAGO_FIELDS),
            # dates без expand уже содержит нужные UNIX start/end. Раскрываем
            # только place, чтобы получить название и адрес без тяжёлых schedules.
            "expand": "place",
            "text_format": "text",
            "page_size": 100,
        }

        records: list[Any] = []
        page = 1
        while True:
            url = f"{KUDAGO_EVENTS_URL}?{urlencode({**base_params, 'page': page})}"
            payload = self._request_page(url)
            results = payload.get("results")
            if not isinstance(results, list):
                raise SourceFetchError("В ответе KudaGo отсутствует список results")
            records.extend(results)

            if not payload.get("next"):
                break
            page += 1
            if page > 100:
                raise SourceFetchError("KudaGo вернул больше 100 страниц")
            self.sleeper(self.page_pause)

        if not records:
            raise SourceFetchError("KudaGo не вернул ни одного события")
        return records

    @staticmethod
    def _plain_text(value: object) -> str:
        if not isinstance(value, str):
            return ""
        return " ".join(unescape(value).split())

    @staticmethod
    def _price_bounds(price: str, is_free: bool) -> tuple[int | None, int | None]:
        if is_free:
            return 0, 0
        values = [
            int(match.group(0).replace(" ", "").replace("\u00a0", ""))
            for match in _PRICE_NUMBER_RE.finditer(price)
        ]
        if not values:
            return None, None
        return min(values), max(values)

    @staticmethod
    def _category_tags(categories: object) -> list[str]:
        if not isinstance(categories, list):
            return []
        tags: list[str] = []
        for category in categories:
            if isinstance(category, dict):
                slug = category.get("slug")
            else:
                slug = category
            if not isinstance(slug, str):
                continue
            for tag in CATEGORY_TAGS.get(slug, (slug,)):
                if tag not in tags:
                    tags.append(tag)
        return tags

    def normalize(self, raw: dict[str, Any]) -> Event | None:
        title = self._plain_text(raw.get("title"))
        source_url = self._plain_text(raw.get("site_url"))
        raw_id = raw.get("id")
        external_id = (
            str(raw_id).strip()
            if isinstance(raw_id, (str, int)) and not isinstance(raw_id, bool)
            else ""
        )
        parsed_url = urlparse(source_url)
        valid_url = (
            parsed_url.scheme in {"http", "https"}
            and parsed_url.hostname is not None
            and (
                parsed_url.hostname == "kudago.com"
                or parsed_url.hostname.endswith(".kudago.com")
            )
        )
        if not title or not source_url or not valid_url or not external_id:
            return None

        dates = raw.get("dates")
        if not isinstance(dates, list):
            return None
        valid_dates = [
            item
            for item in dates
            if isinstance(item, dict) and isinstance(item.get("start"), (int, float))
        ]
        if not valid_dates:
            return None

        now_timestamp = self.now.timestamp()
        future_dates = [item for item in valid_dates if item["start"] >= now_timestamp]
        selected = min(future_dates or valid_dates, key=lambda item: item["start"])
        starts_at = datetime.fromtimestamp(selected["start"], timezone.utc)
        starts_at = starts_at.astimezone(MOSCOW_TZ).replace(tzinfo=None)

        end_timestamp = selected.get("end")
        if not isinstance(end_timestamp, (int, float)):
            end_timestamp = selected["start"]
        ends_at = datetime.fromtimestamp(end_timestamp, timezone.utc)
        ends_at = ends_at.astimezone(MOSCOW_TZ).replace(tzinfo=None)
        status = "past" if end_timestamp < now_timestamp else "active"

        place = raw.get("place") if isinstance(raw.get("place"), dict) else {}
        venue = self._plain_text(place.get("title"))
        address = self._plain_text(place.get("address"))
        description = self._plain_text(raw.get("description"))
        # Цена у KudaGo — произвольная строка: сохраняем её без преобразований.
        raw_price = raw.get("price")
        price = raw_price if isinstance(raw_price, str) else ""
        is_free = raw.get("is_free") is True
        price_min, price_max = self._price_bounds(price, is_free)

        return Event(
            title=title,
            description=description,
            city="Москва",
            address=address,
            date=starts_at,
            end_date=ends_at,
            price_min=price_min,
            price_max=price_max,
            price_text=price or None,
            is_free=is_free,
            tags=self._category_tags(raw.get("categories")),
            venue=venue,
            source_id=self.source_id,
            external_id=external_id,
            source_url=source_url,
            fetched_at=self.fetched_at,
            status=status,
        )
