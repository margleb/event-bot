import json

import numpy as np
import pytest

import event_bot.db as db
from evals.run import compare_reports, print_table, run_evaluation, save_report
from evals.snapshot import export_snapshot


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.mark.asyncio
async def test_evaluation_uses_snapshot_and_calculates_requested_metrics(
    tmp_path,
    monkeypatch,
    fake_embedding_provider,
    capsys,
):
    query = fake_embedding_provider.vector("люблю техно")
    opposite = -query
    events = []
    for event_id in range(1, 7):
        vector = query if event_id in (1, 2) else opposite
        events.append(
            {
                "id": event_id,
                "title": f"Событие {event_id}",
                "embedding": vector.tolist(),
            }
        )
    snapshot_path = tmp_path / "snapshot.json"
    personas_path = tmp_path / "personas.json"
    _write_json(
        snapshot_path,
        {
            "generated_at": "2026-08-28T12:00:00+03:00",
            "embedding_model": fake_embedding_provider.model,
            "vector_size": int(query.size),
            "events": events,
        },
    )
    _write_json(
        personas_path,
        [
            {
                "id": "techno_01",
                "text": "люблю техно",
                "relevant_event_ids": [1],
                "must_not_appear_ids": [2],
            }
        ],
    )

    # Если раннер попытается обратиться к живой базе, тест немедленно упадёт.
    monkeypatch.setattr(
        db,
        "get_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("evals.run не должен читать SQLite")
        ),
    )
    report = await run_evaluation(
        snapshot_path, personas_path, fake_embedding_provider
    )
    print_table(report)
    output = capsys.readouterr().out

    assert report["metrics"]["recall_at_5"] == 1.0
    assert report["metrics"]["forbidden_persona_rate_at_5"] == 1.0
    assert report["personas"][0]["relevant_hits"] == [1]
    assert report["personas"][0]["forbidden_hits"] == [2]
    assert "techno_01" in output
    assert "Событие 1" in output
    assert "Recall@5: 100.0%" in output

    report_path = save_report(report, tmp_path / "reports")
    assert report_path.exists()
    assert json.loads(report_path.read_text(encoding="utf-8"))["metrics"] == report[
        "metrics"
    ]


def test_report_comparison_shows_improvements_and_regressions():
    previous = {
        "personas": [
            {
                "persona_id": "better",
                "relevant_hits": [],
                "forbidden_hits": [9],
                "top5": [{"id": 9}],
            },
            {
                "persona_id": "worse",
                "relevant_hits": [1],
                "forbidden_hits": [],
                "top5": [{"id": 1}],
            },
        ]
    }
    current = {
        "personas": [
            {
                "persona_id": "better",
                "relevant_hits": [1],
                "forbidden_hits": [],
                "top5": [{"id": 1}],
            },
            {
                "persona_id": "worse",
                "relevant_hits": [],
                "forbidden_hits": [9],
                "top5": [{"id": 9}],
            },
        ]
    }

    lines = compare_reports(previous, current)

    assert "better: улучшилось" in lines[0]
    assert "worse: ухудшилось" in lines[1]


def test_snapshot_contains_only_current_events_with_vectors(
    tmp_path,
    event_factory,
    fake_embedding_provider,
):
    current_id = event_factory(
        title="Актуальное",
        embedding=fake_embedding_provider.vector("current"),
    )
    event_factory(
        title="Неактивное",
        status="cancelled",
        embedding=fake_embedding_provider.vector("cancelled"),
    )
    output = tmp_path / "catalog.json"

    snapshot = export_snapshot(output, db_path=db.DB_PATH)

    assert [event["id"] for event in snapshot["events"]] == [current_id]
    assert snapshot["vector_size"] == 32
    assert output.exists()
