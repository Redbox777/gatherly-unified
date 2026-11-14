"""Persistence and membership access for a single event venue."""

import sqlite3

from app.infrastructure.database.connection import transaction


class EventVenuesRepository:
    """Store venue details; linked event members may read, owners may write."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get_for_owner(self, *, event_id: str, owner_id: str) -> dict[str, object] | None:
        row = self._connection.execute(
            """SELECT venue.venue_name, venue.address, venue.latitude, venue.longitude
               FROM event_venues AS venue
               JOIN events ON events.id = venue.event_id
               WHERE venue.event_id = ? AND events.owner_id = ?""",
            (event_id, owner_id.strip()),
        ).fetchone()
        return dict(row) if row is not None else None

    def save_for_owner(
        self, *, event_id: str, owner_id: str, venue_name: str,
        address: str, latitude: float | None, longitude: float | None,
    ) -> dict[str, object] | None:
        name = venue_name.strip()
        normalized_address = address.strip()
        if not name or len(name) > 160:
            raise ValueError("Venue name must contain 1 to 160 characters")
        if not normalized_address or len(normalized_address) > 500:
            raise ValueError("Address must contain 1 to 500 characters")
        if (latitude is None) != (longitude is None):
            raise ValueError("Coordinates must be provided together")
        if latitude is not None and not -90 <= latitude <= 90:
            raise ValueError("Latitude is out of range")
        if longitude is not None and not -180 <= longitude <= 180:
            raise ValueError("Longitude is out of range")
        if self._connection.execute(
            "SELECT 1 FROM events WHERE id = ? AND owner_id = ?",
            (event_id, owner_id.strip()),
        ).fetchone() is None:
            return None
        with transaction(self._connection):
            self._connection.execute(
                """INSERT INTO event_venues(
                    event_id, venue_name, address, latitude, longitude
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    venue_name = excluded.venue_name,
                    address = excluded.address,
                    latitude = excluded.latitude,
                    longitude = excluded.longitude""",
                (event_id, name, normalized_address, latitude, longitude),
            )
        return self.get_for_owner(event_id=event_id, owner_id=owner_id)

    def delete_for_owner(self, *, event_id: str, owner_id: str) -> bool:
        if self._connection.execute(
            "SELECT 1 FROM events WHERE id = ? AND owner_id = ?",
            (event_id, owner_id.strip()),
        ).fetchone() is None:
            return False
        with transaction(self._connection):
            cursor = self._connection.execute(
                "DELETE FROM event_venues WHERE event_id = ?", (event_id,)
            )
        return cursor.rowcount == 1
