# Event expenses

PR33 adds owner-managed expense records to an event.

## Included

- Create, list, edit, and delete event expenses.
- Store amounts as integer minor units and currency codes; the API accepts decimal
  strings and returns both an exact decimal string and canonical minor units.
- Record a title, optional description, payer, and expense date.
- Payer references an event participant, including named guests without Gatherly accounts.
- Validate that the payer belongs to the same event.
- Restrict every read and mutation to the event owner.
- Preserve an expense if its payer participant is removed; the payer reference becomes
  null rather than deleting historical financial data.
- Delete event expenses automatically when their parent event is deleted.

## API

- `GET /events/{event_id}/expenses`
- `POST /events/{event_id}/expenses`
- `PUT /events/{event_id}/expenses/{expense_id}`
- `DELETE /events/{event_id}/expenses/{expense_id}`

Example request:

```json
{
  "title": "Groceries",
  "description": "Food for the dinner",
  "amount": "12.34",
  "currency": "EUR",
  "paid_by_participant_id": "participant-uuid",
  "incurred_at": "2026-11-14T18:30:00+00:00"
}
```

Amounts must be strings, not JSON floating-point numbers. Excess precision is rejected
unless a later interface explicitly offers a rounding choice. Only currencies in the
current finance domain registry are accepted.

## Scope boundary

PR33 records gross expenses and who paid. It does not calculate participant shares,
balances, debts, reimbursements, settlements, exchange rates, or payment transfers.
Those depend on the split rules and balance model introduced in later finance stages.
