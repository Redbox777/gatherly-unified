# Account linking

Gatherly keeps Email and Telegram as separate login identities. The account-linking endpoints let an authenticated user merge a second, independently verified account into the account used for the current session.

## Security requirements

- The destination account must have an active session created within the previous 10 minutes.
- The source account must be independently proven: a verified Email identity requires its current password; a Telegram identity requires a valid Telegram Login Widget signature.
- Telegram proof must be no older than five minutes and its signed payload is single-use.
- An identity cannot be linked by matching email addresses or Telegram profile names.
- If both accounts already contain an identity from the same provider, the merge is rejected. This avoids ambiguous provider selection and accidental identity replacement.
- The operation is transactional. A failed proof, provider collision, or database error leaves both accounts unchanged.
- All source identities and account-owned data move to the destination. The source user row is deleted only after its references have been transferred.
- Existing sessions for both accounts are revoked. A new session is issued for the destination account after the merge.

## Endpoints

### Link a verified Email account

\`POST /account/link/email\`

Requires the destination account's authenticated session cookie and same-origin request headers.

Request body:

\`\`\`json
{
  "email": "second-account@example.com",
  "password": "the second account password"
}
\`\`\`

The email identity must already be verified. The password is checked against the currently stored Argon2id hash; the hash is re-read after authentication and compared again inside the merge transaction to prevent a password-change race.

### Link a Telegram account

\`POST /account/link/telegram\`

Requires the destination account's authenticated session cookie and same-origin request headers. The request body is the unmodified Telegram Login Widget payload (\`id\`, \`auth_date\`, \`hash\`, plus any other signed fields). The bot token must be configured using \`TELEGRAM_BOT_TOKEN\`.

Telegram Login Widget data is checked cryptographically, restricted to a five-minute age window, and recorded in the existing one-time-payload table to prevent replay.

## Data migration during merge

The merge transfers:

- authentication identities (email and Telegram);
- event ownership;
- authored notes;
- notifications;
- linked event participants;
- participant invitation ownership;
- event reminder ownership;
- notification preferences, preserving destination preferences when already configured.

Email verification and password-reset tokens remain attached to their identity IDs. Credential hashes and bearer tokens are not returned by these endpoints. Existing sessions are revoked rather than transferred.

## Current scope

This API-level increment supports merging two existing accounts where their provider sets do not overlap. It does not yet provide a settings screen, attach a brand-new unregistered provider identity, or resolve conflicts where both accounts already have Email or both already have Telegram. Those cases are deliberately rejected rather than guessed.
