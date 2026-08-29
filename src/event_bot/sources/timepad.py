import json
import math
import re
import socket
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from event_bot.models import Event
from event_bot.sources.base import EventSource, SourceFetchError


TIMEPAD_EVENTS_URL = "https://api.timepad.ru/v1/events.json"
TIMEPAD_USER_AGENT = "event-bot/1.0 (+https://bot-ams.margleb.ru)"
TIMEPAD_FIELDS = (
    "id",
    "starts_at",
    "ends_at",
    "name",
    "description_short",
    "description_html",
    "url",
    "location",
    "organization",
    "categories",
    "registration_data",
)
MOSCOW_TZ = ZoneInfo("Europe/Moscow")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


class TimepadSource(EventSource):
    """Московская афиша Timepad на ближайшие 30 дней через официальный API."""

    source_id = "timepad"

    def __init__(
        self,
        api_token: str,
        *,
        timeout: float = 15.0,
        retries: int = 2,
        page_pause: float = 0.2,
        retry_pause: float = 0.5,
        now: datetime | None = None,
        opener: Callable[..., Any] = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_token.strip():
            raise ValueError("Timepad API token не задан")
        self.api_token = api_token.strip()
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
                request = Request(
                    url,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {self.api_token}",
                        # Timepad rejects urllib's default user agent at its WAF.
                        "User-Agent": TIMEPAD_USER_AGENT,
                    },
                )
                with self.opener(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict):
                    raise SourceFetchError("Timepad вернул ответ неверного формата")
                return payload
            except HTTPError as error:
                if error.code < 500:
                    raise SourceFetchError(
                        f"Timepad API ответил HTTP {error.code}"
                    ) from error
                last_error = error
            except (URLError, TimeoutError, socket.timeout, OSError) as error:
                last_error = error
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise SourceFetchError("Timepad вернул некорректный JSON") from error

            if attempt < self.retries:
                self.sleeper(self.retry_pause * (2**attempt))
        raise SourceFetchError(
            f"Timepad API недоступен после {self.retries + 1} попыток: "
            f"{last_error}"
        ) from last_error

    def fetch(self) -> list[Any]:
        until = self.now + timedelta(days=30)
        page_size = 100
        base_params: dict[str, str | int] = {
            "fields": ",".join(TIMEPAD_FIELDS),
            "limit": page_size,
            "cities": "Москва",
            "starts_at_min": self.now.isoformat(timespec="seconds"),
            "starts_at_max": until.isoformat(timespec="seconds"),
            "sort": "+starts_at",
        }
        records: list[Any] = []
        skip = 0
        for _ in range(100):
            url = f"{TIMEPAD_EVENTS_URL}?{urlencode({**base_params, 'skip': skip})}"
            payload = self._request_page(url)
            values = payload.get("values")
            if not isinstance(values, list):
                raise SourceFetchError("Timepad не вернул список values")
            records.extend(values)
            total = payload.get("total")
            skip += len(values)
            if not values or len(values) < page_size:
                break
            if isinstance(total, int) and skip >= total:
                break
            self.sleeper(self.page_pause)
        else:
            raise SourceFetchError("Timepad вернул больше 100 страниц")
        return records

    @staticmethod
    def _plain_text(value: object) -> str:
        if not isinstance(value, str):
            return ""
        return " ".join(unescape(_HTML_TAG_RE.sub(" ", value)).split())

    @staticmethod
    def _date(value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=MOSCOW_TZ)
        return parsed.astimezone(MOSCOW_TZ).replace(tzinfo=None)

    @staticmethod
    def _price(value: object) -> int | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(value) or value < 0:
            return None
        return int(value)

    def normalize(self, raw: dict[str, Any]) -> Event | None:
        raw_id = raw.get("id")
        external_id = (
            str(raw_id).strip()
            if isinstance(raw_id, (str, int)) and not isinstance(raw_id, bool)
            else ""
        )
        title = self._plain_text(raw.get("name"))
        source_url = self._plain_text(raw.get("url"))
        parsed_url = urlparse(source_url)
        valid_url = (
            parsed_url.scheme == "https"
            and parsed_url.hostname is not None
            and (
                parsed_url.hostname == "timepad.ru"
                or parsed_url.hostname.endswith(".timepad.ru")
            )
        )
        starts_at = self._date(raw.get("starts_at"))
        if not external_id or not title or not valid_url or starts_at is None:
            return None

        location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
        city = self._plain_text(location.get("city"))
        if city.casefold() not in {"москва", "moscow"}:
            return None
        ends_at = self._date(raw.get("ends_at"))
        now_msk = self.now.astimezone(MOSCOW_TZ).replace(tzinfo=None)
        effective_end = ends_at or starts_at

        registration = (
            raw.get("registration_data")
            if isinstance(raw.get("registration_data"), dict)
            else {}
        )
        price_min = self._price(registration.get("price_min"))
        price_max = self._price(registration.get("price_max"))
        is_free = (
            price_max == 0
            if price_min is not None and price_max is not None
            else None
        )
        if is_free:
            price_text = "Бесплатно"
        elif price_min is not None and price_max is not None:
            price_text = (
                f"{price_min} ₽"
                if price_min == price_max
                else f"{price_min}–{price_max} ₽"
            )
        else:
            price_text = None

        categories = raw.get("categories")
        tags: list[str] = []
        if isinstance(categories, list):
            for category in categories:
                name = self._plain_text(
                    category.get("name") if isinstance(category, dict) else category
                ).casefold()
                if name and name not in tags:
                    tags.append(name)

        organization = (
            raw.get("organization")
            if isinstance(raw.get("organization"), dict)
            else {}
        )
        description = self._plain_text(raw.get("description_short"))
        if not description:
            description = self._plain_text(raw.get("description_html"))
        return Event(
            title=title,
            description=description,
            city="Москва",
            address=self._plain_text(location.get("address")),
            date=starts_at,
            end_date=ends_at,
            price_min=price_min,
            price_max=price_max,
            price_text=price_text,
            is_free=is_free,
            tags=tags,
            venue=self._plain_text(organization.get("name")),
            source_id=self.source_id,
            external_id=external_id,
            source_url=source_url,
            fetched_at=self.fetched_at,
            status="past" if effective_end < now_msk else "active",
        )
