from datetime import UTC, datetime, timedelta

import pytest

from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.notifications_repository import NotificationsRepository


def test_due_reminders_are_materialized_once_for_owner_and_linked_members(tmp_path) -> None:
    connection = connect(tmp_path / "notifications.sqlite3")
    try:
        migrate(connection)
        now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
        starts_at = now + timedelta(minutes=45)
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            ("owner", now.isoformat()),
        )
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            ("member", now.isoformat()),
        )
        connection.commit()
        connection.execute(
            "INSERT INTO events(id, owner_id, title, starts_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("event-1", "owner", "Общий ужин", starts_at.isoformat(), now.isoformat()),
        )
        connection.execute(
            "INSERT INTO event_participants(id, event_id, display_name, added_at, linked_user_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("participant-1", "event-1", "Участник", now.isoformat(), "member"),
        )
        repository = NotificationsRepository(connection)
        repository.set_event_reminders(
            event_id="event-1", owner_id="owner", offsets=[60], now=now
        )

        assert repository.materialize_due_reminders(now=now) == 2
        assert repository.materialize_due_reminders(now=now) == 0
        assert repository.unread_count(user_id="owner") == 1
        assert repository.unread_count(user_id="member") == 1
        item = repository.list_for_user(user_id="member")[0]
        assert item["kind"] == "event_reminder"
        assert repository.mark_read(
            user_id="member", notification_id=str(item["id"]), now=now
        )
        assert repository.unread_count(user_id="member") == 0
    finally:
        connection.close()


def test_notification_preferences_default_to_enabled_and_can_be_disabled(tmp_path) -> None:
    connection = connect(tmp_path / "preferences.sqlite3")
    try:
        migrate(connection)
        now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            ("user-1", now.isoformat()),
        )
        connection.commit()
        repository = NotificationsRepository(connection)
        assert repository.get_preferences(user_id="user-1") == {
            "email_enabled": True,
            "event_updates_enabled": True,
            "participant_added_enabled": True,
            "reminders_enabled": True,
        }
        result = repository.set_preferences(
            user_id="user-1",
            values={"email_enabled": False, "reminders_enabled": True},
            now=now,
        )
        assert result["email_enabled"] is False
        assert result["event_updates_enabled"] is True
    finally:
        connection.close()


def test_reminder_offsets_reject_unsupported_values(tmp_path) -> None:
    connection = connect(tmp_path / "invalid-reminder.sqlite3")
    try:
        migrate(connection)
        repository = NotificationsRepository(connection)
        with pytest.raises(ValueError, match="Unsupported reminder offset"):
            repository.set_event_reminders(
                event_id="missing",
                owner_id="owner",
                offsets=[30],
                now=datetime(2026, 11, 13, 12, 0, tzinfo=UTC),
            )
    finally:
        connection.close()
