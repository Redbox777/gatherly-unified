# Event expense splits

PR34 adds configurable participant allocations to the event expense records introduced
in PR33. Split records are owner-managed and reference event participant IDs, including
named guests without Gatherly accounts.

## Supported modes

- `equal`: provide the participants who share the expense. Integer minor units are
  divided evenly; remainder units are assigned by normalized participant ID.
- `fixed`: provide an exact decimal-string amount for every participant. Amounts must
  use the expense currency and sum exactly to the gross expense amount.
- `weighted`: provide a positive integer weight for every participant. Shares are
  proportional to weights, with leftover minor units assigned by largest fractional
  remainder and then participant ID as a stable tie-breaker.

No binary floating-point values are accepted. Allocations are stored as integer minor
units. A zero-value weighted share is valid when a participant's proportional share is
smaller than one minor unit.

## API

- `GET /events/{event_id}/expenses/{expense_id}/split`
- `PUT /events/{event_id}/expenses/{expense_id}/split`

Both endpoints require an authenticated event owner. Participants must belong to the
same event as the expense. The PUT operation replaces the complete allocation set
atomically.

Equal split request:

```json
{
  "mode": "equal",
  "allocations": [
    {"participant_id": "participant-uuid-1"},
    {"participant_id": "participant-uuid-2"}
  ]
}
```

Fixed split request:

```json
{
  "mode": "fixed",
  "allocations": [
    {"participant_id": "participant-uuid-1", "amount": "12.50"},
    {"participant_id": "participant-uuid-2", "amount": "7.50"}
  ]
}
```

Weighted split request:

```json
{
  "mode": "weighted",
  "allocations": [
    {"participant_id": "participant-uuid-1", "weight": 2},
    {"participant_id": "participant-uuid-2", "weight": 1}
  ]
}
```

The response includes the selected mode, exact decimal amount and canonical minor-unit
amount for each allocation, currency, and participant name snapshot.

## Lifecycle and boundaries

- Changing an expense's gross amount or currency invalidates its previous split.
- Changing only the payer or descriptive fields does not invalidate allocations.
- Removing a participant preserves their allocation row and name snapshot; the foreign
  key becomes null so historical data is not silently erased.
- Deleting an expense cascades to its split and allocations.
- Split management remains owner-only, matching PR33 expense management.

This stage does not calculate balances, debts, reimbursements, settlements, currency
conversion, or payment transfers. Those belong to the next finance stage.
