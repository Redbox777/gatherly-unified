# Voluntary bug reports and diagnostics

## Consent and transmission

Browser errors are kept only in the current page's memory. Gatherly never sends a browser error automatically. A person must open **Сообщить об ошибке**, review the exact preview, tick the consent checkbox, and submit the report.

## Stored fields

The report stores only a server-generated report ID, UTC creation time, application version, coarse browser family, coarse operating-system family, module, fixed error code, and up to eight line/column pairs. The report is not associated with an account and does not store an IP address, email address, user ID, raw user agent, URL, query string, request/response body, stack file paths, messages, event content, expense content, contacts, or uploaded files.

The API rejects unexpected fields, accepts only fixed enumerations, and filters stack information to line/column pairs. The public endpoint is protected by the existing IP-based request limiter and same-origin checks. The limiter's IP-derived key is held separately in the security rate-limit table; it is not copied into the bug report.

## Operational access and retention

Bug reports are automatically deleted by the scheduled one-shot worker. The default retention period is 90 days and can be changed with `BUG_REPORT_RETENTION_DAYS` (allowed range: 1–3650 days). Expiry runs on each worker execution, even when SMTP delivery is not configured. Schedule the worker at least daily; the existing one-minute schedule also works. Deletion removes only reports strictly older than the cutoff.

Access to the database must still be restricted to authorized operators. Confirm the default retention period against the actual purposes, legal basis, and jurisdictions before production; the technical default is not a legal determination. Technical security/availability logs remain separate and follow their own operational retention policy.

This document describes implementation behavior, not a complete GDPR privacy notice or legal basis. The production privacy notice, lawful basis, retention period, and any required consent language must be reviewed before release in the relevant jurisdictions.
