# Finance foundation

The finance foundation now includes exact money primitives, event expense records,
configurable participant splits, per-currency balances, and recording of completed
settlements. Currency conversion and payment-provider integrations remain out of
scope. Settlement records describe transfers that have already happened; they do not
initiate payments.

## Money representation

- Monetary values are stored as integer minor units (for example, cents or kopecks).
- Decimal strings or Decimal values are accepted at the domain boundary.
- Binary floating-point values are rejected.
- Amounts with more fractional digits than the currency supports are rejected by
  default. If rounding is needed, the caller must explicitly select a rounding mode.
- Arithmetic is permitted only between values with the same currency.

## Currency metadata

The initial registry supports CHF, EUR, GBP, JPY, KWD, PLN, RUB, and USD, using
their configured minor-unit precision. This is an application-supported subset,
not a complete ISO 4217 catalogue. Add currencies deliberately with their correct
minor-unit precision.

## Rounding

The default conversion is strict: excess precision is rejected instead of silently
changing a user's entered amount. Callers may explicitly request one of these policies:

- `HALF_UP`: ties round away from zero.
- `HALF_EVEN`: ties round to the nearest even minor unit.
- `DOWN`: truncate toward zero.
- `UP`: round away from zero.

Rounding happens only when converting decimal major units into integer minor units.
All subsequent calculations use integers.

## Equal split and remainder policy

Equal allocation operates on integer minor units, so no fractional minor units
can be created. If the total cannot be divided evenly, the remaining units are
assigned one at a time to participants ordered by their normalized, stable participant
ID. The result is deterministic regardless of input order and always sums exactly
to the original amount. Negative totals use the same rule for adjustments.

## Implemented finance capabilities

- Event expense creation, editing, listing, and deletion.
- Equal, fixed, and weighted split modes with exact integer-minor-unit allocations.
- Per-currency participant balances, pending unsplit expenses, and unresolved historical
  records when a participant is removed.
- Settlement recording with debtor/creditor and outstanding-balance validation.
- Historical participant-name snapshots for expenses and settlements.

## Remaining finance boundaries

- No foreign-exchange conversion or cross-currency netting.
- No payment initiation, payment-provider integration, or proof that a recorded
  settlement was independently verified.
- Expense edits and deletions remain corrections to the event ledger; previously
  recorded transfers remain historical facts and continue to affect the net balance.

Expense records must reference event participant IDs, not user IDs, so named guests
and linked accounts retain the same event-level expense history.
