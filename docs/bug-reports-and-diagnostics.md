# Voluntary bug reports and diagnostics

## Consent and transmission

Browser errors are kept only in the current page's memory. Gatherly never sends a browser error automatically. A person must open **Сообщить об ошибке**, review the exact preview, tick the consent checkbox, and submit the report.

## Stored fields

The report stores only a server-generated report ID, UTC creation time, application version, coarse browser family, coarse operating-system family, module, fixed error code, and up to eight line/column pairs. The report is not associated with an account and does not store an IP address, email address, user ID, raw user agent, URL, query string, request/response body, stack file paths, messages, event content, expense content, contacts, or uploaded files.

The API rejects unexpected fields, accepts only fixed enumerations, and filters stack information to line/column pairs. The public endpoint is protected by the existing IP-based request limiter and same-origin checks. The limiter's IP-derived key is held separately in the security rate-limit table; it is not copied into the bug report.

## Operational access and retention

Bug reports are stored in the application database for operational review. Access to the database must be restricted to authorized operators. Define and implement a retention/deletion schedule before production launch; this feature does not yet automate expiry. Technical security/availability logs remain separate and may be generated regardless of whether a user submits a report.

This document describes implementation behavior, not a complete GDPR privacy notice or legal basis. The production privacy notice, retention period, lawful basis, and any required consent language must be reviewed before release in the relevant jurisdictions.