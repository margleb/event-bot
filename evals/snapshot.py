import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

import event_bot.db as db
from event_bot.embedding_provider import vector_from_blob


DEFAULT_OUTPUT = Path(__file__).with_name("catalog_snapshot.json")


def export_snapshot(
    output: Path = DEFAULT_OUTPUT,
    *,
    limit: int = 100,
    db_path: Path | None = None,
) -> dict:
    """Выгружает актуальные события одной embedding-модели в JSON."""
    if limit <= 0:
        raise ValueError("limit должен быть положительным")

    with db.get_connection(db_path) as conn:
        model_row = conn.execute(
            """
            SELECT embedding_model, COUNT(*) AS amount
            FROM events
            WHERE city = 'Москва'
              AND status = 'active'
              AND COALESCE(end_date, date) > datetime('now', 'localtime')
              AND embedding IS NOT NULL
              AND embedding_model IS NOT NULL
            GROUP BY embedding_model
            ORDER BY amount DESC, embedding_model
            LIMIT 1
            """
        ).fetchone()
        if model_row is None:
            raise RuntimeError("в каталоге нет актуальных событий с векторами")

        model = model_row["embedding_model"]
        rows = conn.execute(
            """
            SELECT id, title, description, city, address, date, end_date,
                   price_min, price_max, price_text, is_free, tags, venue,
                   source_id, external_id, source_url, embedding, content_hash
            FROM events
            WHERE city = 'Москва'
              AND status = 'active'
              AND COALESCE(end_date, date) > datetime('now', 'localtime')
              AND embedding IS NOT NULL
              AND embedding_model = ?
            ORDER BY
              CASE WHEN date >= datetime('now', 'localtime') THEN 0 ELSE 1 END,
              CASE
                WHEN date >= datetime('now', 'localtime') THEN date
                ELSE COALESCE(end_date, date)
              END,
              id
            LIMIT ?
            """,
            (model, limit),
        ).fetchall()

    events = []
    vector_size = None
    for row in rows:
        vector = vector_from_blob(row["embedding"])
        if vector.ndim != 1 or vector.size == 0 or not np.all(np.isfinite(vector)):
            continue
        if vector_size is None:
            vector_size = int(vector.size)
        if vector.size != vector_size:
            continue
        events.append(
            {
                "id": row["id"],
                "title": row["title"],
                "description": row["description"],
                "city": row["city"],
                "address": row["address"],
                "date": row["date"],
                "end_date": row["end_date"],
                "price_min": row["price_min"],
                "price_max": row["price_max"],
                "price_text": row["price_text"],
                "is_free": (
                    bool(row["is_free"]) if row["is_free"] is not None else None
                ),
                "tags": json.loads(row["tags"]),
                "venue": row["venue"],
                "source_id": row["source_id"],
                "external_id": row["external_id"],
                "source_url": row["source_url"],
                "content_hash": row["content_hash"],
                "embedding": vector.tolist(),
            }
        )

    if not events:
        raise RuntimeError("не найдено пригодных векторов одинаковой размерности")

    snapshot = {
        "generated_at": datetime.now(ZoneInfo("Europe/Moscow")).isoformat(
            timespec="seconds"
        ),
        "embedding_model": model,
        "vector_size": vector_size,
        "events": events,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Снимок каталога для evals.run")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--db", type=Path, default=db.DB_PATH)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    snapshot = export_snapshot(args.output, limit=args.limit, db_path=args.db)
    print(
        f"Сохранено событий: {len(snapshot['events'])} · "
        f"модель: {snapshot['embedding_model']} · файл: {args.output}"
    )


if __name__ == "__main__":
    main()
