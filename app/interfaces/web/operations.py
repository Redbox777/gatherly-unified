"""Operational health and voluntary diagnostics HTTP routes."""

import json
import logging
import re
import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status

from app.infrastructure.database.bug_reports_repository import BugReportsRepository
from app.infrastructure.database.connection import MIGRATIONS, connect
from app.interfaces.web.schemas import BugReportRequest


logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
@router.get("/health/live")
def health_live() -> dict[str, str]:
    """Liveness only: report that the web process can answer requests."""
    return {"status": "alive"}


@router.get("/health/ready")
def health_ready(request: Request) -> dict[str, str]:
    """Readiness: verify SQLite responds and the latest schema is applied."""
    connection: sqlite3.Connection | None = None
    try:
        connection = connect(request.app.state.database_path)
        connection.execute("SELECT 1").fetchone()
        row = connection.execute(
            "SELECT MAX(version) AS version FROM schema_migrations"
        ).fetchone()
        if row is None or row["version"] != MIGRATIONS[-1][0]:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Service not ready",
            )
    except (sqlite3.Error, OSError) as error:
        logger.warning(
            "Database readiness check failed",
            extra={
                "event": "health.readiness.failed",
                "exception_type": type(error).__name__,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service not ready",
        ) from None
    finally:
        if connection is not None:
            connection.close()
    return {"status": "ready"}


@router.post("/diagnostics/bug-reports", status_code=status.HTTP_201_CREATED)
def create_bug_report(payload: BugReportRequest, request: Request) -> dict[str, str]:
    """Persist only the consented, schema-validated diagnostic fields."""
    if not payload.consent:
        raise HTTPException(status_code=422, detail="Explicit consent is required")
    safe_frames = [
        line.strip()
        for line in payload.stack.splitlines()
        if re.fullmatch(r"at line [0-9]{1,7}, column [0-9]{1,7}", line.strip())
    ][:8]
    connection = connect(request.app.state.database_path)
    try:
        report_id = BugReportsRepository(connection).create(
            created_at=datetime.now(UTC),
            app_version=payload.app_version,
            browser=payload.browser,
            operating_system=payload.operating_system,
            module=payload.module,
            error_code=payload.error_code,
            stack="\n".join(safe_frames),
            diagnostics_json=json.dumps(
                [event.model_dump(mode="json") for event in payload.diagnostics],
                ensure_ascii=True,
                separators=(",", ":"),
            ),
        )
    finally:
        connection.close()
    return {"report_id": report_id, "status": "received"}
