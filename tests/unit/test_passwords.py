import base64
import hashlib

import pytest

from app.domain.passwords import (
    hash_password,
    password_hash_needs_rehash,
    verify_password,
)


def _legacy_scrypt_hash(password: str) -> str:
    salt = b"legacy-salt-1234"
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    salt_text = base64.urlsafe_b64encode(salt).decode("ascii")
    digest_text = base64.urlsafe_b64encode(digest).decode("ascii")
    return "scrypt$16384$8$1$" + salt_text + "$" + digest_text


def test_hash_password_uses_argon2id_and_verifies_original_password() -> None:
    encoded = hash_password("correct horse battery staple")

    assert encoded.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert verify_password("correct horse battery staple", encoded)
    assert not password_hash_needs_rehash(encoded)


def test_hash_password_uses_a_fresh_salt() -> None:
    first = hash_password("same password")
    second = hash_password("same password")

    assert first != second
    assert verify_password("same password", first)
    assert verify_password("same password", second)


def test_legacy_scrypt_hash_verifies_and_requires_upgrade() -> None:
    encoded = _legacy_scrypt_hash("legacy password")

    assert verify_password("legacy password", encoded)
    assert password_hash_needs_rehash(encoded)
    assert not verify_password("wrong password", encoded)


def test_verify_password_rejects_argon2_hash_with_unbounded_parameters() -> None:
    encoded = hash_password("correct password").replace("m=19456", "m=999999")

    assert not verify_password("correct password", encoded)


def test_verify_password_rejects_malformed_or_unsupported_hash() -> None:
    for encoded in ("", "not-a-hash", "scrypt$1$1$1$a$b", "x$16384$8$1$a$b"):
        assert not verify_password("some password", encoded)


def test_hash_password_rejects_empty_password() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        hash_password("")


def test_hash_password_rejects_oversized_password() -> None:
    with pytest.raises(ValueError, match="too long"):
        hash_password("x" * 1025)
