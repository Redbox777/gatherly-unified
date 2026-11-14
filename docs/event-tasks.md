# Event planning tasks

Tasks belong to one event and are managed by that event's owner.

- A task has a required title, optional description, optional due date, and optional participant assignee.
- The event owner can list, create, edit, complete, reopen, and delete tasks.
- An assignee must be a participant record belonging to the same event.
- Assignment is only a planning label. It does not link a guest to an account or grant event-owner permissions.
- Removing an assigned participant clears the task assignment through the foreign-key `ON DELETE SET NULL` rule.
- Deleting an event deletes its tasks.
- Due dates sent to the API must include a timezone.

The API checks the authenticated event owner for every task operation. Client-side controls are not an authorization boundary.
