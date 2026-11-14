# Production readiness checklist

This checklist separates repository-level implementation from deployment and operator work. A passing CI workflow does not by itself make a production deployment ready.

## Application configuration

- [ ] Set `APP_ENV=production`.
- [ ] Set `PUBLIC_ORIGIN` to the exact public HTTPS origin (scheme, host, optional port; no path).
- [ ] Set `COOKIE_SECURE=true`.
- [ ] Configure real SMTP host, sender, credentials, and STARTTLS. Keep SMTP credentials out of source control and logs.
- [ ] Set public HTTPS verification, password-reset, and participant-invitation URLs.
- [ ] Decide whether the current scrypt password-hash parameters are acceptable for launch or whether to migrate to Argon2id. If changing algorithms, use a versioned hash format and rehash-on-login/reset; never rewrite stored hashes without a verified migration path.
- [ ] Remember that Telegram and email/password currently create separate internal accounts. Do not promise cross-provider account linking.

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
- [ ] Keep migrations append-only and test every new migration from the immediately previous schema.
- [ ] Reassess PostgreSQL and shared/network rate limiting before horizontal scaling or sustained concurrent writes.

## Monitoring and diagnostics

- [ ] Provision a writable persistent log directory or collect standard error through the process manager.
- [ ] Route process supervision to `/health/live` and traffic readiness to `/health/ready`.
- [ ] Restrict database and log access to authorized operators.
- [ ] Define a bug-report retention/deletion schedule and implement expiry before production.
- [ ] Review the privacy notice, lawful basis, and consent wording for the jurisdictions where Gatherly will operate. The technical consent dialog is not a complete legal notice and does not itself establish GDPR compliance.

## Repository controls

- [ ] Protect `main`: require pull requests and the `quality` workflow on the latest commit.
- [ ] Block force-push and branch deletion; restrict bypass permissions.
- [ ] Verify the ruleset/branch-protection settings in GitHub as a repository administrator. The current connected GitHub integration does not expose administration access, so this checklist item cannot be confirmed from the code repository.

## Release decision

Do not treat the deployment as production-ready until the applicable items above are completed and recorded. Keep unresolved items visible; do not substitute a green CI badge for a deployment, recovery, privacy, or security review.
