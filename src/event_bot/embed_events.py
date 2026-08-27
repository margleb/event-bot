import asyncio
import json
import os
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import AsyncOpenAI

import event_bot.db as db
from event_bot.embedding_provider import (
    EmbeddingProvider,
    content_hash,
    event_embedding_text,
    vector_to_blob,
)


DEFAULT_BATCH_SIZE = 100


@dataclass
class EmbedStats:
    calculated: int = 0
    skipped: int = 0
    errors: int = 0

    def format(self) -> str:
        return (
            f"посчитано: {self.calculated} / "
            f"пропущено: {self.skipped} / ошибок: {self.errors}"
        )


@dataclass(frozen=True)
class EventEmbeddingJob:
    event_id: int
    text: str
    content_hash: str


def _collect_jobs(model: str, stats: EmbedStats) -> list[EventEmbeddingJob]:
    with db.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, title, description, tags, venue,
                   embedding, embedding_model, content_hash
            FROM events
            ORDER BY id
            """
        ).fetchall()

    jobs: list[EventEmbeddingJob] = []
    for row in rows:
        try:
            tags = json.loads(row["tags"])
            if not isinstance(tags, list) or not all(
                isinstance(tag, str) for tag in tags
            ):
                raise ValueError("tags должны быть списком строк")
            text = event_embedding_text(
                row["title"],
                row["description"],
                tags,
                row["venue"],
            )
            if not text:
                raise ValueError("пустой текст события")
            current_hash = content_hash(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            stats.errors += 1
            continue

        if (
            row["embedding"] is not None
            and row["embedding_model"] == model
            and row["content_hash"] == current_hash
        ):
            stats.skipped += 1
            continue
        jobs.append(EventEmbeddingJob(row["id"], text, current_hash))
    return jobs


async def embed_events(
    provider: EmbeddingProvider,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> EmbedStats:
    """Считает только отсутствующие, изменившиеся или устаревшие векторы."""
    if batch_size <= 0:
        raise ValueError("batch_size должен быть положительным")

    db.init_db()
    stats = EmbedStats()
    jobs = _collect_jobs(provider.model, stats)

    for offset in range(0, len(jobs), batch_size):
        batch = jobs[offset : offset + batch_size]
        try:
            vectors = await provider.embed([job.text for job in batch])
            if len(vectors) != len(batch):
                raise ValueError("провайдер вернул неверное число векторов")
        except Exception:
            stats.errors += len(batch)
            continue

        with db.get_connection() as conn:
            conn.executemany(
                """
                UPDATE events
                SET embedding = ?, embedding_model = ?, content_hash = ?
                WHERE id = ?
                """,
                [
                    (
                        vector_to_blob(vector),
                        provider.model,
                        job.content_hash,
                        job.event_id,
                    )
                    for job, vector in zip(batch, vectors, strict=True)
                ],
            )
        stats.calculated += len(batch)

    return stats


async def _main() -> int:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY не задан")
        print(EmbedStats(errors=1).format())
        return 1

    client = AsyncOpenAI(api_key=api_key)
    try:
        stats = await embed_events(EmbeddingProvider(client))
    finally:
        await client.close()
    print(stats.format())
    return 0 if stats.errors == 0 else 1


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
