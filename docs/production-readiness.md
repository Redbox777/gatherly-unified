# Production readiness checklist

This checklist separates repository-level implementation from deployment and operator work. A passing CI workflow does not by itself make a production deployment ready.

## Application configuration

- [ ] Set `APP_ENV=production`.
- [ ] Set `PUBLIC_ORIGIN` to the exact public HTTPS origin (scheme, host, optional port; no path).
- [ ] Set `COOKIE_SECURE=true`.
- [ ] Configure real SMTP host, sender, credentials, and STARTTLS. Keep SMTP credentials out of source control and logs.
- [ ] Set public HTTPS verification, password-reset, and participant-invitation URLs.
- [x] New passwords use bounded Argon2id parameters; existing scrypt hashes are upgraded after successful login without forcing password resets.
- [ ] Benchmark Argon2id latency and memory use on the intended production host under expected concurrent logins.
- [ ] Configure `TELEGRAM_BOT_TOKEN` and `TELEGRAM_BOT_USERNAME` in the web environment for Telegram login/account linking. Configure them in the worker environment as well if Telegram notifications are enabled. Notification delivery still requires users to start the bot and explicitly opt in.
- [ ] On the deployed HTTPS origin, verify Email-to-Telegram and Telegram-to-Email account merges with test accounts, including provider-collision rejection, data transfer, and old-session revocation.
- [ ] If Web Push is enabled, configure the VAPID public/private keys, VAPID subject, and separate subscription-encryption key in both web and worker environments. Keep private keys in the secret manager and verify the push provider/transfer review.
- [x] PWA manifest, root service worker, static shell cache, and offline fallback are implemented; private API data is not cached.
- [x] Notification worker uses an expiring SQLite lease to prevent overlapping worker processes; outbound delivery retries are capped at five attempts with bounded backoff.
- [ ] Verify the scheduled notification worker runs at the intended cadence against the same persistent local SQLite database, and alert on repeated worker failures or skipped runs.
- [ ] Verify PWA installability and Web Push delivery on the actual HTTPS domain and supported target browsers/devices.
- [ ] Account linking merges two existing accounts only when their provider sets do not overlap. It requires recent destination authentication and independent source-account proof; it does not attach a new provider identity or support unlinking.

## Reverse proxy and network boundary

- [ ] Configure `TRUSTED_PROXY_CIDRS` with only the exact proxy peer addresses/CIDRs that connect directly to the app.
- [ ] Run Uvicorn with `--no-proxy-headers`; Gatherly resolves forwarded addresses itself.
- [ ] Ensure the proxy appends the connecting peer to `X-Forwarded-For` and every trusted hop is configured.
- [ ] Block direct public access to the application port so clients cannot bypass the reverse proxy.
- [ ] Verify public HTTPS Origin/Referer mutations succeed and cross-origin mutations fail through the actual proxy path.

## Database and recovery

- [ ] Keep SQLite on a local persistent disk on one application host. Do not share the SQLite file across hosts.
- [ ] Schedule online backups using the documented backup command; store copies off-host and encrypt them at rest.
- [ ] Define access controls and retention for backups.
- [ ] Perform a restore drill in a separate path/environment, run integrity checks, start the app, and verify `/health/ready`.
- [ ] Confirm the independent erasure ledger is available from its off-host remote during the drill, including a deleted account that exists in the selected older backup.
- [ ] Keep migrations append-only and test every new migration from the immediately previous schema.
- [ ] Reassess PostgreSQL and shared/network rate limiting before horizontal scaling or sustained concurrent writes.

## Monitoring and diagnostics

- [ ] Provision a writable persistent log directory or collect standard error through the process manager.
- [ ] Route process supervision to `/health/live` and traffic readiness to `/health/ready`.
- [ ] Restrict database and log access to authorized operators.
- [x] Voluntary diagnostic reports have a configurable expiry policy (90 days by default); verify the configured value and worker schedule.
- [x] Authenticated account data export and confirmed account erasure are implemented, including anonymization of linked participants in shared events.
- [ ] Review the privacy notice, lawful basis, and consent wording for the jurisdictions where Gatherly will operate. Technical export/deletion controls and consent timestamps do not themselves establish GDPR compliance.
- [ ] Review backup retention and define a durable process to reapply erasure requests after restoring an older backup.

## Repository controls

- [ ] Protect `main`: require pull requests and the `quality` workflow on the latest commit.
- [ ] Block force-push and branch deletion; restrict bypass permissions.
- [ ] Verify the ruleset/branch-protection settings in GitHub as a repository administrator. The current connected GitHub integration does not expose administration access, so this checklist item cannot be confirmed from the code repository.

## Release decision

Do not treat the deployment as production-ready until the applicable items above are completed and recorded. Keep unresolved items visible; do not substitute a green CI badge for a deployment, recovery, privacy, or security review.
