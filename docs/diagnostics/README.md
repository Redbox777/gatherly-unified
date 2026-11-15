# Gatherly Diagnostics

This directory is the entry point for the application's diagnostics design and operational procedures.

It is deliberately separate from product features and UI styling. Diagnostics must fail closed: a reporting failure must never interrupt meetings, authentication, offline work, or synchronization.

## Existing implementation (verified on `main`)

| Area | Current location | Current behavior |
|---|---|---|
| Browser bug capture | `app/interfaces/web/static/diagnostics.js` | Captures the latest unhandled browser error/rejection in page memory. |
| User-submitted report | `app/interfaces/web/static/index.html`, `diagnostics.js` | User opens **Сообщить об ошибке**, reviews the preview, explicitly checks consent, and submits. |
| Report API | `app/interfaces/web/operations.py` | `POST /diagnostics/bug-reports`; validates and stores allowlisted fields only. |
| Report schema | `app/interfaces/web/schemas.py` | Rejects unexpected fields and constrains values to fixed enumerations. |
| Report persistence | `app/infrastructure/database/bug_reports_repository.py` | Stores reports in SQLite table `bug_reports`; reports are not associated with accounts. |
| Retention | `app/infrastructure/notifications_worker.py`, `bug_report_retention.py` | Removes expired reports; default retention is 90 days, configurable from 1 to 3650 days. |
| Server logs | `app/infrastructure/observability.py` | Structured JSON logs, rotating files under `var/logs/` by default. |
| Request correlation | `app/interfaces/web/http_api.py` | Server-generated request ID is returned as `X-Request-ID`; caller-supplied IDs are not trusted. |
| Existing tests | `tests/integration/test_bug_reports_api.py`, `test_bug_report_retention.py` | Consent, schema allowlist, minimization, rate limiting, and retention are covered. |

The current browser report is intentionally minimal. It does **not** yet collect a full client-side timeline, API failure metadata, offline-sync transitions, or grouped error statistics. A request ID is available in server responses, but the browser report does not yet attach it.

## Target diagnostic record

Future client-side records may include only technical, non-content metadata:

- UTC timestamp and application/build version;
- fixed subsystem and event code (UI, API, network, offline store, sync);
- coarse browser and operating-system family;
- route template or route category, never a full URL;
- operation/stage identifier from a fixed allowlist;
- HTTP status and method where applicable;
- online/offline state and a coarse sync transition;
- server-generated `X-Request-ID`, when present;
- a sanitized stack summary containing line/column information only.

Do not record raw request or response bodies, full URLs, query strings, cookies, authorization headers, passwords, tokens, email addresses, names, user IDs, event titles, notes, expenses, participant details, or uploaded content.

## Planned work — not yet implemented

1. Add a small client diagnostics collector with a bounded in-memory event buffer. It must never throw into application code.
2. Instrument the shared API client to record safe outcomes: network failure, HTTP status, duration bucket, and request ID. Do not duplicate or alter request behavior.
3. Instrument offline synchronization at state-transition boundaries (pending, sending, done, conflict, blocked) without recording operation payloads.
4. Add an explicit user-reviewed diagnostic bundle preview. Keep transmission opt-in; no automatic upload of browser telemetry.
5. Extend the validated server contract and persistence only after the fields, privacy impact, retention, and tests are reviewed.
6. Add safe grouping by fixed error code/subsystem and frequency. Do not introduce analytics or user tracking.
7. Add tests for redaction, bounded storage, consent, offline behavior, and the rule that diagnostics cannot break core flows.

## Operational locations

- **Consent-based reports:** SQLite `bug_reports` table in the configured application database.
- **Server diagnostics:** `var/logs/gatherly.log` by default, or `GATHERLY_LOG_DIRECTORY`.
- **Retention:** `BUG_REPORT_RETENTION_DAYS`, default 90 days. The scheduled worker must run for expiry cleanup to occur.
- **Detailed policies:** [Bug reports and diagnostics](../bug-reports-and-diagnostics.md), [Observability](../observability.md).

Runtime logs and database files must remain outside the public static directory and must not be committed to Git.

## Release gate

A diagnostics change is not ready until the relevant CI checks pass and tests demonstrate that:
- diagnostics failures do not break normal application flows;
- no sensitive or user-generated content is captured;
- reports are transmitted only after explicit review and consent;
- retention and rate limits remain effective;
- offline and synchronization behavior is unchanged.
