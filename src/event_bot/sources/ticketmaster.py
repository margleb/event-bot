import json
import math
import socket
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from event_bot.models import Event
from event_bot.sources.base import EventSource, SourceFetchError


TICKETMASTER_EVENTS_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


class TicketmasterSource(EventSource):
    """Опциональная московская выборка Ticketmaster Discovery API."""

    source_id = "ticketmaster"

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 15.0,
        retries: int = 2,
        page_pause: float = 0.2,
        retry_pause: float = 0.5,
        now: datetime | None = None,
        opener: Callable[..., Any] = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Ticketmaster API key не задан")
        self.api_key = api_key.strip()
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
                    raise SourceFetchError(
                        "Ticketmaster вернул ответ неверного формата"
                    )
                return payload
            except HTTPError as error:
                if error.code < 500:
                    raise SourceFetchError(
                        f"Ticketmaster API ответил HTTP {error.code}"
                    ) from error
                last_error = error
            except (URLError, TimeoutError, socket.timeout, OSError) as error:
                last_error = error
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise SourceFetchError(
                    "Ticketmaster вернул некорректный JSON"
                ) from error
            if attempt < self.retries:
                self.sleeper(self.retry_pause * (2**attempt))
        raise SourceFetchError(
            "Ticketmaster API недоступен после "
            f"{self.retries + 1} попыток: {last_error}"
        ) from last_error

    def fetch(self) -> list[Any]:
        until = self.now + timedelta(days=30)
        base_params: dict[str, str | int] = {
            "apikey": self.api_key,
            "city": "Moscow",
            "countryCode": "RU",
            "startDateTime": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDateTime": until.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "size": 200,
            "sort": "date,asc",
            "locale": "*",
        }
        records: list[Any] = []
        for page in range(5):
            url = f"{TICKETMASTER_EVENTS_URL}?{urlencode({**base_params, 'page': page})}"
            payload = self._request_page(url)
            embedded = payload.get("_embedded")
            values = embedded.get("events", []) if isinstance(embedded, dict) else []
            if not isinstance(values, list):
                raise SourceFetchError("Ticketmaster не вернул список events")
            records.extend(values)
            page_info = payload.get("page")
            total_pages = (
                page_info.get("totalPages") if isinstance(page_info, dict) else None
            )
            if not values or not isinstance(total_pages, int) or page + 1 >= total_pages:
                break
            self.sleeper(self.page_pause)
        return records

    @staticmethod
    def _text(value: object) -> str:
        return " ".join(value.split()) if isinstance(value, str) else ""

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
    def _number(value: object) -> int | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(value) or value < 0:
            return None
        return int(value)

    def normalize(self, raw: dict[str, Any]) -> Event | None:
        external_id = self._text(raw.get("id"))
        title = self._text(raw.get("name"))
        source_url = self._text(raw.get("url"))
        parsed_url = urlparse(source_url)
        dates = raw.get("dates") if isinstance(raw.get("dates"), dict) else {}
        start = dates.get("start") if isinstance(dates.get("start"), dict) else {}
        starts_at = self._date(start.get("dateTime"))
        if starts_at is None and isinstance(start.get("localDate"), str):
            local = f"{start['localDate']}T{start.get('localTime', '00:00:00')}"
            starts_at = self._date(local)
        if (
            not external_id
            or not title
            or parsed_url.scheme != "https"
            or parsed_url.hostname is None
            or starts_at is None
        ):
            return None

        end = dates.get("end") if isinstance(dates.get("end"), dict) else {}
        ends_at = self._date(end.get("dateTime"))
        venues = raw.get("_embedded")
        venues = venues.get("venues", []) if isinstance(venues, dict) else []
        venue = venues[0] if isinstance(venues, list) and venues else {}
        venue = venue if isinstance(venue, dict) else {}
        city = venue.get("city") if isinstance(venue.get("city"), dict) else {}
        address = (
            venue.get("address") if isinstance(venue.get("address"), dict) else {}
        )

        price_ranges = raw.get("priceRanges")
        rub_ranges = [
            item
            for item in price_ranges
            if isinstance(item, dict) and item.get("currency") == "RUB"
        ] if isinstance(price_ranges, list) else []
        minima = [self._number(item.get("min")) for item in rub_ranges]
        maxima = [self._number(item.get("max")) for item in rub_ranges]
        valid_minima = [value for value in minima if value is not None]
        valid_maxima = [value for value in maxima if value is not None]
        price_min = min(valid_minima) if valid_minima else None
        price_max = max(valid_maxima) if valid_maxima else None
        is_free = price_max == 0 if price_max is not None else None
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

        tags: list[str] = []
        classifications = raw.get("classifications")
        if isinstance(classifications, list):
            for classification in classifications:
                if not isinstance(classification, dict):
                    continue
                for field in ("segment", "genre", "subGenre"):
                    item = classification.get(field)
                    name = self._text(item.get("name") if isinstance(item, dict) else None)
                    if name and name.casefold() not in tags:
                        tags.append(name.casefold())

        status_data = dates.get("status") if isinstance(dates.get("status"), dict) else {}
        status_code = self._text(status_data.get("code")).casefold()
        now_msk = self.now.astimezone(MOSCOW_TZ).replace(tzinfo=None)
        effective_end = ends_at or starts_at
        status = (
            "cancelled"
            if status_code in {"cancelled", "canceled"}
            else "past"
            if effective_end < now_msk
            else "active"
        )
        description = self._text(raw.get("info")) or self._text(raw.get("pleaseNote"))
        return Event(
            title=title,
            description=description,
            city="Москва",
            address=self._text(address.get("line1")),
            date=starts_at,
            end_date=ends_at,
            price_min=price_min,
            price_max=price_max,
            price_text=price_text,
            is_free=is_free,
            tags=tags,
            venue=self._text(venue.get("name")),
            source_id=self.source_id,
            external_id=external_id,
            source_url=source_url,
            fetched_at=self.fetched_at,
            status=status,
        )
