import hashlib
from collections.abc import Sequence
from datetime import datetime
from itertools import count

import numpy as np
import pytest

import event_bot.db as db
from event_bot.embedding_provider import vector_to_blob
from event_bot.models import Event, Profile


class FakeEmbeddingProvider:
    """Детерминированные тестовые эмбеддинги без клиентов и сети."""

    model = "fake-hash-v1"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    @staticmethod
    def vector(text: str) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vector = np.frombuffer(digest, dtype=np.uint8).astype(np.float32)
        vector -= 127.5
        return vector / np.linalg.norm(vector)

    async def embed(self, texts: Sequence[str]) -> list[np.ndarray]:
        batch = list(texts)
        self.calls.append(batch)
        return [self.vector(text) for text in batch]


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Новая SQLite-база со всей актуальной схемой для каждого теста."""

    path = tmp_path / "test.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    return path


@pytest.fixture
def fake_embedding_provider() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider()


@pytest.fixture
def user_factory(temp_db):
    ids = count(1)

    def create_user(
        *,
        user_id: int | None = None,
        interests: list[str] | None = None,
        avoid: list[str] | None = None,
        name: str | None = None,
        username: str | None = None,
        confirmed: bool = True,
        budget_rub: int | None = None,
        group_size_min: int | None = None,
        group_size_max: int | None = None,
    ) -> tuple[int, Profile]:
        user_id = user_id if user_id is not None else next(ids)
        profile = Profile(
            interests=interests or ["музыка"],
            avoid=avoid or [],
            budget_rub=budget_rub,
            preferred_group_size_min=group_size_min,
            preferred_group_size_max=group_size_max,
        )
        if confirmed:
            db.save_user_profile(
                user_id,
                profile,
                name or f"User {user_id}",
                username if username is not None else f"user{user_id}",
            )
        return user_id, profile

    return create_user


@pytest.fixture
def event_factory(temp_db):
    external_ids = count(1)

    def create_event(
        *,
        title: str = "Тестовое событие",
        description: str = "Описание события",
        date: datetime = datetime(2099, 1, 2, 19, 0),
        end_date: datetime | None = None,
        price_min: int | None = 500,
        price_max: int | None = 500,
        is_free: bool | None = False,
        tags: list[str] | None = None,
        status: str = "active",
        source_url: str | None = None,
        external_id: str | None = None,
        embedding: np.ndarray | None = None,
        embedding_model: str = "fake-hash-v1",
    ) -> int:
        external_id = external_id or f"event-{next(external_ids)}"
        event = Event(
            title=title,
            description=description,
            city="Москва",
            address="Тестовая улица, 1",
            date=date,
            end_date=end_date,
            price_min=price_min,
            price_max=price_max,
            price_text=None,
            is_free=is_free,
            tags=tags or ["музыка"],
            venue="Тестовая площадка",
            source_id="test",
            external_id=external_id,
            source_url=source_url or f"https://example.com/{external_id}",
            fetched_at="2098-12-01T00:00:00+00:00",
            status=status,
        )
        added, _, errors = db.upsert_source_events([event])
        assert added == 1 and errors == 0
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM events WHERE source_id = ? AND external_id = ?",
                (event.source_id, external_id),
            ).fetchone()
            event_id = row["id"]
            if embedding is not None:
                conn.execute(
                    """
                    UPDATE events
                    SET embedding = ?, embedding_model = ?, content_hash = 'fixture'
                    WHERE id = ?
                    """,
                    (vector_to_blob(embedding), embedding_model, event_id),
                )
        return event_id

    return create_event


@pytest.fixture
def intent_factory(temp_db):
    def create_intent(
        user_id: int,
        event_id: int,
        *,
        status: str = "going",
        visible: bool = True,
    ) -> None:
        db.save_intent(user_id, event_id, status)
        if visible:
            assert db.set_intent_visibility(user_id, event_id, True)

    return create_intent
