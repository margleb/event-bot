import io
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

from event_bot.import_events import configured_sources
from event_bot.sources.ticketmaster import TicketmasterSource
from event_bot.sources.timepad import TIMEPAD_FIELDS, TIMEPAD_USER_AGENT, TimepadSource


class JsonResponse(io.BytesIO):
    def __init__(self, payload: object) -> None:
        super().__init__(json.dumps(payload).encode())

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def test_timepad_fetch_uses_token_and_documented_filters():
    calls: list[Request] = []

    def opener(request: Request, *, timeout: float):
        assert timeout == 7
        calls.append(request)
        return JsonResponse({"total": 1, "values": [{"id": 1}]})

    source = TimepadSource(
        "secret-token",
        now=datetime(2026, 8, 29, 9, tzinfo=timezone.utc),
        timeout=7,
        opener=opener,
        sleeper=lambda _: None,
    )

    assert source.fetch() == [{"id": 1}]
    request = calls[0]
    assert request.get_header("Authorization") == "Bearer secret-token"
    assert request.get_header("User-agent") == TIMEPAD_USER_AGENT
    query = parse_qs(urlparse(request.full_url).query)
    assert query["cities"] == ["Москва"]
    assert query["limit"] == ["100"]
    assert query["fields"] == [",".join(TIMEPAD_FIELDS)]
    assert "starts_at_min" in query and "starts_at_max" in query


def test_timepad_normalizes_event_and_strips_html():
    source = TimepadSource(
        "token",
        now=datetime(2026, 8, 29, 9, tzinfo=timezone.utc),
    )
    event = source.normalize(
        {
            "id": 501,
            "name": "Лекция &amp; дискуссия",
            "starts_at": "2026-09-01T19:00:00+03:00",
            "ends_at": "2026-09-01T21:00:00+03:00",
            "description_short": "<b>Разговор</b> об искусстве",
            "url": "https://example.timepad.ru/event/501/",
            "location": {"city": "Москва", "address": "Тверская, 1"},
            "organization": {"name": "Лекторий"},
            "categories": [{"name": "Искусство и культура"}],
            "registration_data": {"price_min": 500, "price_max": 1000},
        }
    )

    assert event is not None
    assert event.title == "Лекция & дискуссия"
    assert event.description == "Разговор об искусстве"
    assert event.date == datetime(2026, 9, 1, 19)
    assert (event.price_min, event.price_max) == (500, 1000)
    assert event.price_text == "500–1000 ₽"
    assert event.tags == ["искусство и культура"]
    assert event.source_id == "timepad"


def test_ticketmaster_normalizes_moscow_event():
    source = TicketmasterSource(
        "api-key",
        now=datetime(2026, 8, 29, 9, tzinfo=timezone.utc),
    )
    event = source.normalize(
        {
            "id": "tm-1",
            "name": "Большой концерт",
            "url": "https://www.ticketmaster.com/event/tm-1",
            "info": "Музыкальная программа",
            "dates": {
                "start": {"dateTime": "2026-09-02T16:00:00Z"},
                "status": {"code": "onsale"},
            },
            "_embedded": {
                "venues": [
                    {
                        "name": "Арена",
                        "city": {"name": "Moscow"},
                        "address": {"line1": "Проспект, 1"},
                    }
                ]
            },
            "priceRanges": [{"currency": "RUB", "min": 1200, "max": 3500}],
            "classifications": [
                {
                    "segment": {"name": "Music"},
                    "genre": {"name": "Rock"},
                }
            ],
        }
    )

    assert event is not None
    assert event.date == datetime(2026, 9, 2, 19)
    assert event.venue == "Арена"
    assert event.price_text == "1200–3500 ₽"
    assert event.tags == ["music", "rock"]
    assert event.source_id == "ticketmaster"


def test_configured_sources_enable_only_credentials_present(monkeypatch):
    monkeypatch.delenv("TIMEPAD_API_TOKEN", raising=False)
    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)
    assert [source.source_id for source in configured_sources()] == ["kudago"]

    monkeypatch.setenv("TIMEPAD_API_TOKEN", "timepad-token")
    monkeypatch.setenv("TICKETMASTER_API_KEY", "ticketmaster-key")
    assert [source.source_id for source in configured_sources()] == [
        "kudago",
        "timepad",
        "ticketmaster",
    ]
