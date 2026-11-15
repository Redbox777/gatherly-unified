# Event notes

Notes belong to an event and an authenticated Gatherly account.

- **Private** notes are visible only to their author.
- **Shared** notes are visible to the event owner and participants linked to Gatherly accounts.
- Event members can create notes. Only the author can edit a note.
- The event owner can delete shared notes; authors can delete their own notes.
- Unlinked guest participant records do not have an account and cannot access notes.
- Deleting an event cascades to its notes. Deleting a user cascades to notes authored by that user.

The API enforces membership and visibility in SQLite queries, not only in the web interface.
