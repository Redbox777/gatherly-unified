from concurrent.futures import ThreadPoolExecutor
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
    raw_token = reset.issue(identity_id=identity.identity_id, now=NOW)

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
    raw_token = reset.issue(identity_id=identity.identity_id, now=NOW)

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
        reset.issue(identity_id=identity.identity_id, now=NOW)


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
            identity_id=identity.identity_id,
            now=datetime(2026, 10, 13, 12, 0),
        )



def test_reset_token_only_changes_its_bound_identity(reset_context) -> None:
    connection, _, identity, reset = reset_context
    secondary_password_hash = "secondary-identity-password-hash"
    connection.execute(
        """INSERT INTO auth_identities(
            id, user_id, provider, provider_subject, password_hash, verified_at, created_at
        ) VALUES (?, ?, 'email', ?, ?, ?, ?)""",
        (
            "secondary-identity",
            identity.user_id,
            "secondary@example.test",
            secondary_password_hash,
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    connection.commit()

    raw_token = reset.issue(identity_id=identity.identity_id, now=NOW)
    assert reset.reset_password(
        raw_token=raw_token,
        new_password=NEW_PASSWORD,
        now=NOW + timedelta(minutes=1),
    )

    primary = connection.execute(
        "SELECT password_hash FROM auth_identities WHERE id = ?",
        (identity.identity_id,),
    ).fetchone()
    secondary = connection.execute(
        "SELECT password_hash FROM auth_identities WHERE id = ?",
        ("secondary-identity",),
    ).fetchone()
    assert primary["password_hash"] != identity.password_hash
    assert secondary["password_hash"] == secondary_password_hash


def test_concurrent_password_reset_token_consumption_succeeds_exactly_once(tmp_path) -> None:
    database_path = tmp_path / "concurrent-password-reset.sqlite3"
    setup = connect(database_path)
    migrate(setup)
    identities = AuthIdentitiesService(AuthIdentitiesRepository(setup))
    identity = identities.register_email(
        email="concurrent-reset@example.test",
        password=OLD_PASSWORD,
        now=NOW,
    )
    setup.execute(
        "UPDATE auth_identities SET verified_at = ? WHERE id = ?",
        (NOW.isoformat(), identity.identity_id),
    )
    setup.commit()
    raw_token = PasswordResetService(
        AuthEmailTokensRepository(setup)
    ).issue(identity_id=identity.identity_id, now=NOW)
    setup.close()

    def consume() -> bool:
        connection = connect(database_path)
        try:
            service = PasswordResetService(AuthEmailTokensRepository(connection))
            return service.reset_password(
                raw_token=raw_token,
                new_password=NEW_PASSWORD,
                now=NOW + timedelta(minutes=1),
            )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: consume(), range(2)))

    assert sorted(results) == [False, True]
