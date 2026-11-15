from datetime import UTC, datetime, timedelta

import pytest

from app.infrastructure.database.bug_report_retention import load_bug_report_retention_days
from app.infrastructure.database.bug_reports_repository import BugReportsRepository
from app.infrastructure.database.connection import connect, migrate


def _create_report(repository: BugReportsRepository, created_at: datetime) -> str:
    return repository.create(
        created_at=created_at,
        app_version="0.1.0",
        browser="Chrome",
        operating_system="Linux",
        module="frontend",
        error_code="window_error",
        stack="at line 4, column 2",
    )


def test_bug_report_retention_deletes_only_reports_strictly_before_cutoff(tmp_path) -> None:
    connection = connect(tmp_path / "retention.sqlite3")
    try:
        migrate(connection)
        repository = BugReportsRepository(connection)
        cutoff = datetime(2026, 11, 15, tzinfo=UTC)
        expired_id = _create_report(repository, cutoff - timedelta(seconds=1))
        retained_id = _create_report(repository, cutoff)

        assert repository.delete_older_than(created_before=cutoff) == 1
        remaining = {
            row["id"]
            for row in connection.execute("SELECT id FROM bug_reports").fetchall()
        }
        assert remaining == {retained_id}
        assert expired_id not in remaining
    finally:
        connection.close()


def test_bug_report_retention_requires_timezone_aware_cutoff(tmp_path) -> None:
    connection = connect(tmp_path / "retention-naive.sqlite3")
    try:
        migrate(connection)
        with pytest.raises(ValueError, match="timezone-aware"):
            BugReportsRepository(connection).delete_older_than(
                created_before=datetime(2026, 11, 15)
            )
    finally:
        connection.close()


def test_bug_report_retention_defaults_to_ninety_days() -> None:
    assert load_bug_report_retention_days({}) == 90


@pytest.mark.parametrize("value", ["0", "-1", "3651", "not-a-number"])
def test_bug_report_retention_rejects_invalid_period(value: str) -> None:
    with pytest.raises(ValueError, match="BUG_REPORT_RETENTION_DAYS"):
        load_bug_report_retention_days({"BUG_REPORT_RETENTION_DAYS": value})
