"""HTTPS API и статика Telegram Mini App.

Nginx проксирует публичный префикс /r на этот процесс. Каждый API-запрос
содержит подписанную Telegram строку initData; одного user_id от клиента
недостаточно для авторизации.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from typing import Annotated

from aiogram import Bot
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI

from event_bot.admin_dashboard import build_admin_dashboard
from event_bot.analytics import (
    create_feedback,
    get_admin_ids,
    is_admin,
    notify_admins,
    record_usage,
)
from event_bot.db import (
    INTENT_STATUSES,
    PARTICIPATING_STATUSES,
    accept_connection_request,
    archive_expired_event_groups,
    block_user,
    create_connection_request,
    create_event_group_message,
    create_user_report,
    delete_user_data,
    find_events,
    find_events_with_open_companies,
    get_digest_schedule,
    get_event,
    get_event_company_counts,
    get_event_connection_state,
    get_event_group,
    get_user_intent,
    get_user_intents,
    get_user_event_groups,
    get_user_profile,
    get_user_profile_embeddings,
    init_db,
    join_event_group,
    leave_event_group,
    reject_connection_request,
    save_intent,
    save_user_profile,
    set_digest_schedule,
    set_event_group_meeting_point,
    set_event_group_rsvp,
    set_intent_visibility,
    update_user_identity,
)
from event_bot.embedding_provider import (
    EmbeddingProvider,
    profile_embedding_text,
    vector_to_blob,
)
from event_bot.keyboards import miniapp_keyboard, miniapp_tab_url
from event_bot.models import Event, Profile, UserIntent, format_group_size
from event_bot.research_analytics import (
    build_research_dashboard,
    export_research_events_csv,
    get_research_participant,
    normalize_research_campaign,
    reset_research_session_context,
    set_research_session_context,
)
from event_bot.source_branding import source_brand
from event_bot.web_auth import (
    TelegramUser,
    authenticated_admin,
    authenticated_user,
    validate_init_data,
)
from event_bot.web_schemas import (
    EventGroupRsvpUpdate,
    FeedbackCreate,
    GroupMessageCreate,
    IntentUpdate,
    MeetingPointUpdate,
    ProfileUpdate,
    TrackEvent,
    UserReportCreate,
    VisibilityUpdate,
)


BASE_PATH = "/r"
STATIC_DIR = Path(__file__).resolve().parent / "web"
logger = logging.getLogger(__name__)



def _profile_payload(profile: Profile | None) -> dict[str, object] | None:
    if profile is None:
        return None
    payload = profile.model_dump(mode="json")
    payload["days"] = ProfileUpdate.unique_days(profile.days or [])
    return payload


def _event_payload(
    event: Event,
    intent: UserIntent | None = None,
    *,
    company_count: int = 0,
    company_group_id: int | None = None,
    company_status: str | None = None,
) -> dict[str, object]:
    brand = source_brand(event.source_id)
    return {
        "id": event.id,
        "title": event.title,
        "description": event.description,
        "city": event.city,
        "address": event.address,
        "date": event.date.isoformat(),
        "end_date": event.end_date.isoformat() if event.end_date else None,
        "price": event.get_price_display(),
        "tags": event.tags,
        "venue": event.venue,
        "source_url": event.source_url,
        "source_id": event.source_id,
        "source_name": brand.name,
        "source_mark": brand.mark,
        "intent": intent.status if intent else None,
        "visible": intent.visible if intent else False,
        "company_count": company_count,
        "company_group_id": company_group_id,
        "company_status": company_status,
    }



def _connection_contact(
    member_id: int,
    request,
) -> dict[str, str] | None:
    if request is None or getattr(request, "status", "accepted") != "accepted":
        return None
    if member_id == request.from_user:
        name, username = request.from_name, request.from_username
    else:
        name, username = request.to_name, request.to_username
    url = (
        f"https://t.me/{username.lstrip('@')}"
        if username
        else f"tg://user?id={member_id}"
    )
    return {"name": name, "url": url}



def _event_group_member_key(group_id: int, event_id: int, member_id: int) -> str:
    digest = hmac.new(
        os.environ.get("BOT_TOKEN", "").encode("utf-8"),
        f"event-group:{group_id}:event:{event_id}:member:{member_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:24]


def _event_group_payload(
    group: dict[str, object] | None,
    user_id: int,
) -> dict[str, object] | None:
    if group is None:
        return None
    event = group["event"]
    assert isinstance(event, Event)
    members: list[dict[str, object]] = []
    meeting_point_author: str | None = None
    for member in group["members"]:
        member_id = int(member["user_id"])
        is_me = member_id == user_id
        connection_state, request = (
            ("self", None)
            if is_me
            else get_event_connection_state(event.id, user_id, member_id)
        )
        if member_id == group.get("meeting_point_by"):
            meeting_point_author = "Вы" if is_me else str(member["name"])
        members.append(
            {
                "name": "Вы" if is_me else member["name"],
                "is_me": is_me,
                "member_key": (
                    None
                    if is_me
                    else _event_group_member_key(group["id"], event.id, member_id)
                ),
                "common_interests": member["common_interests"],
                "group_size": format_group_size(
                    member["group_size_min"], member["group_size_max"]
                ),
                "rsvp": member["rsvp"],
                "connection_state": connection_state,
                "request_id": (
                    request.id
                    if request is not None and connection_state == "pending_received"
                    else None
                ),
                "contact": (
                    _connection_contact(member_id, request)
                    if connection_state == "connected"
                    else None
                ),
            }
        )
    return {
        "id": group["id"],
        "status": group["status"],
        "event": _event_payload(
            event,
            get_user_intent(user_id, event.id),
            company_count=group["member_count"],
            company_group_id=group["id"],
            company_status=group["status"],
        ),
        "member_count": group["member_count"],
        "minimum_members": group["minimum_members"],
        "maximum_members": group["maximum_members"],
        "can_interact": group["status"] == "active",
        "meeting_point": group["meeting_point"],
        "meeting_point_author": meeting_point_author,
        "members": members,
        "messages": [
            {
                "id": message["id"],
                "author_name": (
                    "Вы" if message["user_id"] == user_id else message["name"]
                ),
                "is_me": message["user_id"] == user_id,
                "message": message["message"],
                "created_at": message["created_at"],
            }
            for message in group["messages"]
        ],
    }


def _fresh_event_group(group_id: int, user_id: int) -> dict[str, object]:
    payload = _event_group_payload(get_event_group(group_id, user_id), user_id)
    if payload is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event company not found")
    return payload


def _resolve_event_group_member(
    group_id: int,
    user_id: int,
    member_key: str,
) -> tuple[int, int]:
    group = get_event_group(group_id, user_id)
    if group is None or group["status"] != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "Event company is not ready")
    event = group["event"]
    assert isinstance(event, Event)
    for member in group["members"]:
        member_id = int(member["user_id"])
        if member_id == user_id:
            continue
        expected = _event_group_member_key(group_id, event.id, member_id)
        if hmac.compare_digest(expected, member_key):
            return event.id, member_id
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Event company member not found")



def _bootstrap(user: TelegramUser) -> dict[str, object]:
    research = get_research_participant(user.id)
    research_campaign = research.campaign if research is not None else None
    profile = get_user_profile(user.id)
    if profile is not None:
        update_user_identity(user.id, user.first_name, user.username)
    intents = get_user_intents(user.id) if profile is not None else []
    intents_by_event = {intent.event.id: intent for intent in intents}
    recommendations: list[Event] = []
    if profile is not None:
        embeddings = get_user_profile_embeddings(user.id)
        recommendations = find_events(
            profile,
            profile_embedding=embeddings[0],
            profile_embedding_model=embeddings[1],
            avoid_embedding=embeddings[2],
            avoid_embedding_model=embeddings[3],
            limit=20,
        )
    raw_event_groups = get_user_event_groups(user.id) if profile is not None else []
    event_groups = [
        payload
        for group in raw_event_groups
        if (payload := _event_group_payload(group, user.id)) is not None
    ]
    membership_by_event = {
        group["event"]["id"]: group
        for group in event_groups
    }
    company_discovery = (
        find_events_with_open_companies(
            limit=12,
            research_campaign=research_campaign,
        )
        if profile is not None
        else []
    )
    event_ids = list(
        {
            *[event.id for event in recommendations if event.id is not None],
            *[intent.event.id for intent in intents if intent.event.id is not None],
            *[event.id for event in company_discovery if event.id is not None],
        }
    )
    company_counts = get_event_company_counts(
        event_ids,
        research_campaign=research_campaign,
    )

    def event_payload(event: Event, intent: UserIntent | None) -> dict[str, object]:
        membership = membership_by_event.get(event.id)
        return _event_payload(
            event,
            intent,
            company_count=company_counts.get(event.id, 0),
            company_group_id=membership["id"] if membership else None,
            company_status=membership["status"] if membership else None,
        )

    return {
        "user": {
            "id": user.id,
            "first_name": user.first_name,
            "username": user.username,
            "photo_url": user.photo_url,
        },
        "is_admin": is_admin(user.id),
        "research": (
            {
                "campaign": research.campaign,
                "participant_code": research.participant_code,
            }
            if research is not None
            else None
        ),
        "profile": _profile_payload(profile),
        "digest_weekday": get_digest_schedule(user.id) if profile else None,
        "event_groups": event_groups,
        "events": [
            event_payload(event, intents_by_event.get(event.id))
            for event in recommendations
        ],
        "company_events": [
            event_payload(event, intents_by_event.get(event.id))
            for event in company_discovery
        ],
        "my_events": [event_payload(intent.event, intent) for intent in intents],
    }


async def _embed_profile(profile: Profile) -> tuple[bytes | None, str | None, bytes | None, str | None]:
    api_key = os.getenv("OPENAI_API_KEY")
    positive_text = profile_embedding_text(profile.interests)
    negative_text = profile_embedding_text(profile.avoid)
    if not api_key or not positive_text:
        return None, None, None, None

    client = AsyncOpenAI(api_key=api_key)
    provider = EmbeddingProvider(client)
    try:
        texts = [positive_text] + ([negative_text] if negative_text else [])
        vectors = await provider.embed(texts)
        return (
            vector_to_blob(vectors[0]),
            provider.model,
            vector_to_blob(vectors[1]) if negative_text else None,
            provider.model if negative_text else None,
        )
    except Exception:
        logger.exception("Не удалось обновить эмбеддинг профиля из Mini App")
        return None, None, None, None
    finally:
        await client.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    archive_expired_event_groups()
    yield


app = FastAPI(
    title="Event Bot Mini App",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.mount(f"{BASE_PATH}/app/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    research_token = set_research_session_context(
        request.headers.get("X-Research-Session")
    )
    try:
        response = await call_next(request)
    finally:
        reset_research_session_context(research_token)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://telegram.org; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https:; "
        "connect-src 'self'; frame-ancestors https://web.telegram.org https://*.telegram.org"
    )
    if request.url.path.startswith(f"{BASE_PATH}/api"):
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path.rstrip("/") == f"{BASE_PATH}/app":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


async def _notify_event_company(
    user_ids: list[int],
    text: str,
    *,
    exclude_user_id: int | None = None,
) -> int:
    """Короткое Telegram-уведомление участникам событийной компании."""
    bot = Bot(token=os.environ["BOT_TOKEN"])
    delivered = 0
    miniapp_url = os.getenv("MINIAPP_URL", "").strip()
    reply_markup = (
        miniapp_keyboard(
            miniapp_tab_url(miniapp_url, "group"),
            "👥 Открыть компанию",
        )
        if miniapp_url
        else None
    )
    try:
        for member_id in user_ids:
            if member_id == exclude_user_id:
                continue
            try:
                await bot.send_message(
                    member_id,
                    text,
                    reply_markup=reply_markup,
                )
                delivered += 1
            except Exception:
                logger.warning(
                    "Не удалось уведомить участника событийной компании %s",
                    member_id,
                )
    finally:
        await bot.session.close()
    return delivered


REPORT_REASON_LABELS = {
    "spam": "спам или навязчивое общение",
    "harassment": "оскорбления или домогательства",
    "unsafe": "опасное поведение",
    "other": "другая причина",
}


async def _notify_admins_about_report(report: dict[str, object]) -> None:
    admin_ids = get_admin_ids()
    if not admin_ids:
        return
    details = str(report.get("details") or "").strip()
    text = (
        f"🚨 <b>Жалоба #{int(report['id'])}</b>\n"
        f"От: {escape(str(report['reporter_name']))}\n"
        f"На: {escape(str(report['reported_name']))}\n"
        f"Событие: {escape(str(report['event_title']))}\n"
        f"Причина: {escape(REPORT_REASON_LABELS.get(str(report['reason']), str(report['reason'])))}"
    )
    if details:
        text += f"\nКомментарий: {escape(details)}"
    text += (
        f"\n\nОграничить: <code>/ban {int(report['reported_id'])} причина</code>"
        f"\nЗакрыть: <code>/reportdone {int(report['id'])}</code>"
    )
    bot = Bot(token=os.environ["BOT_TOKEN"])
    try:
        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text, parse_mode="HTML")
            except Exception:
                logger.warning("Не удалось отправить жалобу администратору %s", admin_id)
    finally:
        await bot.session.close()


@app.get(f"{BASE_PATH}/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(f"{BASE_PATH}/app", include_in_schema=False)
@app.get(f"{BASE_PATH}/app/", include_in_schema=False)
def miniapp() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get(f"{BASE_PATH}/privacy", include_in_schema=False)
def privacy() -> FileResponse:
    return FileResponse(STATIC_DIR / "privacy.html")


@app.get(f"{BASE_PATH}/rules", include_in_schema=False)
def community_rules() -> FileResponse:
    return FileResponse(STATIC_DIR / "rules.html")


@app.get(f"{BASE_PATH}/api/bootstrap")
def bootstrap(user: Annotated[TelegramUser, Depends(authenticated_user)]) -> dict[str, object]:
    record_usage(user.id, "miniapp.open", "miniapp")
    return _bootstrap(user)


@app.post(f"{BASE_PATH}/api/track")
def track_miniapp_event(
    payload: TrackEvent,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, str]:
    record_usage(
        user.id,
        f"miniapp.{payload.event}",
        "miniapp",
        payload.metadata,
    )
    return {"status": "ok"}


@app.get(f"{BASE_PATH}/api/admin/analytics")
def admin_analytics(
    user: Annotated[TelegramUser, Depends(authenticated_admin)],
    days: int = 30,
) -> dict[str, object]:
    if days not in {7, 30, 90}:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Period must be 7, 30 or 90 days",
        )
    return build_admin_dashboard(days)


@app.get(f"{BASE_PATH}/api/admin/research")
def admin_research_analytics(
    campaign: str,
    user: Annotated[TelegramUser, Depends(authenticated_admin)],
) -> dict[str, object]:
    del user
    try:
        return build_research_dashboard(campaign)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error


@app.get(f"{BASE_PATH}/api/admin/research/export")
def admin_research_export(
    campaign: str,
    user: Annotated[TelegramUser, Depends(authenticated_admin)],
) -> Response:
    del user
    normalized = normalize_research_campaign(campaign)
    if normalized is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Недопустимая исследовательская кампания",
        )
    csv_text = export_research_events_csv(normalized)
    return Response(
        content="\ufeff" + csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{normalized}-research-events.csv"'
            )
        },
    )


@app.post(f"{BASE_PATH}/api/feedback")
async def submit_feedback(
    payload: FeedbackCreate,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, object]:
    item = create_feedback(
        user.id,
        user.first_name,
        user.username,
        payload.message,
        "miniapp",
    )
    bot = Bot(token=os.environ["BOT_TOKEN"])
    try:
        await notify_admins(bot, item)
    finally:
        await bot.session.close()
    return {"status": "ok", "feedback_id": item.id}


@app.delete(f"{BASE_PATH}/api/account")
def delete_account(
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, str]:
    delete_user_data(user.id)
    return {"status": "deleted"}


@app.put(f"{BASE_PATH}/api/profile")
async def update_profile(
    payload: ProfileUpdate,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, object]:
    was_profiled = get_user_profile(user.id) is not None
    profile = Profile(
        interests=payload.interests,
        avoid=payload.avoid,
        days=payload.days or None,
        budget_rub=payload.budget_rub,
        preferred_group_size_min=payload.preferred_group_size_min,
        preferred_group_size_max=payload.preferred_group_size_max,
    )
    embeddings = await _embed_profile(profile)
    save_user_profile(
        user.id,
        profile,
        user.first_name,
        user.username,
        profile_embedding=embeddings[0],
        profile_embedding_model=embeddings[1],
        avoid_embedding=embeddings[2],
        avoid_embedding_model=embeddings[3],
    )
    set_digest_schedule(user.id, payload.digest_weekday)
    record_usage(
        user.id,
        "miniapp.profile_saved",
        "miniapp",
        {
            "status": "updated" if was_profiled else "created",
        },
    )
    return _bootstrap(user)


@app.put(f"{BASE_PATH}/api/events/{{event_id}}/intent")
def update_intent(
    event_id: int,
    payload: IntentUpdate,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, object]:
    if payload.status not in INTENT_STATUSES or get_event(event_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    if get_user_profile(user.id) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Complete your profile first")
    save_intent(user.id, event_id, payload.status)
    record_usage(
        user.id,
        f"miniapp.intent.{payload.status}",
        "miniapp",
        {"event_id": event_id, "status": payload.status},
    )
    intent = get_user_intent(user.id, event_id)
    assert intent is not None
    return _event_payload(intent.event, intent)


@app.put(f"{BASE_PATH}/api/events/{{event_id}}/visibility")
def update_visibility(
    event_id: int,
    payload: VisibilityUpdate,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, object]:
    intent = get_user_intent(user.id, event_id)
    if intent is None or intent.status not in PARTICIPATING_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Choose the event first")
    if not set_intent_visibility(user.id, event_id, payload.visible):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    record_usage(
        user.id,
        "miniapp.visibility",
        "miniapp",
        {"event_id": event_id, "status": "visible" if payload.visible else "hidden"},
    )
    updated = get_user_intent(user.id, event_id)
    assert updated is not None
    return _event_payload(updated.event, updated)


@app.post(f"{BASE_PATH}/api/events/{{event_id}}/company")
async def join_event_company(
    event_id: int,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, object]:
    result, group_id, member_ids = join_event_group(user.id, event_id)
    if result == "unavailable" or group_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Мероприятие уже недоступно")
    group = _fresh_event_group(group_id, user.id)
    if result == "joined":
        record_usage(
            user.id,
            "miniapp.event_company.joined",
            "miniapp",
            {"event_id": event_id, "group_id": group_id, "status": result},
        )
        if len(member_ids) >= 2:
            title = group["event"]["title"]
            if group["status"] == "active":
                await _notify_event_company(
                    member_ids,
                    f"✨ Компания на «{title}» собрана. Уже можно знакомиться и договариваться о встрече.",
                )
            else:
                await _notify_event_company(
                    member_ids,
                    f"👥 К поиску компании на «{title}» присоединился новый участник: {group['member_count']} из {group['maximum_members']}.",
                    exclude_user_id=user.id,
                )
    return {"status": result, "event_group": group}


@app.get(f"{BASE_PATH}/api/event-groups/{{group_id}}")
def event_company_state(
    group_id: int,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, object]:
    return _fresh_event_group(group_id, user.id)


@app.delete(f"{BASE_PATH}/api/event-groups/{{group_id}}")
async def leave_event_company(
    group_id: int,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, str]:
    current = get_event_group(group_id, user.id)
    if not leave_event_group(group_id, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Компания не найдена")
    if current is not None:
        await _notify_event_company(
            [int(member["user_id"]) for member in current["members"]],
            f"👥 Состав компании на «{current['event'].title}» изменился. Мы продолжим поиск замены.",
            exclude_user_id=user.id,
        )
    record_usage(
        user.id,
        "miniapp.event_company.left",
        "miniapp",
        {"group_id": group_id},
    )
    return {"status": "left"}


@app.put(f"{BASE_PATH}/api/event-groups/{{group_id}}/rsvp")
async def update_event_company_rsvp(
    group_id: int,
    payload: EventGroupRsvpUpdate,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, object]:
    current = get_event_group(group_id, user.id)
    if not set_event_group_rsvp(group_id, user.id, payload.status):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Компания не найдена")
    record_usage(
        user.id,
        f"miniapp.event_company.rsvp.{payload.status}",
        "miniapp",
        {"group_id": group_id, "status": payload.status},
    )
    if payload.status == "declined":
        if current is not None:
            await _notify_event_company(
                [int(member["user_id"]) for member in current["members"]],
                f"👥 {user.first_name} не сможет пойти на «{current['event'].title}». Мы освободили место и продолжим поиск.",
                exclude_user_id=user.id,
            )
        return {"status": "left", "event_group": None}
    return {"status": "updated", "event_group": _fresh_event_group(group_id, user.id)}


@app.put(f"{BASE_PATH}/api/event-groups/{{group_id}}/meeting-point")
async def update_event_company_meeting_point(
    group_id: int,
    payload: MeetingPointUpdate,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, object]:
    if not set_event_group_meeting_point(group_id, user.id, payload.meeting_point):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Компания не найдена")
    group = _fresh_event_group(group_id, user.id)
    raw_group = get_event_group(group_id, user.id)
    assert raw_group is not None
    member_ids = [int(member["user_id"]) for member in raw_group["members"]]
    await _notify_event_company(
        member_ids,
        f"📍 {user.first_name} предлагает место встречи: {payload.meeting_point}",
        exclude_user_id=user.id,
    )
    record_usage(
        user.id,
        "miniapp.event_company.meeting_point",
        "miniapp",
        {"group_id": group_id},
    )
    return {"status": "updated", "event_group": group}


@app.post(f"{BASE_PATH}/api/event-groups/{{group_id}}/messages")
async def send_event_company_message(
    group_id: int,
    payload: GroupMessageCreate,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, object]:
    result, member_ids = create_event_group_message(group_id, user.id, payload.message)
    if result == "limit":
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много сообщений подряд")
    if result != "created":
        raise HTTPException(status.HTTP_409_CONFLICT, "Чат пока недоступен")
    preview = payload.message if len(payload.message) <= 180 else f"{payload.message[:177]}…"
    await _notify_event_company(
        member_ids,
        f"💬 {user.first_name}: {preview}",
        exclude_user_id=user.id,
    )
    record_usage(
        user.id,
        "miniapp.event_company.message.sent",
        "miniapp",
        {"group_id": group_id},
    )
    return {"status": "created", "event_group": _fresh_event_group(group_id, user.id)}


@app.post(f"{BASE_PATH}/api/event-groups/{{group_id}}/connections/{{member_key}}")
async def request_event_company_connection(
    group_id: int,
    member_key: str,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, object]:
    event_id, target_user_id = _resolve_event_group_member(
        group_id, user.id, member_key
    )
    result, request = create_connection_request(event_id, user.id, target_user_id)
    if result == "already" and request is not None:
        state, incoming = get_event_connection_state(event_id, user.id, target_user_id)
        if state == "pending_received" and incoming is not None:
            result, request = accept_connection_request(incoming.id, user.id)
    if result == "created" and request is not None:
        await _notify_event_company(
            [request.to_user],
            f"👋 {request.from_name} хочет познакомиться перед «{request.event_title}». Откройте вкладку «Компания», чтобы ответить.",
        )
    elif result == "accepted" and request is not None:
        await _notify_event_company(
            [request.from_user, request.to_user],
            f"✅ Знакомство перед «{request.event_title}» подтверждено. Контакты открылись в приложении.",
        )
    elif result == "limit":
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов за сутки")
    elif result in {"blocked", "unavailable"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Сейчас познакомиться не получится")
    record_usage(
        user.id,
        f"miniapp.event_company.connection.{result}",
        "miniapp",
        {"group_id": group_id, "status": result},
    )
    return {"status": result, "event_group": _fresh_event_group(group_id, user.id)}


@app.post(f"{BASE_PATH}/api/event-groups/{{group_id}}/connections/{{request_id}}/accept")
async def accept_event_company_connection(
    group_id: int,
    request_id: int,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, object]:
    group = _fresh_event_group(group_id, user.id)
    if not any(member.get("request_id") == request_id for member in group["members"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Запрос не найден")
    result, request = accept_connection_request(request_id, user.id)
    if result != "accepted" or request is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Запрос не найден")
    await _notify_event_company(
        [request.from_user, request.to_user],
        f"✅ Знакомство перед «{request.event_title}» подтверждено. Контакты открылись в приложении.",
    )
    record_usage(
        user.id,
        "miniapp.event_company.connection.accepted",
        "miniapp",
        {"group_id": group_id},
    )
    return {"status": result, "event_group": _fresh_event_group(group_id, user.id)}


@app.post(f"{BASE_PATH}/api/event-groups/{{group_id}}/connections/{{request_id}}/reject")
def reject_event_company_connection(
    group_id: int,
    request_id: int,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, object]:
    group = _fresh_event_group(group_id, user.id)
    if not any(member.get("request_id") == request_id for member in group["members"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Запрос не найден")
    if not reject_connection_request(request_id, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Запрос не найден")
    record_usage(
        user.id,
        "miniapp.event_company.connection.rejected",
        "miniapp",
        {"group_id": group_id},
    )
    return {"status": "rejected", "event_group": _fresh_event_group(group_id, user.id)}


@app.post(f"{BASE_PATH}/api/event-groups/{{group_id}}/members/{{member_key}}/report")
async def report_event_company_member(
    group_id: int,
    member_key: str,
    payload: UserReportCreate,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, object]:
    _event_id, target_user_id = _resolve_event_group_member(
        group_id, user.id, member_key
    )
    result, report = create_user_report(
        user.id,
        target_user_id,
        group_id,
        payload.reason,
        payload.details,
    )
    if result == "limit":
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много жалоб за сутки")
    if result != "created" or report is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Не удалось отправить жалобу")
    await _notify_admins_about_report(report)
    record_usage(
        user.id,
        "miniapp.event_company.member.reported",
        "miniapp",
        {"group_id": group_id, "reason": payload.reason},
    )
    return {"status": "reported", "report_id": report["id"]}


@app.post(f"{BASE_PATH}/api/event-groups/{{group_id}}/members/{{member_key}}/block")
def block_event_company_member(
    group_id: int,
    member_key: str,
    user: Annotated[TelegramUser, Depends(authenticated_user)],
) -> dict[str, str]:
    _event_id, target_user_id = _resolve_event_group_member(
        group_id, user.id, member_key
    )
    if not block_user(user.id, target_user_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Не удалось заблокировать пользователя")
    record_usage(
        user.id,
        "miniapp.event_company.member.blocked",
        "miniapp",
        {"group_id": group_id},
    )
    return {"status": "blocked"}



@app.exception_handler(HTTPException)
async def http_error(_request: Request, error: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"detail": error.detail},
    )


def main() -> None:
    import uvicorn

    uvicorn.run(
        "event_bot.webapp:app",
        host="0.0.0.0",
        port=int(os.getenv("MINIAPP_PORT", "8080")),
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )


if __name__ == "__main__":
    main()
