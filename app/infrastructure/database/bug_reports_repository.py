"""Persistence for voluntary, minimized client-side bug reports."""

from datetime import UTC, datetime
import sqlite3
from uuid import uuid4

from app.infrastructure.database.connection import transaction


class BugReportsRepository:
    """Store only reviewed technical diagnostics; never associate reports with accounts."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        *,
        created_at: datetime,
        app_version: str,
        browser: str,
        operating_system: str,
        module: str,
        error_code: str,
        stack: str,
    ) -> str:
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        report_id = uuid4().hex
        with transaction(self._connection):
            self._connection.execute(
                """INSERT INTO bug_reports(
                    id, created_at, app_version, browser, operating_system,
                    module, error_code, stack
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    report_id,
                    created_at.astimezone(UTC).isoformat(),
                    app_version,
                    browser,
                    operating_system,
                    module,
                    error_code,
                    stack,
                ),
            )
        return report_id
