# Event venues

Each event can have one optional venue record.

- The event owner can create, update, read, and delete the venue.
- Linked Gatherly participants can read the venue; unlinked guest records and unrelated accounts cannot.
- Venue data includes a display name and address; coordinates are optional.
- Coordinates must be supplied as a latitude/longitude pair and stay within geographic bounds.
- Deleting an event cascades to its venue record.
- The event detail panel builds a Google Maps search link from coordinates when present, otherwise from the address. No map SDK or external API key is required.
- The venue read endpoint is available to the owner and linked event participants. Write operations remain owner-only.
