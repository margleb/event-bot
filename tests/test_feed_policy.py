import pytest

import event_bot.db as db
from event_bot.analytics import record_usage
from event_bot.research_analytics import enroll_research_participant
from event_bot.webapp import _adaptive_recommendation_limit


@pytest.mark.parametrize(
    ("active_users", "expected"),
    [
        (0, 6),
        (49, 6),
        (50, 8),
        (149, 8),
        (150, 12),
        (349, 12),
        (350, 16),
        (749, 16),
        (750, 20),
    ],
)
def test_recommendation_limit_grows_with_active_audience(active_users, expected):
    assert _adaptive_recommendation_limit(active_users) == expected


def test_active_audience_excludes_admins_and_research_users(temp_db):
    record_usage(1, "miniapp.open", "miniapp")
    record_usage(2, "miniapp.open", "miniapp")
    record_usage(3, "miniapp.open", "miniapp")
    enroll_research_participant(3, "ux_feed_policy")

    assert db.get_active_audience_size(excluded_user_ids={1}) == 1


def test_existing_demand_softly_promotes_relevant_events(
    temp_db,
    event_factory,
    user_factory,
):
    event_ids = [
        event_factory(title=f"Событие {index}", external_id=f"demand-{index}")
        for index in range(5)
    ]
    events = [db.get_event(event_id) for event_id in event_ids]
    assert all(event is not None for event in events)

    for user_id in (10, 11):
        user_factory(user_id=user_id)
        db.save_intent(user_id, event_ids[4], "interested")

    prioritized = db.prioritize_events_by_demand(events)  # type: ignore[arg-type]

    assert [event.id for event in prioritized] == [
        event_ids[0],
        event_ids[4],
        event_ids[1],
        event_ids[2],
        event_ids[3],
    ]
