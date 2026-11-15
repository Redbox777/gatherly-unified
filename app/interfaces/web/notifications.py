"""Notification inbox, preferences, and event reminder HTTP routes."""

import base64
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
import os
import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from app.application.events import EventNotFoundError
from app.interfaces.web.constants import SESSION_COOKIE
from app.interfaces.web.dependencies import Dependencies
from app.interfaces.web.schemas import (
    EventReminderRequest,
    NotificationPreferencesRequest,
    PushSubscriptionRequest,
)
from app.infrastructure.database.push_subscriptions_repository import (
    decode_subscription_encryption_key,
)


def create_notifications_router(
    services: Callable[..., Iterator[Dependencies]],
) -> APIRouter:
    """Build notification endpoints with the app's request-scoped dependency."""
    router = APIRouter()
    ServiceDependency = Annotated[Dependencies, Depends(services)]

    @router.get("/notifications")
    def list_notifications(request: Request, deps: ServiceDependency) -> dict[str, object]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return {
            "items": deps.notifications.list_for_user(user_id=user_id),
            "unread_count": deps.notifications.unread_count(user_id=user_id),
        }

    @router.post("/notifications/{notification_id}/read")
    def mark_notification_read(
        notification_id: UUID, request: Request, deps: ServiceDependency
    ) -> dict[str, bool]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        changed = deps.notifications.mark_read(
            user_id=user_id,
            notification_id=str(notification_id),
            now=datetime.now(UTC),
        )
        if not changed:
            raise HTTPException(status_code=404, detail="Notification not found")
        return {"read": True}

    @router.get("/notification-preferences")
    def get_notification_preferences(
        request: Request, deps: ServiceDependency
    ) -> dict[str, bool | str]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        username = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
        valid_username = bool(re.fullmatch(r"[A-Za-z0-9_]{5,32}", username))
        return {
            **deps.notifications.get_preferences(user_id=user_id),
            "telegram_linked": deps.notifications.has_telegram_identity(user_id=user_id),
            "telegram_bot_url": f"https://t.me/{username}" if valid_username else "",
            "telegram_available": (
                valid_username and bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip())
                and deps.notifications.has_telegram_identity(user_id=user_id)
            ),
            "push_available": _push_configuration_available(),
        }

    @router.put("/notification-preferences")
    def update_notification_preferences(
        payload: NotificationPreferencesRequest,
        request: Request,
        deps: ServiceDependency,
    ) -> dict[str, bool]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        values = payload.model_dump()
        if values["telegram_enabled"]:
            if not deps.notifications.has_telegram_identity(user_id=user_id):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Connect a verified Telegram identity before enabling "
                        "Telegram notifications"
                    ),
                )
            current_preferences = deps.notifications.get_preferences(user_id=user_id)
            username = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
            valid_username = bool(re.fullmatch(r"[A-Za-z0-9_]{5,32}", username))
            if not current_preferences["telegram_enabled"] and (
                not os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
                or not valid_username
            ):
                raise HTTPException(
                    status_code=503,
                    detail="Telegram delivery is not configured on this server",
                )
        return deps.notifications.set_preferences(
            user_id=user_id,
            values=values,
            now=datetime.now(UTC),
        )

    @router.get("/push/public-key")
    def get_push_public_key(
        request: Request, deps: ServiceDependency
    ) -> dict[str, str]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not _push_configuration_available():
            raise HTTPException(
                status_code=503, detail="Browser push is not configured on this server"
            )
        return {"public_key": os.environ["WEB_PUSH_VAPID_PUBLIC_KEY"].strip()}

    @router.post("/push-subscriptions", status_code=201)
    def create_push_subscription(
        payload: PushSubscriptionRequest,
        request: Request,
        deps: ServiceDependency,
    ) -> dict[str, bool]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not _push_configuration_available():
            raise HTTPException(
                status_code=503, detail="Browser push is not configured on this server"
            )
        try:
            encryption_key = decode_subscription_encryption_key(
                os.environ.get("WEB_PUSH_SUBSCRIPTION_ENCRYPTION_KEY", "")
            )
            deps.push_subscriptions.upsert_subscription(
                user_id=user_id,
                endpoint=payload.endpoint,
                p256dh=payload.keys.p256dh,
                auth=payload.keys.auth,
                encryption_key=encryption_key,
                now=datetime.now(UTC),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid push subscription") from error
        deps.notifications.set_push_enabled(
            user_id=user_id,
            enabled=True,
            now=datetime.now(UTC),
        )
        return {"push_enabled": True}

    @router.delete("/push-subscriptions")
    def delete_push_subscriptions(
        request: Request, deps: ServiceDependency
    ) -> dict[str, bool | int]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        removed = deps.push_subscriptions.delete_user_subscriptions(user_id=user_id)
        deps.notifications.set_push_enabled(
            user_id=user_id,
            enabled=False,
            now=datetime.now(UTC),
        )
        return {"push_enabled": False, "removed_subscriptions": removed}

    @router.get("/events/{event_id}/reminders")
    def get_event_reminders(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> dict[str, list[int]]:
        token = request.cookies.get(SESSION_COOKIE, "")
        owner_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            deps.events.get_event(event_id=event_id, owner_id=owner_id)
        except EventNotFoundError as error:
            raise HTTPException(status_code=404, detail="Event not found") from error
        return {
            "offsets_minutes": deps.notifications.get_event_reminders(
                event_id=str(event_id), owner_id=owner_id
            )
        }

    @router.put("/events/{event_id}/reminders")
    def update_event_reminders(
        event_id: UUID,
        payload: EventReminderRequest,
        request: Request,
        deps: ServiceDependency,
    ) -> dict[str, list[int]]:
        token = request.cookies.get(SESSION_COOKIE, "")
        owner_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            event = deps.events.get_event(event_id=event_id, owner_id=owner_id)
        except EventNotFoundError as error:
            raise HTTPException(status_code=404, detail="Event not found") from error
        if payload.offsets_minutes and event.starts_at is None:
            raise HTTPException(
                status_code=422,
                detail="Set the event date and time before enabling reminders",
            )
        try:
            deps.notifications.set_event_reminders(
                event_id=str(event_id),
                owner_id=owner_id,
                offsets=payload.offsets_minutes,
                now=datetime.now(UTC),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Unsupported reminder offset") from error
        return {
            "offsets_minutes": deps.notifications.get_event_reminders(
                event_id=str(event_id), owner_id=owner_id
            )
        }

    return router



def _push_configuration_available() -> bool:
    """Require all public, private, subject, and at-rest encryption settings."""
    required = (
        "WEB_PUSH_VAPID_PUBLIC_KEY",
        "WEB_PUSH_VAPID_PRIVATE_KEY",
        "WEB_PUSH_VAPID_SUBJECT",
        "WEB_PUSH_SUBSCRIPTION_ENCRYPTION_KEY",
    )
    if any(not os.environ.get(name, "").strip() for name in required):
        return False
    subject = os.environ["WEB_PUSH_VAPID_SUBJECT"].strip()
    if not (subject.startswith("mailto:") or subject.startswith("https://")):
        return False
    public_key = os.environ["WEB_PUSH_VAPID_PUBLIC_KEY"].strip()
    try:
        padded = public_key + "=" * ((4 - len(public_key) % 4) % 4)
        decoded_public_key = base64.b64decode(
            padded.encode("ascii"), altchars=b"-_", validate=True
        )
        decode_subscription_encryption_key(
            os.environ["WEB_PUSH_SUBSCRIPTION_ENCRYPTION_KEY"]
        )
    except ValueError:
        return False
    return len(decoded_public_key) == 65
