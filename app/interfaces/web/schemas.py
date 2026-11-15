"""Validated request payload schemas for the Gatherly HTTP adapter."""

from datetime import datetime
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=1024)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class CreateEventRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    starts_at: datetime | None = None


class UpdateEventRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    starts_at: datetime | None = None


class AddParticipantRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)


class CreateParticipantInvitationRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class NotificationPreferencesRequest(BaseModel):
    email_enabled: bool = True
    event_updates_enabled: bool = True
    participant_added_enabled: bool = True
    reminders_enabled: bool = True
    telegram_enabled: bool = False


class PushSubscriptionKeysRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p256dh: str = Field(min_length=1, max_length=256)
    auth: str = Field(min_length=1, max_length=128)


class PushSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(min_length=1, max_length=2048)
    keys: PushSubscriptionKeysRequest


class EventReminderRequest(BaseModel):
    offsets_minutes: list[int] = Field(default_factory=list, max_length=3)


class EventVenueRequest(BaseModel):
    venue_name: str = Field(min_length=1, max_length=160)
    address: str = Field(min_length=1, max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class EventExpenseRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    amount: str = Field(min_length=1, max_length=32)
    currency: str = Field(pattern="^[A-Z]{3}$")
    paid_by_participant_id: UUID
    incurred_at: datetime | None = None


class ExpenseSplitAllocationRequest(BaseModel):
    participant_id: UUID
    amount: str | None = Field(default=None, min_length=1, max_length=32)
    weight: int | None = Field(default=None, ge=1, le=1000000)


class ExpenseSplitRequest(BaseModel):
    mode: str = Field(pattern="^(equal|fixed|weighted)$")
    allocations: list[ExpenseSplitAllocationRequest] = Field(min_length=1, max_length=200)


class EventSettlementRequest(BaseModel):
    from_participant_id: UUID
    to_participant_id: UUID
    amount: str = Field(min_length=1, max_length=32)
    currency: str = Field(pattern="^[A-Z]{3}$")
    settled_at: datetime | None = None
    note: str = Field(default="", max_length=1000)


class EventTaskRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    due_at: datetime | None = None
    assignee_participant_id: UUID | None = None


class EventTaskCompletionRequest(BaseModel):
    completed: bool


class OfflineSyncCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    event_id: UUID
    base_revision: int = Field(ge=1)
    kind: str = Field(
        pattern=r"^(note\.(create|update|delete)|task\.(create|update|delete|complete)|venue\.(set|delete))$"
    )
    payload: dict[str, object]


class CreateNoteRequest(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    visibility: str = Field(default="private", pattern="^(private|shared)$")


class UpdateNoteRequest(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    visibility: str = Field(pattern="^(private|shared)$")


class AcceptParticipantInvitationRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class LinkEmailAccountRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class TelegramLoginRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=32)
    auth_date: str = Field(min_length=1, max_length=16)
    hash: str = Field(min_length=64, max_length=64)


class EmailTokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=12, max_length=1024)


class BugReportEvent(BaseModel):
    """One minimized technical event; arbitrary strings and payloads are forbidden."""

    model_config = ConfigDict(extra="forbid")

    occurred_at: AwareDatetime
    subsystem: str = Field(pattern=r"^(ui|api|network|offline_sync)$")
    event_code: str = Field(
        pattern=r"^(window_error|unhandled_rejection|api_network_error|api_http_error|network_offline|network_online|offline_sync_state)$"
    )
    route_category: str | None = Field(
        default=None,
        pattern=r"^(events|event_detail|auth|account|offline_sync|other)$",
    )
    method: str | None = Field(default=None, pattern=r"^(GET|POST|PUT|PATCH|DELETE)$")
    status_code: int | None = Field(default=None, ge=100, le=599)
    duration_ms: int | None = Field(default=None, ge=0, le=60000)
    request_id: str | None = Field(default=None, pattern=r"^[A-Fa-f0-9-]{8,64}$")
    state: str | None = Field(
        default=None, pattern=r"^(pending|sending|done|conflict|blocked)$"
    )


class BugReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: bool
    diagnostics: list[BugReportEvent] = Field(default_factory=list, max_length=40)
    app_version: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._+-]+$")
    browser: str = Field(pattern=r"^(Chrome|Firefox|Safari|Edge|Other)$")
    operating_system: str = Field(pattern=r"^(Windows|macOS|Linux|Android|iOS|Other)$")
    module: str = Field(pattern=r"^(frontend|manual)$")
    error_code: str = Field(pattern=r"^(window_error|unhandled_rejection|user_reported_issue)$")
    stack: str = Field(default="", max_length=1000)
