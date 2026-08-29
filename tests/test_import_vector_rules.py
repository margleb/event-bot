from datetime import datetime, timezone

import pytest

import event_bot.db as db
from event_bot.embed_events import embed_events
from event_bot.import_events import run_import
from event_bot.models import Event
from event_bot.sources.base import EventSource
from event_bot.sources.kudago import KudaGoSource


class StaticSource(EventSource):
    source_id = "static"

    def __init__(self, event: Event) -> None:
        self.event = event

    def fetch(self) -> list[object]:
        return [{"event": self.event}]

    def normalize(self, raw: dict) -> Event | None:
        return raw["event"]


def _source_event() -> Event:
    return Event(
        title="Импортируемое событие",
        description="Описание",
        city="Москва",
        address="Адрес",
        date=datetime(2099, 1, 1, 19, 0),
        price_min=0,
        price_max=0,
        is_free=True,
        tags=["музыка"],
        venue="Клуб",
        source_id="static",
        external_id="stable-id",
        source_url="https://example.com/stable-id",
        status="active",
    )


def test_repeated_import_does_not_create_duplicates(temp_db):
    source = StaticSource(_source_event())

    first = run_import(source)
    second = run_import(source)

    assert (first.added, first.updated, first.errors) == (1, 0, 0)
    assert (second.added, second.updated, second.errors) == (0, 1, 0)
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_records_without_date_or_source_url_are_skipped_without_crash(temp_db):
    source = KudaGoSource(
        now=datetime(2026, 8, 28, tzinfo=timezone.utc),
        sleeper=lambda _: None,
    )
    good = {
        "id": 1,
        "title": "Событие",
        "dates": [{"start": 1_800_000_000, "end": 1_800_003_600}],
        "place": {},
        "description": "Описание",
        "price": "бесплатно",
        "is_free": True,
        "categories": ["concert"],
        "site_url": "https://kudago.com/msk/event/ok/",
    }

    class InvalidRecords(KudaGoSource):
        def fetch(self):
            without_date = {**good, "id": 2, "dates": []}
            without_url = {**good, "id": 3, "site_url": ""}
            return [without_date, without_url]

    invalid = InvalidRecords(
        now=source.now,
        sleeper=lambda _: None,
    )
    stats = run_import(invalid)

    assert (stats.added, stats.skipped, stats.errors) == (0, 2, 0)
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_embed_events_is_idempotent(
    event_factory,
    fake_embedding_provider,
):
    event_factory(title="Первое", external_id="one")
    event_factory(title="Второе", external_id="two")

    first = await embed_events(fake_embedding_provider)
    second = await embed_events(fake_embedding_provider)

    assert (first.calculated, first.skipped, first.errors) == (2, 0, 0)
    assert (second.calculated, second.skipped, second.errors) == (0, 2, 0)
    assert [len(batch) for batch in fake_embedding_provider.calls] == [2]


@pytest.mark.asyncio
async def test_changed_event_text_changes_hash_and_recalculates_vector(
    event_factory,
    fake_embedding_provider,
):
    event_id = event_factory(title="Старое название")
    await embed_events(fake_embedding_provider)
    with db.get_connection() as conn:
        before = conn.execute(
            "SELECT content_hash, embedding FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        conn.execute(
            "UPDATE events SET title = 'Новое название' WHERE id = ?", (event_id,)
        )

    stats = await embed_events(fake_embedding_provider)
    with db.get_connection() as conn:
        after = conn.execute(
            "SELECT content_hash, embedding FROM events WHERE id = ?", (event_id,)
        ).fetchone()

    assert (stats.calculated, stats.skipped, stats.errors) == (1, 0, 0)
    assert after["content_hash"] != before["content_hash"]
    assert after["embedding"] != before["embedding"]
