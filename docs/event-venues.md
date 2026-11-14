# Event venues

Each event can have one optional venue record.

- The event owner can create, update, read, and delete the venue.
- Venue data includes a display name and address; coordinates are optional.
- Coordinates must be supplied as a latitude/longitude pair and stay within geographic bounds.
- Deleting an event cascades to its venue record.
- The event detail panel builds a Google Maps search link from coordinates when present, otherwise from the address. No map SDK or external API key is required.
- Venue records are private to the event owner in this iteration; participant sharing and embedded maps can be added separately.
