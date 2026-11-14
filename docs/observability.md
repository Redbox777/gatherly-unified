# Application logging and request diagnostics

## Purpose

Gatherly keeps technical logs to diagnose availability and application failures. Logs are not an analytics or advertising system.

## Location and rotation

- Default directory: `var/logs/`
- Default file: `var/logs/gatherly.log`
- Override the directory with `GATHERLY_LOG_DIRECTORY`.
- The log is newline-delimited JSON (one JSON object per record).
- Rotation starts at 5 MB and keeps up to five rotated files.
- The directory is created with owner-only permissions where the operating system permits it; the log file is restricted to owner read/write where supported.
- Keep the directory outside public/static hosting and out of version control.

If persistent log storage is unavailable, the application continues with structured output to standard error. Production deployment should still provision a writable persistent log directory or collect standard error through its process manager.

## Request IDs

Every HTTP request receives a new server-generated 32-character hexadecimal request ID. A caller-supplied `X-Request-ID` is ignored to prevent log-correlation spoofing.

The ID is returned in the `X-Request-ID` response header. Unexpected server errors also include it in the generic error response. Support requests should include this ID so an operator can find the corresponding log entries.

## Recorded fields

Depending on the event, a record may contain:

- UTC timestamp, severity, logger name, and fixed event message;
- request ID;
- HTTP method, path (without query string), response status, and duration;
- exception type and a short list of source file names, line numbers, and function names.

The exception message and source-line contents are deliberately omitted.

## Data that must not be logged

Never log request or response bodies, passwords, cookies, authorization headers, session or reset tokens, email verification links, full URLs with query strings, personal message content, expense descriptions, or SMTP credentials.

Application code should log fixed messages and safe metadata only. When handling an exception, log its type rather than `str(error)`; exception text can contain user-supplied or infrastructure-sensitive values.

## Scope

This phase adds server-side structured logs and request correlation. User-submitted bug reports and browser-side crash reports are a separate opt-in feature planned for the diagnostics phase. Logs are not a substitute for backups, monitoring, or incident response.
