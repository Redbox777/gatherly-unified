# Browser push notifications

Gatherly supports opt-in Web Push through the PWA service worker.

## Server configuration

Configure all values in the web and worker environments:

- `WEB_PUSH_VAPID_PUBLIC_KEY`: public VAPID application-server key returned to authenticated browsers.
- `WEB_PUSH_VAPID_PRIVATE_KEY`: private VAPID key in PEM form; secret-manager only.
- `WEB_PUSH_VAPID_SUBJECT`: valid `mailto:` contact or HTTPS URL.
- `WEB_PUSH_SUBSCRIPTION_ENCRYPTION_KEY`: base64url-encoded random 32-byte AES-256 key, separate from the VAPID private key.
- `WEB_PUSH_TIMEOUT`: optional delivery timeout in seconds (default 10).

Never commit either private key. Do not store the subscription-encryption key beside the database or encrypted backups. Losing the subscription key makes stored browser subscriptions unusable; rotate it only through a planned re-subscription process.

## User opt-in and lifecycle

The browser requests Notification permission, waits for the active root-scoped service worker, obtains a PushManager subscription using the public VAPID key, and posts the endpoint and browser keys to `POST /push-subscriptions`. The server validates HTTPS endpoints, restricts destination hosts to supported browser push providers (Google FCM, Mozilla, Apple, and Windows Push), checks expected key sizes, encrypts the endpoint and keys with AES-256-GCM, and stores only the ciphertext plus a SHA-256 endpoint identifier. This provider allowlist prevents the worker from becoming a general-purpose SSRF proxy.

A successful subscription records `push_enabled`, the consent timestamp, and policy version `push-v1`. The user can revoke the channel using `DELETE /push-subscriptions`; this deletes all account subscriptions and clears the consent metadata. Account deletion cascades the encrypted subscriptions and delivery records.

The endpoint is treated as a bearer capability and is never returned in account exports or written to logs. The browser UI clearly requests permission and the user must explicitly enable push; the default is off.

## Delivery and failure handling

The scheduled notification worker sends push payloads using VAPID and Web Push encryption. Delivery is tracked per notification and per browser subscription, with up to five attempts. Expired subscriptions (HTTP 404/410) are removed. Other failures record only the exception type; endpoint URLs, payload keys, and VAPID secrets are not logged.

Delivery is best-effort and may be delayed or dropped by browser/device power policies, the browser vendor's push service, permission changes, network conditions, or user settings. A process crash after the push service accepts a message but before Gatherly records success can produce a duplicate. Do not promise exactly-once delivery.

## Privacy and operational boundaries

- Push is disabled by default and has a separate timestamped consent record.
- Browser endpoint and encryption keys are encrypted at rest with a dedicated key.
- Push service providers receive the encrypted Web Push payload and routing endpoint as part of the protocol. Review provider processing and applicable transfer terms before production.
- Push payloads contain only the notification title/body and a same-origin relative app path. They must not contain passwords, reset links, session tokens, or sensitive event/finance details.
- HTTPS is required in deployed environments. Localhost is supported for development.
- Push does not provide offline data synchronization. The service worker only displays the notification and opens the app.
