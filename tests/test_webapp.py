import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from event_bot.analytics import build_admin_report, get_recent_feedback
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
