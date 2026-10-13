from datetime import UTC, datetime, timedelta

import pytest

from app.application.auth_identities_service import AuthIdentitiesService
from app.application.password_reset_service import PasswordResetService
from app.infrastructure.database.auth_email_tokens_repository import (
    AuthEmailTokensRepository,
)
from app.infrastructure.database.auth_identities_repository import AuthIdentitiesRepository
from app.infrastructure.database.connection import connect, migrate


NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
OLD_PASSWORD = "a secure example password"
NEW_PASSWORD = "a different secure password"


@pytest.fixture
def reset_context(tmp_path):
    connection = connect(tmp_path / "password-reset.sqlite3")
    migrate(connection)
    identities = AuthIdentitiesService(AuthIdentitiesRepository(connection))
    identity = identities.register_email(
        email="person@example.test",
        password=OLD_PASSWORD,
        now=NOW,
    )
    connection.execute(
        "UPDATE auth_identities SET verified_at = ? WHERE id = ?",
        (NOW.isoformat(), identity.identity_id),
    )
    connection.commit()
    reset = PasswordResetService(
        AuthEmailTokensRepository(connection),
        lifetime=timedelta(minutes=30),
    )
    yield connection, identities, identity, reset
    connection.close()


def test_reset_changes_password_and_token_is_single_use(reset_context) -> None:
    _, identities, identity, reset = reset_context
    raw_token = reset.issue(user_id=identity.user_id, now=NOW)

    assert reset.reset_password(
        raw_token=raw_token,
        new_password=NEW_PASSWORD,
        now=NOW + timedelta(minutes=1),
    )
    assert identities.authenticate_email(
        email="person@example.test", password=OLD_PASSWORD
    ) is None
    assert identities.authenticate_email(
        email="person@example.test", password=NEW_PASSWORD
    ) is not None
    assert not reset.reset_password(
        raw_token=raw_token,
        new_password=OLD_PASSWORD,
        now=NOW + timedelta(minutes=2),
    )


def test_reset_rejects_unknown_expired_and_blank_tokens(reset_context) -> None:
    _, _, identity, reset = reset_context
    raw_token = reset.issue(user_id=identity.user_id, now=NOW)

    assert not reset.reset_password(
        raw_token="unknown-token",
        new_password=NEW_PASSWORD,
        now=NOW + timedelta(minutes=1),
    )
    assert not reset.reset_password(
        raw_token=raw_token,
        new_password=NEW_PASSWORD,
        now=NOW + timedelta(minutes=30),
    )
    assert not reset.reset_password(
        raw_token="   ",
        new_password=NEW_PASSWORD,
        now=NOW + timedelta(minutes=1),
    )


def test_reset_issue_requires_verified_email(reset_context) -> None:
    connection, _, identity, _ = reset_context
    connection.execute(
        "UPDATE auth_identities SET verified_at = NULL WHERE id = ?",
        (identity.identity_id,),
    )
    connection.commit()
    reset = PasswordResetService(AuthEmailTokensRepository(connection))

    with pytest.raises(ValueError, match="verified email identity"):
        reset.issue(user_id=identity.user_id, now=NOW)


def test_reset_service_requires_positive_lifetime(reset_context) -> None:
    connection, _, _, _ = reset_context
    with pytest.raises(ValueError, match="positive"):
        PasswordResetService(
            AuthEmailTokensRepository(connection),
            lifetime=timedelta(0),
        )


def test_reset_rejects_naive_datetime(reset_context) -> None:
    _, _, identity, reset = reset_context
    with pytest.raises(ValueError, match="timezone"):
        reset.issue(
            user_id=identity.user_id,
            now=datetime(2026, 10, 13, 12, 0),
        )
