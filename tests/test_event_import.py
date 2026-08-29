import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

import event_bot.db as db
from event_bot.models import Event, Profile, UserIntent
from event_bot.import_events import run_import
from event_bot.sources.base import EventSource, SourceFetchError
from event_bot.sources.kudago import KUDAGO_FIELDS, KudaGoSource


class JsonResponse(io.BytesIO):
    def __init__(self, payload: object) -> None:
        super().__init__(json.dumps(payload).encode())

    def __enter__(self) -> "JsonResponse":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class KudaGoSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)
        self.source = KudaGoSource(now=self.now, sleeper=lambda _: None)

    def raw_event(self) -> dict:
        return {
            "id": 123,
            "title": "Концерт &amp; друзья",
            "dates": [
                {
                    "start": int(datetime(2026, 8, 28, 12, tzinfo=timezone.utc).timestamp()),
                    "end": int(datetime(2026, 8, 28, 15, tzinfo=timezone.utc).timestamp()),
                }
            ],
            "place": {"title": "Клуб", "address": "ул. Тестовая, 1"},
            "description": "Описание без HTML",
            "price": "от 1 500 до 2 500 рублей",
            "is_free": False,
            "categories": ["concert", "festival"],
            "site_url": "https://kudago.com/msk/event/test/",
        }

    def test_normalize_uses_moscow_time_price_text_and_interest_tags(self) -> None:
        event = self.source.normalize(self.raw_event())

        self.assertIsNotNone(event)
        self.assertEqual(event.title, "Концерт & друзья")
        self.assertEqual(event.date, datetime(2026, 8, 28, 15, 0))
        self.assertEqual(event.end_date, datetime(2026, 8, 28, 18, 0))
        self.assertEqual(event.price_text, "от 1 500 до 2 500 рублей")
        self.assertEqual((event.price_min, event.price_max), (1500, 2500))
        self.assertFalse(event.is_free)
        self.assertEqual(event.tags, ["музыка", "концерт", "фестиваль"])
        self.assertEqual(event.venue, "Клуб")
        self.assertEqual(event.address, "ул. Тестовая, 1")
        self.assertEqual(event.status, "active")
        self.assertEqual(event.source_id, "kudago")
        self.assertEqual(event.external_id, "123")

    def test_missing_required_fields_are_skipped(self) -> None:
        for field in ("title", "dates", "site_url"):
            with self.subTest(field=field):
                raw = self.raw_event()
                raw[field] = "" if field != "dates" else []
                self.assertIsNone(self.source.normalize(raw))

    def test_service_timestamp_for_unknown_start_is_skipped(self) -> None:
        raw = self.raw_event()
        raw["dates"] = [{"start": -62135433000, "end": 1790542800}]

        self.assertIsNone(self.source.normalize(raw))

    def test_fetch_retries_and_uses_documented_pagination_parameters(self) -> None:
        calls: list[str] = []
        sleeps: list[float] = []
        responses: list[object] = [
            URLError("temporary"),
            {"results": [{"id": 1}], "next": "page-2"},
            {"results": [{"id": 2}], "next": None},
        ]

        def opener(url: str, *, timeout: float) -> JsonResponse:
            self.assertEqual(timeout, 7)
            calls.append(url)
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return JsonResponse(response)

        source = KudaGoSource(
            now=self.now,
            timeout=7,
            retry_pause=0.1,
            page_pause=0.2,
            opener=opener,
            sleeper=sleeps.append,
        )
        records = source.fetch()

        self.assertEqual(records, [{"id": 1}, {"id": 2}])
        self.assertEqual(len(calls), 3)
        first_query = parse_qs(urlparse(calls[0]).query)
        second_page_query = parse_qs(urlparse(calls[2]).query)
        self.assertEqual(first_query["location"], ["msk"])
        self.assertEqual(first_query["text_format"], ["text"])
        self.assertEqual(first_query["expand"], ["place"])
        self.assertEqual(first_query["fields"], [",".join(KUDAGO_FIELDS)])
        self.assertEqual(first_query["page_size"], ["100"])
        self.assertEqual(second_page_query["page"], ["2"])
        self.assertEqual(sleeps, [0.1, 0.2])


class EventImportDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.DB_PATH
        db.DB_PATH = Path(self.temp_dir.name) / "events.db"
        db.init_db()

    def tearDown(self) -> None:
        db.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    @staticmethod
    def event(**updates: object) -> Event:
        values = {
            "title": "Реальный концерт",
            "description": "Описание",
            "city": "Москва",
            "address": "ул. Музыкальная, 1",
            "date": datetime(2099, 1, 2, 19, 0),
            "price_min": 1000,
            "price_max": 1000,
            "price_text": "1000 рублей",
            "is_free": False,
            "tags": ["музыка", "концерт"],
            "venue": "Клуб",
            "source_id": "kudago",
            "external_id": "42",
            "source_url": "https://kudago.com/msk/event/real/",
            "fetched_at": "2098-12-01T00:00:00+00:00",
            "status": "active",
        }
        values.update(updates)
        return Event(**values)

    def test_upsert_updates_in_place_and_find_only_returns_active(self) -> None:
        added, updated, errors = db.upsert_source_events([self.event()])
        self.assertEqual((added, updated, errors), (1, 0, 0))

        changed = self.event(title="Обновлённый концерт")
        added, updated, errors = db.upsert_source_events([changed])
        self.assertEqual((added, updated, errors), (0, 1, 0))

        past = self.event(external_id="43", title="Прошедшее", status="past")
        db.upsert_source_events([past])
        with db.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS amount FROM events").fetchone()[
                "amount"
            ]
        self.assertEqual(count, 2)

        found = db.find_events(Profile(interests=["музыка"]))
        self.assertEqual([item.title for item in found], ["Обновлённый концерт"])
        card = db.format_event_card(found[0], 1)
        self.assertIn("Клуб, ул. Музыкальная, 1", card)
        self.assertIn("K KudaGo", card)
        self.assertIn("открыть источник", card)
        self.assertIn('href="https://kudago.com/msk/event/real/"', card)
        intent_card = db.format_intent_card(
            UserIntent(event=found[0], status="going", visible=True)
        )
        self.assertIn("K KudaGo", intent_card)

    def test_find_includes_started_but_not_finished_event(self) -> None:
        ongoing = self.event(
            external_id="ongoing",
            title="Идущая выставка",
            date=datetime(2020, 1, 1, 10, 0),
            end_date=datetime(2099, 1, 3, 20, 0),
            tags=["выставка"],
        )
        db.upsert_source_events([ongoing])

        found = db.find_events(Profile(interests=["выставка"]))

        self.assertIn("Идущая выставка", [item.title for item in found])

    def test_find_skips_row_with_malformed_date(self) -> None:
        db.upsert_source_events([self.event()])
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO events
                    (title, description, city, address, date, end_date, tags,
                     venue, source_id, external_id, status)
                VALUES
                    ('Повреждённое', '', 'Москва', '', '1-01-03 00:00:17',
                     '2099-01-01 00:00:00', '[]', '', 'kudago', 'bad-date',
                     'active')
                """
            )

        found = db.find_events(Profile(interests=[]))

        self.assertEqual([item.title for item in found], ["Реальный концерт"])

    def test_legacy_rows_receive_null_source_fields_and_cleanup_is_explicit(self) -> None:
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO events
                    (title, description, city, address, date, tags, venue)
                VALUES ('Сид', '', 'Москва', '', '2099-01-01 10:00:00', '[]', '')
                """
            )
        with db.get_connection() as conn:
            row = conn.execute(
                """
                SELECT source_id, external_id, source_url, fetched_at, status
                FROM events WHERE title = 'Сид'
                """
            ).fetchone()
        self.assertTrue(all(row[name] is None for name in row.keys()))

        self.assertEqual(db.delete_legacy_events(), 1)
        with db.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS amount FROM events").fetchone()[
                "amount"
            ]
        self.assertEqual(count, 0)

    def test_fetch_failure_does_not_change_database(self) -> None:
        db.upsert_source_events([self.event()])

        class FailingSource(EventSource):
            source_id = "failing"

            def fetch(self) -> list[object]:
                raise SourceFetchError("API недоступен")

            def normalize(self, raw: dict) -> Event | None:
                raise AssertionError("normalize не должен вызываться")

        with self.assertRaises(SourceFetchError):
            run_import(FailingSource())

        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT title, status FROM events WHERE external_id = '42'"
            ).fetchone()
        self.assertEqual(dict(row), {"title": "Реальный концерт", "status": "active"})

    def test_bad_record_counts_as_error_without_stopping_import(self) -> None:
        event = self.event()

        class MixedSource(EventSource):
            source_id = "mixed"

            def fetch(self) -> list[object]:
                return ["повреждённая запись", {"valid": True}]

            def normalize(self, raw: dict) -> Event | None:
                return event

        stats = run_import(MixedSource())

        self.assertEqual((stats.added, stats.errors), (1, 1))


class EventMigrationTests(unittest.TestCase):
    def test_old_events_table_gets_import_columns_and_unique_source_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.db"
            with sqlite3.connect(path) as conn:
                conn.execute(
                    """
                    CREATE TABLE events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL,
                        city TEXT NOT NULL,
                        address TEXT NOT NULL,
                        date TEXT NOT NULL,
                        price_min INTEGER,
                        price_max INTEGER,
                        tags TEXT NOT NULL DEFAULT '[]',
                        venue TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                    """
                )

            original = db.DB_PATH
            db.DB_PATH = path
            try:
                db.init_db()
                with db.get_connection() as conn:
                    columns = {
                        row["name"] for row in conn.execute("PRAGMA table_info(events)")
                    }
                    indexes = {
                        row["name"]: row["unique"]
                        for row in conn.execute("PRAGMA index_list(events)")
                    }
            finally:
                db.DB_PATH = original

        self.assertTrue(
            {
                "source_id",
                "external_id",
                "source_url",
                "fetched_at",
                "status",
                "embedding",
                "embedding_model",
                "content_hash",
            }
            <= columns
        )
        self.assertEqual(indexes["idx_events_source_external"], 1)


if __name__ == "__main__":
    unittest.main()
