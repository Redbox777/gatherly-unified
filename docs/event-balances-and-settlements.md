# Event balances and settlements

PR35 completes the first event-finance workflow: calculate participant balances from
recorded expenses and allocations, then record an actual settlement between participants.

## Balance semantics

- A positive balance means the participant is owed money.
- A negative balance means the participant owes money.
- Balances are calculated independently for each currency. No exchange rates or FX
  conversion are applied.
- For a split expense, the payer receives a credit equal to the gross expense and each
  participant receives a debit equal to their saved allocation.
- A recorded settlement from A to B adds the amount to A's balance and subtracts it
  from B's balance.
- Expenses without a configured split are reported as pending and excluded from
  participant balances until they are split.
- If a payer or allocation belongs to a participant who has since been removed, the
  historical amount is reported as unresolved rather than silently reassigned.

## Settlement rules

A settlement records a completed transfer, not a payment-provider operation.

- The sender must currently have a negative balance in the selected currency.
- The recipient must currently have a positive balance in that currency.
- The amount must not exceed either participant's outstanding balance.
- Both participants must belong to the event and must be different.
- The event owner is the only actor allowed to read balances or create/list settlements.
- Amounts are exact decimal strings at the API boundary and integer minor units in
  storage. Settlement currency must match the currency of the balances being settled.
- Settlement history is retained. Participant name snapshots survive participant
  deletion; the linked participant IDs become null.

## API

- GET /events/{event_id}/balances
- GET /events/{event_id}/settlements
- POST /events/{event_id}/settlements

Example settlement request:

\`\`\`json
{
  "from_participant_id": "debtor-participant-uuid",
  "to_participant_id": "creditor-participant-uuid",
  "amount": "5.00",
  "currency": "EUR",
  "settled_at": "2026-11-14T18:30:00+00:00",
  "note": "Cash"
}
\`\`\`

The API returns the stored settlement with participant name snapshots and exact decimal
and minor-unit amounts. Balances are recalculated from the expense and settlement
ledger on each request; cached or manually editable balance totals are not stored.

## Scope boundaries

This stage does not initiate transfers, connect payment providers, convert currencies,
or calculate cross-currency netting. A settlement is a record that the participants
have already exchanged the specified amount.
