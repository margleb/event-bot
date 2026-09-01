from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from event_bot.analytics import EVENT_LABELS, get_admin_ids
from event_bot.db import DB_DATETIME_FORMAT, get_connection
from event_bot.inactivity_feedback import INACTIVITY_FEEDBACK_LABELS


MOSCOW_TZ = ZoneInfo("Europe/Moscow")
VISIT_EVENTS = ("miniapp.open", "command.start")
ADMIN_EVENTS = (
    "command.admin",
    "command.feedbacks",
    "command.reply",
    "command.reports",
    "command.reportdone",
    "command.ban",
    "command.unban",
)
PASSIVE_EVENTS = (
    *VISIT_EVENTS,
    "miniapp.tab.feed",
    "miniapp.tab.my",
    "miniapp.tab.group",
    "miniapp.tab.profile",
    "miniapp.tab.admin",
)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime(DB_DATETIME_FORMAT)


def _known_users(conn, admin_ids: list[int]) -> int:
    if admin_ids:
        placeholders = ",".join("?" for _ in admin_ids)
        return conn.execute(
            f"""
            SELECT COUNT(*) AS amount FROM (
                SELECT telegram_id AS user_id FROM users
                WHERE telegram_id NOT IN ({placeholders})
                UNION
                SELECT user_id FROM usage_events
                WHERE user_id NOT IN ({placeholders})
            )
            """,
            (*admin_ids, *admin_ids),
        ).fetchone()["amount"]
    return conn.execute(
        """
        SELECT COUNT(*) AS amount FROM (
            SELECT telegram_id AS user_id FROM users
            UNION
            SELECT user_id FROM usage_events
        )
        """
    ).fetchone()["amount"]


def build_admin_dashboard(
    days: int = 30,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Агрегаты для приватного Mini App-дашборда без персональных данных."""
    if days not in {7, 30, 90}:
        raise ValueError("Период должен быть 7, 30 или 90 дней")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current_msk = current.astimezone(MOSCOW_TZ).replace(microsecond=0)
    start_date = current_msk.date() - timedelta(days=days - 1)
    start_msk = datetime.combine(start_date, time.min, tzinfo=MOSCOW_TZ)
    previous_start_msk = start_msk - (current_msk - start_msk)

    start_text = _utc_text(start_msk)
    end_text = _utc_text(current_msk)
    previous_start_text = _utc_text(previous_start_msk)
    previous_end_text = start_text

    admin_ids = sorted(get_admin_ids())
    admin_filter = (
        f" AND user_id NOT IN ({','.join('?' for _ in admin_ids)})"
        if admin_ids
        else ""
    )

    def scalar(conn, sql: str, params: tuple[object, ...]) -> int:
        return int(conn.execute(sql, params).fetchone()["amount"] or 0)

    def active_users(conn, start: str, end: str) -> int:
        return scalar(
            conn,
            """
            SELECT COUNT(DISTINCT user_id) AS amount
            FROM usage_events
            WHERE created_at >= ? AND created_at < ?
            """
            + admin_filter,
            (start, end, *admin_ids),
        )

    def actions(conn, start: str, end: str) -> int:
        return scalar(
            conn,
            """
            SELECT COUNT(*) AS amount
            FROM usage_events
            WHERE created_at >= ? AND created_at < ?
            """
            + admin_filter,
            (start, end, *admin_ids),
        )

    def visits(conn, start: str, end: str) -> int:
        placeholders = ",".join("?" for _ in VISIT_EVENTS)
        return scalar(
            conn,
            f"""
            SELECT COUNT(*) AS amount
            FROM usage_events
            WHERE created_at >= ? AND created_at < ?
              AND event_name IN ({placeholders})
            """
            + admin_filter,
            (start, end, *VISIT_EVENTS, *admin_ids),
        )

    with get_connection() as conn:
        known_users = _known_users(conn, admin_ids)
        active = active_users(conn, start_text, end_text)
        previous_active = active_users(
            conn,
            previous_start_text,
            previous_end_text,
        )
        action_count = actions(conn, start_text, end_text)
        previous_actions = actions(
            conn,
            previous_start_text,
            previous_end_text,
        )
        visit_count = visits(conn, start_text, end_text)
        previous_visits = visits(
            conn,
            previous_start_text,
            previous_end_text,
        )

        new_users = scalar(
            conn,
            """
            SELECT COUNT(*) AS amount
            FROM (
                SELECT user_id, MIN(created_at) AS first_seen
                FROM usage_events
                GROUP BY user_id
            ) AS first_usage
            WHERE first_seen >= ? AND first_seen < ?
            """
            + admin_filter,
            (start_text, end_text, *admin_ids),
        )
        passive_placeholders = ",".join("?" for _ in PASSIVE_EVENTS)
        engaged = scalar(
            conn,
            f"""
            SELECT COUNT(DISTINCT user_id) AS amount
            FROM usage_events
            WHERE created_at >= ? AND created_at < ?
              AND event_name NOT IN ({passive_placeholders})
            """
            + admin_filter,
            (start_text, end_text, *PASSIVE_EVENTS, *admin_ids),
        )
        frequency_rows = conn.execute(
            """
            SELECT user_id,
                   COUNT(DISTINCT date(created_at, '+3 hours')) AS active_days
            FROM usage_events
            WHERE created_at >= ? AND created_at < ?
            """
            + admin_filter
            + """
            GROUP BY user_id
            """,
            (start_text, end_text, *admin_ids),
        ).fetchall()
        returning = sum(1 for row in frequency_rows if row["active_days"] >= 2)
        active_days_total = sum(row["active_days"] for row in frequency_rows)

        visit_placeholders = ",".join("?" for _ in VISIT_EVENTS)
        daily_rows = conn.execute(
            f"""
            SELECT date(created_at, '+3 hours') AS day,
                   COUNT(DISTINCT user_id) AS active_users,
                   COUNT(*) AS actions,
                   SUM(CASE WHEN event_name IN ({visit_placeholders})
                            THEN 1 ELSE 0 END) AS visits
            FROM usage_events
            WHERE created_at >= ? AND created_at < ?
            """
            + admin_filter
            + """
            GROUP BY date(created_at, '+3 hours')
            ORDER BY day
            """,
            (*VISIT_EVENTS, start_text, end_text, *admin_ids),
        ).fetchall()

        admin_placeholders = ",".join("?" for _ in ADMIN_EVENTS)
        feature_rows = conn.execute(
            f"""
            SELECT event_name,
                   COUNT(*) AS amount,
                   COUNT(DISTINCT user_id) AS users
            FROM usage_events
            WHERE created_at >= ? AND created_at < ?
              AND event_name NOT IN ({admin_placeholders})
            """
            + admin_filter
            + """
            GROUP BY event_name
            ORDER BY amount DESC, event_name
            LIMIT 10
            """,
            (start_text, end_text, *ADMIN_EVENTS, *admin_ids),
        ).fetchall()
        source_rows = conn.execute(
            """
            SELECT source,
                   COUNT(*) AS amount,
                   COUNT(DISTINCT user_id) AS users
            FROM usage_events
            WHERE created_at >= ? AND created_at < ?
            """
            + admin_filter
            + """
            GROUP BY source
            ORDER BY amount DESC, source
            """,
            (start_text, end_text, *admin_ids),
        ).fetchall()
        feedback = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) AS fresh
            FROM feedback_messages
            """
        ).fetchone()
        inactivity_feedback = conn.execute(
            """
            SELECT
                SUM(CASE WHEN delivery_status = 'sent' THEN 1 ELSE 0 END)
                    AS prompts_sent,
                SUM(CASE WHEN delivery_status = 'sent'
                              AND response_code IS NOT NULL THEN 1 ELSE 0 END)
                    AS responses
            FROM inactivity_feedback_prompts
            WHERE prompt_sent_at >= ? AND prompt_sent_at < ?
            """
            + admin_filter,
            (start_text, end_text, *admin_ids),
        ).fetchone()
        inactivity_reason_rows = conn.execute(
            """
            SELECT response_code, COUNT(*) AS amount
            FROM inactivity_feedback_prompts
            WHERE prompt_sent_at >= ? AND prompt_sent_at < ?
              AND delivery_status = 'sent'
              AND response_code IS NOT NULL
            """
            + admin_filter
            + """
            GROUP BY response_code
            ORDER BY amount DESC, response_code
            """,
            (start_text, end_text, *admin_ids),
        ).fetchall()
        cohort_admin_clause = (
            f" AND user_id NOT IN ({','.join('?' for _ in admin_ids)})"
            if admin_ids
            else ""
        )
        funnel = conn.execute(
            """
            WITH cohort AS (
                SELECT user_id, MIN(created_at) AS first_seen
                FROM usage_events
                GROUP BY user_id
                HAVING first_seen >= ? AND first_seen < ?
            )
            SELECT
                COUNT(*) AS discovered,
                SUM(EXISTS(
                    SELECT 1 FROM usage_events ue
                    WHERE ue.user_id = cohort.user_id
                      AND ue.event_name = 'miniapp.open'
                )) AS opened_app,
                SUM(EXISTS(
                    SELECT 1 FROM users u WHERE u.telegram_id = cohort.user_id
                )) AS profiled,
                SUM(EXISTS(
                    SELECT 1 FROM intents i WHERE i.user_id = cohort.user_id
                )) AS saved_event,
                SUM(EXISTS(
                    SELECT 1 FROM event_group_members gm
                    WHERE gm.user_id = cohort.user_id
                )) AS searched_company,
                SUM(EXISTS(
                    SELECT 1 FROM requests r
                    WHERE (r.from_user = cohort.user_id OR r.to_user = cohort.user_id)
                      AND r.status = 'accepted'
                )) AS connected
            FROM cohort
            WHERE 1 = 1
            """
            + cohort_admin_clause,
            (start_text, end_text, *admin_ids),
        ).fetchone()
        campaign_admin_clause = (
            f" AND ua.user_id NOT IN ({','.join('?' for _ in admin_ids)})"
            if admin_ids
            else ""
        )
        campaign_rows = conn.execute(
            """
            SELECT ua.campaign,
                   COUNT(*) AS users,
                   SUM(EXISTS(
                       SELECT 1 FROM usage_events ue
                       WHERE ue.user_id = ua.user_id
                         AND ue.event_name = 'miniapp.open'
                   )) AS opened_app,
                   SUM(EXISTS(
                       SELECT 1 FROM users u WHERE u.telegram_id = ua.user_id
                   )) AS profiled,
                   SUM(EXISTS(
                       SELECT 1 FROM event_group_members gm
                       WHERE gm.user_id = ua.user_id
                   )) AS searched_company
            FROM user_acquisition ua
            WHERE ua.first_seen_at >= ? AND ua.first_seen_at < ?
            """
            + campaign_admin_clause
            + """
            GROUP BY ua.campaign
            ORDER BY users DESC, ua.campaign
            LIMIT 20
            """,
            (start_text, end_text, *admin_ids),
        ).fetchall()
        experience_rows = conn.execute(
            """
            SELECT outcome, COUNT(*) AS amount
            FROM event_experience_feedback
            WHERE created_at >= ? AND created_at < ?
            GROUP BY outcome
            ORDER BY amount DESC, outcome
            """,
            (start_text, end_text),
        ).fetchall()
        reports = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) AS fresh
            FROM user_reports
            WHERE created_at >= ? AND created_at < ?
            """,
            (start_text, end_text),
        ).fetchone()
        source_health_rows = conn.execute(
            """
            WITH source_ids AS (
                SELECT DISTINCT source_id
                FROM events
                WHERE source_id IS NOT NULL
                UNION
                SELECT DISTINCT source_id FROM source_sync_runs
            ), active_counts AS (
                SELECT source_id, COUNT(*) AS active_events
                FROM events
                WHERE source_id IS NOT NULL
                  AND COALESCE(status, 'active') = 'active'
                  AND COALESCE(end_date, date) > datetime('now', '+3 hours')
                GROUP BY source_id
            ), latest AS (
                SELECT source_id, MAX(id) AS run_id
                FROM source_sync_runs
                GROUP BY source_id
            )
            SELECT ids.source_id, COALESCE(counts.active_events, 0) AS active_events,
                   runs.status, runs.added, runs.updated, runs.skipped,
                   runs.errors, runs.fetched, runs.error_message,
                   runs.started_at, runs.finished_at
            FROM source_ids ids
            LEFT JOIN active_counts counts ON counts.source_id = ids.source_id
            LEFT JOIN latest ON latest.source_id = ids.source_id
            LEFT JOIN source_sync_runs runs ON runs.id = latest.run_id
            ORDER BY ids.source_id
            """
        ).fetchall()

    daily_by_date = {row["day"]: row for row in daily_rows}
    daily = []
    for offset in range(days):
        day = (start_date + timedelta(days=offset)).isoformat()
        row = daily_by_date.get(day)
        daily.append(
            {
                "date": day,
                "active_users": int(row["active_users"] if row else 0),
                "visits": int(row["visits"] if row else 0),
                "actions": int(row["actions"] if row else 0),
            }
        )

    frequency = {
        "one_day": sum(1 for row in frequency_rows if row["active_days"] == 1),
        "two_three_days": sum(
            1 for row in frequency_rows if 2 <= row["active_days"] <= 3
        ),
        "four_seven_days": sum(
            1 for row in frequency_rows if 4 <= row["active_days"] <= 7
        ),
        "eight_plus_days": sum(
            1 for row in frequency_rows if row["active_days"] >= 8
        ),
    }

    def percentage(value: int, total: int) -> float:
        return round(value * 100 / total, 1) if total else 0.0

    prompts_sent = int(inactivity_feedback["prompts_sent"] or 0)
    inactivity_responses = int(inactivity_feedback["responses"] or 0)
    funnel_labels = (
        ("discovered", "Пришли в бот"),
        ("opened_app", "Открыли Mini App"),
        ("profiled", "Заполнили профиль"),
        ("saved_event", "Отметили событие"),
        ("searched_company", "Искали компанию"),
        ("connected", "Обменялись контактами"),
    )
    funnel_total = int(funnel["discovered"] or 0)
    experience_labels = {
        "met": "Встретились",
        "solo": "Сходили одни",
        "no_show": "Никто не пришёл",
        "unsafe": "Было некомфортно",
    }
    stale_before = current.astimezone(timezone.utc).replace(tzinfo=None) - timedelta(hours=36)
    source_health = []
    for row in source_health_rows:
        finished = None
        if row["finished_at"]:
            try:
                finished = datetime.strptime(row["finished_at"], DB_DATETIME_FORMAT)
            except ValueError:
                finished = None
        health = "unknown"
        if row["status"] == "failed":
            health = "failed"
        elif finished is not None and finished < stale_before:
            health = "stale"
        elif row["status"] in {"success", "warning"}:
            health = row["status"]
        source_health.append(
            {
                "source_id": row["source_id"],
                "health": health,
                "active_events": int(row["active_events"] or 0),
                "fetched": int(row["fetched"] or 0),
                "added": int(row["added"] or 0),
                "updated": int(row["updated"] or 0),
                "skipped": int(row["skipped"] or 0),
                "errors": int(row["errors"] or 0),
                "error_message": row["error_message"],
                "finished_at": row["finished_at"],
            }
        )

    return {
        "days": days,
        "period": {
            "from": start_date.isoformat(),
            "to": current_msk.date().isoformat(),
            "generated_at": current_msk.isoformat(),
        },
        "summary": {
            "known_users": known_users,
            "active_users": active,
            "previous_active_users": previous_active,
            "new_users": new_users,
            "engaged_users": engaged,
            "returning_users": returning,
            "dormant_users": max(0, known_users - active),
            "visits": visit_count,
            "previous_visits": previous_visits,
            "actions": action_count,
            "previous_actions": previous_actions,
            "usage_rate": percentage(active, known_users),
            "engagement_rate": percentage(engaged, active),
            "returning_rate": percentage(returning, active),
            "actions_per_active": round(action_count / active, 1) if active else 0.0,
            "visits_per_active": round(visit_count / active, 1) if active else 0.0,
            "active_days_per_user": round(active_days_total / active, 1) if active else 0.0,
        },
        "daily": daily,
        "frequency": frequency,
        "top_features": [
            {
                "event": row["event_name"],
                "label": EVENT_LABELS.get(row["event_name"], row["event_name"]),
                "amount": int(row["amount"]),
                "users": int(row["users"]),
            }
            for row in feature_rows
        ],
        "sources": [
            {
                "source": row["source"],
                "label": (
                    "Telegram-бот"
                    if row["source"] == "bot"
                    else "Mini App"
                    if row["source"] == "miniapp"
                    else row["source"]
                ),
                "amount": int(row["amount"]),
                "users": int(row["users"]),
            }
            for row in source_rows
        ],
        "feedback": {
            "total": int(feedback["total"] or 0),
            "new": int(feedback["fresh"] or 0),
        },
        "inactivity_feedback": {
            "prompts_sent": prompts_sent,
            "responses": inactivity_responses,
            "response_rate": percentage(inactivity_responses, prompts_sent),
            "reasons": [
                {
                    "code": row["response_code"],
                    "label": INACTIVITY_FEEDBACK_LABELS.get(
                        row["response_code"], row["response_code"]
                    ),
                    "amount": int(row["amount"]),
                }
                for row in inactivity_reason_rows
            ],
        },
        "funnel": [
            {
                "stage": key,
                "label": label,
                "users": int(funnel[key] or 0),
                "conversion": percentage(int(funnel[key] or 0), funnel_total),
            }
            for key, label in funnel_labels
        ],
        "campaigns": [
            {
                "campaign": row["campaign"],
                "users": int(row["users"] or 0),
                "opened_app": int(row["opened_app"] or 0),
                "profiled": int(row["profiled"] or 0),
                "searched_company": int(row["searched_company"] or 0),
            }
            for row in campaign_rows
        ],
        "company_outcomes": [
            {
                "outcome": row["outcome"],
                "label": experience_labels.get(row["outcome"], row["outcome"]),
                "amount": int(row["amount"]),
            }
            for row in experience_rows
        ],
        "reports": {
            "total": int(reports["total"] or 0),
            "new": int(reports["fresh"] or 0),
        },
        "source_health": source_health,
    }
