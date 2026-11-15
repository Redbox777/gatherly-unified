# Gatherly Diagnostics

This directory is the entry point for the application's diagnostics design and operational procedures.

It is deliberately separate from product features and UI styling. Diagnostics must fail closed: a reporting failure must never interrupt meetings, authentication, offline work, or synchronization.

## Existing implementation

| Area | Current location | Current behavior |
|---|---|---|
| Browser bug capture and event buffer | `app/interfaces/web/static/diagnostics.js` | Captures unhandled UI errors and keeps at most 40 minimized technical events in memory. |
| API failure telemetry | `app/interfaces/web/static/api-client.js` | Records network failures and non-success HTTP responses with method, route category, duration, status, and server request ID. |
| Offline-sync telemetry | `app/interfaces/web/static/offline-sync.js` | Records sending, completed, pending, conflict, and blocked states, plus safe API failure metadata. |
| Network state | `diagnostics.js` | Records online/offline transitions. |
| User-submitted report | `app/interfaces/web/static/index.html`, `diagnostics.js` | User opens **Сообщить об ошибке**, reviews the event bundle, explicitly checks consent, and submits. |
| Report API | `app/interfaces/web/operations.py` | `POST /diagnostics/bug-reports`; validates each event against a strict allowlist. |
| Report schema | `app/interfaces/web/schemas.py` | Rejects unexpected fields and constrains event codes, subsystems, route categories, methods, statuses, and sync states. |
| Report persistence | `app/infrastructure/database/bug_reports_repository.py`, migration 25 | Stores minimized event bundles in SQLite `bug_reports.diagnostics_json`; reports are not associated with accounts. |
| Retention | `app/infrastructure/notifications_worker.py`, `bug_report_retention.py` | Removes expired reports; default retention is 90 days, configurable from 1 to 3650 days. |
| Server logs | `app/infrastructure/observability.py` | Structured JSON logs, rotating files under `var/logs/` by default. |
| Request correlation | `app/interfaces/web/http_api.py` | Server-generated request ID is returned as `X-Request-ID`; caller-supplied IDs are not trusted. |
| Tests | `tests/browser/diagnostics.test.cjs`, `tests/integration/test_bug_reports_api.py` and related browser tests | Cover event bounds, minimization, explicit consent, persistence, allowlist rejection, API failures, and sync transitions. |

The client collector is enabled on the main application shell. It keeps its event buffer in memory only. It does not persist browser telemetry between page loads and does not upload it automatically.

## Event contract

Client-side records contain only technical, non-content metadata:

- UTC timestamp;
- fixed subsystem and event code (UI, API, network, offline sync);
- coarse route category, never a full URL or resource identifier;
- HTTP method and status where applicable;
- request duration in milliseconds, capped at 60 seconds;
- server-generated `X-Request-ID`, when present;
- coarse offline-sync state.

The buffer is capped at 40 events. The server independently validates every field and stores only the allowlisted event structure.

Do not record raw request or response bodies, full URLs, query strings, cookies, authorization headers, passwords, tokens, email addresses, names, user IDs, event titles, notes, expenses, participant details, or uploaded content.

## Remaining planned work

1. Add aggregated reporting by fixed error code/subsystem and frequency, without user tracking.
2. Consider a separate operational viewer for authorized maintainers, with access control and audit logging.
3. Add further event coverage only when a concrete diagnostic gap is observed.
4. Continue testing real browser flows, offline transitions, and the rule that diagnostics cannot break core application behavior.

No automatic telemetry upload, third-party analytics, or user profiling is planned.

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
