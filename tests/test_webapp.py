import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from event_bot.analytics import build_admin_report, get_recent_feedback
import event_bot.db as db
import event_bot.webapp as webapp_module
from event_bot.webapp import app, validate_init_data


BOT_TOKEN = "123456:test-token"


def signed_init_data(
    *,
    user_id: int = 42,
    first_name: str = "Мария",
    auth_date: int | None = None,
) -> str:
    values = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAE-test-query",
        "user": json.dumps(
            {
                "id": user_id,
                "first_name": first_name,
                "username": "maria_test",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(values.items())
    )
    secret = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256,
    ).digest()
    values["hash"] = hmac.new(
        secret,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(values)


def auth_headers(user_id: int = 42) -> dict[str, str]:
    return {"X-Telegram-Init-Data": signed_init_data(user_id=user_id)}


def test_init_data_signature_and_freshness(monkeypatch):
    monkeypatch.setenv("MINIAPP_AUTH_MAX_AGE_SECONDS", "3600")
    now = 2_000_000_000
    valid = signed_init_data(auth_date=now - 30)

    user = validate_init_data(valid, BOT_TOKEN, now=now)

    assert user.id == 42
    assert user.first_name == "Мария"

    tampered = valid.replace("maria_test", "attacker")
    try:
        validate_init_data(tampered, BOT_TOKEN, now=now)
    except ValueError as error:
        assert "подпись" in str(error)
    else:
        raise AssertionError("изменённый initData не должен пройти проверку")

    stale = signed_init_data(auth_date=now - 3601)
    try:
        validate_init_data(stale, BOT_TOKEN, now=now)
    except ValueError as error:
        assert "устарели" in str(error)
    else:
        raise AssertionError("устаревший initData не должен пройти проверку")


def test_api_rejects_request_without_telegram_signature(temp_db, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    with TestClient(app) as client:
        response = client.get("/r/api/bootstrap")

    assert response.status_code == 401


def test_profile_bootstrap_and_schedule(temp_db, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    payload = {
        "interests": ["Театр", " Выставки "],
        "avoid": ["спорт"],
        "days": ["sat", "fri", "sat"],
        "budget_rub": 3000,
        "preferred_group_size_min": 2,
        "preferred_group_size_max": 5,
        "digest_weekday": 4,
    }

    with TestClient(app) as client:
        saved = client.put(
            "/r/api/profile",
            headers=auth_headers(),
            json=payload,
        )
        loaded = client.get("/r/api/bootstrap", headers=auth_headers())

    assert saved.status_code == 200
    assert loaded.status_code == 200
    body = loaded.json()
    assert body["profile"]["interests"] == ["Театр", "Выставки"]
    assert body["profile"]["days"] == ["fri", "sat"]
    assert body["digest_weekday"] == 4
    assert body["group_matching_enabled"] is False
    assert body["group"] is None
    assert body["user"]["first_name"] == "Мария"


def test_profile_can_opt_in_to_interest_group(temp_db, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    payload = {
        "interests": ["Концерты", "Выставки"],
        "avoid": [],
        "days": ["sat"],
        "budget_rub": 2500,
        "preferred_group_size_min": 3,
        "preferred_group_size_max": 5,
        "digest_weekday": None,
        "group_matching_enabled": True,
    }

    with TestClient(app) as client:
        response = client.put(
            "/r/api/profile",
            headers=auth_headers(),
            json=payload,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["group_matching_enabled"] is True
    assert body["group"]["status"] == "forming"
    assert body["group"]["member_count"] == 1
    assert body["group"]["members"][0]["is_me"] is True


def test_group_opt_in_rejects_incompatible_company_size(temp_db, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    payload = {
        "interests": ["Театр"],
        "avoid": [],
        "days": [],
        "budget_rub": None,
        "preferred_group_size_min": 1,
        "preferred_group_size_max": 2,
        "digest_weekday": None,
        "group_matching_enabled": True,
    }

    with TestClient(app) as client:
        response = client.put(
            "/r/api/profile",
            headers=auth_headers(),
            json=payload,
        )

    assert response.status_code == 422


def test_miniapp_tracks_tabs_and_accepts_feedback(temp_db, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "")

    with TestClient(app) as client:
        tracked = client.post(
            "/r/api/track",
            headers=auth_headers(),
            json={"event": "tab.group"},
        )
        feedback = client.post(
            "/r/api/feedback",
            headers=auth_headers(),
            json={"message": "Добавьте фильтр по району"},
        )

    assert tracked.status_code == 200
    assert feedback.status_code == 200
    assert feedback.json()["feedback_id"] > 0
    assert get_recent_feedback()[0].message == "Добавьте фильтр по району"
    assert ("miniapp.tab.group", 1) in build_admin_report()["top_features"]


def test_miniapp_rejects_unknown_tracking_event(temp_db, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    with TestClient(app) as client:
        response = client.post(
            "/r/api/track",
            headers=auth_headers(),
            json={"event": "arbitrary.private.data"},
        )

    assert response.status_code == 422


def test_admin_dashboard_is_visible_only_to_configured_admin(temp_db, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "42")

    with TestClient(app) as client:
        admin_bootstrap = client.get("/r/api/bootstrap", headers=auth_headers(42))
        user_bootstrap = client.get("/r/api/bootstrap", headers=auth_headers(43))
        dashboard = client.get(
            "/r/api/admin/analytics?days=7",
            headers=auth_headers(42),
        )
        forbidden = client.get(
            "/r/api/admin/analytics?days=7",
            headers=auth_headers(43),
        )
        invalid_period = client.get(
            "/r/api/admin/analytics?days=8",
            headers=auth_headers(42),
        )
        tracked = client.post(
            "/r/api/track",
            headers=auth_headers(42),
            json={"event": "tab.admin"},
        )

    assert admin_bootstrap.status_code == 200
    assert admin_bootstrap.json()["is_admin"] is True
    assert user_bootstrap.status_code == 200
    assert user_bootstrap.json()["is_admin"] is False
    assert dashboard.status_code == 200
    assert dashboard.json()["days"] == 7
    assert forbidden.status_code == 403
    assert invalid_period.status_code == 422
    assert tracked.status_code == 200


def test_event_intent_and_visibility(
    temp_db,
    monkeypatch,
    user_factory,
    event_factory,
):
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    user_id, _ = user_factory(user_id=42, interests=["музыка"])
    event_id = event_factory(tags=["музыка"])

    with TestClient(app) as client:
        selected = client.put(
            f"/r/api/events/{event_id}/intent",
            headers=auth_headers(user_id),
            json={"status": "going"},
        )
        visible = client.put(
            f"/r/api/events/{event_id}/visibility",
            headers=auth_headers(user_id),
            json={"visible": True},
        )

    assert selected.status_code == 200
    assert selected.json()["intent"] == "going"
    assert visible.status_code == 200
    assert visible.json()["visible"] is True


def test_group_api_supports_contact_invite_and_chat(
    temp_db,
    monkeypatch,
    user_factory,
    event_factory,
):
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(
        webapp_module,
        "notify_group_connection_request",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        webapp_module,
        "notify_group_connection_accepted",
        AsyncMock(return_value=2),
    )
    monkeypatch.setattr(
        webapp_module,
        "notify_group_event_invite",
        AsyncMock(return_value=2),
    )
    monkeypatch.setattr(
        webapp_module,
        "notify_group_message",
        AsyncMock(return_value=2),
    )
    users = [
        user_factory(
            user_id=user_id,
            interests=["Концерты"],
            username=f"member{user_id}",
        )[0]
        for user_id in (42, 43, 44)
    ]
    for user_id in users:
        assert db.set_group_matching_enabled(user_id, True)
        assert db.assign_user_to_interest_group(user_id) is not None
    event_id = event_factory(title="Совместный концерт")

    with TestClient(app) as client:
        first_group = client.get("/r/api/group", headers=auth_headers(users[0])).json()
        target = next(member for member in first_group["members"] if member["name"] == "User 43")
        assert target["contact"] is None
        assert target["member_key"] != str(users[1])
        assert len(target["member_key"]) == 24
        requested = client.post(
            f"/r/api/group/connections/{target['member_key']}",
            headers=auth_headers(users[0]),
        )
        second_group = client.get("/r/api/group", headers=auth_headers(users[1])).json()
        incoming = next(member for member in second_group["members"] if member["name"] == "User 42")
        assert incoming["contact"] is None
        accepted = client.post(
            f"/r/api/group/connections/{incoming['request_id']}/accept",
            headers=auth_headers(users[1]),
        )
        message = client.post(
            "/r/api/group/messages",
            headers=auth_headers(users[0]),
            json={"message": "Встречаемся у метро"},
        )
        invitation = client.post(
            "/r/api/group/invites",
            headers=auth_headers(users[0]),
            json={"event_id": event_id},
        )
        invite_id = invitation.json()["group"]["invites"][0]["id"]
        response = client.put(
            f"/r/api/group/invites/{invite_id}/response",
            headers=auth_headers(users[1]),
            json={"status": "going"},
        )

    assert requested.status_code == 200
    assert accepted.status_code == 200
    connected = next(
        member
        for member in accepted.json()["group"]["members"]
        if member["name"] == "User 42"
    )
    assert connected["connection_state"] == "connected"
    assert connected["contact"]["url"] == "https://t.me/member42"
    assert message.status_code == 200
    assert message.json()["group"]["messages"][-1]["message"] == "Встречаемся у метро"
    assert invitation.status_code == 200
    assert response.status_code == 200
    assert response.json()["group"]["invites"][0]["my_response"] == "going"
    assert db.get_user_intent(users[1], event_id).status == "going"


def test_event_first_company_flow_is_scoped_to_one_event(
    temp_db,
    monkeypatch,
    user_factory,
    event_factory,
):
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(
        webapp_module,
        "_notify_event_company",
        AsyncMock(return_value=0),
    )
    users = [
        user_factory(
            user_id=user_id,
            interests=["Концерты", "Выставки"],
            username=f"eventmember{user_id}",
        )[0]
        for user_id in (42, 43, 44)
    ]
    concert_id = event_factory(title="Концерт для компании")
    exhibition_id = event_factory(title="Выставка для компании")

    with TestClient(app) as client:
        waiting = client.post(
            f"/r/api/events/{concert_id}/company",
            headers=auth_headers(users[0]),
        )
        discovery = client.get(
            "/r/api/bootstrap",
            headers=auth_headers(users[2]),
        )
        active = client.post(
            f"/r/api/events/{concert_id}/company",
            headers=auth_headers(users[1]),
        )
        other_event = client.post(
            f"/r/api/events/{exhibition_id}/company",
            headers=auth_headers(users[0]),
        )

        concert_group = active.json()["event_group"]
        concert_group_id = concert_group["id"]
        exhibition_group_id = other_event.json()["event_group"]["id"]
        target = next(member for member in concert_group["members"] if not member["is_me"])

        requested = client.post(
            f"/r/api/event-groups/{concert_group_id}/connections/{target['member_key']}",
            headers=auth_headers(users[1]),
        )
        recipient_group = client.get(
            f"/r/api/event-groups/{concert_group_id}",
            headers=auth_headers(users[0]),
        ).json()
        incoming = next(
            member
            for member in recipient_group["members"]
            if member["connection_state"] == "pending_received"
        )
        accepted = client.post(
            f"/r/api/event-groups/{concert_group_id}/connections/{incoming['request_id']}/accept",
            headers=auth_headers(users[0]),
        )
        message = client.post(
            f"/r/api/event-groups/{concert_group_id}/messages",
            headers=auth_headers(users[0]),
            json={"message": "Встречаемся у главного входа"},
        )
        meeting = client.put(
            f"/r/api/event-groups/{concert_group_id}/meeting-point",
            headers=auth_headers(users[0]),
            json={"meeting_point": "У главного входа в 18:45"},
        )
        rsvp = client.put(
            f"/r/api/event-groups/{concert_group_id}/rsvp",
            headers=auth_headers(users[1]),
            json={"status": "declined"},
        )
        outsider_message = client.post(
            f"/r/api/event-groups/{concert_group_id}/messages",
            headers=auth_headers(users[2]),
            json={"message": "Я не в этой компании"},
        )

    assert waiting.status_code == 200
    assert waiting.json()["event_group"]["status"] == "forming"
    assert discovery.status_code == 200
    discovered = next(
        event
        for event in discovery.json()["company_events"]
        if event["id"] == concert_id
    )
    assert discovered["company_count"] == 1
    assert discovered["company_group_id"] is None
    assert active.status_code == 200
    assert concert_group["status"] == "active"
    assert concert_group["member_count"] == 2
    assert concert_group["event"]["id"] == concert_id
    assert exhibition_group_id != concert_group_id
    assert other_event.json()["event_group"]["event"]["id"] == exhibition_id
    assert requested.status_code == 200
    assert accepted.status_code == 200
    connected = next(
        member
        for member in accepted.json()["event_group"]["members"]
        if not member["is_me"]
    )
    assert connected["connection_state"] == "connected"
    assert connected["contact"]["url"] == "https://t.me/eventmember43"
    assert message.status_code == 200
    assert message.json()["event_group"]["messages"][-1]["message"] == "Встречаемся у главного входа"
    assert meeting.status_code == 200
    assert meeting.json()["event_group"]["meeting_point"] == "У главного входа в 18:45"
    assert rsvp.status_code == 200
    assert next(
        member for member in rsvp.json()["event_group"]["members"] if member["is_me"]
    )["rsvp"] == "declined"
    assert outsider_message.status_code == 409
