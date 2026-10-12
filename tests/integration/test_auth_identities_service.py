from datetime import UTC, datetime

import pytest

from app.application.auth_identities_service import AuthIdentitiesService
from app.infrastructure.database.auth_identities_repository import (
    AuthIdentitiesRepository,
)
from app.infrastructure.database.connection import connect, migrate


NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def service(tmp_path):
    connection = connect(tmp_path / "auth-service.sqlite3")
    migrate(connection)
    yield AuthIdentitiesService(AuthIdentitiesRepository(connection))
    connection.close()


def test_email_registration_normalizes_email_and_hashes_password(service) -> None:
    identity = service.register_email(
        email="  User@Example.Test ",
        password="a long example password",
        now=NOW,
    )

    assert identity.provider_subject == "user@example.test"
    assert identity.verified_at is None
    assert identity.password_hash is not None
    assert identity.password_hash != "a long example password"
    assert service.authenticate_email(
        email="USER@example.test", password="a long example password"
    ) is None


def test_email_authentication_rejects_wrong_password_and_unknown_email(service) -> None:
    service.register_email(
        email="person@example.test",
        password="correct password",
        now=NOW,
    )

    assert service.authenticate_email(
        email="person@example.test", password="incorrect password"
    ) is None
    assert service.authenticate_email(
        email="missing@example.test", password="correct password"
    ) is None


def test_telegram_registration_records_verified_identity(service) -> None:
    identity = service.register_telegram(
        telegram_subject="telegram-42",
        now=NOW,
        verified_at=NOW,
    )

    assert identity.provider == "telegram"
    assert identity.verified_at == NOW
    assert identity.password_hash is None


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("", "password"),
        ("not-an-email", "password"),
        ("person@example.test", ""),
    ],
)
def test_email_registration_rejects_invalid_inputs(service, email, password) -> None:
    with pytest.raises(ValueError):
        service.register_email(email=email, password=password, now=NOW)


def test_telegram_registration_rejects_blank_subject(service) -> None:
    with pytest.raises(ValueError, match="blank"):
        service.register_telegram(telegram_subject="  ", now=NOW, verified_at=NOW)
