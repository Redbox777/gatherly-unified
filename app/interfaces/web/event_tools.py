"""Event venue, task, and note HTTP routes."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app.application.events import EventNotFoundError
from app.interfaces.web.constants import SESSION_COOKIE
from app.interfaces.web.dependencies import Dependencies
from app.infrastructure.database.offline_sync_repository import OfflineSyncError
from app.interfaces.web.schemas import (
    CreateNoteRequest,
    EventTaskCompletionRequest,
    EventTaskRequest,
    EventVenueRequest,
    UpdateNoteRequest,
    OfflineSyncCommandRequest,
)


def create_event_tools_router(
    services: Callable[..., Iterator[Dependencies]],
) -> APIRouter:
    """Build event tools endpoints with the app's request-scoped dependency."""
    router = APIRouter()
    ServiceDependency = Annotated[Dependencies, Depends(services)]

    @router.post("/offline-sync/commands")
    def apply_offline_sync_command(
        payload: OfflineSyncCommandRequest, request: Request, deps: ServiceDependency
    ) -> JSONResponse:
        """Apply a queued command using the current authenticated session only."""
        try:
            response_status, body = deps.offline_sync.apply(
                session_token=request.cookies.get(SESSION_COOKIE, ""),
                operation_id=str(payload.operation_id),
                event_id=str(payload.event_id),
                base_revision=payload.base_revision,
                kind=payload.kind,
                payload=payload.payload,
            )
        except OfflineSyncError as error:
            detail: dict[str, object] = {"detail": error.detail}
            if error.current_revision is not None:
                detail["current_revision"] = error.current_revision
            return JSONResponse(status_code=error.status_code, content=detail)
        return JSONResponse(status_code=response_status, content=body)


    @router.get("/events/{event_id}/venue")
    def get_event_venue(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> dict[str, object] | None:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            deps.events.get_event(event_id=event_id, owner_id=owner_id)
        except EventNotFoundError as error:
            raise HTTPException(status_code=404, detail="Event not found") from error
        venue = deps.event_venues.get_for_owner(event_id=str(event_id), owner_id=owner_id)
        return venue

    @router.put("/events/{event_id}/venue")
    def save_event_venue(
        event_id: UUID, payload: EventVenueRequest, request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            venue = deps.event_venues.save_for_owner(
                event_id=str(event_id), owner_id=owner_id,
                venue_name=payload.venue_name, address=payload.address,
                latitude=payload.latitude, longitude=payload.longitude,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid venue data") from error
        if venue is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return venue

    @router.delete("/events/{event_id}/venue", status_code=status.HTTP_204_NO_CONTENT)
    def delete_event_venue(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> Response:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not deps.event_venues.delete_for_owner(event_id=str(event_id), owner_id=owner_id):
            raise HTTPException(status_code=404, detail="Venue not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/events/{event_id}/tasks")
    def list_event_tasks(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> list[dict[str, object]]:
        token = request.cookies.get(SESSION_COOKIE, "")
        owner_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        tasks = deps.event_tasks.list_for_owner(
            event_id=str(event_id), owner_id=owner_id
        )
        if tasks is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return tasks

    @router.post(
        "/events/{event_id}/tasks", status_code=status.HTTP_201_CREATED
    )
    def create_event_task(
        event_id: UUID, payload: EventTaskRequest,
        request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        token = request.cookies.get(SESSION_COOKIE, "")
        owner_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if payload.due_at is not None and (
            payload.due_at.tzinfo is None or payload.due_at.utcoffset() is None
        ):
            raise HTTPException(status_code=422, detail="Task due date must include a timezone")
        try:
            task = deps.event_tasks.create(
                event_id=str(event_id), owner_id=owner_id,
                title=payload.title, description=payload.description,
                due_at=payload.due_at.isoformat() if payload.due_at else None,
                assignee_participant_id=(
                    str(payload.assignee_participant_id)
                    if payload.assignee_participant_id else None
                ),
                now=datetime.now(UTC),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid task data") from error
        if task is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return task

    @router.put("/events/{event_id}/tasks/{task_id}")
    def update_event_task(
        event_id: UUID, task_id: UUID, payload: EventTaskRequest,
        request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        token = request.cookies.get(SESSION_COOKIE, "")
        owner_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if payload.due_at is not None and (
            payload.due_at.tzinfo is None or payload.due_at.utcoffset() is None
        ):
            raise HTTPException(status_code=422, detail="Task due date must include a timezone")
        try:
            task = deps.event_tasks.update(
                task_id=str(task_id), event_id=str(event_id), owner_id=owner_id,
                title=payload.title, description=payload.description,
                due_at=payload.due_at.isoformat() if payload.due_at else None,
                assignee_participant_id=(
                    str(payload.assignee_participant_id)
                    if payload.assignee_participant_id else None
                ),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid task data") from error
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    @router.patch("/events/{event_id}/tasks/{task_id}/completion")
    def set_event_task_completion(
        event_id: UUID, task_id: UUID, payload: EventTaskCompletionRequest,
        request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        token = request.cookies.get(SESSION_COOKIE, "")
        owner_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        task = deps.event_tasks.set_completed(
            task_id=str(task_id), event_id=str(event_id),
            owner_id=owner_id, completed=payload.completed,
        )
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    @router.delete(
        "/events/{event_id}/tasks/{task_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_event_task(
        event_id: UUID, task_id: UUID, request: Request, deps: ServiceDependency
    ) -> Response:
        token = request.cookies.get(SESSION_COOKIE, "")
        owner_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not deps.event_tasks.delete(
            task_id=str(task_id), event_id=str(event_id), owner_id=owner_id
        ):
            raise HTTPException(status_code=404, detail="Task not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/events/{event_id}/notes")
    def list_event_notes(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> list[dict[str, object]]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        notes = deps.notes.list_for_event(event_id=str(event_id), user_id=user_id)
        if notes is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return notes

    @router.post("/events/{event_id}/notes", status_code=status.HTTP_201_CREATED)
    def create_event_note(
        event_id: UUID, payload: CreateNoteRequest,
        request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            note = deps.notes.create(
                event_id=str(event_id), author_id=user_id,
                content=payload.content, visibility=payload.visibility,
                now=datetime.now(UTC),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid note data") from error
        if note is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return note

    @router.put("/events/{event_id}/notes/{note_id}")
    def update_event_note(
        event_id: UUID, note_id: UUID, payload: UpdateNoteRequest,
        request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            note = deps.notes.update(
                event_id=str(event_id), note_id=str(note_id), user_id=user_id,
                content=payload.content, visibility=payload.visibility,
                now=datetime.now(UTC),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid note data") from error
        if note is None:
            raise HTTPException(status_code=404, detail="Note not found")
        return note

    @router.delete(
        "/events/{event_id}/notes/{note_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_event_note(
        event_id: UUID, note_id: UUID, request: Request, deps: ServiceDependency
    ) -> Response:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not deps.notes.delete(
            event_id=str(event_id), note_id=str(note_id), user_id=user_id
        ):
            raise HTTPException(status_code=404, detail="Note not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)


    return router
