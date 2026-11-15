# Privacy controls and data-subject operations

This document records the technical behavior implemented by Gatherly. It is not a legal privacy notice, legal basis assessment, DPA, or confirmation of GDPR compliance. Before public release, the operator must have the notice and processing purposes reviewed for the countries where the service is offered.

## Account data export

An authenticated user can download a JSON export from:

```http
GET /account/export
Cookie: gatherly_session=...
```

The export is returned as an attachment using the versioned format `gatherly-account-export-v1`. It includes account creation time, login identity metadata, session metadata, owned events and their event data, linked participant records, authored notes, invitations associated with the account, notifications, and notification/reminder preferences including Telegram and Web Push consent metadata. Encrypted browser push endpoints and their encryption keys are excluded.

Credential and bearer secrets are deliberately excluded: password hashes, session token hashes, email verification/reset token hashes, and participant invitation token hashes. Export access requires an active authenticated session. Responses inherit `Cache-Control: no-store`.

## Account deletion

An account can be deleted using:

```http
DELETE /account
Cookie: gatherly_session=...
Origin: https://your-public-origin.example
X-Account-Deletion-Confirmation: DELETE
```

The request requires:
- an active session;
- a session created within the last 10 minutes (sign in again immediately before deletion);
- the exact explicit confirmation header;
- the normal same-origin browser request checks.

Deletion is transactional. It removes the user, login identities, sessions, email tokens, notification data/preferences, notes authored by the user, reminder preferences owned by the user, invitations created by the user or addressed to their verified email, and events owned by the user (including their event-scoped dependent data).

If the user was linked to a participant record in another person's event, the link is removed and the participant label is replaced with a non-identifying `Former member <id-prefix>` label. Shared expense allocations and settlement history are retained under that label so other event members do not lose the accounting record. This is a deliberate product data-retention decision and must be reviewed against the applicable lawful basis and retention policy.

## Data retention

- Voluntary diagnostics: default 90 days; configure `BUG_REPORT_RETENTION_DAYS` from 1 to 3650. Expiry runs in the notification worker.
- Local encrypted database backups: default 30 days in the documented backup script; configure `GATHERLY_BACKUP_RETENTION_DAYS`. Remote object-storage retention must be configured separately using the provider's lifecycle policy.
- Security/rate-limit operational records are stored separately from voluntary bug reports and require their own reviewed retention period.
- Backups are not edited in place after an account erasure. A deleted record may remain in an encrypted, access-restricted backup until that backup expires. Gatherly now records a signed, pseudonymous tombstone in an independent erasure ledger before an account deletion can commit. Production requires replication to an off-host rclone remote. Startup and restore synchronize and verify the ledger, then replay matching tombstones before serving traffic or publishing a restored database. If the ledger cannot be verified or retrieved, startup/restore fails closed. The ledger is not pruned with database backups and must be retained at least as long as any backup that could predate a deletion remains recoverable. Remote object-lock/versioning and lifecycle settings remain operator responsibilities and must be tested before production.

## Operator/legal release checklist

Before production:
1. Identify controller/processor roles, hosting and subprocessors, transfer locations, and contact details.
2. Document each processing purpose and its lawful basis; do not assume a consent checkbox is the correct basis for every feature.
3. Publish a complete privacy notice explaining data categories, purposes, recipients, international transfers, retention, user rights, and complaint route.
4. Review the technical retention periods, especially shared event/finance records and backup retention.
5. Define and test a process for access, correction, export, deletion, restriction, objection, and any applicable portability requests.
6. Review cookie/session behavior, analytics (if later added), email delivery, Telegram, and push providers before enabling them.
7. Define incident/breach handling, access control, operator access logging, and backup key recovery.
8. Verify deletion behavior and backup restore/replay procedure in a non-production environment, including off-host rclone access, immutable/object-locked ledger retention, and recovery with a deleted account in an older snapshot.

Do not advertise the implementation as “GDPR compliant” solely because export, deletion, or consent controls exist. Compliance depends on the actual organization, purposes, contracts, configuration, notice, and operating procedures.
