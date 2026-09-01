"""Research cohorts, pseudonymous sessions and admin-safe UX exports."""

from __future__ import annotations

import csv
import io
import json
import re
import secrets
import sqlite3
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime
from statistics import median

from event_bot.db import DB_DATETIME_FORMAT, get_connection


RESEARCH_CAMPAIGN_PATTERN = re.compile(r"^(?:ux|research)_[a-z0-9_-]{1,55}$")
RESEARCH_SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
PARTICIPANT_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
COMPLETION_EVENT = "miniapp.event_company.joined"

_current_session_id: ContextVar[str | None] = ContextVar(
    "research_session_id",
    default=None,
)


@dataclass(frozen=True)
class ResearchParticipant:
    campaign: str
    user_id: int
    participant_code: str
    enrolled_at: str
    completed_at: str | None


def normalize_research_campaign(value: str | None) -> str | None:
    campaign = (value or "").strip().lower()
    return campaign if RESEARCH_CAMPAIGN_PATTERN.fullmatch(campaign) else None


def _new_participant_code() -> str:
    suffix = "".join(secrets.choice(PARTICIPANT_ALPHABET) for _ in range(6))
    return f"UX-{suffix}"


def enroll_research_participant(
    user_id: int,
    campaign: str | None,
) -> ResearchParticipant | None:
    """Enroll or reactivate a user and return a non-identifying report code."""
    normalized = normalize_research_campaign(campaign)
    if user_id <= 0 or normalized is None:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT campaign, user_id, participant_code, enrolled_at, completed_at
            FROM research_participants
            WHERE campaign = ? AND user_id = ?
            """,
            (normalized, user_id),
        ).fetchone()
        if row is None:
            for _ in range(10):
                code = _new_participant_code()
                try:
                    conn.execute(
                        """
                        INSERT INTO research_participants
                            (campaign, user_id, participant_code)
                        VALUES (?, ?, ?)
                        """,
                        (normalized, user_id, code),
                    )
                    break
                except sqlite3.IntegrityError as error:
                    if "participant_code" not in str(error):
                        raise
            else:
                raise RuntimeError("Не удалось создать уникальный код исследования")
        else:
            conn.execute(
                """
                UPDATE research_participants
                SET active = 1, completed_at = NULL
                WHERE campaign = ? AND user_id = ?
                """,
                (normalized, user_id),
            )
        saved = conn.execute(
            """
            SELECT campaign, user_id, participant_code, enrolled_at, completed_at
            FROM research_participants
            WHERE campaign = ? AND user_id = ?
            """,
            (normalized, user_id),
        ).fetchone()
    assert saved is not None
    return ResearchParticipant(**dict(saved))


def get_research_participant(user_id: int) -> ResearchParticipant | None:
    """Return the latest active research cohort for a Telegram user."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT campaign, user_id, participant_code, enrolled_at, completed_at
            FROM research_participants
            WHERE user_id = ? AND active = 1
            ORDER BY enrolled_at DESC, campaign DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    return ResearchParticipant(**dict(row)) if row is not None else None


def set_research_session_context(value: str | None) -> Token:
    session_id = (value or "").strip()
    if not RESEARCH_SESSION_PATTERN.fullmatch(session_id):
        session_id = None
    return _current_session_id.set(session_id)


def reset_research_session_context(token: Token) -> None:
    _current_session_id.reset(token)


def bind_research_event(
    user_id: int,
    event_name: str,
    metadata: dict[str, object] | None,
    *,
    campaign: str | None = None,
    session_id: str | None = None,
) -> tuple[str | None, str | None, dict[str, object]]:
    """Attach a research campaign/session and update its lifecycle."""
    payload = dict(metadata or {})
    participant = get_research_participant(user_id)
    normalized_campaign = normalize_research_campaign(campaign)
    if participant is not None and normalized_campaign is None:
        normalized_campaign = participant.campaign
    active_session = session_id or _current_session_id.get()
    if active_session and not RESEARCH_SESSION_PATTERN.fullmatch(active_session):
        active_session = None
    if participant is None or normalized_campaign != participant.campaign:
        return None, normalized_campaign, payload

    payload.setdefault("participant_code", participant.participant_code)
    if active_session is not None:
        completed = event_name == COMPLETION_EVENT
        with get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO research_sessions
                    (session_id, user_id, campaign, participant_code)
                VALUES (?, ?, ?, ?)
                """,
                (
                    active_session,
                    user_id,
                    participant.campaign,
                    participant.participant_code,
                ),
            )
            owner = conn.execute(
                """
                SELECT user_id, campaign FROM research_sessions
                WHERE session_id = ?
                """,
                (active_session,),
            ).fetchone()
            if (
                owner is None
                or int(owner["user_id"]) != user_id
                or owner["campaign"] != participant.campaign
            ):
                active_session = None
            else:
                conn.execute(
                    """
                    UPDATE research_sessions
                    SET last_seen_at = datetime('now'),
                        completed_at = CASE
                            WHEN ? THEN COALESCE(completed_at, datetime('now'))
                            ELSE completed_at
                        END
                    WHERE session_id = ? AND user_id = ? AND campaign = ?
                    """,
                    (completed, active_session, user_id, participant.campaign),
                )
                if completed:
                    conn.execute(
                        """
                        UPDATE research_participants
                        SET completed_at = COALESCE(completed_at, datetime('now'))
                        WHERE campaign = ? AND user_id = ?
                        """,
                        (participant.campaign, user_id),
                    )
    return active_session, participant.campaign, payload


def list_research_campaigns() -> list[dict[str, object]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT rp.campaign,
                   COUNT(DISTINCT rp.user_id) AS participants,
                   COUNT(DISTINCT CASE WHEN rp.completed_at IS NOT NULL
                                       THEN rp.user_id END) AS completed,
                   COUNT(DISTINCT rs.session_id) AS sessions,
                   MAX(COALESCE(rs.last_seen_at, rp.enrolled_at)) AS last_activity
            FROM research_participants rp
            LEFT JOIN research_sessions rs
              ON rs.campaign = rp.campaign AND rs.user_id = rp.user_id
            GROUP BY rp.campaign
            ORDER BY MAX(rp.enrolled_at) DESC, rp.campaign
            """
        ).fetchall()
    return [
        {
            "campaign": row["campaign"],
            "participants": int(row["participants"] or 0),
            "completed": int(row["completed"] or 0),
            "sessions": int(row["sessions"] or 0),
            "last_activity": row["last_activity"],
        }
        for row in rows
    ]


RESEARCH_FUNNEL = (
    ("enrolled", "Получили код", None),
    ("opened_app", "Открыли Mini App", "miniapp.open"),
    ("profiled", "Сохранили профиль", "miniapp.profile_saved"),
    ("event_opened", "Открыли мероприятие", "miniapp.event_details"),
    ("company_prompt", "Открыли поиск компании", "miniapp.company_prompt_opened"),
    ("joined_company", "Запустили поиск", "miniapp.event_company.joined"),
    ("group_opened", "Открыли компанию", "miniapp.group_details"),
)


def _parse_db_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, DB_DATETIME_FORMAT)
    except ValueError:
        return None


def build_research_dashboard(campaign: str) -> dict[str, object]:
    normalized = normalize_research_campaign(campaign)
    if normalized is None:
        raise ValueError("Недопустимая исследовательская кампания")
    with get_connection() as conn:
        participants = conn.execute(
            """
            SELECT rp.participant_code, rp.enrolled_at, rp.completed_at,
                   COUNT(DISTINCT rs.session_id) AS sessions,
                   COUNT(DISTINCT ue.id) AS events,
                   MIN(rs.started_at) AS first_session,
                   MAX(rs.last_seen_at) AS last_seen,
                   COALESCE((
                       SELECT SUM(CAST(
                           (julianday(rs2.last_seen_at) - julianday(rs2.started_at))
                           * 86400 AS INTEGER
                       ))
                       FROM research_sessions rs2
                       WHERE rs2.campaign = rp.campaign
                         AND rs2.user_id = rp.user_id
                   ), 0) AS duration_seconds,
                   (
                       SELECT ue2.event_name FROM usage_events ue2
                       WHERE ue2.campaign = rp.campaign
                         AND json_extract(ue2.metadata, '$.participant_code') = rp.participant_code
                       ORDER BY ue2.created_at DESC, ue2.id DESC LIMIT 1
                   ) AS last_event
            FROM research_participants rp
            LEFT JOIN research_sessions rs
              ON rs.campaign = rp.campaign AND rs.user_id = rp.user_id
            LEFT JOIN usage_events ue
              ON ue.campaign = rp.campaign
             AND json_extract(ue.metadata, '$.participant_code') = rp.participant_code
            WHERE rp.campaign = ?
            GROUP BY rp.campaign, rp.user_id
            ORDER BY rp.enrolled_at, rp.participant_code
            """,
            (normalized,),
        ).fetchall()
        funnel_counts: dict[str, int] = {}
        for key, _, event_name in RESEARCH_FUNNEL:
            if event_name is None:
                funnel_counts[key] = len(participants)
            else:
                funnel_counts[key] = int(
                    conn.execute(
                        """
                        SELECT COUNT(DISTINCT json_extract(metadata, '$.participant_code'))
                        FROM usage_events
                        WHERE campaign = ? AND event_name = ?
                        """,
                        (normalized, event_name),
                    ).fetchone()[0]
                    or 0
                )
        top_events = conn.execute(
            """
            SELECT event_name, COUNT(*) AS amount,
                   COUNT(DISTINCT json_extract(metadata, '$.participant_code')) AS participants
            FROM usage_events
            WHERE campaign = ? AND event_name != 'miniapp.session_heartbeat'
            GROUP BY event_name
            ORDER BY amount DESC, event_name
            LIMIT 20
            """,
            (normalized,),
        ).fetchall()
        session_rows = conn.execute(
            """
            SELECT started_at, last_seen_at FROM research_sessions
            WHERE campaign = ?
            """,
            (normalized,),
        ).fetchall()

    durations = []
    for row in session_rows:
        started = _parse_db_time(row["started_at"])
        last_seen = _parse_db_time(row["last_seen_at"])
        if started is not None and last_seen is not None:
            durations.append(max(0, int((last_seen - started).total_seconds())))
    total = len(participants)
    participant_rows = []
    for row in participants:
        participant_rows.append(
            {
                "participant_code": row["participant_code"],
                "sessions": int(row["sessions"] or 0),
                "events": int(row["events"] or 0),
                "duration_seconds": max(0, int(row["duration_seconds"] or 0)),
                "completed": row["completed_at"] is not None,
                "enrolled_at": row["enrolled_at"],
                "last_seen_at": row["last_seen"],
                "last_event": row["last_event"],
            }
        )
    return {
        "campaign": normalized,
        "summary": {
            "participants": total,
            "sessions": len(session_rows),
            "completed": sum(1 for row in participants if row["completed_at"] is not None),
            "completion_rate": round(funnel_counts["joined_company"] * 100 / total, 1) if total else 0.0,
            "median_session_seconds": int(median(durations)) if durations else 0,
        },
        "funnel": [
            {
                "stage": key,
                "label": label,
                "users": funnel_counts[key],
                "conversion": round(funnel_counts[key] * 100 / total, 1) if total else 0.0,
            }
            for key, label, _ in RESEARCH_FUNNEL
        ],
        "top_events": [
            {
                "event": row["event_name"],
                "amount": int(row["amount"] or 0),
                "participants": int(row["participants"] or 0),
            }
            for row in top_events
        ],
        "participants": participant_rows,
    }


def export_research_events_csv(campaign: str) -> str:
    normalized = normalize_research_campaign(campaign)
    if normalized is None:
        raise ValueError("Недопустимая исследовательская кампания")
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT json_extract(ue.metadata, '$.participant_code') AS participant_code,
                   ue.session_id, rs.started_at, rs.last_seen_at, rs.completed_at,
                   ue.created_at, ue.event_name, ue.source, ue.metadata
            FROM usage_events ue
            LEFT JOIN research_sessions rs ON rs.session_id = ue.session_id
            WHERE ue.campaign = ?
            ORDER BY participant_code, ue.created_at, ue.id
            """,
            (normalized,),
        ).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "campaign",
            "participant_code",
            "session_id",
            "session_started_at_utc",
            "session_last_seen_at_utc",
            "session_completed_at_utc",
            "event_at_utc",
            "seconds_from_session_start",
            "event_name",
            "source",
            "metadata_json",
        ]
    )
    for row in rows:
        started = _parse_db_time(row["started_at"])
        event_at = _parse_db_time(row["created_at"])
        offset = (
            max(0, int((event_at - started).total_seconds()))
            if started is not None and event_at is not None
            else ""
        )
        metadata = json.loads(row["metadata"] or "{}")
        metadata.pop("participant_code", None)
        writer.writerow(
            [
                normalized,
                row["participant_code"] or "",
                row["session_id"] or "",
                row["started_at"] or "",
                row["last_seen_at"] or "",
                row["completed_at"] or "",
                row["created_at"],
                offset,
                row["event_name"],
                row["source"],
                json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
            ]
        )
    return output.getvalue()
