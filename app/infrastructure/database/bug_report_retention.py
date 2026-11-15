"""Configuration for automatic expiry of voluntary diagnostic reports."""

from collections.abc import Mapping


DEFAULT_BUG_REPORT_RETENTION_DAYS = 90
MAX_BUG_REPORT_RETENTION_DAYS = 3650


def load_bug_report_retention_days(environ: Mapping[str, str]) -> int:
    """Return a bounded retention period; default to 90 days."""
    setting = "BUG_REPORT_RETENTION_DAYS"
    try:
        days = int(environ.get(setting, str(DEFAULT_BUG_REPORT_RETENTION_DAYS)))
    except ValueError as error:
        raise ValueError(f"{setting} must be an integer") from error
    if not 1 <= days <= MAX_BUG_REPORT_RETENTION_DAYS:
        raise ValueError(
            f"{setting} must be between 1 and {MAX_BUG_REPORT_RETENTION_DAYS}"
        )
    return days
