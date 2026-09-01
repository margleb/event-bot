import csv
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import event_bot.db as db
from event_bot.admin_dashboard import build_admin_dashboard
from event_bot.analytics import record_usage
from event_bot.handlers import start
from event_bot.research_analytics import (
    build_research_dashboard,
    enroll_research_participant,
    export_research_events_csv,
    reset_research_session_context,
    set_research_session_context,
)
from event_bot.webapp import app
from tests.test_webapp import BOT_TOKEN, auth_headers


CAMPAIGN = "ux_unu_sep2026"
SESSION_ID = "research-session-0001"


def test_research_session_funnel_export_and_general_metric_isolation(
    temp_db,
    user_factory,
):
    research_user, _ = user_factory(user_id=42)
    regular_user, _ = user_factory(user_id=43)
    participant = enroll_research_participant(research_user, CAMPAIGN)
    assert participant is not None

    token = set_research_session_context(SESSION_ID)
    try:
        record_usage(research_user, "miniapp.open", "miniapp")
        record_usage(
            research_user,
            "miniapp.event_details",
            "miniapp",
            {"event_id": 7, "source_id": "timepad"},
        )
        record_usage(
            research_user,
            "miniapp.company_prompt_opened",
            "miniapp",
            {"event_id": 7, "company_count": 0},
        )
        record_usage(
            research_user,
            "miniapp.event_company.joined",
            "miniapp",
            {"event_id": 7, "group_id": 11, "status": "joined"},
        )
    finally:
        reset_research_session_context(token)
    record_usage(regular_user, "miniapp.open", "miniapp")

    report = build_research_dashboard(CAMPAIGN)
    assert report["summary"]["participants"] == 1
    assert report["summary"]["sessions"] == 1
    assert report["summary"]["completed"] == 1
    assert report["summary"]["completion_rate"] == 100.0
    assert report["participants"][0]["participant_code"] == participant.participant_code
    assert report["participants"][0]["completed"] is True

    exported = export_research_events_csv(CAMPAIGN)
    rows = list(csv.DictReader(io.StringIO(exported)))
    assert {row["event_name"] for row in rows} >= {
        "miniapp.open",
        "miniapp.event_details",
        "miniapp.event_company.joined",
    }
    assert {row["participant_code"] for row in rows} == {
        participant.participant_code
    }
    assert all(row["session_id"] == SESSION_ID for row in rows)

    general = build_admin_dashboard(7, now=datetime.now(timezone.utc) + timedelta(minutes=1))
    assert general["summary"]["known_users"] == 1
    assert general["summary"]["active_users"] == 1
    assert general["research_campaigns"][0]["campaign"] == CAMPAIGN


def test_research_groups_are_isolated_from_regular_users_and_other_campaigns(
    temp_db,
    user_factory,
    event_factory,
):
    regular_user, _ = user_factory(user_id=1, group_size_min=3, group_size_max=3)
    first_research_user, _ = user_factory(user_id=2, group_size_min=3, group_size_max=3)
    second_research_user, _ = user_factory(user_id=3, group_size_min=3, group_size_max=3)
    other_research_user, _ = user_factory(user_id=4, group_size_min=3, group_size_max=3)
    event_id = event_factory()
    enroll_research_participant(first_research_user, CAMPAIGN)
    enroll_research_participant(second_research_user, CAMPAIGN)
    enroll_research_participant(other_research_user, "ux_channel_sep2026")

    _, regular_group, _ = db.join_event_group(regular_user, event_id)
    _, first_group, _ = db.join_event_group(first_research_user, event_id)
    _, second_group, _ = db.join_event_group(second_research_user, event_id)
    _, other_group, _ = db.join_event_group(other_research_user, event_id)

    assert regular_group is not None
    assert first_group == second_group
    assert regular_group != first_group
    assert other_group not in {regular_group, first_group}
    assert db.get_event_company_counts([event_id])[event_id] == 1
    assert db.get_event_company_counts(
        [event_id], research_campaign=CAMPAIGN
    )[event_id] == 2
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT id, research_campaign FROM event_groups ORDER BY id"
        ).fetchall()
    assert [row["research_campaign"] for row in rows] == [
        None,
        CAMPAIGN,
        "ux_channel_sep2026",
    ]


def test_research_admin_api_tracks_context_and_exports_csv(temp_db, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "99")
    participant = enroll_research_participant(42, CAMPAIGN)
    assert participant is not None
    research_headers = {
        **auth_headers(42),
        "X-Research-Session": SESSION_ID,
    }

    with TestClient(app) as client:
        opened = client.get("/r/api/bootstrap", headers=research_headers)
        tracked = client.post(
            "/r/api/track",
            headers=research_headers,
            json={
                "event": "event_details",
                "metadata": {"event_id": 123, "source_id": "kudago"},
            },
        )
        dashboard = client.get(
            f"/r/api/admin/research?campaign={CAMPAIGN}",
            headers=auth_headers(99),
        )
        exported = client.get(
            f"/r/api/admin/research/export?campaign={CAMPAIGN}",
            headers=auth_headers(99),
        )
        forbidden = client.get(
            f"/r/api/admin/research?campaign={CAMPAIGN}",
            headers=auth_headers(42),
        )

    assert opened.status_code == 200
    assert opened.json()["research"]["participant_code"] == participant.participant_code
    assert tracked.status_code == 200
    assert dashboard.status_code == 200
    assert dashboard.json()["summary"]["participants"] == 1
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert "miniapp.event_details" in exported.text
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_research_start_returns_participant_code(temp_db, monkeypatch):
    monkeypatch.setenv("MINIAPP_URL", "https://bot.example/r/app")
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=77),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())

    await start(message, state, SimpleNamespace(args=CAMPAIGN))

    state.clear.assert_awaited_once()
    message.answer.assert_awaited_once()
    text = message.answer.await_args.args[0]
    assert "участвуете в исследовании" in text
    assert "<code>UX-" in text
    with db.get_connection() as conn:
        saved = conn.execute(
            "SELECT campaign, participant_code FROM research_participants WHERE user_id = 77"
        ).fetchone()
    assert saved["campaign"] == CAMPAIGN
    assert saved["participant_code"] in text
