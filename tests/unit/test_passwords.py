import pytest

from app.domain.passwords import hash_password, verify_password


def test_hash_password_verifies_original_password() -> None:
    encoded = hash_password("correct horse battery staple")

    assert encoded.startswith("scrypt$16384$8$1$")
    assert verify_password("correct horse battery staple", encoded)


def test_hash_password_uses_a_fresh_salt() -> None:
    first = hash_password("same password")
    second = hash_password("same password")

    assert first != second
    assert verify_password("same password", first)
    assert verify_password("same password", second)


def test_verify_password_rejects_wrong_password() -> None:
    encoded = hash_password("correct password")

    assert not verify_password("incorrect password", encoded)


@pytest.mark.parametrize("encoded", ["", "not-a-hash", "scrypt$1$1$1$a$b", "x$16384$8$1$a$b"])
def test_verify_password_rejects_malformed_or_unsupported_hash(encoded: str) -> None:
    assert not verify_password("some password", encoded)


@pytest.mark.parametrize("password", ["", ""])
def test_hash_password_rejects_empty_password(password: str) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        hash_password(password)


def test_hash_password_rejects_oversized_password() -> None:
    with pytest.raises(ValueError, match="too long"):
        hash_password("x" * 1025)
