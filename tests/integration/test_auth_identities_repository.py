from datetime import UTC, datetime

import pytest

from app.infrastructure.database.auth_identities_repository import (
    AuthIdentitiesRepository,
)
from app.infrastructure.database.connection import connect, migrate


NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def repository(tmp_path):
    connection = connect(tmp_path / "auth-identities.sqlite3")
    migrate(connection)
    yield AuthIdentitiesRepository(connection)
    connection.close()


def test_email_identity_creates_user_and_stores_password_hash(repository) -> None:
    identity = repository.create_user_with_identity(
        provider="email",
        provider_subject="user@example.test",
        password_hash="argon2id$test-hash",
        now=NOW,
    )

    user = repository.connection.execute(
        "SELECT id FROM users WHERE id = ?", (identity.user_id,)
    ).fetchone()
    stored = repository.connection.execute(
        "SELECT password_hash FROM auth_identities WHERE id = ?",
        (identity.identity_id,),
    ).fetchone()

    assert user is not None
    assert stored["password_hash"] == "argon2id$test-hash"
    assert repository.find_by_provider_subject(
        provider="email", provider_subject="user@example.test"
    ) == identity


def test_telegram_identity_has_no_password_hash(repository) -> None:
    identity = repository.create_user_with_identity(
        provider="telegram",
        provider_subject="telegram-user-123",
        now=NOW,
        verified_at=NOW,
    )

    assert identity.password_hash is None
    assert identity.verified_at == NOW
    assert repository.find_by_provider_subject(
        provider="telegram", provider_subject="telegram-user-123"
    ) == identity


def test_same_email_subject_cannot_create_or_merge_another_user(repository) -> None:
    first = repository.create_user_with_identity(
        provider="email",
        provider_subject="same@example.test",
        password_hash="hash-one",
        now=NOW,
    )

    with pytest.raises(Exception):
        repository.create_user_with_identity(
            provider="email",
            provider_subject="same@example.test",
            password_hash="hash-two",
            now=NOW,
        )

    users = repository.connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    assert users == 1
    assert repository.find_by_provider_subject(
        provider="email", provider_subject="same@example.test"
    ).user_id == first.user_id


@pytest.mark.parametrize(
    ("provider", "subject", "password_hash"),
    [
        ("email", "user@example.test", None),
        ("email", "user@example.test", "  "),
        ("telegram", "123", "should-not-be-stored"),
        ("unknown", "123", None),
        ("telegram", "  ", None),
    ],
)
def test_invalid_identity_inputs_are_rejected(
    repository, provider, subject, password_hash
) -> None:
    with pytest.raises(ValueError):
        repository.create_user_with_identity(
            provider=provider,
            provider_subject=subject,
            password_hash=password_hash,
            now=NOW,
        )

    assert repository.connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_naive_or_future_verification_time_is_rejected(repository) -> None:
    with pytest.raises(ValueError, match="timezone"):
        repository.create_user_with_identity(
            provider="telegram",
            provider_subject="123",
            now=datetime(2026, 10, 12, 12, 0),
        )

    with pytest.raises(ValueError, match="future"):
        repository.create_user_with_identity(
            provider="telegram",
            provider_subject="123",
            now=NOW,
            verified_at=datetime(2026, 10, 12, 12, 1, tzinfo=UTC),
        )

    assert repository.connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
