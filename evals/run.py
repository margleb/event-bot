import argparse
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Protocol, Sequence
from zoneinfo import ZoneInfo

import numpy as np
from dotenv import load_dotenv
from openai import AsyncOpenAI

from event_bot.embedding_provider import EmbeddingProvider


EVALS_DIR = Path(__file__).resolve().parent
DEFAULT_SNAPSHOT = EVALS_DIR / "catalog_snapshot.json"
DEFAULT_PERSONAS = EVALS_DIR / "personas.json"
DEFAULT_REPORTS = EVALS_DIR / "reports"
TOP_K = 5


class VectorProvider(Protocol):
    model: str

    async def embed(self, texts: Sequence[str]) -> list[np.ndarray]: ...


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"файл не найден: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"некорректный JSON в {path}: {error}") from error


def load_snapshot(path: Path) -> dict:
    payload = _load_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ValueError("снимок должен содержать список events")
    if not isinstance(payload.get("embedding_model"), str):
        raise ValueError("в снимке не указана embedding_model")
    if not payload["events"]:
        raise ValueError("снимок каталога пуст")

    expected_size = payload.get("vector_size")
    seen_ids = set()
    for event in payload["events"]:
        if not isinstance(event, dict):
            raise ValueError("каждое событие снимка должно быть объектом")
        if event.get("id") in seen_ids:
            raise ValueError(f"повторяющийся event id: {event.get('id')}")
        seen_ids.add(event.get("id"))
        vector = np.asarray(event.get("embedding"), dtype=np.float32)
        if (
            vector.ndim != 1
            or vector.size == 0
            or not np.all(np.isfinite(vector))
            or (expected_size is not None and vector.size != expected_size)
        ):
            raise ValueError(f"некорректный вектор события {event.get('id')}")
    return payload


def load_personas(path: Path) -> list[dict]:
    payload = _load_json(path)
    if not isinstance(payload, list) or not payload:
        raise ValueError("personas.json должен содержать непустой список")
    ids = set()
    for persona in payload:
        if not isinstance(persona, dict):
            raise ValueError("каждая персона должна быть объектом")
        if not isinstance(persona.get("id"), str) or not persona["id"]:
            raise ValueError("у каждой персоны должен быть строковый id")
        if persona["id"] in ids:
            raise ValueError(f"повторяющийся id персоны: {persona['id']}")
        ids.add(persona["id"])
        if not isinstance(persona.get("text"), str) or not persona["text"].strip():
            raise ValueError(f"у персоны {persona['id']} нет текста")
        for field in ("relevant_event_ids", "must_not_appear_ids"):
            if not isinstance(persona.get(field), list):
                raise ValueError(f"{persona['id']}: {field} должен быть списком")
    return payload


def _cosine_top(events: list[dict], query: np.ndarray, top_k: int) -> list[dict]:
    matrix = np.asarray([event["embedding"] for event in events], dtype=np.float32)
    query = np.asarray(query, dtype=np.float32)
    if query.ndim != 1 or query.size != matrix.shape[1]:
        raise ValueError(
            f"размер вектора персоны {query.size} не совпадает со снимком "
            f"({matrix.shape[1]})"
        )
    query_norm = np.linalg.norm(query)
    event_norms = np.linalg.norm(matrix, axis=1)
    if not np.isfinite(query_norm) or query_norm == 0:
        raise ValueError("провайдер вернул нулевой вектор персоны")
    usable = np.isfinite(event_norms) & (event_norms > 0)
    scores = np.full(len(events), -np.inf, dtype=np.float32)
    scores[usable] = matrix[usable] @ query / (event_norms[usable] * query_norm)
    order = sorted(range(len(events)), key=lambda i: (-float(scores[i]), i))
    return [
        {
            "id": events[index]["id"],
            "title": events[index]["title"],
            "score": round(float(scores[index]), 6),
        }
        for index in order[:top_k]
    ]


async def run_evaluation(
    snapshot_path: Path,
    personas_path: Path,
    provider: VectorProvider,
) -> dict:
    snapshot = load_snapshot(snapshot_path)
    personas = load_personas(personas_path)
    if provider.model != snapshot["embedding_model"]:
        raise ValueError(
            f"модель провайдера {provider.model!r} не совпадает со снимком "
            f"{snapshot['embedding_model']!r}"
        )

    vectors = await provider.embed([persona["text"] for persona in personas])
    if len(vectors) != len(personas):
        raise ValueError("провайдер вернул неверное число векторов персон")

    results = []
    for persona, vector in zip(personas, vectors, strict=True):
        top = _cosine_top(snapshot["events"], vector, TOP_K)
        top_ids = {item["id"] for item in top}
        relevant = set(persona["relevant_event_ids"])
        forbidden = set(persona["must_not_appear_ids"])
        results.append(
            {
                "persona_id": persona["id"],
                "relevant_hits": sorted(top_ids & relevant, key=str),
                "forbidden_hits": sorted(top_ids & forbidden, key=str),
                "top5": top,
            }
        )

    labeled = [
        (persona, result)
        for persona, result in zip(personas, results, strict=True)
        if persona["relevant_event_ids"]
    ]
    recall = (
        sum(bool(result["relevant_hits"]) for _, result in labeled) / len(labeled)
        if labeled
        else None
    )
    forbidden_rate = sum(bool(item["forbidden_hits"]) for item in results) / len(
        results
    )
    return {
        "created_at": datetime.now(ZoneInfo("Europe/Moscow")).isoformat(
            timespec="seconds"
        ),
        "snapshot": str(snapshot_path),
        "snapshot_generated_at": snapshot.get("generated_at"),
        "embedding_model": snapshot["embedding_model"],
        "metrics": {
            "recall_at_5": recall,
            "labeled_personas": len(labeled),
            "forbidden_persona_rate_at_5": forbidden_rate,
            "personas": len(personas),
        },
        "personas": results,
    }


def save_report(report: dict, reports_dir: Path = DEFAULT_REPORTS) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_date = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
    path = reports_dir / f"{report_date}.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def print_table(report: dict) -> None:
    rows = []
    for item in report["personas"]:
        marks = [f"+{event_id}" for event_id in item["relevant_hits"]]
        marks.extend(f"!{event_id}" for event_id in item["forbidden_hits"])
        titles = "; ".join(event["title"] for event in item["top5"][:3])
        rows.append((item["persona_id"], ", ".join(marks) or "—", titles))
    headers = ("Персона", "Попадания", "Первые три названия")
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(3)
    ]
    print(" | ".join(headers[i].ljust(widths[i]) for i in range(3)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(row[i].ljust(widths[i]) for i in range(3)))

    metrics = report["metrics"]
    recall = metrics["recall_at_5"]
    recall_text = (
        f"{recall:.1%} ({metrics['labeled_personas']} размечено)"
        if recall is not None
        else "н/д (relevant_event_ids пока пусты)"
    )
    print(f"\nRecall@5: {recall_text}")
    print(
        "Запрещённый id в топ-5: "
        f"{metrics['forbidden_persona_rate_at_5']:.1%}"
    )


def compare_reports(previous: dict, current: dict) -> list[str]:
    previous_by_id = {item["persona_id"]: item for item in previous["personas"]}
    lines = []
    for item in current["personas"]:
        persona_id = item["persona_id"]
        old = previous_by_id.get(persona_id)
        if old is None:
            lines.append(f"{persona_id}: новая персона")
            continue
        improvements = []
        regressions = []
        old_relevant = set(old.get("relevant_hits", []))
        new_relevant = set(item["relevant_hits"])
        old_forbidden = set(old.get("forbidden_hits", []))
        new_forbidden = set(item["forbidden_hits"])
        if len(new_relevant) > len(old_relevant):
            improvements.append("больше релевантных попаданий")
        elif len(new_relevant) < len(old_relevant):
            regressions.append("меньше релевантных попаданий")
        if len(new_forbidden) < len(old_forbidden):
            improvements.append("меньше запрещённых попаданий")
        elif len(new_forbidden) > len(old_forbidden):
            regressions.append("больше запрещённых попаданий")
        parts = []
        if improvements:
            parts.append("улучшилось: " + ", ".join(improvements))
        if regressions:
            parts.append("ухудшилось: " + ", ".join(regressions))
        if not parts:
            old_ids = [event["id"] for event in old.get("top5", [])]
            new_ids = [event["id"] for event in item["top5"]]
            parts.append("без изменений" if old_ids == new_ids else "топ-5 изменился")
        lines.append(f"{persona_id}: {'; '.join(parts)}")
    return lines


async def _async_main(args: argparse.Namespace) -> int:
    snapshot = load_snapshot(args.snapshot)
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY не задан: он нужен только для векторов персон")
    client = AsyncOpenAI(api_key=api_key)
    try:
        provider = EmbeddingProvider(client, model=snapshot["embedding_model"])
        report = await run_evaluation(args.snapshot, args.personas, provider)
    finally:
        await client.close()

    print_table(report)
    path = save_report(report, args.reports_dir)
    print(f"Отчёт: {path}")
    if args.previous_report:
        previous = _load_json(args.previous_report)
        if not isinstance(previous, dict) or not isinstance(
            previous.get("personas"), list
        ):
            raise ValueError("предыдущий отчёт имеет неверный формат")
        print("\nСравнение с предыдущим отчётом:")
        for line in compare_reports(previous, report):
            print(line)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Recall@5 по снимку каталога")
    parser.add_argument("previous_report", nargs="?", type=Path)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--personas", type=Path, default=DEFAULT_PERSONAS)
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS)
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(_async_main(args)))
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
