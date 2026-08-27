import hashlib
import os
from collections.abc import Sequence

import numpy as np
from openai import AsyncOpenAI


DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


def get_embedding_model() -> str:
    """Модель из окружения или проверенный дефолт OpenAI."""
    return os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)


class EmbeddingProvider:
    """Тонкая async-обёртка над OpenAI Embeddings API."""

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str | None = None,
    ) -> None:
        self._client = client
        self.model = model or get_embedding_model()

    async def embed(self, texts: Sequence[str]) -> list[np.ndarray]:
        """Возвращает float32-векторы в том же порядке, что и входные тексты."""
        inputs = [text.strip() for text in texts]
        if not inputs or any(not text for text in inputs):
            raise ValueError("текст для эмбеддинга не должен быть пустым")

        response = await self._client.embeddings.create(
            input=inputs,
            model=self.model,
            encoding_format="float",
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in ordered] != list(range(len(inputs))):
            raise ValueError("OpenAI вернул неполный набор эмбеддингов")

        vectors = [np.asarray(item.embedding, dtype=np.float32) for item in ordered]
        if any(vector.ndim != 1 or vector.size == 0 for vector in vectors):
            raise ValueError("OpenAI вернул эмбеддинг неверной формы")
        return vectors


def event_embedding_text(
    title: str,
    description: str,
    tags: Sequence[str],
    venue: str,
) -> str:
    """Стабильный текст события: название + описание + теги + площадка."""
    parts = (title, description, " ".join(tags), venue)
    return "\n".join(part.strip() for part in parts if part and part.strip())


def profile_embedding_text(interests: Sequence[str]) -> str:
    """Профильный текст содержит только положительные интересы."""
    return "\n".join(item.strip() for item in interests if item.strip())


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def vector_to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def vector_from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
