import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np

import event_bot.db as db
from event_bot.embed_events import embed_events
from event_bot.embedding_provider import (
    EmbeddingProvider,
    event_embedding_text,
    vector_to_blob,
)
from event_bot.handlers import confirm_profile
from event_bot.models import Event, Profile
from event_bot.storage import ProfileStore


class FakeEmbeddingProvider:
    model = "test-embedding"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[np.ndarray]:
        self.calls.append(list(texts))
        return [
            np.asarray([index + 1, 1, 0], dtype=np.float32)
            for index, _ in enumerate(texts)
        ]


class EmbeddingProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_call_uses_list_input_and_preserves_index_order(self) -> None:
        client = SimpleNamespace(embeddings=SimpleNamespace(create=AsyncMock()))
        client.embeddings.create.return_value = SimpleNamespace(
            data=[
                SimpleNamespace(index=1, embedding=[0.0, 1.0]),
                SimpleNamespace(index=0, embedding=[1.0, 0.0]),
            ]
        )
        provider = EmbeddingProvider(client, model="text-embedding-3-small")

        vectors = await provider.embed(["техно", "вязание"])

        client.embeddings.create.assert_awaited_once_with(
            input=["техно", "вязание"],
            model="text-embedding-3-small",
            encoding_format="float",
        )
        self.assertEqual(vectors[0].dtype, np.float32)
        np.testing.assert_array_equal(vectors[0], [1.0, 0.0])
        np.testing.assert_array_equal(vectors[1], [0.0, 1.0])


class EmbeddingDatabaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.DB_PATH
        db.DB_PATH = Path(self.temp_dir.name) / "embeddings.db"
        db.init_db()

    def tearDown(self) -> None:
        db.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    @staticmethod
    def event(external_id: str, title: str, **updates: object) -> Event:
        values = {
            "title": title,
            "description": "Описание",
            "city": "Москва",
            "address": "",
            "date": datetime(2099, 1, 2, 19, 0),
            "price_min": 500,
            "price_max": 500,
            "price_text": "500 рублей",
            "is_free": False,
            "tags": ["разное"],
            "venue": "Площадка",
            "source_id": "test",
            "external_id": external_id,
            "source_url": f"https://example.com/{external_id}",
            "status": "active",
        }
        values.update(updates)
        return Event(**values)

    def _set_embedding(self, external_id: str, vector: list[float]) -> None:
        with db.get_connection() as conn:
            conn.execute(
                """
                UPDATE events
                SET embedding = ?, embedding_model = 'test-embedding',
                    content_hash = 'test'
                WHERE external_id = ?
                """,
                (vector_to_blob(np.asarray(vector, dtype=np.float32)), external_id),
            )

    async def test_embed_events_is_batched_incremental_and_idempotent(self) -> None:
        db.upsert_source_events(
            [
                self.event("one", "Первое"),
                self.event("two", "Второе"),
            ]
        )
        provider = FakeEmbeddingProvider()

        first = await embed_events(provider)
        second = await embed_events(provider)
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE events SET description = 'Новое описание' "
                "WHERE external_id = 'two'"
            )
        third = await embed_events(provider)

        self.assertEqual((first.calculated, first.skipped, first.errors), (2, 0, 0))
        self.assertEqual((second.calculated, second.skipped, second.errors), (0, 2, 0))
        self.assertEqual((third.calculated, third.skipped, third.errors), (1, 1, 0))
        self.assertEqual([len(call) for call in provider.calls], [2, 1])

    def test_event_text_has_required_fields_in_stable_order(self) -> None:
        text = event_embedding_text(
            "Название",
            "Описание",
            ["музыка", "ночь"],
            "Клуб",
        )
        self.assertEqual(text, "Название\nОписание\nмузыка ночь\nКлуб")

    def test_semantic_search_finds_event_without_matching_tag(self) -> None:
        db.upsert_source_events(
            [
                self.event("semantic", "Ночной электронный рейв", tags=["разное"]),
                self.event("tag", "Кружок рукоделия", tags=["техно"]),
            ]
        )
        self._set_embedding("semantic", [1.0, 0.0, 0.0])
        self._set_embedding("tag", [0.0, 1.0, 0.0])

        found = db.find_events(
            Profile(interests=["техно"]),
            profile_embedding=vector_to_blob(
                np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
            ),
            profile_embedding_model="test-embedding",
        )

        self.assertEqual(found[0].title, "Ночной электронный рейв")

    def test_avoid_embedding_reduces_score(self) -> None:
        db.upsert_source_events(
            [
                self.event("noisy", "Похожее, но нежелательное"),
                self.event("clean", "Чуть менее похожее, но подходящее"),
            ]
        )
        self._set_embedding("noisy", [0.9, 0.43589, 0.0])
        self._set_embedding("clean", [0.8, 0.0, 0.6])

        found = db.find_events(
            Profile(interests=["музыка"], avoid=["толпы"]),
            profile_embedding=vector_to_blob(
                np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
            ),
            profile_embedding_model="test-embedding",
            avoid_embedding=vector_to_blob(
                np.asarray([0.0, 1.0, 0.0], dtype=np.float32)
            ),
            avoid_embedding_model="test-embedding",
        )

        self.assertEqual(found[0].title, "Чуть менее похожее, но подходящее")

    def test_hard_filters_precede_semantic_ranking(self) -> None:
        db.upsert_source_events(
            [
                self.event("valid", "Допустимое"),
                self.event("expensive", "Слишком дорого", price_min=5000, price_max=5000),
                self.event("past", "Прошедшее", date=datetime(2020, 1, 1)),
                self.event("other-city", "Другой город", city="Санкт-Петербург"),
            ]
        )
        for external_id in ("valid", "expensive", "past", "other-city"):
            self._set_embedding(external_id, [1.0, 0.0, 0.0])

        found = db.find_events(
            Profile(interests=["что угодно"], budget_rub=1000),
            profile_embedding=vector_to_blob(
                np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
            ),
            profile_embedding_model="test-embedding",
        )

        self.assertEqual([event.title for event in found], ["Допустимое"])

    def test_missing_profile_vector_falls_back_to_tags(self) -> None:
        db.upsert_source_events(
            [
                self.event("matching", "Совпало по тегу", tags=["театр"]),
                self.event("other", "Другое", tags=["спорт"]),
            ]
        )

        found = db.find_events(Profile(interests=["театр"]))

        self.assertEqual(found[0].title, "Совпало по тегу")

    def test_profile_embeddings_round_trip(self) -> None:
        positive = vector_to_blob(np.asarray([1.0, 2.0], dtype=np.float32))
        negative = vector_to_blob(np.asarray([3.0, 4.0], dtype=np.float32))
        db.save_user_profile(
            1,
            Profile(interests=["музыка"], avoid=["толпы"]),
            "Alice",
            profile_embedding=positive,
            profile_embedding_model="test-embedding",
            avoid_embedding=negative,
            avoid_embedding_model="test-embedding",
        )

        stored = db.get_user_profile_embeddings(1)

        self.assertEqual(stored, (positive, "test-embedding", negative, "test-embedding"))

    async def test_profile_confirmation_batches_and_recomputes_embeddings(
        self,
    ) -> None:
        provider = FakeEmbeddingProvider()
        store = ProfileStore(
            drafts={1: Profile(interests=["техно"], avoid=["вязание"])}
        )
        callback = SimpleNamespace(
            from_user=SimpleNamespace(
                id=1,
                first_name="Alice",
                username="alice",
            ),
            message=None,
            answer=AsyncMock(),
        )

        await confirm_profile(callback, store, provider)

        self.assertEqual(provider.calls, [["техно", "вязание"]])
        stored = db.get_user_profile_embeddings(1)
        self.assertTrue(stored[0])
        self.assertEqual(stored[1], "test-embedding")
        self.assertTrue(stored[2])
        self.assertEqual(stored[3], "test-embedding")
        callback.answer.assert_awaited_once()

        store.drafts[1] = Profile(interests=["джаз"])
        await confirm_profile(callback, store, provider)

        self.assertEqual(provider.calls, [["техно", "вязание"], ["джаз"]])
        self.assertEqual(db.get_user_profile(1).interests, ["джаз"])

    async def test_profile_confirmation_without_provider_keeps_tag_fallback(
        self,
    ) -> None:
        store = ProfileStore(drafts={1: Profile(interests=["театр"])})
        callback = SimpleNamespace(
            from_user=SimpleNamespace(
                id=1,
                first_name="Alice",
                username=None,
            ),
            message=None,
            answer=AsyncMock(),
        )

        await confirm_profile(callback, store, None)

        self.assertEqual(db.get_user_profile(1).interests, ["театр"])
        self.assertEqual(db.get_user_profile_embeddings(1), (None, None, None, None))


if __name__ == "__main__":
    unittest.main()
